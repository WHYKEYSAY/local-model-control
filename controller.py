#!/usr/bin/env python3
"""Safe local model lifecycle controller shared by the TUI and MCP server."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
REGISTRY_FILE = ROOT / "models.json"
STATE_DIR = Path.home() / ".local-model-control"
STATE_FILE = STATE_DIR / "state.json"
INSTALLED_FILE = STATE_DIR / "installed-models.json"
LOG_DIR = STATE_DIR / "logs"
LOCK_FILE = STATE_DIR / "lifecycle.lock"
Progress = Callable[[str], None]


class ControlError(RuntimeError):
    pass


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def registry() -> dict[str, dict[str, Any]]:
    data = _load_json(REGISTRY_FILE, {"models": []})
    allowed_hosts = [str(value).lower() for value in data.get("profile_hostnames", [])]
    builtins = data.get("models", []) if not allowed_hosts or os.uname().nodename.lower() in allowed_hosts else []
    installed = _load_json(INSTALLED_FILE, {"models": []}).get("models", [])
    return {item["id"]: item for item in [*builtins, *installed]}


def state() -> dict[str, Any]:
    return _load_json(STATE_FILE, {})


def save_state(value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_FILE)


def _systemd_active(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "active"


def _process_start_ticks(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().split()[21])
    except (FileNotFoundError, IndexError, ValueError):
        return None


def _direct_alive(info: dict[str, Any]) -> bool:
    pid = info.get("pid")
    recorded = info.get("start_ticks")
    return bool(pid and recorded and _process_start_ticks(pid) == recorded)


@contextmanager
def lifecycle_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def healthy(model: dict[str, Any], timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{model['port']}/v1/models", timeout=timeout
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
        return model.get("alias", model["id"]) in body
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def available(model: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [path for path in model.get("required_paths", []) if not Path(path).exists()]
    for artifact in model.get("required_files", []):
        path = Path(artifact["path"])
        expected = int(artifact["size_bytes"])
        if not path.exists():
            missing.append(str(path))
        elif path.stat().st_size != expected:
            missing.append(f"{path} (size {path.stat().st_size} != {expected})")
    if model.get("manager") == "systemd":
        unit = model.get("unit", "")
        probe = subprocess.run(
            ["systemctl", "--user", "cat", unit], capture_output=True, check=False
        )
        if probe.returncode != 0:
            missing.append(f"systemd:{unit}")
    elif model.get("command") and not Path(model["command"][0]).exists():
        missing.append(model["command"][0])
    return not missing, missing


def is_live(model: dict[str, Any], current_state: dict[str, Any] | None = None) -> bool:
    if model.get("manager") == "systemd":
        return _systemd_active(model["unit"])
    info = (current_state or state()).get("direct", {})
    return info.get("model_id") == model["id"] and _direct_alive(info)


def list_models() -> list[dict[str, Any]]:
    current_state = state()
    output = []
    for model in registry().values():
        is_available, missing = available(model)
        live = is_live(model, current_state)
        output.append(
            {
                "id": model["id"],
                "name": model["name"],
                "description": model.get("description", ""),
                "port": model["port"],
                "alias": model.get("alias", model["id"]),
                "gpus": model.get("gpus", []),
                "available": is_available,
                "missing": missing,
                "running": live,
                "healthy": live and healthy(model),
            }
        )
    return output


def status() -> dict[str, Any]:
    models = list_models()
    active = next((model for model in models if model["healthy"]), None)
    return {
        "active": active,
        "models": models,
    }


def _stop_direct(progress: Progress) -> None:
    current_state = state()
    info = current_state.get("direct", {})
    pid = info.get("pid")
    if _direct_alive(info):
        progress(f"Stopping {info.get('model_id', 'direct model')} (pid {pid})")
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 20
        while _direct_alive(info) and time.monotonic() < deadline:
            time.sleep(0.5)
        if _direct_alive(info):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    current_state.pop("direct", None)
    save_state(current_state)


def _stop_all_unlocked(progress: Progress) -> None:
    _stop_direct(progress)
    for model in registry().values():
        if model.get("manager") == "systemd" and _systemd_active(model["unit"]):
            progress(f"Stopping {model['name']}")
            subprocess.run(["systemctl", "--user", "stop", model["unit"]], check=True)


def stop_all(confirm: bool, progress: Progress = lambda _: None) -> dict[str, Any]:
    if not confirm:
        raise ControlError("Stopping models requires confirm=true")
    with lifecycle_lock():
        _stop_all_unlocked(progress)
        return status()


def _wait_healthy(model: dict[str, Any], progress: Progress) -> None:
    timeout = int(model.get("timeout_seconds", 300))
    deadline = time.monotonic() + timeout
    last_notice = 0.0
    while time.monotonic() < deadline:
        if healthy(model):
            return
        if model.get("manager") == "direct":
            info = state().get("direct", {})
            if not _direct_alive(info):
                raise ControlError(f"{model['name']} exited during startup; see {info.get('log')}")
        now = time.monotonic()
        if now - last_notice >= 10:
            remaining = max(0, int(deadline - now))
            progress(f"Loading {model['name']} (up to {remaining}s remaining)")
            last_notice = now
        time.sleep(2)
    raise ControlError(f"{model['name']} was not healthy within {timeout}s")


def switch_model(
    model_id: str,
    confirm: bool,
    progress: Progress = lambda _: None,
) -> dict[str, Any]:
    with lifecycle_lock():
        models = registry()
        model = models.get(model_id)
        if model is None:
            raise ControlError(f"Unknown registered model: {model_id}")
        is_available, missing = available(model)
        if not is_available:
            raise ControlError(f"Model is unavailable; missing: {', '.join(missing)}")
        if healthy(model):
            return {"changed": False, "model": model_id, "status": status()}
        if not confirm:
            raise ControlError("Switching models stops the current GPU owner; pass confirm=true")
        _stop_all_unlocked(progress)
        if model.get("manager") == "systemd":
            progress(f"Starting {model['name']} through {model['unit']}")
            subprocess.run(["systemctl", "--user", "start", model["unit"]], check=True)
        else:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"{model_id}.log"
            environment = os.environ.copy()
            environment.update({str(k): str(v) for k, v in model.get("env", {}).items()})
            progress(f"Starting {model['name']}; log: {log_path}")
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} =====\n")
                process = subprocess.Popen(
                    model["command"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    start_new_session=True,
                )
            current_state = state()
            current_state["direct"] = {
                "model_id": model_id,
                "pid": process.pid,
                "start_ticks": _process_start_ticks(process.pid),
                "port": model["port"],
                "log": str(log_path),
                "started_at": time.time(),
            }
            save_state(current_state)

        try:
            _wait_healthy(model, progress)
        except Exception:
            if model.get("manager") == "systemd":
                subprocess.run(["systemctl", "--user", "stop", model["unit"]], check=False)
            else:
                _stop_direct(progress)
            raise
        progress(f"{model['name']} is healthy on port {model['port']}")
        return {"changed": True, "model": model_id, "status": status()}


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local model lifecycle controller")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    switch = sub.add_parser("switch")
    switch.add_argument("model_id")
    switch.add_argument("--confirm", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "list":
            result: Any = list_models()
        elif args.command == "status":
            result = status()
        elif args.command == "switch":
            result = switch_model(
                args.model_id,
                args.confirm,
                _progress,
            )
        else:
            result = stop_all(args.confirm, _progress)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ControlError, subprocess.CalledProcessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
