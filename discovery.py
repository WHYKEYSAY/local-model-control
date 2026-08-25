#!/usr/bin/env python3
"""Hardware-aware Hugging Face GGUF discovery and confirmed installation."""

from __future__ import annotations

import json
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import controller


GIB = 1024 ** 3
HF_API = "https://huggingface.co/api"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
SPLIT_PATTERN = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
PREFERRED_QUANTS = ("Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S", "IQ4_XS")
KNOWN_QUANTIZERS = {"bartowski", "unsloth", "ggml-org", "mradermacher", "lmstudio-community"}
Progress = Callable[[str], None]


def _json_url(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "local-model-control/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def hardware_profile() -> dict[str, Any]:
    gpus = []
    try:
        query = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        query = None
    if query and query.returncode == 0:
        for line in query.stdout.splitlines():
            fields = [field.strip() for field in line.split(",", 2)]
            if len(fields) == 3:
                gpus.append({"index": int(fields[0]), "name": fields[1], "vram_mib": int(fields[2])})
    meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
    values = {line.split(":", 1)[0]: int(line.split()[1]) for line in meminfo.splitlines() if ":" in line}
    root = model_root()
    root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(root)
    llama_server = find_llama_server()
    return {
        "platform": os.uname().sysname,
        "hostname": os.uname().nodename,
        "gpus": gpus,
        "total_vram_bytes": sum(gpu["vram_mib"] for gpu in gpus) * 1024 ** 2,
        "max_single_gpu_vram_bytes": max((gpu["vram_mib"] for gpu in gpus), default=0) * 1024 ** 2,
        "ram_total_bytes": values.get("MemTotal", 0) * 1024,
        "ram_available_bytes": values.get("MemAvailable", 0) * 1024,
        "model_root": str(root),
        "disk_free_bytes": disk.free,
        "llama_server": llama_server,
    }


def model_root() -> Path:
    configured = os.environ.get("LOCAL_MODEL_CONTROL_MODEL_ROOT")
    if configured:
        return Path(configured).expanduser()
    if Path("/data/models").is_dir() and os.access("/data/models", os.W_OK):
        return Path("/data/models/local-model-control")
    return Path.home() / "models" / "local-model-control"


def find_llama_server() -> str | None:
    configured = os.environ.get("LLAMA_SERVER")
    candidates = [
        configured,
        "/opt/llama.cpp/build-master/bin/llama-server",
        "/usr/local/bin/llama-server",
        shutil.which(configured) if configured else None,
        shutil.which("llama-server"),
    ]
    return next((value for value in candidates if value and Path(value).exists()), None)


def _fit(size: int, hardware: dict[str, Any]) -> dict[str, Any]:
    runtime_overhead = max(2 * GIB, int(size * 0.08))
    estimated = size + runtime_overhead
    single = int(hardware["max_single_gpu_vram_bytes"] * 0.90)
    total = int(hardware["total_vram_bytes"] * 0.88)
    hybrid = total + int(hardware["ram_available_bytes"] * 0.65)
    if not hardware["gpus"] and estimated <= int(hardware["ram_available_bytes"] * 0.65):
        tier, reason = "cpu_only", "Capacity may fit available RAM, but CPU-only generation can be slow."
    elif estimated <= single:
        tier, reason = "single_gpu", "Expected to fit one GPU with conservative runtime headroom."
    elif estimated <= total and len(hardware["gpus"]) > 1:
        tier, reason = "multi_gpu", "Expected to fit combined VRAM; topology can reduce single-stream speed."
    elif estimated <= hybrid:
        tier, reason = "cpu_gpu_hybrid", "Capacity may fit with CPU offload; speed depends strongly on active weights and RAM bandwidth."
    else:
        tier, reason = "not_recommended", "Estimated weights plus runtime state exceed the conservative VRAM/RAM budget."
    return {"tier": tier, "estimated_runtime_bytes": estimated, "reason": reason}


def _license(tags: list[str]) -> str | None:
    item = next((tag for tag in tags if tag.startswith("license:")), None)
    return item.split(":", 1)[1] if item else None


def _artifact_group(siblings: list[dict[str, Any]], selected: dict[str, Any]) -> dict[str, Any] | None:
    filename = selected.get("rfilename", "")
    match = SPLIT_PATTERN.match(filename)
    if not match:
        return {"filename": filename, "files": [selected], "size": int(selected["size"])}
    prefix, _, total_text = match.groups()
    total = int(total_text)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{5}})-of-{total_text}\.gguf$", re.IGNORECASE)
    files = sorted(
        [item for item in siblings if pattern.match(item.get("rfilename", "")) and item.get("size")],
        key=lambda item: item["rfilename"],
    )
    if len(files) != total:
        return None
    return {"filename": files[0]["rfilename"], "files": files, "size": sum(int(item["size"]) for item in files)}


