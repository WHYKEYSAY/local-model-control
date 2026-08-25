# Operations reference

## Safety boundary

The controller only stops exact registered systemd user units or the direct process group it started and recorded with Linux process start ticks. It does not search for and kill arbitrary GPU processes. Lifecycle operations are serialized with a file lock.

`switch_local_model` and `stop_local_model` require `confirm=true`. `install_hf_gguf_model` requires it because installation downloads data, writes a persistent registry, and may replace the current GPU owner.

## Fit interpretation

- `single_gpu`: weights plus conservative overhead fit within 90% of one GPU's detected VRAM.
- `multi_gpu`: estimated runtime fits within 88% of combined VRAM.
- `cpu_gpu_hybrid`: estimated runtime fits only after adding 65% of currently available RAM.
- `cpu_only`: no NVIDIA GPU was detected and the estimate fits 65% of currently available RAM; expect substantially lower speed.
- `not_recommended`: the conservative capacity budget is exceeded and automatic installation is blocked.

The estimator uses artifact bytes plus an overhead allowance. It cannot infer active MoE weights, memory topology, KV-cache needs at every context length, supported operators, chat-template correctness, or achieved tokens per second. Validate those after installation.

## User-installed profiles

Downloaded artifacts default to the existing writable model directory with the most free space among `/data/models`, `~/models`, and WSL drive model directories such as `/mnt/d/models`. Override this with `LOCAL_MODEL_CONTROL_MODEL_ROOT`.

Profiles are stored in `~/.local-model-control/installed-models.json`. Runtime state and logs live under `~/.local-model-control/`. To add a hand-tuned profile, use a unique lowercase ID, an argv array rather than a shell command, exact required paths, a localhost port, an alias, and a realistic startup timeout.

## Failure diagnosis

1. Read `local_model_status` and the missing-path fields.
2. Confirm `llama-server` exists and the artifact size matches the install plan.
3. Inspect the exact log path returned while loading.
4. Check port ownership and available VRAM without killing unrelated processes.
5. Adjust a user profile only after identifying the specific engine, memory, or template failure.
