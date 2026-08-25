# Local Model Control

One console for discovering, installing, switching, chatting with, and delegating work to local GGUF language models. It includes a Windows terminal UI, a dependency-free Python controller, an MCP server for Claude/Codex, and a Codex skill/plugin.

The project is solely for local language-model management. It has no ComfyUI dependency.

## What it does

- Arrow-key model picker and streaming local chat in a Windows `.exe`.
- Safe switching between registered llama.cpp processes and explicit systemd user services.
- Hardware inspection for NVIDIA VRAM, system RAM, free model storage, and `llama-server`.
- Live discovery of recently updated public, non-gated, licensed text-generation GGUF repositories through the official Hugging Face API.
- Conservative fit estimates: single GPU, multi-GPU, CPU/GPU hybrid, or not recommended.
- Confirmed, resumable one-click download, exact-size verification, registration, startup, and real chat smoke test.
- MCP tools and a Codex skill for agent-driven local-model selection and delegation.

Discovery is not an endorsement. Artifact size can estimate capacity, but engine support, architecture, active MoE weights, context memory, chat template, quality, and speed require an actual load test.

## Requirements

- Windows 10/11 with WSL2, or direct Linux use of the Python tools.
- Python 3.10+ in Linux/WSL; no Python packages are required.
- NVIDIA drivers and `nvidia-smi` for GPU-aware estimates.
- A current [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`.
- `curl` for resumable model downloads.
- .NET 6 Desktop Runtime to run the framework-dependent Windows build, or .NET 6 SDK to rebuild it.

## Windows console

Run `dist\LocalModelConsole.exe`. It discovers the copied backend beside the executable and uses the default WSL distribution. Set these only when needed:

```powershell
$env:LOCAL_MODEL_CONTROL_DISTRO = "Ubuntu"
$env:LOCAL_MODEL_CONTROL_MODEL_ROOT = "/data/models/local-model-control"
```

Build:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The main menu can select a registered model, release all registered model processes, or search/install a new GGUF. The installer always displays the exact repository, file, license, size, destination, fit tier, and disk check before asking for confirmation.

## Linux / WSL CLI

```bash
python3 controller.py status
python3 controller.py switch qwen38-27b-q5 --confirm
python3 controller.py stop --confirm

python3 discovery.py hardware
python3 discovery.py discover --limit 10 --search Qwen
python3 discovery.py plan OWNER/REPOSITORY model-Q4_K_M.gguf
python3 discovery.py install OWNER/REPOSITORY model-Q4_K_M.gguf my-model --confirm
```

Built-in profiles in `models.json` are host-scoped examples for `KeyingD`. Other computers start with an empty list and populate `~/.local-model-control/installed-models.json` through the installer. Hand-tuned profiles can also be added there.

## MCP

The server speaks JSON-lines MCP over stdio and exposes hardware inspection, discovery, install planning, confirmed installation, list/status/switch/stop, and local chat tools.

For Claude Desktop, copy `claude-desktop.mcp.example.json` into its MCP configuration and replace the WSL distribution and repository path. If Claude itself runs inside Linux, invoke `python3 /absolute/path/mcp/server.py` directly.

For Codex, this repository is also a plugin. Install it from a configured marketplace, or copy `skills/local-model-control` into the Codex skills directory and register `mcp/server.py` as a stdio MCP server. Start a new Codex thread after installation so the MCP tool inventory is refreshed.

## State and safety

Runtime state, installed profiles, and logs are under `~/.local-model-control`. The controller serializes lifecycle changes. It stops only registered systemd user units or a direct process group it launched and identifies using both PID and process-start ticks; it does not indiscriminately kill GPU processes.

Downloads require confirmation, use resume/retry, enforce public non-gated repositories with a declared license, and verify the exact artifact byte count. Review each model's license and upstream code or custom modeling requirements yourself.

## License

MIT