def _choose_file(siblings: list[dict[str, Any]]) -> dict[str, Any] | None:
    ggufs = [item for item in siblings if item.get("rfilename", "").lower().endswith(".gguf") and "mmproj" not in item.get("rfilename", "").lower() and item.get("size")]
    for quant in PREFERRED_QUANTS:
        candidate = next((item for item in ggufs if quant.lower() in item["rfilename"].lower()), None)
        if candidate:
            return _artifact_group(siblings, candidate)
    candidate = min(ggufs, key=lambda item: item["size"], default=None)
    return _artifact_group(siblings, candidate) if candidate else None


def discover_models(limit: int = 10, search: str | None = None) -> dict[str, Any]:
    limit = max(1, min(limit, 25))
    hardware = hardware_profile()
    params = {"filter": "gguf", "sort": "lastModified", "direction": "-1", "limit": str(max(30, limit * 4)), "full": "true"}
    if search:
        params["search"] = search
    summaries = _json_url(f"{HF_API}/models?{urllib.parse.urlencode(params)}")
    results = []
    for summary in summaries:
        if len(results) >= limit:
            break
        repo_id = summary.get("id", "")
        tags = summary.get("tags", [])
        if not REPO_PATTERN.fullmatch(repo_id) or summary.get("gated") or summary.get("private"):
            continue
        license_name = _license(tags)
        if not license_name or "text-generation" not in tags:
            continue
        detail = _json_url(f"{HF_API}/models/{repo_id}?blobs=true")
        artifact = _choose_file(detail.get("siblings", []))
        if not artifact:
            continue
        size = int(artifact["size"])
        author = repo_id.split("/", 1)[0].lower()
        results.append({
            "repo_id": repo_id,
            "filename": artifact["filename"],
            "files": [item["rfilename"] for item in artifact["files"]],
            "size_bytes": size,
            "size_gib": round(size / GIB, 2),
            "license": license_name,
            "last_modified": summary.get("lastModified"),
            "likes": summary.get("likes", 0),
            "downloads": summary.get("downloads", 0),
            "architecture": detail.get("gguf", {}).get("architecture"),
            "context_length": detail.get("gguf", {}).get("context_length"),
            "publisher_signal": "known_quantizer" if author in KNOWN_QUANTIZERS else "unreviewed_publisher",
            "fit": _fit(size, hardware),
            "caveat": "Capacity estimate only. License, chat template, architecture support, quality, and speed still require validation."
        })
    return {"hardware": hardware, "candidates": results}


def install_plan(repo_id: str, filename: str) -> dict[str, Any]:
    if not REPO_PATTERN.fullmatch(repo_id):
        raise controller.ControlError("Invalid Hugging Face repo_id")
    if filename.startswith("/") or ".." in Path(filename).parts or not filename.lower().endswith(".gguf"):
        raise controller.ControlError("Invalid GGUF filename")
    detail = _json_url(f"{HF_API}/models/{repo_id}?blobs=true")
    if detail.get("gated") or detail.get("private"):
        raise controller.ControlError("This installer only handles public, non-gated repositories")
    selected = next((item for item in detail.get("siblings", []) if item.get("rfilename") == filename), None)
    artifact = _artifact_group(detail.get("siblings", []), selected) if selected and selected.get("size") else None
    if not artifact:
        raise controller.ControlError("The requested artifact and size were not found in the repository")
    size = int(artifact["size"])
    license_name = _license(detail.get("tags", []))
    if not license_name:
        raise controller.ControlError("The repository does not declare a license tag; automatic installation is blocked")
    hardware = hardware_profile()
    destination_root = model_root() / repo_id.replace("/", "--")
    artifacts = [
        {
            "filename": item["rfilename"],
            "size_bytes": int(item["size"]),
            "destination": str(destination_root / item["rfilename"]),
        }
        for item in artifact["files"]
    ]
    return {
        "repo_id": repo_id,
        "filename": artifact["filename"],
        "artifacts": artifacts,
        "size_bytes": size,
        "destination": artifacts[0]["destination"],
        "disk_free_bytes": hardware["disk_free_bytes"],
        "enough_disk": hardware["disk_free_bytes"] >= int(size * 1.10),
        "fit": _fit(size, hardware),
        "sha": detail.get("sha"),
        "license": license_name,
        "llama_server": hardware["llama_server"],
    }


