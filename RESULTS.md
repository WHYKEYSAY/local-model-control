# Validation results

Validated on 2026-08-25 on `KeyingD` (WSL2), with an NVIDIA GeForce RTX 5090 32 GB, RTX 5080 16 GB, and 88 GB available system RAM.

## Completed checks

- Python syntax and JSON parsing passed.
- Core unit tests passed for complete multi-file GGUF grouping, incomplete-shard rejection, CPU-only classification, and MCP initialization/tool inventory.
- Codex skill and plugin passed the official `quick_validate.py` and `validate_plugin.py` validators.
- Windows `win-x64` .NET 6 console built without warnings and launched from `dist/LocalModelConsole.exe`.
- The Windows app automatically mapped its own directory into WSL, loaded all nine host profiles, rendered the arrow-key menu, and exited without changing model state.
- Official Hugging Face API discovery returned public, non-gated, license-tagged GGUF candidates with artifact metadata and hardware-fit estimates.
- Exact install planning resolved repository commit SHA, license, artifact byte count, destination, fit, and disk safety margin. No model was downloaded during this test.
- The controller switched from no model to Qwen3.8 27B Q5, waited until `/v1/models` was healthy, then the stdio MCP `ask_local_model` tool returned exactly `LOCAL_MCP_OK`.
- Measured MCP smoke request: 125.52 prompt tok/s, 95.12 generation tok/s, and 22 of 24 draft tokens accepted.
- The controller stopped the direct process by its recorded process group and start ticks. Final state: no active registered model; `keying-122b.service` inactive.

## Known limits

- Fit is a conservative capacity estimate, not a benchmark or compatibility verdict.
- The automatic installer requires an existing current `llama-server` and `curl`.
- Gated/private or no-license repositories are intentionally blocked.
- The current host had only about 7.6 GiB free at the default `/data` model root during validation, so large downloads would be blocked until space is freed or `LOCAL_MODEL_CONTROL_MODEL_ROOT` points elsewhere.
