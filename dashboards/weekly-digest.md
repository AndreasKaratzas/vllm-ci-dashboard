# Weekly Digest

Week of 2026-05-02 to 2026-05-09

## New Releases

- **vllm**: [v0.20.1](https://github.com/vllm-project/vllm/releases/tag/v0.20.1)

## PRs This Week

### vllm
- Opened: [#42126](https://github.com/vllm-project/vllm/pull/42126) [CI][AMD] Skip tests where models have problems or fails on  (@rasmith)
- Opened: [#42104](https://github.com/vllm-project/vllm/pull/42104) [CI] set max transformers version for skywork model (@divakar-amd)
- Opened: [#41577](https://github.com/vllm-project/vllm/pull/41577) [ROCm][CI] Fix ROCm LoRA Transformers fallback with full CUD (@AndreasKaratzas)
- Opened: [#41572](https://github.com/vllm-project/vllm/pull/41572) [ROCm][CI] Skip ROCm batch invalid-input test pending torch  (@AndreasKaratzas)
- Opened: [#41573](https://github.com/vllm-project/vllm/pull/41573) [ROCm][CI] Stabilize ROCm shutdown and distributed compile C (@AndreasKaratzas)
- Opened: [#41532](https://github.com/vllm-project/vllm/pull/41532) [ROCm][CI] Gate incompatible HF references on Transformers v (@AndreasKaratzas)
- Opened: [#41575](https://github.com/vllm-project/vllm/pull/41575) [ROCm][CI] Use vLLM generation defaults for DeepSeek prefetc (@AndreasKaratzas)
- Opened: [#41569](https://github.com/vllm-project/vllm/pull/41569) [ROCm][CI] Fix MLA prefill scale for DeepSeek GSM8K (@AndreasKaratzas)
- Merged: [#39136](https://github.com/vllm-project/vllm/pull/39136) [ROCm][Quantization][2/N] Refactor quark_moe w4a8 w/ oracle  (@BowenBao)
- Merged: [#41335](https://github.com/vllm-project/vllm/pull/41335) [ROCm][CI] Align spec decode logprob test prefill settings (@AndreasKaratzas)
- Merged: [#41210](https://github.com/vllm-project/vllm/pull/41210) [ROCm][CI] Upgraded UCX and RIXL (@AndreasKaratzas)
- Merged: [#41341](https://github.com/vllm-project/vllm/pull/41341) [ROCm][CI] Add ROCm score absolute tolerance floor (@AndreasKaratzas)

## New Issues This Week

### vllm
- [#41854](https://github.com/vllm-project/vllm/issues/41854) [CI Failure]:  mi300_1: Multi-Modal Models (Extended Generat (@AndreasKaratzas)
- [#41852](https://github.com/vllm-project/vllm/issues/41852) [CI Failure]:  mi300_4: RayExecutorV2 (4 GPUs) (@AndreasKaratzas)
- [#42020](https://github.com/vllm-project/vllm/issues/42020) [CI Failure]:  mi300_1: Multi-Modal Models (Extended Generat (@AndreasKaratzas)
- [#41989](https://github.com/vllm-project/vllm/issues/41989) [CI Failure]:  mi300_1: Quantized Models Test (@AndreasKaratzas)
- [#41584](https://github.com/vllm-project/vllm/issues/41584) [CI Failure]:  mi325_1: Language Models Test (Extended Gener (@AndreasKaratzas)
- [#41583](https://github.com/vllm-project/vllm/issues/41583) [CI Failure]:  mi355_2: LM Eval Small Models (2xB200-2xMI355 (@AndreasKaratzas)
- [#41582](https://github.com/vllm-project/vllm/issues/41582) [CI Failure]:  mi355_1: Entrypoints Integration (API Server  (@AndreasKaratzas)
- [#41581](https://github.com/vllm-project/vllm/issues/41581) [CI Failure]:  mi300_2: Distributed Compile Unit Tests (2xH1 (@AndreasKaratzas)
- [#41580](https://github.com/vllm-project/vllm/issues/41580) [CI Failure]:  mi355_1: Entrypoints Integration (API Server  (@AndreasKaratzas)
- [#41579](https://github.com/vllm-project/vllm/issues/41579) [CI Failure]:  mi300_1: DeepSeek V2-Lite Prefetch Offload Ac (@AndreasKaratzas)
- [#41578](https://github.com/vllm-project/vllm/issues/41578) [CI Failure]:  mi250_1: LoRA %N (@AndreasKaratzas)