def _save_installed(model: dict[str, Any]) -> None:
    data = controller._load_json(controller.INSTALLED_FILE, {"models": []})
    models = [item for item in data.get("models", []) if item.get("id") != model["id"]]
    models.append(model)
    controller.STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = controller.INSTALLED_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"models": models}, indent=2) + "\n", encoding="utf-8")
    temporary.replace(controller.INSTALLED_FILE)


def install_model(
    repo_id: str,
    filename: str,
    model_id: str,
    confirm: bool,
    start_and_smoke: bool = True,
    progress: Progress = lambda _: None,
) -> dict[str, Any]:
    if not confirm:
        raise controller.ControlError("Downloading and registering a model requires confirm=true")
    if not MODEL_ID_PATTERN.fullmatch(model_id):
        raise controller.ControlError("model_id must be 2-64 lowercase letters, digits, or hyphens")
    plan = install_plan(repo_id, filename)
    if not plan["enough_disk"]:
        raise controller.ControlError("Not enough disk space for the artifact plus 10% safety margin")
    if plan["fit"]["tier"] == "not_recommended":
        raise controller.ControlError("Model is not recommended for this hardware; registration aborted")
    server = find_llama_server()
    if not server:
        raise controller.ControlError("llama-server was not found; install llama.cpp or set LLAMA_SERVER before downloading")
    curl = shutil.which("curl")
    if not curl:
        raise controller.ControlError("curl is required for resumable downloads")
    destinations = []
    for index, artifact in enumerate(plan["artifacts"], start=1):
        destination = Path(artifact["destination"])
        destinations.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected = int(artifact["size_bytes"])
        current_size = destination.stat().st_size if destination.exists() else 0
        if current_size != expected:
            url = f"https://huggingface.co/{repo_id}/resolve/{plan['sha']}/{urllib.parse.quote(artifact['filename'])}"
            progress(f"Downloading shard {index}/{len(plan['artifacts'])}: {expected / GIB:.2f} GiB to {destination}")
            subprocess.run([
                curl, "--fail", "--location", "--http1.1", "--retry", "20", "--retry-all-errors",
                "--retry-delay", "3", "--continue-at", "-", "--output", str(destination), url
            ], check=True)
        actual = destination.stat().st_size
        if actual != expected:
            raise controller.ControlError(f"Downloaded size mismatch for {artifact['filename']}: {actual} != {expected}")
    model = {
        "id": model_id,
        "name": f"{repo_id} / {filename}",
        "description": f"User-installed public GGUF; license={plan.get('license')}; fit={plan['fit']['tier']}.",
        "manager": "direct",
        "port": 8002,
        "alias": model_id,
        "gpus": [gpu["name"] for gpu in hardware_profile()["gpus"]],
        "timeout_seconds": 600,
        "required_paths": [str(path) for path in destinations],
        "command": [server, "-m", str(destinations[0]), "--host", "127.0.0.1", "--port", "8002", "--alias", model_id, "-c", "8192", "-ngl", "99", "-fa", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--no-mmap", "--jinja"],
        "source": {"repo_id": repo_id, "filename": filename, "sha": plan["sha"]}
    }
    _save_installed(model)
    smoke = None
    if start_and_smoke:
        progress("Starting the installed model for health and chat validation")
        controller.switch_model(model_id, True, progress)
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with exactly: LOCAL_MODEL_OK"}],
            "max_tokens": 64,
            "temperature": 0,
        }).encode("utf-8")
        request = urllib.request.Request("http://127.0.0.1:8002/v1/chat/completions", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=600) as response:
            smoke_response = json.load(response)
        content = smoke_response.get("choices", [{}])[0].get("message", {}).get("content", "")
        smoke = {"passed": "LOCAL_MODEL_OK" in content, "content": content, "response": smoke_response}
    return {"plan": plan, "registered": model, "smoke": smoke}


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and install hardware-fitting public GGUF models")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hardware")
    discover = sub.add_parser("discover")
    discover.add_argument("--limit", type=int, default=10)
    discover.add_argument("--search")
    plan = sub.add_parser("plan")
    plan.add_argument("repo_id")
    plan.add_argument("filename")
    install = sub.add_parser("install")
    install.add_argument("repo_id")
    install.add_argument("filename")
    install.add_argument("model_id")
    install.add_argument("--confirm", action="store_true")
    install.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "hardware":
            result = hardware_profile()
        elif args.command == "discover":
            result = discover_models(args.limit, args.search)
        elif args.command == "plan":
            result = install_plan(args.repo_id, args.filename)
        else:
            result = install_model(
                args.repo_id, args.filename, args.model_id, args.confirm, not args.no_smoke, _progress
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (controller.ControlError, subprocess.CalledProcessError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
