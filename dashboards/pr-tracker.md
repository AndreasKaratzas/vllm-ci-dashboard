# PR Tracker

All tracked PRs across projects, grouped by project.

## vllm (Upstream Watch)
Repo: `vllm-project/vllm` | Last collected: 2026-11-07T12:57:10Z

| # | Title | Author | Status | Created | Updated |
|---|-------|--------|--------|---------|---------|
| [#66219](https://github.com/vllm-project/vllm/pull/66219) | [ROCm][Perf][GLM-5.3-Flash] Keep the fused gated RMSNorm for... | @ZhengGong-amd | draft | 2026-11-07 | 2026-11-07 |
| [#61109](https://github.com/vllm-project/vllm/pull/61109) | [ROCm][Perf] Two-level all-reduce for TP=8 across two XGMI i... | @omar-bahaa | open | 2026-10-11 | 2026-11-07 |
| [#64087](https://github.com/vllm-project/vllm/pull/64087) | [Bugfix][Kernel] Fix reshape & cache dropping KV heads past ... | @giuseppegrossi | open | 2026-10-27 | 2026-11-07 |
| [#65092](https://github.com/vllm-project/vllm/pull/65092) | [Bugfix][ROCm] Reserve kpool decode indexer logits in worksp... | @vllmellm | open | 2026-11-02 | 2026-11-07 |
| [#66268](https://github.com/vllm-project/vllm/pull/66268) | [Recipe][ROCm] MiniMax-M3.1-Flash MI355X AgentX 1P2D over Mo... | @whx-sjtu | draft | 2026-11-07 | 2026-11-07 |
| [#62675](https://github.com/vllm-project/vllm/pull/62675) | [Bugfix][ROCm] Pad Qwen4Exp PLE n-gram inputs for piecewise ... | @vllmellm | open | 2026-10-20 | 2026-11-07 |
| [#63166](https://github.com/vllm-project/vllm/pull/63166) | [ROCm][CI] Fix the test_qwen.py failure when AITER is on | @qli88 | open | 2026-10-22 | 2026-11-07 |
| [#65957](https://github.com/vllm-project/vllm/pull/65957) | [ROCm][CI] Cache only a ROCm stack the PR's CI pushed itself | @AndreasKaratzas | open | 2026-11-05 | 2026-11-07 |
| [#61662](https://github.com/vllm-project/vllm/pull/61662) | [ROCm][Perf][GLM-5.3-Flash] Mono decode layer for GLM-5.3-Fl... | @amd-sriram | open | 2026-10-14 | 2026-11-07 |
| [#63039](https://github.com/vllm-project/vllm/pull/63039) | [ROCm][MiMo] Add TP8 MegaMoE backend | @LiuYinfeng01 | open | 2026-10-22 | 2026-11-07 |
| [#66027](https://github.com/vllm-project/vllm/pull/66027) | [Attention][DCP] Sparse MLA DCP optimizations: packed top-k ... | @ZJY0516 | draft | 2026-11-06 | 2026-11-07 |
| [#65685](https://github.com/vllm-project/vllm/pull/65685) | [Model] Kimi K3.1 Pro (split 4/5: ROCm, P/D, indexer top-k) | @ZJY0516 | open | 2026-11-04 | 2026-11-07 |
| [#63623](https://github.com/vllm-project/vllm/pull/63623) | [Perf][MLA] Cap TRITON_MLA decode num_kv_splits for single-r... | @toan-tq | open | 2026-10-25 | 2026-11-07 |
| [#65595](https://github.com/vllm-project/vllm/pull/65595) | [ROCm][DSv4.1] Share Engram host tables across DP ranks with... | @AndreasKaratzas | open | 2026-11-04 | 2026-11-07 |
| [#63847](https://github.com/vllm-project/vllm/pull/63847) | [ROCm][Kernel] Support persistent_topk on ROCm | @AndreasKaratzas | open | 2026-10-26 | 2026-11-07 |
| [#64025](https://github.com/vllm-project/vllm/pull/64025) | [ROCm][Perf][Qwen4Exp] Pass the true W4A4 SiLU intermediate ... | @mjkvaak-amd | draft | 2026-10-27 | 2026-11-07 |
| [#66189](https://github.com/vllm-project/vllm/pull/66189) | [ROCm][CI] Wait for DPX GPU lock to avoid collisions | @sheralskumar | open | 2026-11-06 | 2026-11-07 |
| [#61452](https://github.com/vllm-project/vllm/pull/61452) | [Core][KV Cache] Move KV cache allocation policy out of KVCa... | @LucasWilkinson | open | 2026-10-13 | 2026-11-07 |
| [#61243](https://github.com/vllm-project/vllm/pull/61243) | [HiSparse] GLM-5.3-Flash support | @MatthewBonanni | open | 2026-10-12 | 2026-11-07 |
| [#57972](https://github.com/vllm-project/vllm/pull/57972) | [CI][ROCm] Match the AMD steps to the mirror declarations th... | @Bevisy | open | 2026-09-21 | 2026-11-07 |
| [#66216](https://github.com/vllm-project/vllm/pull/66216) | [ROCm][Model] Add Flint support on AMD GPUs | @akii96 | draft | 2026-11-07 | 2026-11-07 |
| [#66223](https://github.com/vllm-project/vllm/pull/66223) | [ROCm][Perf] Pick the GDN chunk-state V tile from the batch,... | @ZhengGong-amd | draft | 2026-11-07 | 2026-11-07 |
| [#66210](https://github.com/vllm-project/vllm/pull/66210) | [ROCm][MoE] Forward the quant-config SwiGLU clamp to AITER f... | @ZhengGong-amd | draft | 2026-11-07 | 2026-11-07 |
| [#66212](https://github.com/vllm-project/vllm/pull/66212) | [ROCm][Perf][DSpark] Stack the DeepSeek-V4/V4.1 context WKV ... | @ZhengGong-amd | draft | 2026-11-07 | 2026-11-07 |
| [#66215](https://github.com/vllm-project/vllm/pull/66215) | [ROCm] Read the DSv4.1 mono target arch from FLYDSL_GPU_ARCH | @coderfeli | open | 2026-11-07 | 2026-11-07 |
| [#65227](https://github.com/vllm-project/vllm/pull/65227) | [Perf][Multimodal] Encoder CUDA graphs: one replay per batch... | @Pb314314 | open | 2026-11-02 | 2026-11-07 |
| [#66214](https://github.com/vllm-project/vllm/pull/66214) | [ROCm][gfx1250] Run per-token FP8 linear layers on aiter's g... | @omuhamma | draft | 2026-11-07 | 2026-11-07 |
| [#66208](https://github.com/vllm-project/vllm/pull/66208) | [ROCm][gfx1250] Kimi-K3 FP8 MLA: aiter FP8 MHA + MLA Prefill... | @omuhamma | draft | 2026-11-07 | 2026-11-07 |
| [#49864](https://github.com/vllm-project/vllm/pull/49864) | [Bugfix][ROCm] Honor VLLM_WSL2_ENABLE_PIN_MEMORY on the ROCm... | @mingChen414 | open | 2026-07-26 | 2026-11-07 |
| [#66203](https://github.com/vllm-project/vllm/pull/66203) | [Doc][Recipe] Muse Spark Open: DSpark + specialized decode m... | @esmeetu | draft | 2026-11-07 | 2026-11-07 |
| [#66206](https://github.com/vllm-project/vllm/pull/66206) | [ROCm][Test] Cover the MXFP4 W4A16 intermediate pad that rea... | @AndreasKaratzas | open | 2026-11-07 | 2026-11-07 |
| [#58139](https://github.com/vllm-project/vllm/pull/58139) | [Bugfix][ROCm] Force Triton MXFP4 for 96-row ASM scale tiles | @abrahamzewoudie | open | 2026-09-22 | 2026-11-07 |
| [#64098](https://github.com/vllm-project/vllm/pull/64098) | [ROCm] Bound DeepSeek V4 steady-state memory on gfx1151 | @keneoneth | open | 2026-10-27 | 2026-11-07 |
| [#63725](https://github.com/vllm-project/vllm/pull/63725) | [ROCm][Perf][Muse Spark] Run the pooler and unpooler through... | @mjkvaak-amd | open | 2026-10-26 | 2026-11-07 |
| [#60076](https://github.com/vllm-project/vllm/pull/60076) | [ROCm][DSv4.1] KV-only insert for the DSpark context KV | @cagrikymk | open | 2026-10-05 | 2026-11-07 |
| [#57523](https://github.com/vllm-project/vllm/pull/57523) | [ROCm][DSv4.1] Read the V4.1 KV records (MXFP8 and NVFP4) on... | @JohnQin-llm | open | 2026-09-18 | 2026-11-07 |
| [#63373](https://github.com/vllm-project/vllm/pull/63373) | [Core] Remove Model Runner V1 | @WoosukKwon | open | 2026-10-23 | 2026-11-07 |
| [#66031](https://github.com/vllm-project/vllm/pull/66031) | [ROCm] Add AITER CK DASH attention for Unlimited-OCR-2 | @gty111 | open | 2026-11-06 | 2026-11-07 |
| [#65940](https://github.com/vllm-project/vllm/pull/65940) | [ROCm][CI] Run the MI355 BF16 AR-RMS fusion E2E matrices nig... | @ahsan-ca | open | 2026-11-05 | 2026-11-07 |
| [#66051](https://github.com/vllm-project/vllm/pull/66051) | [ROCm][Muse Spark] DSpark Markov sampling with one launch a ... | @azaidy | open | 2026-11-06 | 2026-11-07 |
| [#65985](https://github.com/vllm-project/vllm/pull/65985) | [ROCm][Muse Spark] DSpark draft layers as one persistent Fly... | @azaidy | open | 2026-11-05 | 2026-11-07 |
| [#57756](https://github.com/vllm-project/vllm/pull/57756) | [ROCm] Fuse DeepSeek V4.1 decode metadata and final norm | @LiuYinfeng01 | open | 2026-09-20 | 2026-11-07 |
| [#63078](https://github.com/vllm-project/vllm/pull/63078) | [Bugfix][Muse Spark] Keep the FP32 RoPE tables in FP32 | @fanxingran | open | 2026-10-22 | 2026-11-07 |
| [#59098](https://github.com/vllm-project/vllm/pull/59098) | [Bugfix][ROCm] Fix ROCM_AITER_FA context loss on out-of-orde... | @phambinhfin | open | 2026-09-28 | 2026-11-06 |
| [#65410](https://github.com/vllm-project/vllm/pull/65410) | [ROCm][Step5] Use native fnuz FP8 MFMA in CSA prefill indexe... | @akii96 | open | 2026-11-03 | 2026-11-06 |
| [#63280](https://github.com/vllm-project/vllm/pull/63280) | [ROCm][Kernel] Add AITER W4A16 linear kernel (gemm_a16w4) fo... | @TwelveNights | open | 2026-10-23 | 2026-11-06 |
| [#63199](https://github.com/vllm-project/vllm/pull/63199) | [ROCm] Add gfx1151 W8A8 blockscale skinny GEMM | @keneoneth | open | 2026-10-22 | 2026-11-06 |
| [#65512](https://github.com/vllm-project/vllm/pull/65512) | [WIP][ROCm][Perf][DeepSeek-V4.1] Mega-attention prefill kern... | @frida-andersson | draft | 2026-11-03 | 2026-11-06 |
| [#66184](https://github.com/vllm-project/vllm/pull/66184) | [ROCm][MiniMax-M3] Select the whole-layer mono decode with -... | @micah-wil | draft | 2026-11-06 | 2026-11-06 |
| [#65711](https://github.com/vllm-project/vllm/pull/65711) | [ROCm][Perf] Use tuned AITER Triton `gemm_a8w8` for per-tens... | @amd-jrosas | open | 2026-11-04 | 2026-11-06 |
| [#66036](https://github.com/vllm-project/vllm/pull/66036) | [ROCm][DSv4/4.1] Enable Tensor scale FP8 KV Cache for Sparse... | @cagrikymk | draft | 2026-11-06 | 2026-11-06 |
| [#63229](https://github.com/vllm-project/vllm/pull/63229) | [ROCm][MoE] Move AITER MegaMoEV2 To Oracle | @micah-wil | open | 2026-10-23 | 2026-11-06 |
| [#63510](https://github.com/vllm-project/vllm/pull/63510) | Rocm/beam common consolidation | @jamesETsmith | open | 2026-10-24 | 2026-11-06 |
| [#63401](https://github.com/vllm-project/vllm/pull/63401) | [Bugfix][ROCm] Size the extensible KV cache from exact VRAM ... | @GodRishUniverse | open | 2026-10-23 | 2026-11-06 |
| [#65655](https://github.com/vllm-project/vllm/pull/65655) | [Core][DP][WIP] Add a cross-DP prefill delayer scheduling po... | @shen-shanshan | draft | 2026-11-04 | 2026-11-06 |
| [#61093](https://github.com/vllm-project/vllm/pull/61093) | [ROCm][CI][BugFix][AITER ON] Make LM Eval Small Models tests... | @rasmith | open | 2026-10-11 | 2026-11-06 |
| [#58731](https://github.com/vllm-project/vllm/pull/58731) | [ROCm][AITER] Wire relu2 (non-gated) activation into AITER f... | @shantipriya-amd | open | 2026-09-25 | 2026-11-06 |
| [#62527](https://github.com/vllm-project/vllm/pull/62527) | [ROCm][Bugfix] Skip AITER unquantized MoE for hidden sizes n... | @Concurrensee | open | 2026-10-19 | 2026-11-06 |
| [#61244](https://github.com/vllm-project/vllm/pull/61244) | [Bugfix][ROCm] Make Mooncake an optional connector dependenc... | @Asthenia0412 | draft | 2026-10-12 | 2026-11-06 |
| [#66172](https://github.com/vllm-project/vllm/pull/66172) | [ROCm][Kimi-K3.1] Specialized decode: run DCP1 without M1 on... | @hongxiayang | open | 2026-11-06 | 2026-11-06 |
| [#60839](https://github.com/vllm-project/vllm/pull/60839) | [ROCm][Qwen4Exp] Fall back from unsupported AITER FP8 MoE an... | @mjkvaak-amd | draft | 2026-10-09 | 2026-11-06 |
| [#61498](https://github.com/vllm-project/vllm/pull/61498) | [ROCm][Perf][Kimi-K3] Mono decode layer for Kimi-K3 on MI355... | @jamesETsmith | open | 2026-10-13 | 2026-11-06 |
| [#58838](https://github.com/vllm-project/vllm/pull/58838) | [vLLM IR][RoPE] Port base RotaryEmbedding to a vLLM IR op (C... | @mpashkovskii | open | 2026-09-26 | 2026-11-06 |
| [#62490](https://github.com/vllm-project/vllm/pull/62490) | [Model][Hardware][AMD] Use AITER Gluon MLA for Small FP8-KV ... | @farlukas | open | 2026-10-19 | 2026-11-06 |
| [#62746](https://github.com/vllm-project/vllm/pull/62746) | [Model][Hardware][AMD] Fuse DSR1 Input Projection with Dual ... | @farlukas | open | 2026-10-20 | 2026-11-06 |
| [#54820](https://github.com/vllm-project/vllm/pull/54820) | [ROCm] Avoid DSpark placeholder shared vocab allocations | @keneoneth | open | 2026-09-01 | 2026-11-06 |
| [#63861](https://github.com/vllm-project/vllm/pull/63861) | [Model][Hardware][AMD] Fuse MLA Attention + Per-token FP8 Qu... | @farlukas | open | 2026-10-26 | 2026-11-06 |
| [#64428](https://github.com/vllm-project/vllm/pull/64428) | [ROCm][DP] QuickReduce combine for allgather/reducescatter M... | @Fangzhou-Ai | open | 2026-10-29 | 2026-11-06 |
| [#65911](https://github.com/vllm-project/vllm/pull/65911) | [ROCm][Perf] W4A16: serve 6-8 decode rows with the HIP skinn... | @weimin023 | open | 2026-11-05 | 2026-11-06 |
| [#61864](https://github.com/vllm-project/vllm/pull/61864) | [ROCm][Perf] Fuse QK-norm + MRoPE + KV-cache write for gated... | @eky-amd | draft | 2026-10-15 | 2026-11-06 |
| [#63721](https://github.com/vllm-project/vllm/pull/63721) | [ROCm][Perf][MoE] Fuse SiLU-and-mul with per-token FP8 quant... | @mjkvaak-amd | open | 2026-10-26 | 2026-11-06 |
| [#65421](https://github.com/vllm-project/vllm/pull/65421) | [ROCm][Perf][DSv4.1] Asm decode layers for 17-256-row decode... | @tvirolai-amd | open | 2026-11-03 | 2026-11-06 |
| [#45559](https://github.com/vllm-project/vllm/pull/45559) | [ROCm][Kernel] Extend skinny gemm N=5 to N=8 cases on GFX12 ... | @keneoneth | open | 2026-06-14 | 2026-11-06 |
| [#64142](https://github.com/vllm-project/vllm/pull/64142) | [ROCm][Perf] Re-land the AITER preshuffled FP8 block-scale G... | @amd-sriram | draft | 2026-10-27 | 2026-11-06 |
| [#66161](https://github.com/vllm-project/vllm/pull/66161) | [Bugfix][ROCm] Drop the dead min_seqlen_q parameter from AIT... | @divakar-amd | draft | 2026-11-06 | 2026-11-06 |
| [#66160](https://github.com/vllm-project/vllm/pull/66160) | [ROCm][Perf] Skip the z copy in the Qwen GDN gated RMSNorm | @Rishab540 | open | 2026-11-06 | 2026-11-06 |
| [#53823](https://github.com/vllm-project/vllm/pull/53823) | [ROCm][Perf] Prequantized FP8 attention for Qwen3NextAttenti... | @nholmber | open | 2026-08-26 | 2026-11-06 |
| [#66159](https://github.com/vllm-project/vllm/pull/66159) | [ROCm] Support DP in AiterW4A16ExpertsMonolithic | @simondanielsson | draft | 2026-11-06 | 2026-11-06 |
| [#62480](https://github.com/vllm-project/vllm/pull/62480) | [ROCm][Kernel] Add W4A8 MoE decode kernel for RDNA3/RDNA3.5 ... | @xiao-llm | open | 2026-10-19 | 2026-11-06 |
| [#62536](https://github.com/vllm-project/vllm/pull/62536) | [ROCm] Update ROCm to 10.1 and MORI to v1.2.4 and amd-smi fi... | @rasmith | open | 2026-10-19 | 2026-11-06 |
| [#65202](https://github.com/vllm-project/vllm/pull/65202) | [ROCm][RDNA] Add gfx1170 and gfx1171 support | @umarkovi-amd | open | 2026-11-02 | 2026-11-06 |
| [#66152](https://github.com/vllm-project/vllm/pull/66152) | [ROCm][MLA] Refuse the AITER fp8 decode config that silently... | @fululi12 | open | 2026-11-06 | 2026-11-06 |
| [#64130](https://github.com/vllm-project/vllm/pull/64130) | [Perf][ROCm] Split the top-k mask across programs for small ... | @phambinhfin | draft | 2026-10-27 | 2026-11-06 |
| [#61433](https://github.com/vllm-project/vllm/pull/61433) | [ROCm][Spec Decode] Rank unified attention above ROCM_ATTN f... | @zhentaocc | open | 2026-10-13 | 2026-11-06 |
| [#66068](https://github.com/vllm-project/vllm/pull/66068) | [ROCm][Quantization] Add tuned W8A8 block-FP8 Triton GEMM co... | @ZhengGong-amd | open | 2026-11-06 | 2026-11-06 |
| [#56328](https://github.com/vllm-project/vllm/pull/56328) | [ROCm] Keep gfx1151 paged-MQA fused path and skip invalid FU... | @keneoneth | open | 2026-09-10 | 2026-11-06 |
| [#64737](https://github.com/vllm-project/vllm/pull/64737) | [ROCm][Bugfix] Fix AITER shared-expert fusion routing for ga... | @xaguilar-amd | open | 2026-10-30 | 2026-11-06 |
| [#61091](https://github.com/vllm-project/vllm/pull/61091) | [ROCm][Perf] Add native per-token FP8 Triton linear backend ... | @big-yellow-duck | open | 2026-10-11 | 2026-11-06 |
| [#65963](https://github.com/vllm-project/vllm/pull/65963) | [Bugfix][ROCm][RDNA4] Snap the gfx1201 TRITON_ATTN decode sp... | @nemanjaudovic | draft | 2026-11-05 | 2026-11-06 |
| [#60783](https://github.com/vllm-project/vllm/pull/60783) | [Bugfix][KV Connector][ROCm] MoRIIO: accept kernel-block-spl... | @MIR-AMD | open | 2026-10-09 | 2026-11-06 |
| [#65759](https://github.com/vllm-project/vllm/pull/65759) | Quant config refactor 3 | @bnellnm | draft | 2026-11-04 | 2026-11-06 |
| [#66133](https://github.com/vllm-project/vllm/pull/66133) | [ROCm][Perf] W4A16: fix the gfx1151 bf16 tile cliff at decod... | @weimin023 | open | 2026-11-06 | 2026-11-06 |
| [#66130](https://github.com/vllm-project/vllm/pull/66130) | [ROCm] Add a FlyDSL MXFP4 linear backend for RDNA4 | @big-yellow-duck | open | 2026-11-06 | 2026-11-06 |
| [#64752](https://github.com/vllm-project/vllm/pull/64752) | [ROCm] Skip RTLD_GLOBAL libtorch_cpu.so promotion when HSA_T... | @gurpreet-dhami | open | 2026-10-30 | 2026-11-06 |
| [#61897](https://github.com/vllm-project/vllm/pull/61897) | [Feature][MoE] Support routed experts capture (R3) with pipe... | @chenjr97 | open | 2026-10-15 | 2026-11-06 |
| [#63821](https://github.com/vllm-project/vllm/pull/63821) | [ROCm][Perf][DSv4.1] TP1 MXFP8 GEMM tiles and a pre-quantize... | @maslan-coop26 | open | 2026-10-26 | 2026-11-06 |
| [#66128](https://github.com/vllm-project/vllm/pull/66128) | [ROCm][Perf][DSv4.1] Score adaptive-verification decode step... | @maslan-coop26 | draft | 2026-11-06 | 2026-11-06 |
| [#63086](https://github.com/vllm-project/vllm/pull/63086) | [ROCm][Kimi-K3] Enable encoder graph capture for Kimi-K3 Vis... | @samutamm | open | 2026-10-22 | 2026-11-06 |
| [#65132](https://github.com/vllm-project/vllm/pull/65132) | [ROCm][Perf] Build AITER MLA full-graph decode metadata in o... | @zengleixin-amd | open | 2026-11-02 | 2026-11-06 |
| [#62093](https://github.com/vllm-project/vllm/pull/62093) | [ROCm] do not blindly pick gpu 0 for asic info, consider *_V... | @Kezii | open | 2026-10-16 | 2026-11-06 |
| [#63867](https://github.com/vllm-project/vllm/pull/63867) | [ROCm][Perf] Skip masked-out context tiles for sliding-windo... | @phambinhfin | draft | 2026-10-27 | 2026-11-06 |
| [#54776](https://github.com/vllm-project/vllm/pull/54776) | [ROCm] Fix APU unified-memory accounting across GTT and host... | @sstamenk | open | 2026-09-01 | 2026-11-06 |
| [#62530](https://github.com/vllm-project/vllm/pull/62530) | [ROCm][CI] Bump the ROCm 10.0 image to torch 2.14 | @Rohan138 | open | 2026-10-19 | 2026-11-06 |
| [#65084](https://github.com/vllm-project/vllm/pull/65084) | [Multimodal] Refactor encoder cuda graph to support share gr... | @Isotr0py | open | 2026-11-02 | 2026-11-06 |
| [#65923](https://github.com/vllm-project/vllm/pull/65923) | [ROCm][MORI IO]Moriio hybrid load failure | @amd-weisun | draft | 2026-11-05 | 2026-11-06 |
| [#65188](https://github.com/vllm-project/vllm/pull/65188) | [ROCm][Perf] Qwen3.8-Flash-Next specialized decode at 1-128 ... | @samremes | draft | 2026-11-02 | 2026-11-06 |
| [#65690](https://github.com/vllm-project/vllm/pull/65690) | [ROCm][MORI IO]Moriio handshake failure  | @amd-weisun | open | 2026-11-04 | 2026-11-06 |
| [#63724](https://github.com/vllm-project/vllm/pull/63724) | [ROCm][Perf][Muse Spark] Enable the bf16x3 router GEMM on gf... | @mjkvaak-amd | open | 2026-10-26 | 2026-11-06 |
| [#63518](https://github.com/vllm-project/vllm/pull/63518) | [ROCm][CI][Bug] granite on gfx942/950 has bad accuracy when ... | @qli88 | open | 2026-10-24 | 2026-11-06 |
| [#63727](https://github.com/vllm-project/vllm/pull/63727) | [ROCm][Perf][Muse Spark] Add MI300X FP8 W8A8 fused-MoE confi... | @mjkvaak-amd | open | 2026-10-26 | 2026-11-06 |
| [#61119](https://github.com/vllm-project/vllm/pull/61119) | [ROCm][Perf][MiniMax-M3] Defer the FFN all-reduce into the n... | @akii96 | open | 2026-10-11 | 2026-11-06 |
| [#61214](https://github.com/vllm-project/vllm/pull/61214) | [ROCm][MoE] MXFP8 MoE via AITER moe_gemm_a8w8 on gfx942 | @akii96 | draft | 2026-10-12 | 2026-11-06 |
| [#65078](https://github.com/vllm-project/vllm/pull/65078) | [ROCm][Perf][Qwen3.8] Dense mono decode on 128-CU gfx950 (MI... | @jhartika-amd | draft | 2026-11-02 | 2026-11-06 |
| [#63397](https://github.com/vllm-project/vllm/pull/63397) | [ROCm][Perf][MLA] Use AITER Triton bf16 BMM for the decode W... | @lucas-santos-amd | open | 2026-10-23 | 2026-11-06 |
| [#41446](https://github.com/vllm-project/vllm/pull/41446) | [Kernel][AMD] Optimize GatedDeltaNet FLA prefill kernels on ... | @zobinHuang | open | 2026-05-01 | 2026-11-06 |
| [#61397](https://github.com/vllm-project/vllm/pull/61397) | [ROCm][Misc] Write GDN prefill output in place for the Trito... | @samremes | open | 2026-10-13 | 2026-11-06 |
| [#66090](https://github.com/vllm-project/vllm/pull/66090) | [Bugfix] Stop advertising fp8_e5m2 where the KV writer store... | @Mazukiri | open | 2026-11-06 | 2026-11-06 |
| [#57079](https://github.com/vllm-project/vllm/pull/57079) | [ROCm][Opt] DeepSeek-V4 optimizations | @ekuznetsov139 | open | 2026-09-15 | 2026-11-06 |
| [#66089](https://github.com/vllm-project/vllm/pull/66089) | [ROCm][CI] Bound DeepSeek V4 MoE output by per-token error | @stefankoncarevic | open | 2026-11-06 | 2026-11-06 |
| [#66084](https://github.com/vllm-project/vllm/pull/66084) | [ROCm][Spec Decode] Support draft_lm_head_quantization on RD... | @weimin023 | draft | 2026-11-06 | 2026-11-06 |
| [#66082](https://github.com/vllm-project/vllm/pull/66082) | [Perf][ROCm][ROCM_ATTN] Run large fp8 decode batches on the ... | @lishuoshuo-amd | open | 2026-11-06 | 2026-11-06 |
| [#65918](https://github.com/vllm-project/vllm/pull/65918) | [Quark] Rotate MXFP4 MoE activations before both GEMMs | @jimmy-adams | draft | 2026-11-05 | 2026-11-06 |
| [#65555](https://github.com/vllm-project/vllm/pull/65555) | [ROCm][DSv4.1] Score the sparse-indexer prefill on ShardQ to... | @AMD-yanfeiwang | open | 2026-11-04 | 2026-11-06 |
| [#63085](https://github.com/vllm-project/vllm/pull/63085) | [ROCm][Perf] Muse Spark Open Shard residual stream across TP | @kliuae | open | 2026-10-22 | 2026-11-06 |
| [#65833](https://github.com/vllm-project/vllm/pull/65833) | [ROCm] Run SiluAndMulWithClamp through AITER's fused clamped... | @xudonlyu | open | 2026-11-05 | 2026-11-06 |
| [#64722](https://github.com/vllm-project/vllm/pull/64722) | [ROCm][MoRIIO] Support DCP for decode instance in PD disaggr... | @kliuae | open | 2026-10-30 | 2026-11-06 |
| [#64693](https://github.com/vllm-project/vllm/pull/64693) | [ROCm][MoRIIO] Support PP for prefill instance in PD disaggr... | @kliuae | open | 2026-10-30 | 2026-11-06 |
| [#65628](https://github.com/vllm-project/vllm/pull/65628) | [ROCm][Perf][Qwen4Exp] Opt-in exact AITER IPC and INT4 Quick... | @eppaneamd | draft | 2026-11-04 | 2026-11-06 |
| [#64715](https://github.com/vllm-project/vllm/pull/64715) | [ROCm][UMBP] Support UMBP standalone mode for DeepSeek-V4.1-... | @shen-shanshan | open | 2026-10-30 | 2026-11-06 |
| [#62190](https://github.com/vllm-project/vllm/pull/62190) | [Bugfix][KV Connector][ROCm] MoRIIO hybrid READ: skip locall... | @whx-sjtu | open | 2026-10-17 | 2026-11-06 |
| [#65128](https://github.com/vllm-project/vllm/pull/65128) | [MORI][DBO] Dual batch overlap with MoRI EPv2 (native and As... | @ChengYM5685 | open | 2026-11-02 | 2026-11-06 |
| [#65897](https://github.com/vllm-project/vllm/pull/65897) | [ROCm][Bugfix] DeepSeek-V4: B-preshuffle the FP8 linears in ... | @xiaohong42 | draft | 2026-11-05 | 2026-11-06 |
| [#66001](https://github.com/vllm-project/vllm/pull/66001) | [ROCm][Perf][MoE] Run the router GEMM before the shared-expe... | @davzhuAMD | draft | 2026-11-05 | 2026-11-06 |
| [#64823](https://github.com/vllm-project/vllm/pull/64823) | [ROCm][Perf][GLM-5.3-Flash] mxfp4_mla: native gfx950 kernel ... | @amd-dlimpus | draft | 2026-10-30 | 2026-11-06 |
| [#64323](https://github.com/vllm-project/vllm/pull/64323) | [ROCm][Perf][GLM-5.3-Flash] Native gfx950 sparse decode for ... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-06 |
| [#64258](https://github.com/vllm-project/vllm/pull/64258) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: fetch the shared exp... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-06 |
| [#64245](https://github.com/vllm-project/vllm/pull/64245) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: quantize routed expe... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-06 |
| [#64225](https://github.com/vllm-project/vllm/pull/64225) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: pad a narrow step's ... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-06 |
| [#64207](https://github.com/vllm-project/vllm/pull/64207) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: take the mHC Sinkhor... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-06 |
| [#64121](https://github.com/vllm-project/vllm/pull/64121) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: read and write the m... | @amd-dlimpus | draft | 2026-10-27 | 2026-11-06 |
| [#63555](https://github.com/vllm-project/vllm/pull/63555) | [ROCm] GLM-5.3-Flash MonoKernel: MTP verify step | @amd-sriram | draft | 2026-10-25 | 2026-11-06 |
| [#56834](https://github.com/vllm-project/vllm/pull/56834) | [ROCm][Perf] Fuse the DeepSeek-V4 MXFP4 indexer K writer int... | @LiuYinfeng01 | open | 2026-09-14 | 2026-11-06 |
| [#63483](https://github.com/vllm-project/vllm/pull/63483) | [Core][DBO] Dual batch overlap under pure tensor parallelism... | @benenzhu | open | 2026-10-24 | 2026-11-06 |
| [#63226](https://github.com/vllm-project/vllm/pull/63226) | [ROCm] Enable the custom all-reduce on MI200 (gfx90a) | @jbelofamd | open | 2026-10-22 | 2026-11-06 |
| [#65716](https://github.com/vllm-project/vllm/pull/65716) | More gfx1250 models, di, and perf fixes  | @jpvillam-amd | open | 2026-11-04 | 2026-11-06 |
| [#66047](https://github.com/vllm-project/vllm/pull/66047) | [ROCm][Perf][DSv4.1] Shard the sparse-indexer decode rows ac... | @okorzh-amd | open | 2026-11-06 | 2026-11-06 |
| [#56944](https://github.com/vllm-project/vllm/pull/56944) | [ROCm][Perf] Shard DSA prefill indexer rows across TP ranks | @sumin-hong | open | 2026-09-15 | 2026-11-06 |
| [#65310](https://github.com/vllm-project/vllm/pull/65310) | [ROCm][MiniMax-M3] LM-path speedups: fused norm + FP8 quant,... | @vorapolsiloai | draft | 2026-11-03 | 2026-11-06 |
| [#62509](https://github.com/vllm-project/vllm/pull/62509) | [ROCm][Perf][MiniMax-M3.1] Tune MXFP8 linear tiles for 16 < ... | @jiacao-amd | open | 2026-10-19 | 2026-11-06 |
| [#63180](https://github.com/vllm-project/vllm/pull/63180) | [ROCm] Heterogeneous fused shared experts for DeepSeek-V4.1 ... | @alexm-redhat | open | 2026-10-22 | 2026-11-06 |
| [#66032](https://github.com/vllm-project/vllm/pull/66032) | [ROCm][CI] Run `/ci rocm cache` on the ci pipeline instead o... | @AndreasKaratzas | draft | 2026-11-06 | 2026-11-06 |
| [#57900](https://github.com/vllm-project/vllm/pull/57900) | [ROCm][Bugfix] Make the RoPE + KV-cache fusion reachable by ... | @ZhengGong-amd | open | 2026-09-21 | 2026-11-06 |
| [#61584](https://github.com/vllm-project/vllm/pull/61584) | [Bugfix] Reserve construction-time scratch across workspace ... | @maithilijoshi20 | open | 2026-10-14 | 2026-11-06 |
| [#63491](https://github.com/vllm-project/vllm/pull/63491) | [ROCm][MiniMax-M3.1] Sequence-parallel prefill under pure TP... | @benenzhu | draft | 2026-10-24 | 2026-11-06 |
| [#65858](https://github.com/vllm-project/vllm/pull/65858) | [ROCm][Perf][MiniMax-M3] Fuse prefill all-reduce + GemmaRMSN... | @akii96 | draft | 2026-11-05 | 2026-11-06 |
| [#64928](https://github.com/vllm-project/vllm/pull/64928) | [ROCm][Multimodal] FP8 ViT attention: opt-in AITER asm path ... | @vorapolsiloai | open | 2026-10-31 | 2026-11-06 |
| [#65837](https://github.com/vllm-project/vllm/pull/65837) | [ROCm][DSV4.1] Quantize the shared query once with AITER's f... | @xudonlyu | open | 2026-11-05 | 2026-11-06 |
| [#65767](https://github.com/vllm-project/vllm/pull/65767) | [ROCm][Perf] Add Gluon MXFP8 prefill kernels on gfx950 | @iraj465 | draft | 2026-11-05 | 2026-11-06 |
| [#65754](https://github.com/vllm-project/vllm/pull/65754) | [ROCm][RDNA][MoE] Add hybrid W4A16 MoE backend for gfx115x (... | @stoivonen-amd | draft | 2026-11-04 | 2026-11-06 |
| [#65832](https://github.com/vllm-project/vllm/pull/65832) | [ROCm][DSV4.1] Honor the multi-stream GEMM token threshold i... | @xudonlyu | open | 2026-11-05 | 2026-11-06 |
| [#64459](https://github.com/vllm-project/vllm/pull/64459) | [ROCm] MXFP8 block32 GEMM: add TP1 tuned configs for DeepSee... | @zovonoir | open | 2026-10-29 | 2026-11-06 |
| [#64883](https://github.com/vllm-project/vllm/pull/64883) | [ROCm][Perf][DSv4.1] Mono: tuned K2 FFN seam at TP4 / TP8 | @okorzh-amd | draft | 2026-10-31 | 2026-11-06 |
| [#64152](https://github.com/vllm-project/vllm/pull/64152) | [ROCm][Perf][DSv4.1] Mono: tuned K1 at TP8 | @okorzh-amd | draft | 2026-10-27 | 2026-11-05 |
| [#63893](https://github.com/vllm-project/vllm/pull/63893) | [ROCm][Perf][DSv4.1] Mono: tuned K1 at TP4 | @okorzh-amd | open | 2026-10-27 | 2026-11-05 |
| [#66003](https://github.com/vllm-project/vllm/pull/66003) | [ROCm][Perf] AITER FA: build the extend metadata without a h... | @sawsa307 | draft | 2026-11-05 | 2026-11-05 |
| [#65715](https://github.com/vllm-project/vllm/pull/65715) | [ROCm][Perf][Kimi-K3] Add HipCustomCommunicator, a communica... | @micmelesse | draft | 2026-11-04 | 2026-11-05 |
| [#57925](https://github.com/vllm-project/vllm/pull/57925) | [ROCm][DO NOT MERGE] RDNA3 (gfx1100) full inference stack — ... | @JartX | draft | 2026-09-21 | 2026-11-05 |
| [#62981](https://github.com/vllm-project/vllm/pull/62981) | [ROCm] Enable eager PCP for DeepSeek-V4.1-Flash | @haic0 | draft | 2026-10-21 | 2026-11-05 |
| [#65658](https://github.com/vllm-project/vllm/pull/65658) | [ROCm][PP] Stage hand-off on SDMA copy engines (--pp-sdma-ha... | @benenzhu | draft | 2026-11-04 | 2026-11-05 |
| [#65998](https://github.com/vllm-project/vllm/pull/65998) | [ROCm] Pass the (B, next_n) context lengths to AITER paged M... | @amd-sriram | draft | 2026-11-05 | 2026-11-05 |
| [#65995](https://github.com/vllm-project/vllm/pull/65995) | [Bugfix][ROCm] Support AITER's preload() in FlyDSL INT4 Quic... | @amd-sriram | draft | 2026-11-05 | 2026-11-05 |
| [#65992](https://github.com/vllm-project/vllm/pull/65992) | [ROCm][DSv4.1][Perf] Run the fused mHC seams as AITER's sing... | @davzhuAMD | draft | 2026-11-05 | 2026-11-05 |
| [#65785](https://github.com/vllm-project/vllm/pull/65785) | [ROCm][Kimi-K3][Perf] Support BF16 MLA projections in specia... | @tanpinsiang | open | 2026-11-05 | 2026-11-05 |
| [#52869](https://github.com/vllm-project/vllm/pull/52869) |  [ROCm] Enable safe AITER MoE dispatch for Qwen3.6 on gfx12 | @keneoneth | open | 2026-08-19 | 2026-11-05 |
| [#65749](https://github.com/vllm-project/vllm/pull/65749) | [ROCm][Perf][DSv4.1] Split-K BF16 router GEMM for gfx950 dec... | @davzhuAMD | draft | 2026-11-04 | 2026-11-05 |
| [#65243](https://github.com/vllm-project/vllm/pull/65243) | [ROCm][Perf][DSv4.1] Skip the redundant output copy in MoRI ... | @davzhuAMD | draft | 2026-11-02 | 2026-11-05 |
| [#59469](https://github.com/vllm-project/vllm/pull/59469) | [Model][Hardware][AMD] Fuse Two-pass MHA into single Causal ... | @farlukas | open | 2026-09-30 | 2026-11-05 |
| [#60294](https://github.com/vllm-project/vllm/pull/60294) | [ROCm][Perf] gfx950: use aiter.mqa_logits for the sparse ind... | @amd-sriram | draft | 2026-10-06 | 2026-11-05 |
| [#60042](https://github.com/vllm-project/vllm/pull/60042) | [ROCm][Perf] Call aiter.paged_mqa_logits for the sparse inde... | @amd-sriram | draft | 2026-10-05 | 2026-11-05 |
| [#64208](https://github.com/vllm-project/vllm/pull/64208) | [ROCm][Kernel] Wave64 topk_softplus_sqrt fast path for DeepS... | @amd-dlimpus | draft | 2026-10-28 | 2026-11-05 |
| [#64785](https://github.com/vllm-project/vllm/pull/64785) | [ROCm][KV Connector] Add MoRIIO transfer stats and Prometheu... | @charlifu | open | 2026-10-30 | 2026-11-05 |
| [#63979](https://github.com/vllm-project/vllm/pull/63979) | [ROCm] Offer TRITON_UNFUSED for MXFP4 MoE before emulation | @Gelato1337 | open | 2026-10-27 | 2026-11-05 |
| [#63402](https://github.com/vllm-project/vllm/pull/63402) | [ROCm][Quantization] Add int8 lm_head quantization (per-chan... | @zohaibm-amd | open | 2026-10-23 | 2026-11-05 |
| [#65461](https://github.com/vllm-project/vllm/pull/65461) | [ROCm][Perf][DSv4.1] Mono: merge K2(l) and K1(l + 1) into on... | @okorzh-amd | draft | 2026-11-03 | 2026-11-05 |
| [#62913](https://github.com/vllm-project/vllm/pull/62913) | [ROCm][Attention] Accelerate ROCm Prefill Attention with FP8... | @phambinhfin | draft | 2026-10-21 | 2026-11-05 |
| [#62708](https://github.com/vllm-project/vllm/pull/62708) | [ROCm][Quantization] Add load-time int4 lm_head quantization... | @xiao-llm | open | 2026-10-20 | 2026-11-05 |
| [#53108](https://github.com/vllm-project/vllm/pull/53108) | [ROCm][BugFix] Trim MoE GEMM input to valid token prefix  | @amd-weisun | open | 2026-08-20 | 2026-11-05 |
| [#63029](https://github.com/vllm-project/vllm/pull/63029) | [ROCm][DSv4.1] Dispatch pre-quantized MXFP4 through MoRI for... | @maslan-coop26 | open | 2026-10-22 | 2026-11-05 |
| [#64153](https://github.com/vllm-project/vllm/pull/64153) | [ROCm][Perf][DSv4.1] Mono: batch TP8 K2's X8 copies, load it... | @okorzh-amd | open | 2026-10-27 | 2026-11-05 |
| [#61962](https://github.com/vllm-project/vllm/pull/61962) | [AMD][DSV4.1Flash] Quantize MoE activations to MXFP8 before ... | @heachary | open | 2026-10-16 | 2026-11-05 |
| [#64045](https://github.com/vllm-project/vllm/pull/64045) | [ROCm][Kernel] Add fused MoE int4_w4a16 config for E=256,N=5... | @oleksandrdubyna88 | open | 2026-10-27 | 2026-11-05 |
| [#56326](https://github.com/vllm-project/vllm/pull/56326) | [Model][Hardware][AMD] Fuse AR + RMSNorm + Per-token Quant f... | @farlukas | open | 2026-09-10 | 2026-11-05 |
| [#63096](https://github.com/vllm-project/vllm/pull/63096) | [ROCm][Spec Decode] Enable fused multi-step draft decode for... | @fengshaoyi-amd | open | 2026-10-22 | 2026-11-05 |
| [#65935](https://github.com/vllm-project/vllm/pull/65935) | [Bugfix][ROCm] benchmark_moe: keep HIP_VISIBLE_DEVICES for R... | @YaashuDave | open | 2026-11-05 | 2026-11-05 |
| [#64261](https://github.com/vllm-project/vllm/pull/64261) | [ROCm][MLA][DCP] Serve the non-causal DSpark draft on TRITON... | @xaguilar-amd | open | 2026-10-28 | 2026-11-05 |
| [#62832](https://github.com/vllm-project/vllm/pull/62832) | [ROCm][KV Connector] Support sleep mode with MoRIIOConnector... | @TianDi101 | draft | 2026-10-21 | 2026-11-05 |
| [#65403](https://github.com/vllm-project/vllm/pull/65403) | [Perf][Sparse MLA][GLM-5.3] Index-group workspace savings an... | @gaby | open | 2026-11-03 | 2026-11-05 |
| [#51406](https://github.com/vllm-project/vllm/pull/51406) | [ROCm] Enable fused QK-norm+RoPE+gate Triton kernel for Qwen... | @xuebwang | open | 2026-08-07 | 2026-11-05 |
| [#64288](https://github.com/vllm-project/vllm/pull/64288) | [ROCm][Perf] Add direct FP8 kv_b_proj output for FlyDSL MLA ... | @ajith-sirra-amd | draft | 2026-10-28 | 2026-11-05 |
| [#63124](https://github.com/vllm-project/vllm/pull/63124) | [ROCm][Qwen3.8-Flash-Next] Specialized decode: MTP verify st... | @mjkvaak-amd | draft | 2026-10-22 | 2026-11-05 |
| [#56234](https://github.com/vllm-project/vllm/pull/56234) | [ROCm] Select AITER FP8 blockscale B-preshuffle when the sha... | @bichowdh | draft | 2026-09-10 | 2026-11-05 |
| [#65899](https://github.com/vllm-project/vllm/pull/65899) | [ROCm][MiniMax-M3] Serve --kv-cache-dtype mxfp4 on gfx942 | @akii96 | draft | 2026-11-05 | 2026-11-05 |
| [#65898](https://github.com/vllm-project/vllm/pull/65898) | [ROCm][MiniMax-M3] gfx942 MXFP4 indexer: fixes, fused decode... | @akii96 | draft | 2026-11-05 | 2026-11-05 |
| [#61942](https://github.com/vllm-project/vllm/pull/61942) | [ROCm][Perf] Skip DSA indexer scoring for short dense-MHA pr... | @whx-sjtu | open | 2026-10-16 | 2026-11-05 |
| [#63272](https://github.com/vllm-project/vllm/pull/63272) | [ROCm][Quantization][Perf] Fuse the block Hadamard input rot... | @mjkvaak-amd | draft | 2026-10-23 | 2026-11-05 |
| [#63603](https://github.com/vllm-project/vllm/pull/63603) | [ROCm][Perf][GLM-5.3-Flash] Allow AITER fused shared experts... | @eppaneamd | draft | 2026-10-25 | 2026-11-05 |
| [#59483](https://github.com/vllm-project/vllm/pull/59483) | [ROCm][Perf] Fuse the MXFP4 activation quant into AITER AR+R... | @mjkvaak-amd | draft | 2026-09-30 | 2026-11-05 |
| [#63612](https://github.com/vllm-project/vllm/pull/63612) | [ROCm][Perf] Use AITER single-launch KDA decode in GLM-5.3-F... | @eppaneamd | open | 2026-10-25 | 2026-11-05 |
| [#39168](https://github.com/vllm-project/vllm/pull/39168) | [ROCm] Fixing GLM-5.1, DeepSeek-V3.2, DeepSeek-V4 on gfx942 ... | @ekuznetsov139 | open | 2026-04-07 | 2026-11-05 |
| [#49278](https://github.com/vllm-project/vllm/pull/49278) | [Core] Fix EngineDeadError on late KV transfer completion | @Tejas-Raj01 | open | 2026-07-21 | 2026-11-05 |
| [#64001](https://github.com/vllm-project/vllm/pull/64001) | [ROCm][DSv4.1] Avoid runtime JIT for offset combine_topk_swa... | @BaoYunkai | open | 2026-10-27 | 2026-11-05 |
| [#65822](https://github.com/vllm-project/vllm/pull/65822) | [Perf][KV Connector][ROCm] MoRIIO WRITE: skip blocks the dec... | @whx-sjtu | open | 2026-11-05 | 2026-11-05 |
| [#64403](https://github.com/vllm-project/vllm/pull/64403) | [Bugfix][ROCm] Avoid unused index caches in MiniMax-M3 shari... | @lunarwhite | open | 2026-10-29 | 2026-11-05 |
| [#59812](https://github.com/vllm-project/vllm/pull/59812) | [ROCm][DSv4.1][Perf] Pass no MoE padding to AITER on the a4w... | @cagrikymk | draft | 2026-10-02 | 2026-11-05 |
| [#60217](https://github.com/vllm-project/vllm/pull/60217) | [ROCm] MoE balanced routing | @tniemine | open | 2026-10-06 | 2026-11-05 |
| [#52646](https://github.com/vllm-project/vllm/pull/52646) | [Bug][ROCm] DSv4 with MRV2 + FULL_DECODE_ONLY has bad accura... | @qli88 | open | 2026-08-17 | 2026-11-05 |
| [#49555](https://github.com/vllm-project/vllm/pull/49555) | [Feature][AuxOutput] Replay ordinary raw indexer selections | @Ronald1995 | open | 2026-07-23 | 2026-11-05 |
| [#63392](https://github.com/vllm-project/vllm/pull/63392) | [ROCm][Perf] AITER FA: trim the sliding-window decode table ... | @okorzh-amd | open | 2026-10-23 | 2026-11-05 |
| [#57078](https://github.com/vllm-project/vllm/pull/57078) | [ROCm][Perf] Tile DSV4 inverse RoPE for sparse prefill | @jiacao-amd | draft | 2026-09-15 | 2026-11-05 |
| [#64917](https://github.com/vllm-project/vllm/pull/64917) | [ROCm][Perf][DSv4.1] Balance the FP8 paged decode indexer ac... | @karverma-amd | draft | 2026-10-31 | 2026-11-05 |
| [#56426](https://github.com/vllm-project/vllm/pull/56426) | [ROCm] Route DSv4 sparse decode to the aiter v4 nm ASM kerne... | @LiuYinfeng01 | draft | 2026-09-11 | 2026-11-05 |
| [#63418](https://github.com/vllm-project/vllm/pull/63418) | [ROCm][Perf][DSv4] Enable custom allreduce on gfx1250 | @k50112113 | open | 2026-10-23 | 2026-11-05 |
| [#63396](https://github.com/vllm-project/vllm/pull/63396) | [ROCm][Perf][DSv4] gfx1250 W4A4 MoE: fused silu + fused quan... | @k50112113 | open | 2026-10-23 | 2026-11-05 |
| [#63243](https://github.com/vllm-project/vllm/pull/63243) | [ROCm][Perf][DSv4] Use AITER tuned GEMM for the Router GEMM ... | @k50112113 | open | 2026-10-23 | 2026-11-05 |
| [#52402](https://github.com/vllm-project/vllm/pull/52402) | [ROCm][gfx942] DSv4 sparse-attn indexer: native fp8 MFMA + c... | @MohitAMD | open | 2026-08-14 | 2026-11-05 |
| [#59782](https://github.com/vllm-project/vllm/pull/59782) | [ROCm][DSv4.1][Perf] Quantize the activation inside the MXFP... | @cpersson-amd | draft | 2026-10-02 | 2026-11-05 |
| [#61968](https://github.com/vllm-project/vllm/pull/61968) | [ROCm][DSv4.1][Perf] gfx942 sparse MLA decode: BLOCK_K=16 wi... | @lohiaj | draft | 2026-10-16 | 2026-11-05 |
| [#61967](https://github.com/vllm-project/vllm/pull/61967) | [ROCm][Perf][DSv4.1] HIP sparse MLA decode kernel for batche... | @lohiaj | draft | 2026-10-16 | 2026-11-05 |
| [#65742](https://github.com/vllm-project/vllm/pull/65742) | [ROCm][Perf][DSv4.1] Use the single-kernel DSv4 top-k on ROC... | @davzhuAMD | draft | 2026-11-04 | 2026-11-05 |
| [#47921](https://github.com/vllm-project/vllm/pull/47921) | feat: Add AMD NPU vision support for Qwen2.5-VL | @liangliangchang | open | 2026-07-07 | 2026-11-05 |
| [#49872](https://github.com/vllm-project/vllm/pull/49872) | [ROCm][Perf] Speed up large MoE ReLU-squared on gfx90a | @rlrs | open | 2026-07-26 | 2026-11-05 |
| [#63878](https://github.com/vllm-project/vllm/pull/63878) | [ROCm][Perf][DSA] Honor skip_kv_gather in the ROCm sparse-in... | @MohitAMD | open | 2026-10-27 | 2026-11-05 |
| [#65748](https://github.com/vllm-project/vllm/pull/65748) | [ROCm][Perf][Muse Spark] Decode monokernel TP4 EP4 | @sawsa307 | draft | 2026-11-04 | 2026-11-05 |
| [#65645](https://github.com/vllm-project/vllm/pull/65645) | [ROCm][MiniMax-M3] Default to breakable CUDA graphs tests on... | @benenzhu | draft | 2026-11-04 | 2026-11-05 |
| [#59693](https://github.com/vllm-project/vllm/pull/59693) | [ROCm][Perf] Kimi-K3: token-sharded residual stream for long... | @vanshbhatia-amd | open | 2026-10-01 | 2026-11-05 |
| [#64366](https://github.com/vllm-project/vllm/pull/64366) | [Dependency] Upgrade numba to 0.68.1 | @pylaterreur | open | 2026-10-28 | 2026-11-04 |
| [#55147](https://github.com/vllm-project/vllm/pull/55147) | [Bugfix][ROCm] Allocate topk_ids at the width AITER's topk k... | @i-kosarev | open | 2026-09-03 | 2026-11-04 |
| [#63015](https://github.com/vllm-project/vllm/pull/63015) | [ROCm][Perf] Keep wvSplitK at YTILE 2 for N=5 on wave64 to a... | @WhatGhost | open | 2026-10-22 | 2026-11-04 |
| [#65747](https://github.com/vllm-project/vllm/pull/65747) | [ROCm][Perf][Muse Spark] fp8 (e4m3) KV cache for the decode ... | @sawsa307 | draft | 2026-11-04 | 2026-11-04 |
| [#51599](https://github.com/vllm-project/vllm/pull/51599) | [Bugfix][MRv1] Decouple async Mamba align D2H counts from In... | @bandham-manikanta | open | 2026-08-10 | 2026-11-04 |
| [#65525](https://github.com/vllm-project/vllm/pull/65525) | [ROCm][UMBP][Muse Spark] Support UMBP for Muse Spark Open, k... | @Fangzhou-Ai | draft | 2026-11-04 | 2026-11-04 |
| [#65718](https://github.com/vllm-project/vllm/pull/65718) | [ROCm] Guard FlashInfer import and add portable rsqrt fallba... | @rjrock | draft | 2026-11-04 | 2026-11-04 |
| [#50813](https://github.com/vllm-project/vllm/pull/50813) | [ROCm][Kimi-K3] Enable opt-in K3 SiTUv2 A8W4 routed MoE | @LiuYinfeng01 | open | 2026-08-03 | 2026-11-04 |
| [#65251](https://github.com/vllm-project/vllm/pull/65251) | [ROCm] Move AITER MXFP4 linear op registration into _aiter_o... | @afriedri | open | 2026-11-02 | 2026-11-04 |
| [#65248](https://github.com/vllm-project/vllm/pull/65248) | [ROCm][Perf][Attention] AITER gluon attention backend for Qw... | @eky-amd | open | 2026-11-02 | 2026-11-04 |
| [#65224](https://github.com/vllm-project/vllm/pull/65224) | [ROCm][Perf][GLM-5.3-Flash] Split decodes off the AITER Flas... | @waqahmed-amd-fi | draft | 2026-11-02 | 2026-11-04 |
| [#64534](https://github.com/vllm-project/vllm/pull/64534) | [Model] Add Mistral Large 4 | @NickLucche | draft | 2026-10-29 | 2026-11-04 |
| [#65717](https://github.com/vllm-project/vllm/pull/65717) | [ROCm][Perf][MLA] Fold the decode RoPE + KV-cache write into... | @lucas-santos-amd | draft | 2026-11-04 | 2026-11-04 |
| [#50449](https://github.com/vllm-project/vllm/pull/50449) | [ROCm] Unify ViT FlashAttention backend selection | @hyoon1 | open | 2026-07-30 | 2026-11-04 |
| [#64369](https://github.com/vllm-project/vllm/pull/64369) | [ROCm][Qwen4Exp][DeepSeek-V4.1] Add per_token PLE/Engram loo... | @amd-satre | open | 2026-10-28 | 2026-11-04 |
| [#42136](https://github.com/vllm-project/vllm/pull/42136) | refactor(envs): migrate vllm/envs.py to pydantic-settings | @vrdn-23 | open | 2026-05-09 | 2026-11-04 |
| [#65703](https://github.com/vllm-project/vllm/pull/65703) | [Doc][ROCm] Qwen3.5-122B-A10B recipe: move AMD to v0.33.0, r... | @jolpindo96 | open | 2026-11-04 | 2026-11-04 |
| [#65098](https://github.com/vllm-project/vllm/pull/65098) | [ROCm][Disagg][CI] Give Wide-EP headless nodes the MoRIIO KV... | @avininjamay8 | open | 2026-11-02 | 2026-11-04 |
| [#57547](https://github.com/vllm-project/vllm/pull/57547) | [ROCm][Perf] Fuse all-reduce + RMSNorm with AITER FlyDSL INT... | @mjkvaak-amd | draft | 2026-09-18 | 2026-11-04 |
| [#61332](https://github.com/vllm-project/vllm/pull/61332) | [ROCm][Attention] Honor VLLM_ROCM_USE_AITER=1 by ranking ROC... | @xxraincandyxx | open | 2026-10-13 | 2026-11-04 |
| [#65313](https://github.com/vllm-project/vllm/pull/65313) | [CI/Build] Make Python 3.14 the default for images and CI | @njhill | draft | 2026-11-03 | 2026-11-04 |
| [#56451](https://github.com/vllm-project/vllm/pull/56451) | [ROCm][Spec Decode] Enable adaptive DSpark verification for ... | @olka-amd | open | 2026-09-11 | 2026-11-04 |
| [#64643](https://github.com/vllm-project/vllm/pull/64643) | [Feature][ROCm][DSv4.1] Relay KV-sharing state across ROCm p... | @yangyuhuiling | open | 2026-10-30 | 2026-11-04 |
| [#64642](https://github.com/vllm-project/vllm/pull/64642) | [Bugfix][ROCm][DSv4.1] Inject Engram at a PP stage that star... | @yangyuhuiling | open | 2026-10-30 | 2026-11-04 |
| [#54899](https://github.com/vllm-project/vllm/pull/54899) | [ROCm][MLA][DCP] Pad the AITER MLA decode head count to a na... | @okorzh-amd | open | 2026-09-02 | 2026-11-04 |
| [#62239](https://github.com/vllm-project/vllm/pull/62239) | [ROCm][Perf] MXFP8 emulation: run the dequantized weight thr... | @abdmalik-amd | open | 2026-10-18 | 2026-11-04 |
| [#63383](https://github.com/vllm-project/vllm/pull/63383) | [ROCm][gfx1151] ROCM_RDNA_ATTN: attention backend for RDNA | @roberteg16 | open | 2026-10-23 | 2026-11-04 |
| [#64560](https://github.com/vllm-project/vllm/pull/64560) | [ROCm][Perf][MLA] Fuse the decode W_UK absorb, q concat + fp... | @lucas-santos-amd | draft | 2026-10-29 | 2026-11-04 |
| [#65427](https://github.com/vllm-project/vllm/pull/65427) | [ROCm][Kernel] Pick the split of `top_k_per_row_decode` per ... | @xaguilar-amd | draft | 2026-11-03 | 2026-11-04 |
| [#62197](https://github.com/vllm-project/vllm/pull/62197) | [Bugfix][ROCm] Fix DeepSeek-V4.1 decode on gfx11/gfx12 (miss... | @abdmalik-amd | open | 2026-10-17 | 2026-11-04 |
| [#59628](https://github.com/vllm-project/vllm/pull/59628) | [ROCm] Enable DeepSeek-V3.2 / GLM-5.3 sparse MLA on gfx11 | @abdmalik-amd | open | 2026-10-01 | 2026-11-04 |
| [#65638](https://github.com/vllm-project/vllm/pull/65638) | [ROCm][MiniMax-M3] Mono decode on the NVFP4 KV and index cac... | @benenzhu | draft | 2026-11-04 | 2026-11-04 |
| [#55985](https://github.com/vllm-project/vllm/pull/55985) | [Model Loader] Bound fastsafetensors peak memory with the 0.... | @gitbisector | open | 2026-09-09 | 2026-11-04 |
| [#59035](https://github.com/vllm-project/vllm/pull/59035) | [ROCm][Perf] Fold Q fp8 quantization into QkNormRopeKvCacheF... | @mpashkovskii | open | 2026-09-28 | 2026-11-04 |
| [#61902](https://github.com/vllm-project/vllm/pull/61902) | [ROCm][Perf] Opt-in flag for the act+MXFP4 quant fusion | @fyun89 | open | 2026-10-15 | 2026-11-04 |
| [#65351](https://github.com/vllm-project/vllm/pull/65351) | [Multimodal][CG] Share encoder CUDA graph capture buffers ac... | @shen-shanshan | open | 2026-11-03 | 2026-11-04 |
| [#58568](https://github.com/vllm-project/vllm/pull/58568) | [Feature][KV Connector] Add UMBPStoreConnector for embedded ... | @charxwu | draft | 2026-09-24 | 2026-11-04 |
| [#65146](https://github.com/vllm-project/vllm/pull/65146) | [ROCm][Perf][DeepSeek-V4.1] Fused mega-attention prefill ker... | @yhx-12243 | open | 2026-11-02 | 2026-11-04 |
| [#57369](https://github.com/vllm-project/vllm/pull/57369) | [CI] Relax sharded-sampling divergence bound on ROCm | @khluu | open | 2026-09-17 | 2026-09-24 |
| [#57362](https://github.com/vllm-project/vllm/pull/57362) | [CI] Reclaim GPU memory between model initialization tests | @khluu | merged | 2026-09-17 | 2026-09-19 |
