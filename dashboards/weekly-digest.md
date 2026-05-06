# Weekly Digest

Week of 2026-04-29 to 2026-05-06

## New Releases

- **vllm**: [v0.20.1](https://github.com/vllm-project/vllm/releases/tag/v0.20.1)

## PRs This Week

### vllm
- Opened: [#41575](https://github.com/vllm-project/vllm/pull/41575) [ROCm][CI] Use vLLM generation defaults for DeepSeek prefetc (@AndreasKaratzas)
- Opened: [#41572](https://github.com/vllm-project/vllm/pull/41572) [ROCm][CI] Skip ROCm batch invalid-input test pending torch  (@AndreasKaratzas)
- Opened: [#41532](https://github.com/vllm-project/vllm/pull/41532) [ROCm][CI] Gate incompatible HF references on Transformers v (@AndreasKaratzas)
- Opened: [#41569](https://github.com/vllm-project/vllm/pull/41569) [ROCm][CI] Fix MLA prefill scale for DeepSeek GSM8K (@AndreasKaratzas)
- Opened: [#41335](https://github.com/vllm-project/vllm/pull/41335) [ROCm][CI] Align spec decode logprob test prefill settings (@AndreasKaratzas)
- Opened: [#41313](https://github.com/vllm-project/vllm/pull/41313) [ROCm][CI] Fix NIXL spec-decode acceptance startup and diagn (@AndreasKaratzas)
- Opened: [#41573](https://github.com/vllm-project/vllm/pull/41573) [ROCm][CI] Stabilize ROCm shutdown and distributed compile C (@AndreasKaratzas)
- Opened: [#41577](https://github.com/vllm-project/vllm/pull/41577) [ROCm][CI] Fix ROCm LoRA Transformers fallback with full CUD (@AndreasKaratzas)
- Opened: [#41210](https://github.com/vllm-project/vllm/pull/41210) [ROCm][CI] Upgraded UCX and RIXL (@AndreasKaratzas)
- Opened: [#41341](https://github.com/vllm-project/vllm/pull/41341) [ROCm][CI] Add ROCm score absolute tolerance floor (@AndreasKaratzas)
- Opened: [#41294](https://github.com/vllm-project/vllm/pull/41294) [ROCm][CI] Fix and stabilize EAGLE3 acceptance tests (@AndreasKaratzas)
- Opened: [#41290](https://github.com/vllm-project/vllm/pull/41290) [Bugfix][CI][Hardware][AMD] Fix various e4m3fn -> e4m3fnuz n (@mawong-amd)
- Opened: [#41330](https://github.com/vllm-project/vllm/pull/41330) [ROCm][CI] Fix GPT-OSS Quark MXFP4+FP8 MoE startup (@AndreasKaratzas)
- Merged: [#39136](https://github.com/vllm-project/vllm/pull/39136) [ROCm][Quantization][2/N] Refactor quark_moe w4a8 w/ oracle  (@BowenBao)

## New Issues This Week

### vllm
- [#41584](https://github.com/vllm-project/vllm/issues/41584) [CI Failure]:  mi325_1: Language Models Test (Extended Gener (@AndreasKaratzas)
- [#41583](https://github.com/vllm-project/vllm/issues/41583) [CI Failure]:  mi355_2: LM Eval Small Models (2xB200-2xMI355 (@AndreasKaratzas)
- [#41582](https://github.com/vllm-project/vllm/issues/41582) [CI Failure]:  mi355_1: Entrypoints Integration (API Server  (@AndreasKaratzas)
- [#41581](https://github.com/vllm-project/vllm/issues/41581) [CI Failure]:  mi300_2: Distributed Compile Unit Tests (2xH1 (@AndreasKaratzas)
- [#41580](https://github.com/vllm-project/vllm/issues/41580) [CI Failure]:  mi355_1: Entrypoints Integration (API Server  (@AndreasKaratzas)
- [#41579](https://github.com/vllm-project/vllm/issues/41579) [CI Failure]:  mi300_1: DeepSeek V2-Lite Prefetch Offload Ac (@AndreasKaratzas)
- [#41578](https://github.com/vllm-project/vllm/issues/41578) [CI Failure]:  mi250_1: LoRA %N (@AndreasKaratzas)
- [#41321](https://github.com/vllm-project/vllm/issues/41321) [CI Failure]:  mi300_1: Acceptance Length Test (Large Models (@AndreasKaratzas)
- [#41342](https://github.com/vllm-project/vllm/issues/41342) [CI Failure]:  mi355_1: Entrypoints Integration (Pooling) (@AndreasKaratzas)
- [#41324](https://github.com/vllm-project/vllm/issues/41324) [CI Failure]:  mi355_2: GPQA Eval (GPT-OSS) (2xB200-2xMI355) (@AndreasKaratzas)
- [#41336](https://github.com/vllm-project/vllm/issues/41336) [CI Failure]:  mi355_1: V1 Sample + Logits (@AndreasKaratzas)
- [#41295](https://github.com/vllm-project/vllm/issues/41295) [CI Failure]:  mi355_1: Quantization (@AndreasKaratzas)
- [#41323](https://github.com/vllm-project/vllm/issues/41323) [CI Failure]:  mi300_1: V1 Core + KV + Metrics (@AndreasKaratzas)
- [#41319](https://github.com/vllm-project/vllm/issues/41319) [CI Failure]:  mi355_2: NixlConnector PD + Spec Decode accep (@AndreasKaratzas)
