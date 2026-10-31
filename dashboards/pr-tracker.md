# PR Tracker

All tracked PRs across projects, grouped by project.

## vllm (Upstream Watch)
Repo: `vllm-project/vllm` | Last collected: 2026-10-31T19:09:10Z

| # | Title | Author | Status | Created | Updated |
|---|-------|--------|--------|---------|---------|
| [#51406](https://github.com/vllm-project/vllm/pull/51406) | [ROCm] Enable fused QK-norm+RoPE+gate Triton kernel for Qwen... | @xuebwang | open | 2026-08-07 | 2026-10-31 |
| [#64763](https://github.com/vllm-project/vllm/pull/64763) | [ROCm][HY4] Use breakable graphs without torch.compile | @zixi-qi | open | 2026-10-30 | 2026-10-31 |
| [#62057](https://github.com/vllm-project/vllm/pull/62057) | [ROCm][Model] Add Beam 1.0 support on ROCm  | @jamesETsmith | open | 2026-10-16 | 2026-10-31 |
| [#63910](https://github.com/vllm-project/vllm/pull/63910) | [ROCm][Perf][GLM-5.3-Flash] Drop int32/int64 copies in the k... | @amd-sriram | draft | 2026-10-27 | 2026-10-31 |
| [#63072](https://github.com/vllm-project/vllm/pull/63072) | [ROCm][GLM-5.3-Flash] Pass precomputed chunk indices to chun... | @zhentaocc | draft | 2026-10-22 | 2026-10-31 |
| [#64883](https://github.com/vllm-project/vllm/pull/64883) | [ROCm][Perf][DSv4.1] Mono: tuned K2 FFN seam at TP4 / TP8 | @okorzh-amd | draft | 2026-10-31 | 2026-10-31 |
| [#64153](https://github.com/vllm-project/vllm/pull/64153) | [ROCm][Perf][DSv4.1] Mono: batch TP8 K2's X8 copies, load it... | @okorzh-amd | draft | 2026-10-27 | 2026-10-31 |
| [#64152](https://github.com/vllm-project/vllm/pull/64152) | [ROCm][Perf][DSv4.1] Mono: tuned K1 at TP8 | @okorzh-amd | draft | 2026-10-27 | 2026-10-31 |
| [#63893](https://github.com/vllm-project/vllm/pull/63893) | [ROCm][Perf][DSv4.1] Mono: tuned K1 at TP4 | @okorzh-amd | draft | 2026-10-27 | 2026-10-31 |
| [#63892](https://github.com/vllm-project/vllm/pull/63892) | [ROCm][Perf][DSv4.1] Mono: batch TP4 K2's X8 copies, load it... | @okorzh-amd | open | 2026-10-27 | 2026-10-31 |
| [#64894](https://github.com/vllm-project/vllm/pull/64894) | [Bugfix][ROCm] Fall back to torch.cuda in RocmPlatform when ... | @1fanwang | open | 2026-10-31 | 2026-10-31 |
| [#63891](https://github.com/vllm-project/vllm/pull/63891) | [ROCm][Perf][DSv4.1] Run DSpark's fused Markov sampling on g... | @okorzh-amd | open | 2026-10-27 | 2026-10-31 |
| [#63888](https://github.com/vllm-project/vllm/pull/63888) | [ROCm][Perf][DSv4.1] Skip the top-k -1 fill on the rows vLLM... | @okorzh-amd | open | 2026-10-27 | 2026-10-31 |
| [#63890](https://github.com/vllm-project/vllm/pull/63890) | [ROCm][Perf] Use AITER's all-gather for small TP all-gathers | @okorzh-amd | open | 2026-10-27 | 2026-10-31 |
| [#64888](https://github.com/vllm-project/vllm/pull/64888) | [Bugfix][ROCm] Fix AITER FA extend_forward zero-chunk and dr... | @1fanwang | open | 2026-10-31 | 2026-10-31 |
| [#63719](https://github.com/vllm-project/vllm/pull/63719) | [Bugfix][ROCm] Take DSA candidate scores and sampler fp32 lo... | @kasunpmkt | open | 2026-10-26 | 2026-10-31 |
| [#64886](https://github.com/vllm-project/vllm/pull/64886) | [Bugfix][ROCm][Kimi-K3] Refresh derived weight copies after ... | @okorzh-amd | open | 2026-10-31 | 2026-10-31 |
| [#63027](https://github.com/vllm-project/vllm/pull/63027) | [ROCm][Perf][DSv4.1] Mono: tuned K1/K2 for the TP2 compress-... | @chao-xu-spec | open | 2026-10-22 | 2026-10-31 |
| [#57861](https://github.com/vllm-project/vllm/pull/57861) | [Bugfix][ROCm] Avoid RowWise FP8 scaled MM on RDNA4 | @Cross2pro | open | 2026-09-21 | 2026-10-31 |
| [#63402](https://github.com/vllm-project/vllm/pull/63402) | [ROCm][Quantization] Add int8 per-channel lm_head quantizati... | @zohaibm-amd | draft | 2026-10-23 | 2026-10-31 |
| [#63076](https://github.com/vllm-project/vllm/pull/63076) | [ROCm][Perf] Triton sigmoid+bias top-k when MoE grouping eli... | @chennyiiis | open | 2026-10-22 | 2026-10-31 |
| [#53578](https://github.com/vllm-project/vllm/pull/53578) | [Bugfix][ROCm][Disagg] Always run DP dummy on zero-token ran... | @avininjamay8 | open | 2026-08-24 | 2026-10-31 |
| [#63029](https://github.com/vllm-project/vllm/pull/63029) | [ROCm][DSv4.1] Dispatch pre-quantized MXFP4 through MoRI for... | @maslan-coop26 | open | 2026-10-22 | 2026-10-31 |
| [#61119](https://github.com/vllm-project/vllm/pull/61119) | [ROCm][Perf][MiniMax-M3] Defer the FFN all-reduce into the n... | @akii96 | draft | 2026-10-11 | 2026-10-31 |
| [#59421](https://github.com/vllm-project/vllm/pull/59421) | [ROCm][Perf] Allow DSA indexer native decode above next_n=2 | @amd-sriram | draft | 2026-09-30 | 2026-10-31 |
| [#60762](https://github.com/vllm-project/vllm/pull/60762) | [Bugfix][DSv4.1] Declare 64-token kernel blocks for SM120 sp... | @xzwgit | open | 2026-10-09 | 2026-10-31 |
| [#49700](https://github.com/vllm-project/vllm/pull/49700) | [EPLB] Add lifecycle hooks for out-of-tree devices | @freyfwt | open | 2026-07-24 | 2026-10-31 |
| [#62190](https://github.com/vllm-project/vllm/pull/62190) | [Bugfix][KV Connector][ROCm] MoRIIO hybrid READ: skip locall... | @whx-sjtu | draft | 2026-10-17 | 2026-10-31 |
| [#61945](https://github.com/vllm-project/vllm/pull/61945) | [ROCm][Step5] Enable shared Step5 Triton kernels | @YukioZzz | draft | 2026-10-16 | 2026-10-31 |
| [#62536](https://github.com/vllm-project/vllm/pull/62536) | [ROCm] Update ROCm to 10.1 and MORI to v1.2.4 and amd-smi fi... | @rasmith | open | 2026-10-19 | 2026-10-31 |
| [#64794](https://github.com/vllm-project/vllm/pull/64794) | [ROCm][DSv4.1] Read V4.1 KV records in Mono decode on gfx950 | @JohnQin-llm | open | 2026-10-30 | 2026-10-31 |
| [#57523](https://github.com/vllm-project/vllm/pull/57523) | [ROCm][DSv4.1] Read the V4.1 KV records (MXFP8 and NVFP4) on... | @JohnQin-llm | open | 2026-09-18 | 2026-10-31 |
| [#64804](https://github.com/vllm-project/vllm/pull/64804) | [Docs][ROCm] Add DeepSeek-V4.1-Flash MI355X TP8 concurrency-... | @Fangzhou-Ai | open | 2026-10-30 | 2026-10-31 |
| [#62902](https://github.com/vllm-project/vllm/pull/62902) | [Bugfix][ROCm] Inkling: zero CUDA-graph padding rows in deco... | @jbelofamd | open | 2026-10-21 | 2026-10-31 |
| [#63821](https://github.com/vllm-project/vllm/pull/63821) | [ROCm][Perf][DSv4.1] TP1 MXFP8 GEMM tiles and a pre-quantize... | @maslan-coop26 | open | 2026-10-26 | 2026-10-31 |
| [#59802](https://github.com/vllm-project/vllm/pull/59802) | [ROCm][DSv4.1][Perf] Overlap the mHC gate projection with at... | @cpersson-amd | open | 2026-10-02 | 2026-10-31 |
| [#64857](https://github.com/vllm-project/vllm/pull/64857) | [ROCm][Perf] gfx942 sparse MLA decode: load e8m0 scales once... | @ishara0925 | open | 2026-10-31 | 2026-10-31 |
| [#64545](https://github.com/vllm-project/vllm/pull/64545) | [ROCm][Kimi-K3] Attention kernel cleanup for mixed Prefill +... | @omuhamma | draft | 2026-10-29 | 2026-10-31 |
| [#58568](https://github.com/vllm-project/vllm/pull/58568) | [Feature][KV Connector] Add UMBPStoreConnector for embedded ... | @charxwu | draft | 2026-09-24 | 2026-10-31 |
| [#61797](https://github.com/vllm-project/vllm/pull/61797) | [UMBP][9/10] Add lazy GPU cache offloading | @charxwu | open | 2026-10-15 | 2026-10-31 |
| [#61796](https://github.com/vllm-project/vllm/pull/61796) | [Test][UMBP][8/10] Add standalone runtime coverage | @charxwu | open | 2026-10-15 | 2026-10-31 |
| [#61795](https://github.com/vllm-project/vllm/pull/61795) | [UMBP][7/10] Add standalone mode backed by a node-local serv... | @charxwu | open | 2026-10-15 | 2026-10-31 |
| [#60321](https://github.com/vllm-project/vllm/pull/60321) | [ROCm][Attention] Add an mxfp4_mla KV cache for rope-free sp... | @amd-dlimpus | open | 2026-10-07 | 2026-10-31 |
| [#64839](https://github.com/vllm-project/vllm/pull/64839) | [ROCm][CI] Add a build.sh prebuild mode for maintainer-appro... | @AndreasKaratzas | open | 2026-10-31 | 2026-10-31 |
| [#57134](https://github.com/vllm-project/vllm/pull/57134) | [ROCm][Bugfix] Support FP8 KV cache for NoPE sparse MLA | @amd-dlimpus | open | 2026-09-16 | 2026-10-31 |
| [#41596](https://github.com/vllm-project/vllm/pull/41596) | [ROCm] Fix circular import in GCN arch detection | @naarob | open | 2026-05-04 | 2026-10-31 |
| [#42374](https://github.com/vllm-project/vllm/pull/42374) | [Core][WIP][1/N] Standardize kv layout | @LucasWilkinson | open | 2026-05-12 | 2026-10-31 |
| [#45370](https://github.com/vllm-project/vllm/pull/45370) | [Feature] Fused K-RoPE + static FP8 per-tensor KV Cache writ... | @A-R-Dhedeep-Reddy | open | 2026-06-12 | 2026-10-31 |
| [#63510](https://github.com/vllm-project/vllm/pull/63510) | Rocm/beam common consolidation | @jamesETsmith | draft | 2026-10-24 | 2026-10-31 |
| [#63232](https://github.com/vllm-project/vllm/pull/63232) | [Perf][FlashAttention] FA4: pass no FP8 descale for a unit p... | @zyongye | open | 2026-10-23 | 2026-10-31 |
| [#64359](https://github.com/vllm-project/vllm/pull/64359) | [ROCm][MiniMax-M3] MXFP4 mode for the fused norm / RoPE / KV... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-31 |
| [#64273](https://github.com/vllm-project/vllm/pull/64273) | [ROCm] MiniMax-M3: allow the AMD monokernel decode on MiniMa... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-31 |
| [#64264](https://github.com/vllm-project/vllm/pull/64264) | [ROCm][MiniMax-M3] Prebuild the NVFP4 FlyDSL kernels at star... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-31 |
| [#64206](https://github.com/vllm-project/vllm/pull/64206) | [WIP][ROCm][MiniMax-M3] Fold routed scaling into the AITER t... | @micah-wil | draft | 2026-10-28 | 2026-10-31 |
| [#62509](https://github.com/vllm-project/vllm/pull/62509) | [ROCm][Perf][MiniMax-M3.1] MXFP8 linear tiles for 16 < M <= ... | @jiacao-amd | open | 2026-10-19 | 2026-10-31 |
| [#61452](https://github.com/vllm-project/vllm/pull/61452) | [Core][KV Cache] Move KV cache allocation policy out of KVCa... | @LucasWilkinson | open | 2026-10-13 | 2026-10-31 |
| [#64836](https://github.com/vllm-project/vllm/pull/64836) | [ROCm][Step5] Exercise the real CSA decode and engine paths ... | @AndreasKaratzas | draft | 2026-10-31 | 2026-10-31 |
| [#53823](https://github.com/vllm-project/vllm/pull/53823) | [ROCm][Perf] Prequantized FP8 attention for Qwen3NextAttenti... | @nholmber | open | 2026-08-26 | 2026-10-31 |
| [#64833](https://github.com/vllm-project/vllm/pull/64833) | [ROCm][Step5] Require CDNA3 or newer for the sparse attentio... | @AndreasKaratzas | open | 2026-10-31 | 2026-10-31 |
| [#64355](https://github.com/vllm-project/vllm/pull/64355) | [CI] Enroll five steps and four AMD mirrors in automatic sha... | @Thangnguyenvn98 | open | 2026-10-28 | 2026-10-31 |
| [#64181](https://github.com/vllm-project/vllm/pull/64181) | [CI] Enroll three standalone AMD steps in automatic sharding | @Thangnguyenvn98 | open | 2026-10-28 | 2026-10-31 |
| [#56451](https://github.com/vllm-project/vllm/pull/56451) | [ROCm][Spec Decode] Enable adaptive DSpark verification for ... | @olka-amd | open | 2026-09-11 | 2026-10-31 |
| [#63486](https://github.com/vllm-project/vllm/pull/63486) | [CI][ROCm] Check AMD monokernels stage by stage against floa... | @AndreasKaratzas | draft | 2026-10-24 | 2026-10-31 |
| [#60076](https://github.com/vllm-project/vllm/pull/60076) | [ROCm][DSv4.1] KV-only insert for the DSpark context KV | @cagrikymk | open | 2026-10-05 | 2026-10-31 |
| [#64089](https://github.com/vllm-project/vllm/pull/64089) | [ROCm][Kernel] Add AITER Triton MXFP8 linear kernel for CDNA... | @afriedri | open | 2026-10-27 | 2026-10-30 |
| [#54820](https://github.com/vllm-project/vllm/pull/54820) | [ROCm] Avoid DSpark placeholder shared vocab allocations | @keneoneth | open | 2026-09-01 | 2026-10-30 |
| [#63612](https://github.com/vllm-project/vllm/pull/63612) | [ROCm][Perf] Use AITER single-launch KDA decode in GLM-5.3-F... | @eppaneamd | open | 2026-10-25 | 2026-10-30 |
| [#59693](https://github.com/vllm-project/vllm/pull/59693) | [ROCm][Perf] Kimi-K3: token-sharded residual stream for long... | @vanshbhatia-amd | open | 2026-10-01 | 2026-10-30 |
| [#56328](https://github.com/vllm-project/vllm/pull/56328) | [ROCm] Fix gfx1151 DeepSeek-V4 paged-MQA CUDAGraph capture a... | @keneoneth | open | 2026-09-10 | 2026-10-30 |
| [#64823](https://github.com/vllm-project/vllm/pull/64823) | [ROCm][Perf][GLM-5.3-Flash] mxfp4_mla: native gfx950 kernel ... | @amd-dlimpus | draft | 2026-10-30 | 2026-10-30 |
| [#62799](https://github.com/vllm-project/vllm/pull/62799) | [ROCm][Perf] Add Triton skinny GEMM for 6-128 rows on RDNA4 | @WhatGhost | open | 2026-10-21 | 2026-10-30 |
| [#64271](https://github.com/vllm-project/vllm/pull/64271) | [ROCm][Perf] Retune the gfx950 fp32 router GEMM for 384-512 ... | @amd-dlimpus | open | 2026-10-28 | 2026-10-30 |
| [#64792](https://github.com/vllm-project/vllm/pull/64792) | [Docs][ROCm] Mistral Large 4 recipe: adds TP4 and lowest-lat... | @indivats | open | 2026-10-30 | 2026-10-30 |
| [#64172](https://github.com/vllm-project/vllm/pull/64172) | [ROCm][MiniMax-M3] Keep Q and P in BF16 in the AITER sparse ... | @AndreasKaratzas | open | 2026-10-28 | 2026-10-30 |
| [#63401](https://github.com/vllm-project/vllm/pull/63401) | [Bugfix][ROCm][MRV2] Measure CUDA graph memory with the proc... | @GodRishUniverse | draft | 2026-10-23 | 2026-10-30 |
| [#64241](https://github.com/vllm-project/vllm/pull/64241) | [CI] Pin existing dependency hashes and enforce immutable re... | @jperezdealgaba | open | 2026-10-28 | 2026-10-30 |
| [#63426](https://github.com/vllm-project/vllm/pull/63426) | [ROCm][CI] Print the native backtrace of a fault in AMD CI p... | @AndreasKaratzas | open | 2026-10-23 | 2026-10-30 |
| [#61816](https://github.com/vllm-project/vllm/pull/61816) | [ROCm][Bugfix] Stop declaring the MXFP4 MoE intermediate pad... | @PerryZhang01 | open | 2026-10-15 | 2026-10-30 |
| [#61243](https://github.com/vllm-project/vllm/pull/61243) | [HiSparse] GLM-5.3-Flash support | @MatthewBonanni | open | 2026-10-12 | 2026-10-30 |
| [#61662](https://github.com/vllm-project/vllm/pull/61662) | [ROCm][Perf][GLM-5.3-Flash] Mono decode layer for GLM-5.3-Fl... | @amd-sriram | open | 2026-10-14 | 2026-10-30 |
| [#64317](https://github.com/vllm-project/vllm/pull/64317) | [ROCm][Perf] Keep 32-bit buffer addressing for per-token-hea... | @umarkovi-amd | open | 2026-10-28 | 2026-10-30 |
| [#60919](https://github.com/vllm-project/vllm/pull/60919) | [JIT][ROCm] Fully warmup GLM-5.3-Flash | @simondanielsson | open | 2026-10-09 | 2026-10-30 |
| [#62716](https://github.com/vllm-project/vllm/pull/62716) | [ROCm][Build][1/5] Speed up ROCm build: generate the paged a... | @fxmarty-amd | open | 2026-10-20 | 2026-10-30 |
| [#56155](https://github.com/vllm-project/vllm/pull/56155) | [Core][Attention] Resolve per-layer KV dtype in attention ru... | @meenchen | open | 2026-09-09 | 2026-10-30 |
| [#64785](https://github.com/vllm-project/vllm/pull/64785) | [ROCm][KV Connector] Add MoRIIO transfer stats and Prometheu... | @charlifu | open | 2026-10-30 | 2026-10-30 |
| [#63935](https://github.com/vllm-project/vllm/pull/63935) | [Perf][Attention][DCP] Gather the MLA chunked prefill contex... | @mispa-ms | draft | 2026-10-27 | 2026-10-30 |
| [#64243](https://github.com/vllm-project/vllm/pull/64243) | [ROCm][Perf][GLM-5.3-Flash] Split decodes off the AITER Flas... | @waqahmed-amd-fi | draft | 2026-10-28 | 2026-10-30 |
| [#64805](https://github.com/vllm-project/vllm/pull/64805) | [Docs][ROCm] Add MiniMax-M3.1-Flash MI355X TP4 AgentX preset | @Fangzhou-Ai | open | 2026-10-30 | 2026-10-30 |
| [#61155](https://github.com/vllm-project/vllm/pull/61155) | [ROCm][Model][BugFix] Fix DiffusionGemma hang with TP4 with ... | @rasmith | open | 2026-10-12 | 2026-10-30 |
| [#64737](https://github.com/vllm-project/vllm/pull/64737) | [ROCm][Bugfix] Fix AITER shared-expert fusion routing for ga... | @xaguilar-amd | open | 2026-10-30 | 2026-10-30 |
| [#58731](https://github.com/vllm-project/vllm/pull/58731) | [ROCm][AITER] Wire relu2 (non-gated) activation into AITER f... | @shantipriya-amd | open | 2026-09-25 | 2026-10-30 |
| [#63182](https://github.com/vllm-project/vllm/pull/63182) | [ROCm] Draft-prepare fastpaths: shared-output dedup + parall... | @alexm-redhat | open | 2026-10-22 | 2026-10-30 |
| [#57239](https://github.com/vllm-project/vllm/pull/57239) | [Model][ROCm][DCP] DeepSeek-V4 decode context parallelism | @edwinlim0919 | draft | 2026-09-16 | 2026-10-30 |
| [#63949](https://github.com/vllm-project/vllm/pull/63949) | [ROCm][CI] Untrusted base builds wait for main's images inst... | @AndreasKaratzas | open | 2026-10-27 | 2026-10-30 |
| [#64657](https://github.com/vllm-project/vllm/pull/64657) | [Test][UMBP] Cover TP=2 restore through per-rank MORI pools | @AndreasKaratzas | open | 2026-10-30 | 2026-10-30 |
| [#64677](https://github.com/vllm-project/vllm/pull/64677) | [Bugfix][UMBP] Copy every KV cache layout's per-head runs | @AndreasKaratzas | open | 2026-10-30 | 2026-10-30 |
| [#64174](https://github.com/vllm-project/vllm/pull/64174) | [CI] Enroll four Nightly steps and three AMD mirrors in auto... | @Thangnguyenvn98 | open | 2026-10-28 | 2026-10-30 |
| [#61435](https://github.com/vllm-project/vllm/pull/61435) | [ROCm][Perf][Kimi-K3] Specialized decode on MI355X: K1/K2 + ... | @seungrokj | open | 2026-10-13 | 2026-10-30 |
| [#64658](https://github.com/vllm-project/vllm/pull/64658) | [Test][UMBP] Cover end-to-end restore while the embedded DRA... | @AndreasKaratzas | draft | 2026-10-30 | 2026-10-30 |
| [#64656](https://github.com/vllm-project/vllm/pull/64656) | [Test][UMBP] Restore a Mamba2 hybrid end to end through UMBP | @AndreasKaratzas | draft | 2026-10-30 | 2026-10-30 |
| [#61942](https://github.com/vllm-project/vllm/pull/61942) | [ROCm][Perf] Skip DSA indexer scoring for short dense-MHA pr... | @whx-sjtu | open | 2026-10-16 | 2026-10-30 |
| [#59265](https://github.com/vllm-project/vllm/pull/59265) | [KV Offload] Preserve packed layouts for layer-wise KV cache | @meenchen | draft | 2026-09-29 | 2026-10-30 |
| [#56116](https://github.com/vllm-project/vllm/pull/56116) | [Quantization] Support ModelOpt layer-wise mixed KV cache | @meenchen | open | 2026-09-09 | 2026-10-30 |
| [#56156](https://github.com/vllm-project/vllm/pull/56156) | [Core] Pack heterogeneous KV-cache pages for hybrid models | @meenchen | open | 2026-09-09 | 2026-10-30 |
| [#63396](https://github.com/vllm-project/vllm/pull/63396) | [ROCm][Perf][DSv4] gfx1250 W4A4 MoE: fused silu + fused quan... | @k50112113 | open | 2026-10-23 | 2026-10-30 |
| [#63418](https://github.com/vllm-project/vllm/pull/63418) | [ROCm][Perf][DSv4] Enable custom allreduce on gfx1250 | @k50112113 | open | 2026-10-23 | 2026-10-30 |
| [#63243](https://github.com/vllm-project/vllm/pull/63243) | [ROCm][Perf][DSv4] Use AITER tuned GEMM for the Router GEMM ... | @k50112113 | open | 2026-10-23 | 2026-10-30 |
| [#63217](https://github.com/vllm-project/vllm/pull/63217) | [ROCm][Perf][DSv4] GFX1250 A8W8 Blockscale GEMM preshuffled ... | @k50112113 | open | 2026-10-22 | 2026-10-30 |
| [#61926](https://github.com/vllm-project/vllm/pull/61926) | [ROCm][Perf][DSv4] Use AITER tuned GEMM for the compressor p... | @k50112113 | open | 2026-10-15 | 2026-10-30 |
| [#53108](https://github.com/vllm-project/vllm/pull/53108) | [ROCm][BugFix] Trim MoE GEMM input to valid token prefix  | @amd-weisun | open | 2026-08-20 | 2026-10-30 |
| [#63040](https://github.com/vllm-project/vllm/pull/63040) | [ROCm][DeepSeek-V4] Add TP8 MegaMoE backend | @LiuYinfeng01 | draft | 2026-10-22 | 2026-10-30 |
| [#63039](https://github.com/vllm-project/vllm/pull/63039) | [ROCm][MiMo] Add TP8 MegaMoE backend | @LiuYinfeng01 | draft | 2026-10-22 | 2026-10-30 |
| [#64784](https://github.com/vllm-project/vllm/pull/64784) | [ROCm][CI] Fix GPU memory startup flakes in PP and sleep mod... | @aarushjain29 | open | 2026-10-30 | 2026-10-30 |
| [#63440](https://github.com/vllm-project/vllm/pull/63440) | [ROCm][Perf][Muse Spark] FlyDSL decode monokernel for DSpark... | @azaidy | open | 2026-10-24 | 2026-10-30 |
| [#64131](https://github.com/vllm-project/vllm/pull/64131) | [ROCm][CI] Keep nightlies off PR-writable images and trim th... | @AndreasKaratzas | open | 2026-10-27 | 2026-10-30 |
| [#60102](https://github.com/vllm-project/vllm/pull/60102) | [ROCm][DSv4.1][Perf] Route gfx942 sparse MLA prefill to AITE... | @frida-andersson | open | 2026-10-05 | 2026-10-30 |
| [#63419](https://github.com/vllm-project/vllm/pull/63419) | [ROCm] Migrate Aiter sigmoid to topk_gating | @rjrock | open | 2026-10-23 | 2026-10-30 |
| [#63491](https://github.com/vllm-project/vllm/pull/63491) | [ROCm][MiniMax-M3.1] Sequence-parallel prefill under pure TP... | @benenzhu | draft | 2026-10-24 | 2026-10-30 |
| [#63086](https://github.com/vllm-project/vllm/pull/63086) | [ROCm][Kimi-K3] Enable encoder graph capture for Kimi-K3 Vis... | @samutamm | open | 2026-10-22 | 2026-10-30 |
| [#39168](https://github.com/vllm-project/vllm/pull/39168) | [ROCm] Fixing GLM-5.1, DeepSeek-V3.2, DeepSeek-V4 on gfx942 ... | @ekuznetsov139 | open | 2026-04-07 | 2026-10-30 |
| [#64448](https://github.com/vllm-project/vllm/pull/64448) | [ROCm][MiniMax-M3.1] NVFP4 indexer: decode CP for DSpark 7 | @benenzhu | open | 2026-10-29 | 2026-10-30 |
| [#64581](https://github.com/vllm-project/vllm/pull/64581) | [ROCm][Feature][MiniMax-M3.1-Flash] Support aiter_sparse_pa ... | @simondanielsson | draft | 2026-10-29 | 2026-10-30 |
| [#60395](https://github.com/vllm-project/vllm/pull/60395) | [ROCm][Test] Add Qwen3.5 GDN zero-runtime-JIT regression cov... | @AIwork4me | open | 2026-10-07 | 2026-10-30 |
| [#64734](https://github.com/vllm-project/vllm/pull/64734) | [ROCm][CI] Raise the MI355 DeepSeek-V3.2 GSM8K startup wait ... | @bogdan-petkovic | open | 2026-10-30 | 2026-10-30 |
| [#54109](https://github.com/vllm-project/vllm/pull/54109) | [MoE][DeepEPV2] Add PaddedStandard activation format | @bnellnm | open | 2026-08-27 | 2026-10-30 |
| [#64375](https://github.com/vllm-project/vllm/pull/64375) | [ROCm][RDNA][Perf] W4A16: fused scale/zero-point carrier for... | @stoivonen-amd | open | 2026-10-28 | 2026-10-30 |
| [#56307](https://github.com/vllm-project/vllm/pull/56307) | [ROCm][Kernel] New "hip" GDN backend for gfx1151 | @roberteg16 | open | 2026-09-10 | 2026-10-30 |
| [#64752](https://github.com/vllm-project/vllm/pull/64752) | [ROCm] Skip RTLD_GLOBAL libtorch_cpu.so promotion when HSA_T... | @gurpreet-dhami | open | 2026-10-30 | 2026-10-30 |
| [#64369](https://github.com/vllm-project/vllm/pull/64369) | [ROCm][Qwen4Exp][DeepSeek-V4.1] Add per-token and zero looku... | @amd-satre | draft | 2026-10-28 | 2026-10-30 |
| [#64744](https://github.com/vllm-project/vllm/pull/64744) | [ROCm][Bugfix] W4A16 dense prefill cache: count cached alloc... | @zohaibm-amd | open | 2026-10-30 | 2026-10-30 |
| [#63367](https://github.com/vllm-project/vllm/pull/63367) | [ROCm][DSv4.1] Shard the Engram gate so TP ranks gather only... | @cpersson-amd | open | 2026-10-23 | 2026-10-30 |
| [#62088](https://github.com/vllm-project/vllm/pull/62088) | [ROCm][Quantization] Add AITER hipBLASLt MXFP8 linear kernel | @rkayaith | draft | 2026-10-16 | 2026-10-30 |
| [#62913](https://github.com/vllm-project/vllm/pull/62913) | [ROCm][Attention] Accelerate ROCm Prefill Attention with FP8... | @phambinhfin | draft | 2026-10-21 | 2026-10-30 |
| [#57925](https://github.com/vllm-project/vllm/pull/57925) | [ROCm][DO NOT MERGE] RDNA3 (gfx1100) full inference stack — ... | @JartX | draft | 2026-09-21 | 2026-10-30 |
| [#56450](https://github.com/vllm-project/vllm/pull/56450) | [ROCm][Perf] Encoder prefill attention on gfx1151: split-D h... | @roberteg16 | open | 2026-09-11 | 2026-10-30 |
| [#64428](https://github.com/vllm-project/vllm/pull/64428) | [ROCm][DP] QuickReduce combine for allgather/reducescatter M... | @Fangzhou-Ai | open | 2026-10-29 | 2026-10-30 |
| [#64288](https://github.com/vllm-project/vllm/pull/64288) | [ROCm][Perf] Add direct FP8 kv_b_proj output for FlyDSL MLA ... | @ajith-sirra-amd | draft | 2026-10-28 | 2026-10-30 |
| [#63181](https://github.com/vllm-project/vllm/pull/63181) | [ROCm] Fused DSpark draft sampling: markov GEMV + sample epi... | @alexm-redhat | open | 2026-10-22 | 2026-10-30 |
| [#64534](https://github.com/vllm-project/vllm/pull/64534) | [Model] Add Mistral Large 4 | @NickLucche | draft | 2026-10-29 | 2026-10-30 |
| [#61573](https://github.com/vllm-project/vllm/pull/61573) | [ROCm][Perf][Qwen3.8] Add dense mono decode for Qwen3.8-27B ... | @abrahamzewoudie | open | 2026-10-14 | 2026-10-30 |
| [#64715](https://github.com/vllm-project/vllm/pull/64715) | [ROCm][UMBP] Support UMBP standalone mode for DeepSeek-V4.1-... | @shen-shanshan | draft | 2026-10-30 | 2026-10-30 |
| [#63568](https://github.com/vllm-project/vllm/pull/63568) | [ROCm][Perf] Add AITER FlashKDA prefill backend for GLM KDA | @eppaneamd | draft | 2026-10-25 | 2026-10-30 |
| [#64722](https://github.com/vllm-project/vllm/pull/64722) | [ROCm][MoRIIO] Support DCP for decode instance in PD disaggr... | @kliuae | open | 2026-10-30 | 2026-10-30 |
| [#63117](https://github.com/vllm-project/vllm/pull/63117) | [Bugfix][ROCm] Take the AITER MLA KV cache dtype from the gr... | @BaoYunkai | open | 2026-10-22 | 2026-10-30 |
| [#62313](https://github.com/vllm-project/vllm/pull/62313) | [Bugfix][KV Connector][ROCm] MoRIIO READ: skip zeroing READ ... | @benenzhu | open | 2026-10-18 | 2026-10-30 |
| [#62270](https://github.com/vllm-project/vllm/pull/62270) | [ROCm][Perf][MiniMax-M3] Enable the fp8 indexer cache on gfx... | @akii96 | open | 2026-10-18 | 2026-10-30 |
| [#64331](https://github.com/vllm-project/vllm/pull/64331) | [ROCm][RDNA][Perf] Bump triton_kernels to ROCm 3.8.x with th... | @umarkovi-amd | open | 2026-10-28 | 2026-10-30 |
| [#64720](https://github.com/vllm-project/vllm/pull/64720) | [ROCm][Perf][Qwen3.8] Keep dense mono K2 on during MTP verif... | @abrahamzewoudie | open | 2026-10-30 | 2026-10-30 |
| [#62750](https://github.com/vllm-project/vllm/pull/62750) | [Quantization] Block Hadamard input rotation for linear laye... | @mjkvaak-amd | open | 2026-10-20 | 2026-10-30 |
| [#63002](https://github.com/vllm-project/vllm/pull/63002) | [ROCm][UMBP] Support UMBP standalone mode for DeepSeek-V4 | @shen-shanshan | draft | 2026-10-22 | 2026-10-30 |
| [#61091](https://github.com/vllm-project/vllm/pull/61091) | [ROCm][Perf] Add native per-token FP8 Triton linear backend ... | @big-yellow-duck | open | 2026-10-11 | 2026-10-30 |
| [#64640](https://github.com/vllm-project/vllm/pull/64640) | [ROCm][MoE] Add opt-in AITER FlyDSL clamped MXFP4 BF16 exper... | @LiuYinfeng01 | open | 2026-10-30 | 2026-10-30 |
| [#64676](https://github.com/vllm-project/vllm/pull/64676) | [Bugfix][UMBP] Reject DRAM watermarks that keep MORI from ev... | @AndreasKaratzas | draft | 2026-10-30 | 2026-10-30 |
| [#64130](https://github.com/vllm-project/vllm/pull/64130) | [Perf][ROCm] Split the top-k mask across programs for small ... | @phambinhfin | draft | 2026-10-27 | 2026-10-30 |
| [#64693](https://github.com/vllm-project/vllm/pull/64693) | [ROCm][MoRIIO] Support PP for prefill instance in PD disaggr... | @kliuae | open | 2026-10-30 | 2026-10-30 |
| [#62589](https://github.com/vllm-project/vllm/pull/62589) | [ROCm][Perf][Qwen4Exp] Tile QSA indexer scoring and skip inv... | @tc3oliver | open | 2026-10-20 | 2026-10-30 |
| [#62298](https://github.com/vllm-project/vllm/pull/62298) | [CI][ROCm] Run ROCm-capable tests that are currently skipped... | @AndreasKaratzas | open | 2026-10-18 | 2026-10-30 |
| [#64029](https://github.com/vllm-project/vllm/pull/64029) | [ROCm][Perf][Kimi-K3] Specialized decode K2: sentinel all-re... | @hyukjlee | open | 2026-10-27 | 2026-10-30 |
| [#64403](https://github.com/vllm-project/vllm/pull/64403) | [Bugfix][ROCm] Avoid unused index caches in MiniMax-M3 shari... | @lunarwhite | open | 2026-10-29 | 2026-10-30 |
| [#59437](https://github.com/vllm-project/vllm/pull/59437) | [ROCm][Perf] AITER FlyDSL kernels for the QSA indexer and sp... | @mjkvaak-amd | draft | 2026-09-30 | 2026-10-30 |
| [#63847](https://github.com/vllm-project/vllm/pull/63847) | [ROCm][Kernel] Support persistent_topk on ROCm | @AndreasKaratzas | open | 2026-10-26 | 2026-10-30 |
| [#64405](https://github.com/vllm-project/vllm/pull/64405) | [ROCm][Qwen3.8-Flash-Next] gfx950 fast path for the mxfp4_qs... | @amd-dlimpus | draft | 2026-10-29 | 2026-10-30 |
| [#64123](https://github.com/vllm-project/vllm/pull/64123) | [ROCm][Qwen3.8-Flash-Next] mxfp4_qsa main KV cache for the A... | @amd-dlimpus | draft | 2026-10-27 | 2026-10-30 |
| [#63730](https://github.com/vllm-project/vllm/pull/63730) | [ROCm][Perf] Kimi-K3 prefill GEMMs on AITER tuned kernels | @whx-sjtu | open | 2026-10-26 | 2026-10-30 |
| [#63867](https://github.com/vllm-project/vllm/pull/63867) | [ROCm][Perf] Skip fully-masked context tiles in sliding-wind... | @phambinhfin | draft | 2026-10-27 | 2026-10-30 |
| [#59553](https://github.com/vllm-project/vllm/pull/59553) | [ROCm][Perf] Fuse the MXFP4 activation quant into the GDN ga... | @mjkvaak-amd | draft | 2026-10-01 | 2026-10-30 |
| [#64662](https://github.com/vllm-project/vllm/pull/64662) | [ROCm][Perf] Add MI325X FP8 fused MoE configs for Qwen3.8-Fl... | @tc3oliver | open | 2026-10-30 | 2026-10-30 |
| [#64424](https://github.com/vllm-project/vllm/pull/64424) | [ROCm] Enable Gemma 4 image-prefix attention in ROCM_SEGMENT... | @big-yellow-duck | open | 2026-10-29 | 2026-10-30 |
| [#62666](https://github.com/vllm-project/vllm/pull/62666) | [ROCm][Perf] Extend ROCM_SEGMENTED_ATTN to D=512 for Gemma 4 | @big-yellow-duck | open | 2026-10-20 | 2026-10-30 |
| [#63180](https://github.com/vllm-project/vllm/pull/63180) | [ROCm] Heterogeneous fused shared experts for DeepSeek-V4.1 ... | @alexm-redhat | open | 2026-10-22 | 2026-10-30 |
| [#63965](https://github.com/vllm-project/vllm/pull/63965) | [Bugfix][ROCm] Skip Mooncake on Python 3.14 in requirements/... | @BaoYunkai | open | 2026-10-27 | 2026-10-30 |
| [#64643](https://github.com/vllm-project/vllm/pull/64643) | [Feature][ROCm][DSv4.1] Relay KV-sharing state across ROCm p... | @yangyuhuiling | open | 2026-10-30 | 2026-10-30 |
| [#64642](https://github.com/vllm-project/vllm/pull/64642) | [Bugfix][ROCm][DSv4.1] Inject Engram at a PP stage that star... | @yangyuhuiling | open | 2026-10-30 | 2026-10-30 |
| [#49539](https://github.com/vllm-project/vllm/pull/49539) | [Bugfix][Quantization] Keep unquantized routed experts unqua... | @pjdurden | open | 2026-07-23 | 2026-10-30 |
| [#64637](https://github.com/vllm-project/vllm/pull/64637) | [ROCm][DSv4.1] Warm up AITER FlyDSL MXFP8 GEMM kernels befor... | @Fangzhou-Ai | draft | 2026-10-30 | 2026-10-30 |
| [#60169](https://github.com/vllm-project/vllm/pull/60169) | [ROCm][DSv4.1][Perf] Use AITER FlyDSL GEMM for DeepSeek V4.1... | @ahmed-bsod | open | 2026-10-06 | 2026-10-30 |
| [#64031](https://github.com/vllm-project/vllm/pull/64031) | [ROCm][Perf][Kimi-K3] DSpark Markov sampling sharded over th... | @hyukjlee | open | 2026-10-27 | 2026-10-30 |
| [#61576](https://github.com/vllm-project/vllm/pull/61576) | fix(benchmarks): harden SSE handling | @Asthenia0412 | draft | 2026-10-14 | 2026-10-30 |
| [#61498](https://github.com/vllm-project/vllm/pull/61498) | [ROCm][Perf][Kimi-K3] Mono decode layer for Kimi-K3 on MI355... | @jamesETsmith | open | 2026-10-13 | 2026-10-30 |
| [#49870](https://github.com/vllm-project/vllm/pull/49870) | [ROCm][Perf] Speed up single-group MoE routing | @rlrs | open | 2026-07-26 | 2026-10-30 |
| [#64135](https://github.com/vllm-project/vllm/pull/64135) | [ROCm][DSv4.1] Native ASM sparse attention for DP deployment... | @Fangzhou-Ai | draft | 2026-10-27 | 2026-10-30 |
| [#63562](https://github.com/vllm-project/vllm/pull/63562) | [ROCm][Bugfix][Perf] Route AutoAWQ checkpoints to the RDNA W... | @daibenzhe | open | 2026-10-25 | 2026-10-30 |
| [#64614](https://github.com/vllm-project/vllm/pull/64614) | [Bugfix][ROCm] Use 64-bit row offsets in the RDNA hybrid W4A... | @vllmellm | open | 2026-10-30 | 2026-10-30 |
| [#62909](https://github.com/vllm-project/vllm/pull/62909) | [ROCm][Kernel] Optimize GDN Prefill with 16×16 MFMA on AMD G... | @phambinhfin | draft | 2026-10-21 | 2026-10-29 |
| [#62092](https://github.com/vllm-project/vllm/pull/62092) | [ROCm][AITER] Enable AITER by default on CDNA3+, keep AMD CI... | @AndreasKaratzas | draft | 2026-10-16 | 2026-10-29 |
| [#64323](https://github.com/vllm-project/vllm/pull/64323) | [ROCm][Perf][GLM-5.3-Flash] Native gfx950 sparse decode for ... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-29 |
| [#62708](https://github.com/vllm-project/vllm/pull/62708) | [ROCm][Quantization] Add load-time int4 lm_head quantization... | @xiao-llm | open | 2026-10-20 | 2026-10-29 |
| [#64121](https://github.com/vllm-project/vllm/pull/64121) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: read and write the m... | @amd-dlimpus | draft | 2026-10-27 | 2026-10-29 |
| [#60009](https://github.com/vllm-project/vllm/pull/60009) | [Model][Core] Register every persistent device tensor as a p... | @aoshen02 | open | 2026-10-05 | 2026-10-29 |
| [#63603](https://github.com/vllm-project/vllm/pull/63603) | [ROCm][Perf][GLM-5.3-Flash] Allow AITER fused shared experts... | @eppaneamd | draft | 2026-10-25 | 2026-10-29 |
| [#63522](https://github.com/vllm-project/vllm/pull/63522) | [ROCm][MoE] Align the AITER Triton MXFP4 W4A16 intermediate ... | @ishara0925 | open | 2026-10-25 | 2026-10-29 |
| [#63725](https://github.com/vllm-project/vllm/pull/63725) | [ROCm][Perf][Muse Spark] Run the unpooler through AITER tune... | @mjkvaak-amd | draft | 2026-10-26 | 2026-10-29 |
| [#64574](https://github.com/vllm-project/vllm/pull/64574) | [Bugfix][ROCm][KV Connector] MoRIIO: fix port collision with... | @lucasliao0403 | draft | 2026-10-29 | 2026-10-29 |
| [#61584](https://github.com/vllm-project/vllm/pull/61584) | [Bugfix] Reserve construction-time scratch across workspace ... | @maithilijoshi20 | open | 2026-10-14 | 2026-10-29 |
| [#61554](https://github.com/vllm-project/vllm/pull/61554) | [DSv4.1][PCP] Share CPU-offloaded Engram tables | @xijiaat | open | 2026-10-14 | 2026-10-29 |
| [#64366](https://github.com/vllm-project/vllm/pull/64366) | [Dependency] Upgrade numba to 0.68.1 | @pylaterreur | open | 2026-10-28 | 2026-10-29 |
| [#50449](https://github.com/vllm-project/vllm/pull/50449) | [ROCm] Unify ViT FlashAttention backend selection | @hyoon1 | open | 2026-07-30 | 2026-10-29 |
| [#57412](https://github.com/vllm-project/vllm/pull/57412) | [ROCm][Kimi-K3] Add moonmath kernels for gfx942 MXFP4 SiTU M... | @tarik-sarac | open | 2026-09-17 | 2026-10-29 |
| [#64560](https://github.com/vllm-project/vllm/pull/64560) | [ROCm][Perf][MLA] Fuse the decode W_UK absorb, q concat + fp... | @lucas-santos-amd | draft | 2026-10-29 | 2026-10-29 |
| [#52700](https://github.com/vllm-project/vllm/pull/52700) | [Bugfix][ROCm] Restore KV-offload Triton fast path via mappe... | @nitsingh2 | draft | 2026-08-18 | 2026-10-29 |
| [#60595](https://github.com/vllm-project/vllm/pull/60595) | [CI][ROCm] Drop MI300 MRv2 groups and other dead pipeline le... | @khluu | draft | 2026-10-08 | 2026-10-29 |
| [#63724](https://github.com/vllm-project/vllm/pull/63724) | [ROCm][Perf][Muse Spark] Enable the bf16x3 router GEMM on gf... | @mjkvaak-amd | draft | 2026-10-26 | 2026-10-29 |
| [#61444](https://github.com/vllm-project/vllm/pull/61444) | [ROCm][BugFix][FP8] Pad weights and inputs to fix TP8 crash ... | @rasmith | open | 2026-10-13 | 2026-10-29 |
| [#64132](https://github.com/vllm-project/vllm/pull/64132) | [ROCm][CI] Bake the fast-exit plugin into the test image and... | @AndreasKaratzas | draft | 2026-10-27 | 2026-10-29 |
| [#50717](https://github.com/vllm-project/vllm/pull/50717) | [Bugfix][NIXL] Preserve block geometry for packed MLA caches | @xijiaat | open | 2026-08-02 | 2026-10-29 |
| [#63204](https://github.com/vllm-project/vllm/pull/63204) | [ROCm][AITER] Split per-quant-scheme call arguments out of A... | @divakar-amd | draft | 2026-10-22 | 2026-10-29 |
| [#63365](https://github.com/vllm-project/vllm/pull/63365) | [ROCm][Build][5/5] Speed up ROCm build: instantiate the fuse... | @fxmarty-amd | open | 2026-10-23 | 2026-10-29 |
| [#61694](https://github.com/vllm-project/vllm/pull/61694) | [ROCm][Perf][Kimi-K3] Specialized decode: verify steps of 17... | @jiacao-amd | open | 2026-10-14 | 2026-10-29 |
| [#64459](https://github.com/vllm-project/vllm/pull/64459) | [ROCm] MXFP8 block32 GEMM: add TP1 tuned configs for DeepSee... | @zovonoir | open | 2026-10-29 | 2026-10-29 |
| [#63838](https://github.com/vllm-project/vllm/pull/63838) | [ROCm][Kimi-K3] Keep the AttnRes block bank in a persistent ... | @josh-schell-amd | open | 2026-10-26 | 2026-10-29 |
| [#60783](https://github.com/vllm-project/vllm/pull/60783) | [Bugfix][KV Connector][ROCm] MoRIIO: accept kernel-block-spl... | @MIR-AMD | open | 2026-10-09 | 2026-10-29 |
| [#61093](https://github.com/vllm-project/vllm/pull/61093) | [ROCm][CI][BugFix][AITER ON] Make LM Eval Small Models tests... | @rasmith | open | 2026-10-11 | 2026-10-29 |
| [#64176](https://github.com/vllm-project/vllm/pull/64176) | [ROCm][Bugfix] Allow the deliberate syncs the GPU sync check... | @AndreasKaratzas | open | 2026-10-28 | 2026-10-29 |
| [#60997](https://github.com/vllm-project/vllm/pull/60997) | [ROCm][Perf][Kimi-K3] Specialized decode on gfx942 (MI325X) | @xaguilar-amd | open | 2026-10-10 | 2026-10-29 |
| [#50897](https://github.com/vllm-project/vllm/pull/50897) | [Core][Spec Decode] Add lookahead-aware prefix cache hashing... | @ZJY0516 | open | 2026-08-03 | 2026-10-29 |
| [#54955](https://github.com/vllm-project/vllm/pull/54955) | [DeepSeek-V4][Preview][DO NOT MERGE] Batch-invariant kernels... | @ISEEKYAN | draft | 2026-09-02 | 2026-10-29 |
| [#56110](https://github.com/vllm-project/vllm/pull/56110) | [ROCm][Performance] Coalesce contiguous SimpleCPUOffload DMA... | @Fangzhou-Ai | open | 2026-09-09 | 2026-10-29 |
| [#63227](https://github.com/vllm-project/vllm/pull/63227) | [ROCm][CI] nixl_integration/test_edge_cases.py fails when AI... | @qli88 | open | 2026-10-23 | 2026-10-29 |
| [#60358](https://github.com/vllm-project/vllm/pull/60358) | [ROCm] [Feat] Enable HiSparse on ROCm | @tjtanaa | draft | 2026-10-07 | 2026-10-29 |
| [#59124](https://github.com/vllm-project/vllm/pull/59124) | [ROCm][Perf][MLA] Add opt-in FlyDSL FP8 prefill attention fo... | @gbyu-amd | open | 2026-09-29 | 2026-10-29 |
| [#58962](https://github.com/vllm-project/vllm/pull/58962) | [ROCM][KVConnector][MoRIIO] Add WRITE-mode consumer timeout | @amirakb89 | open | 2026-09-28 | 2026-10-29 |
| [#63835](https://github.com/vllm-project/vllm/pull/63835) | [ROCm][Perf][Bugfix] wvSplitK: launch tiles of their own for... | @roberteg16 | open | 2026-10-26 | 2026-10-29 |
| [#63547](https://github.com/vllm-project/vllm/pull/63547) | [ROCm][MiniMax] Add MiniMax-M3.1 AMD compatibility via MXFP4... | @ChuanLi1101 | draft | 2026-10-25 | 2026-10-29 |
| [#60400](https://github.com/vllm-project/vllm/pull/60400) | [ROCm][Perf] GDN packed decode: tuned launch configs for gfx... | @nemanjaudovic | open | 2026-10-07 | 2026-10-29 |
| [#64001](https://github.com/vllm-project/vllm/pull/64001) | [ROCm][DSv4.1] Avoid runtime JIT for offset combine_topk_swa... | @BaoYunkai | open | 2026-10-27 | 2026-10-29 |
| [#63722](https://github.com/vllm-project/vllm/pull/63722) | [ROCm][Perf][Muse Spark] Shard the MoE block's dense ops ove... | @mjkvaak-amd | draft | 2026-10-26 | 2026-10-29 |
| [#58352](https://github.com/vllm-project/vllm/pull/58352) | [ROCm][DSV4][Perf] Pack sparse-MLA ragged decode metadata in... | @ZhengGong-amd | open | 2026-09-23 | 2026-10-29 |
| [#57900](https://github.com/vllm-project/vllm/pull/57900) | [ROCm][Bugfix] Make the RoPE + KV-cache fusion reachable thr... | @ZhengGong-amd | open | 2026-09-21 | 2026-10-29 |
| [#55695](https://github.com/vllm-project/vllm/pull/55695) | [ROCm] Harden DeepSeek-V4/V4.1 sparse prefill against zero-q... | @ZhengGong-amd | open | 2026-09-07 | 2026-10-29 |
| [#61397](https://github.com/vllm-project/vllm/pull/61397) | [ROCm][Misc] Write GDN prefill output in place for the Trito... | @samremes | open | 2026-10-13 | 2026-10-29 |
| [#61950](https://github.com/vllm-project/vllm/pull/61950) | [Bugfix][ROCm] Fix Kimi K3 online quantization with padded p... | @whx-sjtu | open | 2026-10-16 | 2026-10-29 |
| [#60659](https://github.com/vllm-project/vllm/pull/60659) | [Perf][GDN][AMD]Internal prefill checkpoints for the Triton/... | @yuttian1 | open | 2026-10-08 | 2026-10-29 |
| [#64258](https://github.com/vllm-project/vllm/pull/64258) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: fetch the shared exp... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-29 |
| [#64245](https://github.com/vllm-project/vllm/pull/64245) | [ROCm][Perf][GLM-5.3-Flash] MonoKernel: quantize routed expe... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-29 |
| [#62605](https://github.com/vllm-project/vllm/pull/62605) | [ROCm][Perf] prefix_prefill: run mixed batches on all XCDs a... | @zhentaocc | open | 2026-10-20 | 2026-10-29 |
| [#61433](https://github.com/vllm-project/vllm/pull/61433) | [ROCm][Spec Decode] Rank unified attention above ROCM_ATTN f... | @zhentaocc | draft | 2026-10-13 | 2026-10-29 |
| [#64461](https://github.com/vllm-project/vllm/pull/64461) | [ROCm][Perf] Muse Spark: MXFP4 MoE on native routes and colu... | @fanxingran | open | 2026-10-29 | 2026-10-29 |
| [#60217](https://github.com/vllm-project/vllm/pull/60217) | [ROCm] MoE balanced routing | @tniemine | open | 2026-10-06 | 2026-10-29 |
| [#60833](https://github.com/vllm-project/vllm/pull/60833) | [ROCm][Perf] Qwen3.8-2.4T specialized decode on MI355X (--mo... | @mjkvaak-amd | draft | 2026-10-09 | 2026-10-29 |
| [#64407](https://github.com/vllm-project/vllm/pull/64407) | [Bugfix][ROCm] attention: Bound AITER FA varlen graph suppor... | @larryli2-amd | draft | 2026-10-29 | 2026-10-29 |
| [#63254](https://github.com/vllm-project/vllm/pull/63254) | [ROCm][Perf] Muse Spark: finalize the token-sharded MoE redu... | @fanxingran | open | 2026-10-23 | 2026-10-29 |
| [#64478](https://github.com/vllm-project/vllm/pull/64478) | [XPU][UT] Skip CUDA-IPC weight cache test with EP and TP on ... | @RyanMa29 | open | 2026-10-29 | 2026-10-29 |
| [#58139](https://github.com/vllm-project/vllm/pull/58139) | [Bugfix][ROCm] Force Triton MXFP4 for 96-row ASM scale tiles | @abrahamzewoudie | open | 2026-09-22 | 2026-10-29 |
| [#61273](https://github.com/vllm-project/vllm/pull/61273) | [ROCm][AMD] Generalize MiniMax-M3 mono decode architecture f... | @amd-bishwoadhikari | draft | 2026-10-12 | 2026-10-29 |
| [#60889](https://github.com/vllm-project/vllm/pull/60889) | [ROCm] MonoKernel for GLM 5.2 | @dllehr-amd | draft | 2026-10-09 | 2026-10-29 |
| [#64195](https://github.com/vllm-project/vllm/pull/64195) | [ROCm] MXFP8 linear: one tile row for 17-64 token decode bat... | @amd-dlimpus | draft | 2026-10-28 | 2026-10-29 |
| [#63878](https://github.com/vllm-project/vllm/pull/63878) | [ROCm][Perf][DSA] Honor skip_kv_gather in the ROCm sparse-in... | @MohitAMD | open | 2026-10-27 | 2026-10-29 |
| [#64098](https://github.com/vllm-project/vllm/pull/64098) | [ROCm] Bound DeepSeek V4 steady-state memory on gfx1151 | @keneoneth | open | 2026-10-27 | 2026-10-29 |
| [#63038](https://github.com/vllm-project/vllm/pull/63038) | [Perf][DSA] Budget sparse-indexer prefill chunks by the comp... | @nbommi19 | open | 2026-10-22 | 2026-10-29 |
| [#64386](https://github.com/vllm-project/vllm/pull/64386) | [ROCm][Bugfix] Route padded MoE rows to expert 0 with zero w... | @amd-dlimpus | open | 2026-10-29 | 2026-10-29 |
| [#61941](https://github.com/vllm-project/vllm/pull/61941) | [ROCm][Perf] Default AITER fused shared experts on for GLM-5... | @whx-sjtu | draft | 2026-10-16 | 2026-10-29 |
| [#63069](https://github.com/vllm-project/vllm/pull/63069) | [Bugfix][ROCm] Let ROCM_ATTN share the BLHNC KV cache layout | @zhentaocc | draft | 2026-10-22 | 2026-10-29 |
| [#63070](https://github.com/vllm-project/vllm/pull/63070) | [Perf][ROCm][GLM-5.3-Flash] Default to breakable CUDA graphs... | @zhentaocc | draft | 2026-10-22 | 2026-10-29 |
| [#64325](https://github.com/vllm-project/vllm/pull/64325) | [ROCm][Perf] Defer GDN spec decode state stores in the Trito... | @zhentaocc | open | 2026-10-28 | 2026-10-29 |
| [#36855](https://github.com/vllm-project/vllm/pull/36855) | [ROCm] Fix AITER sparse MLA crash for num_heads < 16 (e.g. G... | @ChuanLi1101 | open | 2026-03-12 | 2026-10-29 |
| [#38313](https://github.com/vllm-project/vllm/pull/38313) | [ROCm] Add AITER RoPE + KV cache fusion for MLA prefill and ... | @khairulkabir1661 | open | 2026-03-27 | 2026-10-29 |
| [#38454](https://github.com/vllm-project/vllm/pull/38454) | [ROCm][Test] Add hybrid block size and RDNA4 backend selecti... | @dondetir | open | 2026-03-29 | 2026-10-29 |
| [#39453](https://github.com/vllm-project/vllm/pull/39453) | Port SiluAndMul activation to vLLM IR op part 1 | @bohnstingl | open | 2026-04-09 | 2026-10-29 |
| [#62457](https://github.com/vllm-project/vllm/pull/62457) | [Bugfix][ROCm][MoRIIO] Support heterogeneous TP in READ mode... | @tjtanaa | open | 2026-10-19 | 2026-10-29 |
| [#63229](https://github.com/vllm-project/vllm/pull/63229) | [WIP][ROCm] Move AITER MegaMoEV2 To Oracle | @micah-wil | draft | 2026-10-23 | 2026-10-29 |
| [#64120](https://github.com/vllm-project/vllm/pull/64120) | [ROCm][DSv4.1] MXFP4 compressed KV cache (mxfp4_ds_mla) on g... | @amd-dlimpus | draft | 2026-10-27 | 2026-10-29 |
| [#64211](https://github.com/vllm-project/vllm/pull/64211) | [CI][Bugfix][ROCm] Seed each MLA backend correctness case; a... | @AndreasKaratzas | draft | 2026-10-28 | 2026-10-28 |
| [#56869](https://github.com/vllm-project/vllm/pull/56869) | [ROCm][Bugfix][Spec Decode][DCP] Align ranks before DFlash s... | @xiaohuguo2023 | open | 2026-09-14 | 2026-10-28 |
| [#63096](https://github.com/vllm-project/vllm/pull/63096) | [ROCm][Spec Decode] Enable fused multi-step draft decode for... | @fengshaoyi-amd | open | 2026-10-22 | 2026-10-28 |
| [#58838](https://github.com/vllm-project/vllm/pull/58838) | [vLLM IR][RoPE] Port base RotaryEmbedding to a vLLM IR op (C... | @mpashkovskii | open | 2026-09-26 | 2026-10-28 |
| [#63398](https://github.com/vllm-project/vllm/pull/63398) | [ROCm][Perf] Replace MiniMax-M3 MonoKernel with ATOM full-la... | @Fangzhou-Ai | open | 2026-10-23 | 2026-10-28 |
| [#63199](https://github.com/vllm-project/vllm/pull/63199) | [ROCm] Add gfx1151 W8A8 blockscale skinny GEMM | @keneoneth | open | 2026-10-22 | 2026-10-28 |
| [#57369](https://github.com/vllm-project/vllm/pull/57369) | [CI] Relax sharded-sampling divergence bound on ROCm | @khluu | open | 2026-09-17 | 2026-09-24 |
| [#57362](https://github.com/vllm-project/vllm/pull/57362) | [CI] Reclaim GPU memory between model initialization tests | @khluu | merged | 2026-09-17 | 2026-09-19 |
