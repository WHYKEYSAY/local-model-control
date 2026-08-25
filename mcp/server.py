#!/usr/bin/env python3
"""Dependency-free stdio MCP server for the local model controller."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import controller  # noqa: E402
import discovery  # noqa: E402


PROTOCOL_VERSION = "2025-06-18"


TOOLS = [
    {
        "name": "inspect_local_hardware",
        "description": "Inspect local GPUs, VRAM, RAM, model disk, and llama-server availability for model-fit decisions.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "discover_recent_open_models",
        "description": "Discover recent open-weight models from Artificial Analysis and Ollama, estimate local capacity fit, and expose official-weight links before resolving exact Hugging Face GGUF artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "minimum": 1, "maximum": 24, "default": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "discover_hf_gguf_models",
        "description": "Find and rank public, non-gated, licensed text or multimodal GGUFs on Hugging Face and estimate single-GPU, multi-GPU, hybrid, or unsuitable capacity fit. Estimates require real load validation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                "search": {"type": "string", "description": "Optional Hugging Face search text."}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_hf_gguf_install",
        "description": "Resolve an exact public GGUF artifact, license, size, destination, disk margin, and hardware-fit estimate without downloading it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {"type": "string"},
                "filename": {"type": "string"}
            },
            "required": ["repo_id", "filename"],
            "additionalProperties": False,
        },
    },
    {
        "name": "install_hf_gguf_model",
        "description": "Download an exact planned public GGUF with resume and size verification, register it, then optionally start and smoke-test it. This writes multiple GB and changes GPU process state; confirm=true is required.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {"type": "string"},
                "filename": {"type": "string"},
                "model_id": {"type": "string", "description": "New lowercase local ID."},
                "confirm": {"type": "boolean"},
                "start_and_smoke": {"type": "boolean", "default": True}
            },
            "required": ["repo_id", "filename", "model_id", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_local_models",
        "description": "List registered local LLMs, availability, GPU placement, speed notes, and health.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "local_model_status",
        "description": "Return the active local LLM and health of every registered model.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "switch_local_model",
        "description": "Stop the current registered model, start the selected model, and wait for health. This changes GPU process state and requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string", "description": "Exact ID returned by list_local_models."},
                "confirm": {"type": "boolean", "description": "Must be true to authorize the switch."}
            },
            "required": ["model_id", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stop_local_model",
        "description": "Stop the active registered local LLM and leave all models off. Requires confirm=true.",
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "boolean"}},
            "required": ["confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_local_model",
        "description": "Send one task to the currently active local LLM through its OpenAI-compatible endpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "system": {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 16384, "default": 2048},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.2},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"], "default": "medium"}
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "chat_local_model",
        "description": "Send an OpenAI-format message history to the active local LLM. Useful for multi-turn delegation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["system", "user", "assistant", "tool"]},
                            "content": {}
                        },
                        "required": ["role", "content"]
                    }
                },
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 16384, "default": 2048},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.2},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"], "default": "medium"}
            },
            "required": ["messages"],
            "additionalProperties": False,
        },
    },
]


def _active_model() -> dict[str, Any]:
    active = controller.status().get("active")
    if not active:
        raise controller.ControlError("No healthy local model is active. List and switch a model first.")
    return active


def _chat(messages: list[dict[str, Any]], max_tokens: int, temperature: float, reasoning_effort: str = "medium") -> dict[str, Any]:
    active = _active_model()
    payload = {
        "model": active["alias"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": reasoning_effort,
        "stream": False,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{active['port']}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise controller.ControlError(f"Local model HTTP {error.code}: {detail[:1000]}") from error
    choice = result.get("choices", [{}])[0]
    message = choice.get("message", {})
    return {
        "model_id": active["id"],
        "model": result.get("model", active["alias"]),
        "content": message.get("content", ""),
        "reasoning_content": message.get("reasoning_content"),
        "tool_calls": message.get("tool_calls"),
        "finish_reason": choice.get("finish_reason"),
        "usage": result.get("usage"),
        "timings": result.get("timings"),
    }


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "inspect_local_hardware":
        return discovery.hardware_profile()
    if name == "discover_recent_open_models":
        return discovery.discover_recent_catalog(int(arguments.get("months", 4)), int(arguments.get("limit", 30)))
    if name == "discover_hf_gguf_models":
        return discovery.discover_models(int(arguments.get("limit", 10)), arguments.get("search"))
    if name == "plan_hf_gguf_install":
        return discovery.install_plan(arguments["repo_id"], arguments["filename"])
    if name == "install_hf_gguf_model":
        return discovery.install_model(
            arguments["repo_id"],
            arguments["filename"],
            arguments["model_id"],
            bool(arguments.get("confirm")),
            bool(arguments.get("start_and_smoke", True)),
        )
    if name == "list_local_models":
        return controller.list_models()
    if name == "local_model_status":
        return controller.status()
    if name == "switch_local_model":
        return controller.switch_model(arguments["model_id"], bool(arguments.get("confirm")))
    if name == "stop_local_model":
        return controller.stop_all(bool(arguments.get("confirm")))
    if name == "ask_local_model":
        messages = []
        if arguments.get("system"):
            messages.append({"role": "system", "content": arguments["system"]})
        messages.append({"role": "user", "content": arguments["prompt"]})
        return _chat(messages, int(arguments.get("max_tokens", 2048)), float(arguments.get("temperature", 0.2)), arguments.get("reasoning_effort", "medium"))
    if name == "chat_local_model":
        return _chat(arguments["messages"], int(arguments.get("max_tokens", 2048)), float(arguments.get("temperature", 0.2)), arguments.get("reasoning_effort", "medium"))
    raise controller.ControlError(f"Unknown tool: {name}")


def response(message_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result
    return value


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        return response(message_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "local-model-control", "version": "0.2.0"},
        })
    if method == "ping":
        return response(message_id, {})
    if method == "tools/list":
        return response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params", {})
        try:
            result = call_tool(params.get("name", ""), params.get("arguments", {}))
            return response(message_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                "structuredContent": {"result": result},
                "isError": False,
            })
        except Exception as error:  # MCP tools return operational failures as tool errors.
            return response(message_id, {
                "content": [{"type": "text", "text": str(error)}],
                "isError": True,
            })
    if method and method.startswith("notifications/"):
        return None
    if message_id is not None:
        return response(message_id, error={"code": -32601, "message": f"Method not found: {method}"})
    return None


def main() -> None:
    for raw_line in sys.stdin.buffer:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
            result = handle(message)
            if result is not None:
                sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as error:
            sys.stdout.write(json.dumps(response(None, error={"code": -32603, "message": str(error)})) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
