# Recent open-model survey and local validation — August 2026

Survey date: 2026-08-25. Test host: RTX 5090 32 GB + RTX 5080 16 GB, approximately 95 GB system RAM.

## Selection method

The controller now collects recent model leads from [Artificial Analysis](https://artificialanalysis.ai/models) and the [Ollama registry](https://ollama.com/search). It estimates Q4 capacity from total parameters, ranks models that may fit the host, and uses the [Hugging Face API](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api) to resolve exact public GGUF files, license metadata, immutable revisions, and byte counts. Capacity estimates remain deliberately conservative and are not performance claims.

## Candidates

| Model | Release | Parameters | License shown upstream | Local decision |
|---|---:|---:|---|---|
| Qwen3.8 27B | 2026-08-14 | 27B dense | Apache-2.0 | Existing validated profile; strong coding/general default |
| Muse Glimmer 30B | 2026-08-10 | 30B dense | Apache-2.0 | Installed and validated with vision and DFlash |
| NVIDIA Nemotron 3.5 Lightning | 2026-08-11 | 31.6B total / 3.6B active | OpenMDW-1.1 | Selected for local validation |
| Mistral Medium 3.5 | 2026-04-29 | 128B dense | Modified MIT | Hybrid-only estimate; deferred because it would be much slower than the single-GPU candidates |
| Solar Open2 | 2026-08-12 | 250B total / 15B active | Upstage Solar License | Rejected: estimated Q4 weights exceed the conservative VRAM/RAM budget |
| Kimi K3, Qwen3.8 2.4T, DeepSeek V4 Pro, MiniMax M3 | 2026-06 to 2026-08 | 428B–2.8T total | Varies | Rejected: weights are far beyond this workstation's capacity |

Licenses are recorded as upstream metadata, not legal advice. Review the complete license before redistribution or commercial use.

## Muse Glimmer result

Exact official artifacts from `meta-models/Muse-Glimmer-30B-GGUF` were pinned and byte-verified:

- Q4_K_M main model: 16,756,683,904 bytes
- vision projector: 1,400,328,928 bytes
- DFlash speculative drafter: 1,631,208,128 bytes

The older local llama.cpp build could not load the architecture, so a separate current CUDA build was created at `/opt/llama-latest`; the existing engine was left untouched. The model loaded wholly on the RTX 5090 at a 32,768-token context with the vision projector and DFlash enabled.

Measured decode rates across three smoke runs were 76–78 tok/s for native completion, 78–90 tok/s for Chinese instruction following, 100–113 tok/s for code, 89–92 tok/s for native tool calling, 86–101 tok/s for short reasoning, and 57.7 tok/s for image understanding. The image description matched the inspected source image. `reasoning_effort=low` gave the best routine latency and obeyed the three-sentence Chinese constraint.

Quality caveat: one of three code generations had a list/tuple mismatch in its assertions, and one reasoning answer contained a contradictory sentence before calculating the correct result. Muse is therefore registered as a fast multimodal/general profile, while Qwen remains preferable for strict coding consistency. Raw responses and timings are preserved under `results/`.

## Nemotron 3.5 compatibility investigation

The pinned `ggml-org/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF` Q4_0 main artifact is 18,898,091,584 bytes and its external MTP artifact is 1,155,907,520 bytes. The main GGUF declares zero KV heads for every layer even though six blocks contain attention tensors. Unmodified llama.cpp consequently misclassifies block 5 as recurrent and fails while looking for `blk.5.ssm_in.weight`.

The isolated `/opt/llama-latest` build was patched to detect those attention tensors and recover two KV heads from their dimensions. The exact patch is preserved in `patches/llama-cpp-nemotron35-zero-kv-heads.patch`; it does not alter the user's existing `/opt/llama.cpp` builds. The external MTP GGUF independently triggers `vector::_M_range_check` while loading in the tested runtime, so MTP is not enabled.

The patched main model did load completely on the RTX 5090 with a 32,768-token context, but a cold start from the Windows-mounted model disk took 9 minutes 32 seconds. Without MTP it decoded at only 16.6–17.4 tok/s. Tool calling and the short percentage answer were correct, while the Chinese and coding tests exhausted 768 tokens in hidden reasoning and returned no final content. This result is not competitive with the existing validated profiles, so Nemotron 3.5 Lightning is deliberately not registered in the model switcher. Raw evidence is under `results/20260825-nemotron-3.5-lightning/`.

## Reproduction

```bash
python3 discovery.py catalog --months 4 --limit 30
python3 benchmarks/smoke.py --model muse-glimmer-30b --reasoning-effort low \
  --output results/muse/smoke.json
```

The generic benchmark records complete responses, tool calls, server timings, and speculative-decoding acceptance data. A successful load is necessary but not sufficient: each newly installed architecture should still be checked for output format, coding correctness, reasoning consistency, vision grounding when applicable, and throughput on the target machine.
