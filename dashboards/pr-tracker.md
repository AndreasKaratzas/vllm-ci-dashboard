# PR Tracker

All tracked PRs across projects, grouped by project.

## vllm (Upstream Watch)
Repo: `vllm-project/vllm` | Last collected: 2026-11-28T08:31:13Z

| # | Title | Author | Status | Created | Updated |
|---|-------|--------|--------|---------|---------|
| [#70485](https://github.com/vllm-project/vllm/pull/70485) | [ROCm][Perf] Skip empty query tiles in the prefix_prefill ke... | @SRKRZ23 | open | 2026-11-28 | 2026-11-28 |
| [#66485](https://github.com/vllm-project/vllm/pull/66485) | [ROCm][Perf] Use 64 split-KV segments for small decode batch... | @tc3oliver | open | 2026-11-09 | 2026-11-28 |
| [#68493](https://github.com/vllm-project/vllm/pull/68493) | [ROCm][CI] Enable batch invariance for test_without_spec_dec... | @Concurrensee | open | 2026-11-18 | 2026-11-28 |
| [#53355](https://github.com/vllm-project/vllm/pull/53355) | [CI] Move Hugging Face downloads out of pytest collection | @AndreasKaratzas | open | 2026-08-22 | 2026-11-28 |
| [#70194](https://github.com/vllm-project/vllm/pull/70194) | [Tests] Consolidate online quantization numerics coverage | @fxmarty-amd | open | 2026-11-26 | 2026-11-28 |
| [#69971](https://github.com/vllm-project/vllm/pull/69971) | [Online quantization] Use FP32 intermediary in online re-qua... | @fxmarty-amd | draft | 2026-11-25 | 2026-11-28 |
| [#65923](https://github.com/vllm-project/vllm/pull/65923) | [ROCm][MORI IO]Moriio hybrid load failure | @amd-weisun | open | 2026-11-05 | 2026-11-28 |
| [#69240](https://github.com/vllm-project/vllm/pull/69240) | [Feature][ROCm][Quantization] Requantize DeepSeek-V4.1 MXFP4... | @yuzhouo7 | open | 2026-11-22 | 2026-11-28 |
| [#70427](https://github.com/vllm-project/vllm/pull/70427) | [Bugfix][Quantization] Keep zero inside the INT4 per-token-h... | @AndreasKaratzas | open | 2026-11-27 | 2026-11-28 |
| [#70230](https://github.com/vllm-project/vllm/pull/70230) | [ROCm] ShardQ in ROCM_AITER_MLA_SPARSE | @simondanielsson | draft | 2026-11-26 | 2026-11-28 |
| [#67685](https://github.com/vllm-project/vllm/pull/67685) | [ROCm][Model] Add Inkling 2 Small support on ROCm | @indivats | open | 2026-11-14 | 2026-11-28 |
| [#70456](https://github.com/vllm-project/vllm/pull/70456) | [ROCm][CI] Allow for BF16 reference rounding in scheduling a... | @AndreasKaratzas | open | 2026-11-28 | 2026-11-28 |
| [#69390](https://github.com/vllm-project/vllm/pull/69390) | [Bugfix][ROCm][Quantization] Use kFp8StaticChannelSym for FB... | @gwokhou | open | 2026-11-23 | 2026-11-28 |
| [#47183](https://github.com/vllm-project/vllm/pull/47183) | [Attention] Fix cascade NameError + D2H sync in triton/rocm ... | @mridullpandey | open | 2026-06-30 | 2026-11-28 |
| [#70114](https://github.com/vllm-project/vllm/pull/70114) | [ROCm][Qwen4Exp] Declare and test UMBP KV offloading support... | @haoshan98 | open | 2026-11-26 | 2026-11-28 |
| [#69954](https://github.com/vllm-project/vllm/pull/69954) | [EPLB][ROCm] Make the sync-EPLB skip-rearrange threshold con... | @hibagus | open | 2026-11-25 | 2026-11-28 |
| [#69000](https://github.com/vllm-project/vllm/pull/69000) | [Model][Hardware][AMD] Enable AITER CudaCommunicator | @farlukas | open | 2026-11-20 | 2026-11-28 |
| [#62530](https://github.com/vllm-project/vllm/pull/62530) | [ROCm][CI] Bump the ROCm 10.0 image to torch 2.14 | @Rohan138 | open | 2026-10-19 | 2026-11-27 |
| [#70448](https://github.com/vllm-project/vllm/pull/70448) | [Bugfix][ROCm] Allow native fallback in SparseAttnIndexer.fo... | @charlie12345 | open | 2026-11-27 | 2026-11-27 |
| [#70447](https://github.com/vllm-project/vllm/pull/70447) | [Bugfix][ROCm][DSv4.1] Guard AMD decoder replay on swa_cache... | @charlie12345 | open | 2026-11-27 | 2026-11-27 |
| [#70209](https://github.com/vllm-project/vllm/pull/70209) | [ROCm][Perf] W4A16: per-dtype dense prefill threshold, bf16 ... | @zohaibm-amd | draft | 2026-11-26 | 2026-11-27 |
| [#70443](https://github.com/vllm-project/vllm/pull/70443) | [ROCm][Perf] Add fused Triton BF16-WMMA fallback for rocm_fp... | @charlie12345 | open | 2026-11-27 | 2026-11-27 |
| [#70343](https://github.com/vllm-project/vllm/pull/70343) | [ROCm][Bugfix] Gate EPLB rearrangement on per-layer rank loa... | @shen-shanshan | open | 2026-11-27 | 2026-11-27 |
| [#69479](https://github.com/vllm-project/vllm/pull/69479) | [ROCm][Qwen4Exp] Support PLE disk offload on ROCm | @amd-satre | open | 2026-11-23 | 2026-11-27 |
| [#67523](https://github.com/vllm-project/vllm/pull/67523) | [Test][ROCm] Move Kimi-K3 AMD tests and added parity to nvid... | @giuseppegrossi | open | 2026-11-13 | 2026-11-27 |
| [#68392](https://github.com/vllm-project/vllm/pull/68392) | [ROCm] Update ROCm to 10.2  and MORI to v.1.2.5 and amd-smi ... | @rasmith | open | 2026-11-18 | 2026-11-27 |
| [#69090](https://github.com/vllm-project/vllm/pull/69090) | [ROCm][Perf][DSv4] gfx942 sparse MLA decode: one workgroup p... | @ZhengGong-amd | open | 2026-11-21 | 2026-11-27 |
| [#70436](https://github.com/vllm-project/vllm/pull/70436) | [ROCm][Kernel] Migrate Octave KV cache to libtorch stable | @giuseppegrossi | draft | 2026-11-27 | 2026-11-27 |
| [#69951](https://github.com/vllm-project/vllm/pull/69951) | [ROCm] Install AITER from AMD's prebuilt release wheel | @josh-schell-amd | draft | 2026-11-25 | 2026-11-27 |
| [#68666](https://github.com/vllm-project/vllm/pull/68666) | [ROCm][Recipe] Mistral Large 4: MI325X block-FP8 path with g... | @xaguilar-amd | open | 2026-11-19 | 2026-11-27 |
| [#67773](https://github.com/vllm-project/vllm/pull/67773) | [Attention] Skip backends without ALiBi support during selec... | @hokwangchoi | open | 2026-11-15 | 2026-11-27 |
| [#68540](https://github.com/vllm-project/vllm/pull/68540) | [KV Connector] Sync the builtin LMCache MP connector with LM... | @deng451e | open | 2026-11-19 | 2026-11-27 |
| [#70220](https://github.com/vllm-project/vllm/pull/70220) | [ROCm][Perf] Use CDNA paged attention for dense LBHNC KV cac... | @Super262 | open | 2026-11-26 | 2026-11-27 |
| [#67573](https://github.com/vllm-project/vllm/pull/67573) | [ROCm][Build] Install device code via ROCM_DEVICE_EXTRAS in ... | @kiran-thumma | open | 2026-11-13 | 2026-11-27 |
| [#69700](https://github.com/vllm-project/vllm/pull/69700) | [ROCm][Perf][gfx942] Use AITER's FlyDSL top-k for DSA indexe... | @xaguilar-amd | draft | 2026-11-24 | 2026-11-27 |
| [#70422](https://github.com/vllm-project/vllm/pull/70422) | [Bugfix][ROCm] Measure CUDA graph capture memory by the exac... | @Bye-legumes | open | 2026-11-27 | 2026-11-27 |
| [#68033](https://github.com/vllm-project/vllm/pull/68033) | [ROCm][DSv4.1] gfx942 large-step MoE for decode steps above ... | @maeehart | open | 2026-11-16 | 2026-11-27 |
| [#66964](https://github.com/vllm-project/vllm/pull/66964) | [ROCm][MORI] Dispatch the activation format AITER's fused Mo... | @ChengYM5685 | draft | 2026-11-11 | 2026-11-27 |
| [#66597](https://github.com/vllm-project/vllm/pull/66597) | [Model][Hardware][AMD] Enable DSR1 MXFP4 on gfx1250 | @farlukas | open | 2026-11-09 | 2026-11-27 |
| [#69290](https://github.com/vllm-project/vllm/pull/69290) | [Amnaro][AMD][MI300X][DS V4.1 Flash] One copy of the routed ... | @Qam2ng | open | 2026-11-22 | 2026-11-27 |
| [#70136](https://github.com/vllm-project/vllm/pull/70136) | [Performance][Multimodal] Cache interpolated get_abs_pos emb... | @QwertyJack | open | 2026-11-26 | 2026-11-27 |
| [#69232](https://github.com/vllm-project/vllm/pull/69232) | [ROCm][Perf] FlyDSL INT4 QuickReduce: opt-in TP2 xGMI relay ... | @mjkvaak-amd | draft | 2026-11-22 | 2026-11-27 |
| [#64142](https://github.com/vllm-project/vllm/pull/64142) | [ROCm][Perf] Re-land the AITER preshuffled FP8 block-scale G... | @amd-sriram | open | 2026-10-27 | 2026-11-27 |
| [#60583](https://github.com/vllm-project/vllm/pull/60583) | [ROCm][Perf][GLM-5.3-Flash] Enable AITER sparse MLA on gfx94... | @jin-amd | open | 2026-10-08 | 2026-11-27 |
| [#70223](https://github.com/vllm-project/vllm/pull/70223) | [ROCm][Perf] Default GPU_MAX_HW_QUEUES=2 on RDNA4 to avoid t... | @Bye-legumes | open | 2026-11-26 | 2026-11-27 |
| [#67392](https://github.com/vllm-project/vllm/pull/67392) | [ROCm][Perf][Muse Spark] Run the PTPC FP8 routed experts thr... | @mjkvaak-amd | open | 2026-11-13 | 2026-11-27 |
| [#67208](https://github.com/vllm-project/vllm/pull/67208) | [ROCm][Perf][GLM-5.3-Flash] Shard the kpool indexer prefill ... | @phambinhfin | draft | 2026-11-12 | 2026-11-27 |
| [#61662](https://github.com/vllm-project/vllm/pull/61662) | [ROCm][Perf][GLM-5.3-Flash] Mono decode layer for GLM-5.3-Fl... | @amd-sriram | open | 2026-10-14 | 2026-11-27 |
| [#68680](https://github.com/vllm-project/vllm/pull/68680) | [ROCm][Recipe] Llama-3.3-70B-Instruct: add AMD FP8 variant (... | @mjkvaak-amd | draft | 2026-11-19 | 2026-11-27 |
| [#70396](https://github.com/vllm-project/vllm/pull/70396) | [ROCm] PCP with FULL cudagraphs during decode | @simondanielsson | draft | 2026-11-27 | 2026-11-27 |
| [#60294](https://github.com/vllm-project/vllm/pull/60294) | [ROCm] gfx950: use aiter.mqa_logits for the sparse indexer p... | @amd-sriram | open | 2026-10-06 | 2026-11-27 |
| [#60042](https://github.com/vllm-project/vllm/pull/60042) | [ROCm] Use aiter.paged_mqa_logits for the sparse indexer dec... | @amd-sriram | open | 2026-10-05 | 2026-11-27 |
| [#67529](https://github.com/vllm-project/vllm/pull/67529) | [Online quantization] Deprecate `VLLM_FP8_MOE_REQUANT`/`VLLM... | @fxmarty-amd | draft | 2026-11-13 | 2026-11-27 |
| [#70391](https://github.com/vllm-project/vllm/pull/70391) | [ROCm][Kernel] Migrate rocm paged attention to torch stable ... | @giuseppegrossi | open | 2026-11-27 | 2026-11-27 |
| [#67367](https://github.com/vllm-project/vllm/pull/67367) | [ROCm][Perf][DSv4.1] Keep few-row gfx950 top-512 calls in on... | @okorzh-amd | open | 2026-11-13 | 2026-11-27 |
| [#70002](https://github.com/vllm-project/vllm/pull/70002) | [ROCm][Kernel] Migrate skinny gemms to libtorch stable | @giuseppegrossi | open | 2026-11-25 | 2026-11-27 |
| [#62666](https://github.com/vllm-project/vllm/pull/62666) | [ROCm][Perf] Extend ROCM_SEGMENTED_ATTN to D=512 for Gemma 4 | @big-yellow-duck | open | 2026-10-20 | 2026-11-27 |
| [#70295](https://github.com/vllm-project/vllm/pull/70295) | [MoE oracle][1/N] Move weight conversion onto the experts cl... | @R3hankhan123 | draft | 2026-11-27 | 2026-11-27 |
| [#68325](https://github.com/vllm-project/vllm/pull/68325) | [ROCm][Perf] Add tuned W8A8 block-FP8 Triton GEMM configs fo... | @BoyuZhang-amd | open | 2026-11-18 | 2026-11-27 |
| [#61091](https://github.com/vllm-project/vllm/pull/61091) | [ROCm][Perf] Add native per-token FP8 Triton linear backend ... | @big-yellow-duck | open | 2026-10-11 | 2026-11-27 |
| [#67844](https://github.com/vllm-project/vllm/pull/67844) | [ROCm] Enable Octave KV on RDNA4 with native HIP kernels | @big-yellow-duck | open | 2026-11-16 | 2026-11-27 |
| [#55098](https://github.com/vllm-project/vllm/pull/55098) | [ROCm] Route AWQ linear through choose_mp_linear_kernel | @mgehre-amd | open | 2026-09-03 | 2026-11-27 |
| [#69785](https://github.com/vllm-project/vllm/pull/69785) | [ROCm][Perf] Default the ViT attention backend to TRITON_ATT... | @BoyuZhang-amd | open | 2026-11-25 | 2026-11-27 |
| [#68060](https://github.com/vllm-project/vllm/pull/68060) | [ROCm][Perf] W4A16: serve deep-K skinny GEMM shapes with the... | @BoyuZhang-amd | open | 2026-11-17 | 2026-11-27 |
| [#63729](https://github.com/vllm-project/vllm/pull/63729) | [ROCm][Muse Spark] Support EPLB with FP8 and BF16 experts | @mjkvaak-amd | open | 2026-10-26 | 2026-11-27 |
| [#65021](https://github.com/vllm-project/vllm/pull/65021) | [Doc][ROCm] Add verified MI300X and MI325X entries to Qwen3.... | @phambinhfin | open | 2026-11-01 | 2026-11-27 |
| [#69266](https://github.com/vllm-project/vllm/pull/69266) | [ROCm][Perf] QSA: run FP8 K/V sparse attention as FP8 MFMA o... | @mjkvaak-amd | open | 2026-11-22 | 2026-11-27 |
| [#66564](https://github.com/vllm-project/vllm/pull/66564) | [Quark][ROCm] MXFP4 MoE with dynamic MXFP8 activations | @jimmy-adams | open | 2026-11-09 | 2026-11-27 |
| [#64089](https://github.com/vllm-project/vllm/pull/64089) | [ROCm][Kernel] Add AITER Triton MXFP8 linear kernel for CDNA... | @afriedri-amd | open | 2026-10-27 | 2026-11-27 |
| [#64722](https://github.com/vllm-project/vllm/pull/64722) | [ROCm][MoRIIO] Support DCP for decode instance in PD disaggr... | @kliuae | open | 2026-10-30 | 2026-11-27 |
| [#70331](https://github.com/vllm-project/vllm/pull/70331) | [ROCm][MoE] Overlap large eager routed MoE dispatch and comb... | @fanxingran | open | 2026-11-27 | 2026-11-27 |
| [#69541](https://github.com/vllm-project/vllm/pull/69541) | [ROCm][Perf] Skip chunked-context metadata in ROCM_AITER_FA ... | @dorado269 | open | 2026-11-24 | 2026-11-27 |
| [#68646](https://github.com/vllm-project/vllm/pull/68646) | [ROCm][MiniMax] Enable UMBP KV offloading with M3 and M3.1 m... | @maralbahari | open | 2026-11-19 | 2026-11-27 |
| [#70318](https://github.com/vllm-project/vllm/pull/70318) | [ROCm][Perf][Gemma4] Opt-in 4-bit (e2m1) KV cache for the gl... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70317](https://github.com/vllm-project/vllm/pull/70317) | [ROCm][Perf][Gemma4] Opt-in FlyDSL FP8 gate_up with bf16 dow... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70316](https://github.com/vllm-project/vllm/pull/70316) | [ROCm][Perf][Gemma4] Fuse the per-layer-input (PLE) branch | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70315](https://github.com/vllm-project/vllm/pull/70315) | [ROCm][Perf][Gemma4] Fuse the per-layer residual / RMSNorm c... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70314](https://github.com/vllm-project/vllm/pull/70314) | [ROCm][Perf] FlyDSL FP8 GEMM and fused gated-MLP kernels for... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70312](https://github.com/vllm-project/vllm/pull/70312) | [ROCm][Perf][Gemma4] One Triton kernel for q/k/v RMSNorm + R... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70311](https://github.com/vllm-project/vllm/pull/70311) | [ROCm][Perf] Fuse GeluAndMul(tanh) with per-token FP8 quanti... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70310](https://github.com/vllm-project/vllm/pull/70310) | [ROCm][Perf][Gemma4] Opt-in FP8 lm_head (VLLM_ROCM_FP8_LM_HE... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70309](https://github.com/vllm-project/vllm/pull/70309) | [ROCm][Perf] ROCM_AITER_UNIFIED_ATTN: separate decode and pr... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#70308](https://github.com/vllm-project/vllm/pull/70308) | [ROCm][Perf][Sampler] Bounded top-k sampling through AITER t... | @mpashkovskii | draft | 2026-11-27 | 2026-11-27 |
| [#65407](https://github.com/vllm-project/vllm/pull/65407) | [Bugfix][ROCm][Qwen4Exp] Bind the opaque SP wrappers in the ... | @eppaneamd | draft | 2026-11-03 | 2026-11-27 |
| [#65128](https://github.com/vllm-project/vllm/pull/65128) | [MORI][DBO] Dual batch overlap with MoRI EPv2 (native and As... | @ChengYM5685 | open | 2026-11-02 | 2026-11-27 |
| [#68946](https://github.com/vllm-project/vllm/pull/68946) | [ROCm][Bugfix] Fix breakable cudagraph defaults for GLM DSA | @Ankit847234 | open | 2026-11-20 | 2026-11-27 |
| [#67704](https://github.com/vllm-project/vllm/pull/67704) | [ROCm][Spec Decode] Add get_top_tokens to the GLM-5.2 / Deep... | @phambinhfin | open | 2026-11-14 | 2026-11-27 |
| [#70294](https://github.com/vllm-project/vllm/pull/70294) | [Bugfix][ROCm][DSv4.1] Keep the FNUZ NaN byte out of the ind... | @yuzhouo7 | open | 2026-11-27 | 2026-11-27 |
| [#63674](https://github.com/vllm-project/vllm/pull/63674) | [Feature][ROCm] Add MoRI-IO data transport for the EC CPU co... | @nitsingh2 | open | 2026-10-26 | 2026-11-27 |
| [#70211](https://github.com/vllm-project/vllm/pull/70211) | [ROCm][Perf][Qwen4Exp] Tune the Triton QSA indexer on CDNA4 | @mjkvaak-amd | draft | 2026-11-26 | 2026-11-27 |
| [#69606](https://github.com/vllm-project/vllm/pull/69606) | [Feat] [AMD] dspark: Packed UltraQuant verify and windowed A... | @amd-preetesh | open | 2026-11-24 | 2026-11-27 |
| [#62092](https://github.com/vllm-project/vllm/pull/62092) | [ROCm][AITER] Enable AITER by default on CDNA3+, keep AMD CI... | @AndreasKaratzas | draft | 2026-10-16 | 2026-11-27 |
| [#69511](https://github.com/vllm-project/vllm/pull/69511) | [ROCm] DeepSeek-V4: share the FP8 WO_A cos/sin cache across ... | @peizhang56 | open | 2026-11-24 | 2026-11-27 |
| [#69953](https://github.com/vllm-project/vllm/pull/69953) | [Bugfix][ROCm][MLA] Give padded single-token decode uniform ... | @hibagus | open | 2026-11-25 | 2026-11-27 |
| [#70258](https://github.com/vllm-project/vllm/pull/70258) | [ROCm][Perf][DSv4.1] Support dynamic EPLB with AITER MegaMoE | @shen-shanshan | draft | 2026-11-27 | 2026-11-27 |
| [#69797](https://github.com/vllm-project/vllm/pull/69797) | [ROCm] ViT: fall back from FLASH_ATTN when flash_attn cannot... | @Shan2L | open | 2026-11-25 | 2026-11-27 |
| [#62239](https://github.com/vllm-project/vllm/pull/62239) | [ROCm][Perf] MXFP8 emulation: run the dequantized weight thr... | @abdmalik-amd | open | 2026-10-18 | 2026-11-27 |
| [#62197](https://github.com/vllm-project/vllm/pull/62197) | [Bugfix][ROCm] Fix DeepSeek-V4.1 decode on gfx11/gfx12 (miss... | @abdmalik-amd | open | 2026-10-17 | 2026-11-27 |
| [#70238](https://github.com/vllm-project/vllm/pull/70238) | [ROCm][KV Offload] SimpleCPUOffload: keep the CPU KV cache u... | @Fangzhou-Ai | draft | 2026-11-27 | 2026-11-27 |
| [#70237](https://github.com/vllm-project/vllm/pull/70237) | [ROCm] [MonoKernel] [Qwen3] Add RDNA dense specialized decod... | @keneoneth | draft | 2026-11-26 | 2026-11-26 |
| [#66339](https://github.com/vllm-project/vllm/pull/66339) | [CI][ROCm] Mirror NixlConnector PD accuracy core on MI355 | @AndreasKaratzas | draft | 2026-11-08 | 2026-11-26 |
| [#69477](https://github.com/vllm-project/vllm/pull/69477) | [ROCm][RDNA][Perf] Hybrid W4A16: Triton 3.9 prep | @eble-amd | draft | 2026-11-23 | 2026-11-26 |
| [#59693](https://github.com/vllm-project/vllm/pull/59693) | [ROCm][Perf] Kimi-K3: token-sharded residual stream for long... | @vanshbhatia-amd | open | 2026-10-01 | 2026-11-26 |
| [#61433](https://github.com/vllm-project/vllm/pull/61433) | [ROCm][Spec Decode] Rank unified attention above ROCM_ATTN f... | @zhentaocc | open | 2026-10-13 | 2026-11-26 |
| [#67702](https://github.com/vllm-project/vllm/pull/67702) | [Bugfix][ROCm] Accept a float16/bfloat16 KV cache in the cus... | @phambinhfin | draft | 2026-11-14 | 2026-11-26 |
| [#54258](https://github.com/vllm-project/vllm/pull/54258) | [ROCm][Perf] Widen the sparse index-remap tile for prefill-s... | @cagrikymk | open | 2026-08-28 | 2026-11-26 |
| [#70007](https://github.com/vllm-project/vllm/pull/70007) | [ROCm][DSv4] Score the sparse-indexer prefill on ShardQ toke... | @peizhang56 | open | 2026-11-26 | 2026-11-26 |
| [#67875](https://github.com/vllm-project/vllm/pull/67875) | [Bugfix][ROCm] Pick a backend that fits the kernel block siz... | @ChengYM5685 | draft | 2026-11-16 | 2026-11-26 |
| [#58962](https://github.com/vllm-project/vllm/pull/58962) | [ROCM][KVConnector][MoRIIO] Add WRITE-mode consumer timeout | @amirakb89 | open | 2026-09-28 | 2026-11-26 |
| [#57134](https://github.com/vllm-project/vllm/pull/57134) | [ROCm][Bugfix] Support FP8 KV cache for NoPE sparse MLA | @amd-dlimpus | open | 2026-09-16 | 2026-11-26 |
| [#62490](https://github.com/vllm-project/vllm/pull/62490) | [Model][Hardware][AMD] Use AITER Gluon MLA for Small FP8-KV ... | @farlukas | open | 2026-10-19 | 2026-11-26 |
| [#70129](https://github.com/vllm-project/vllm/pull/70129) | [ROCm][MoE] MORI EPv1: bound expert rows to the receivable t... | @raviguptaamd | open | 2026-11-26 | 2026-11-26 |
| [#70126](https://github.com/vllm-project/vllm/pull/70126) | [ROCm][Perf][Qwen4Exp] QSA: FP8 K/V sparse attention launch ... | @mjkvaak-amd | draft | 2026-11-26 | 2026-11-26 |
| [#69686](https://github.com/vllm-project/vllm/pull/69686) | [ROCm][RDNA][MoE] Hybrid W4A16 MoE: Triton 3.9 prep | @stoivonen-amd | draft | 2026-11-24 | 2026-11-26 |
| [#54120](https://github.com/vllm-project/vllm/pull/54120) | [Tool Parser] Add an Inkling structural tag so forced tool c... | @sungsooha | open | 2026-08-27 | 2026-11-26 |
| [#68512](https://github.com/vllm-project/vllm/pull/68512) | [ROCm][Perf][Muse Spark] DSpark: Markov sampling on each ran... | @sawsa307 | draft | 2026-11-18 | 2026-11-26 |
| [#55985](https://github.com/vllm-project/vllm/pull/55985) | [Model Loader] Bound fastsafetensors peak memory with the fi... | @gitbisector | open | 2026-09-09 | 2026-11-26 |
| [#65403](https://github.com/vllm-project/vllm/pull/65403) | [Perf][Sparse MLA][GLM-5.3] Index-group workspace savings an... | @gaby | open | 2026-11-03 | 2026-11-26 |
| [#69912](https://github.com/vllm-project/vllm/pull/69912) | [Perf][Sparse MLA][GLM-5.3] Opt-in masked-MHA prefill on qua... | @gaby | open | 2026-11-25 | 2026-11-26 |
| [#62909](https://github.com/vllm-project/vllm/pull/62909) | [ROCm][Kernel] Tune the GatedDeltaNet Triton prefill kernels... | @phambinhfin | draft | 2026-10-21 | 2026-11-26 |
| [#69919](https://github.com/vllm-project/vllm/pull/69919) | [ROCm] Replace deprecated __hip_atomic_load with __scoped_at... | @roberteg16 | open | 2026-11-25 | 2026-11-26 |
| [#69378](https://github.com/vllm-project/vllm/pull/69378) | [ROCm][Perf] AITER FA: build the extend metadata without a h... | @amd-mghanimi | open | 2026-11-23 | 2026-11-26 |
| [#70148](https://github.com/vllm-project/vllm/pull/70148) | [ROCm][Kernel] Run W4A16 linear layers on hipBLASLt through ... | @roberteg16 | open | 2026-11-26 | 2026-11-26 |
| [#69254](https://github.com/vllm-project/vllm/pull/69254) | [Bugfix][ROCm][KV Offload] Check the CPU offload region agai... | @yuzhouo7 | open | 2026-11-22 | 2026-11-26 |
| [#69339](https://github.com/vllm-project/vllm/pull/69339) | [ROCm][Perf][DSv4.1] All-gather wo_b's MXFP8 input instead o... | @fanxingran | open | 2026-11-23 | 2026-11-26 |
| [#70176](https://github.com/vllm-project/vllm/pull/70176) | [Fast Start][ROCm] Share AITER's compiled kernels through a ... | @gchinora | draft | 2026-11-26 | 2026-11-26 |
| [#69962](https://github.com/vllm-project/vllm/pull/69962) | [ROCm][Perf][MLA] AITER Gluon MLA decode: split rebalance an... | @lucas-santos-amd | draft | 2026-11-25 | 2026-11-26 |
| [#69142](https://github.com/vllm-project/vllm/pull/69142) | [ROCm][Perf] wvSplitK: packed bf16 dot on gfx950 | @mawong-amd | open | 2026-11-21 | 2026-11-26 |
| [#69191](https://github.com/vllm-project/vllm/pull/69191) | [ROCm][Attention] FlyDSL FP8 extend kernel for the FP8-query... | @mawong-amd | open | 2026-11-22 | 2026-11-26 |
| [#67145](https://github.com/vllm-project/vllm/pull/67145) | [ROCm][Attention] AITER FA: FP8-query prefill attention (use... | @mawong-amd | open | 2026-11-12 | 2026-11-26 |
| [#64928](https://github.com/vllm-project/vllm/pull/64928) | [ROCm][Multimodal][MiniMax-M3, M3.1-Flash] FP8 ViT attention... | @vorapolsiloai | open | 2026-10-31 | 2026-11-26 |
| [#67499](https://github.com/vllm-project/vllm/pull/67499) | [ROCm][Kimi-K3] Run the specialized decode with online FP8 l... | @jakki-amd | draft | 2026-11-13 | 2026-11-26 |
| [#47191](https://github.com/vllm-project/vllm/pull/47191) | [Examples] Add Ray Data batch VLM inference example with ROC... | @jakki-amd | open | 2026-06-30 | 2026-11-26 |
| [#68064](https://github.com/vllm-project/vllm/pull/68064) | [Core][DBO] Allow dual batch overlap with pipeline paralleli... | @ChengYM5685 | draft | 2026-11-17 | 2026-11-26 |
| [#68519](https://github.com/vllm-project/vllm/pull/68519) | [ROCm][Perf][Muse Spark] DSpark draft: fused residual bounda... | @sawsa307 | draft | 2026-11-18 | 2026-11-26 |
| [#68518](https://github.com/vllm-project/vllm/pull/68518) | [ROCm][Perf][Muse Spark] DSpark draft: fused context norms a... | @sawsa307 | draft | 2026-11-18 | 2026-11-26 |
| [#68517](https://github.com/vllm-project/vllm/pull/68517) | [ROCm][Perf][Muse Spark] Decode monokernel TP4: EP buddy exp... | @sawsa307 | draft | 2026-11-18 | 2026-11-26 |
| [#68150](https://github.com/vllm-project/vllm/pull/68150) | [ROCm][Quantization] Re-quantize block-FP8 MoE experts to in... | @xaguilar-amd | open | 2026-11-17 | 2026-11-26 |
| [#66330](https://github.com/vllm-project/vllm/pull/66330) | [ROCm][Perf] MXFP8 native linear: 128x128x128 two-stage tile... | @iraj465 | open | 2026-11-07 | 2026-11-26 |
| [#68520](https://github.com/vllm-project/vllm/pull/68520) | [ROCm][Perf][Muse Spark] DSpark: one-launch greedy combine f... | @sawsa307 | draft | 2026-11-18 | 2026-11-26 |
| [#68960](https://github.com/vllm-project/vllm/pull/68960) | [CI][ROCm] Layer math in the float64 reference (T2 of #68016... | @divakar-amd | draft | 2026-11-20 | 2026-11-26 |
| [#56944](https://github.com/vllm-project/vllm/pull/56944) | [ROCm][Perf] Shard DSA prefill indexer rows across TP ranks | @sumin-hong | open | 2026-09-15 | 2026-11-26 |
| [#65188](https://github.com/vllm-project/vllm/pull/65188) | [ROCm][Perf] Qwen3.8-Flash-Next specialized decode at 1-192 ... | @samremes | open | 2026-11-02 | 2026-11-26 |
| [#70097](https://github.com/vllm-project/vllm/pull/70097) | [Refactor][Kimi K3.1] Clean up dead code, duplication, and s... | @ZJY0516 | draft | 2026-11-26 | 2026-11-26 |
| [#67570](https://github.com/vllm-project/vllm/pull/67570) | [ROCm][Perf][Qwen4Exp] Allow replicated MoE with --monokerne... | @eppaneamd | draft | 2026-11-13 | 2026-11-26 |
| [#68160](https://github.com/vllm-project/vllm/pull/68160) | [Quantization][ROCm] Remove `AITER_BF16_FP8_MOE_BOUND` patch... | @fxmarty-amd | open | 2026-11-17 | 2026-11-26 |
| [#70102](https://github.com/vllm-project/vllm/pull/70102) | [ROCm] AITER cascade (shared-prefix) decode attention integr... | @mpashkovskii | draft | 2026-11-26 | 2026-11-26 |
| [#70033](https://github.com/vllm-project/vllm/pull/70033) | [ROCm][DeepSeek-V4.1] Enable native monokernel decode on the... | @yuzhouo7 | open | 2026-11-26 | 2026-11-26 |
| [#68119](https://github.com/vllm-project/vllm/pull/68119) | fix(rocm): drain GPU during MLA fp8 BMM load warmup | @zhenhantech | open | 2026-11-17 | 2026-11-26 |
| [#68395](https://github.com/vllm-project/vllm/pull/68395) | [ROCm] ROCM_RDNA_ATTN: full builds for a window covering max... | @roberteg16 | open | 2026-11-18 | 2026-11-26 |
| [#67445](https://github.com/vllm-project/vllm/pull/67445) | [ROCm][RDNA] ROCM_RDNA_ATTN: gfx1150..gfx1153 and RDNA4 tabl... | @roberteg16 | open | 2026-11-13 | 2026-11-26 |
| [#70087](https://github.com/vllm-project/vllm/pull/70087) | [ROCm][AITER] Support in-place fused add RMSNorm | @lunarwhite | open | 2026-11-26 | 2026-11-26 |
| [#63322](https://github.com/vllm-project/vllm/pull/63322) | [ROCm][Perf] Muse Spark: compute pooler/unpooler output colu... | @fanxingran | open | 2026-10-23 | 2026-11-26 |
| [#70085](https://github.com/vllm-project/vllm/pull/70085) | [Bugfix][ROCm] Skip unavailable all-reduce fusion without AI... | @lunarwhite | open | 2026-11-26 | 2026-11-26 |
| [#62660](https://github.com/vllm-project/vllm/pull/62660) | [ROCm][Perf] Opt-in FlyDSL paged-attention decode for ROCM_A... | @tpopp | draft | 2026-10-20 | 2026-11-26 |
| [#63555](https://github.com/vllm-project/vllm/pull/63555) | [ROCm] GLM-5.3-Flash MonoKernel: MTP verify step | @amd-sriram | draft | 2026-10-25 | 2026-11-26 |
| [#68141](https://github.com/vllm-project/vllm/pull/68141) | [ROCm][Perf][Kimi-K3.1] DSA layers in the AMD specialized de... | @nsusanto | open | 2026-11-17 | 2026-11-26 |
| [#52684](https://github.com/vllm-project/vllm/pull/52684) | [ROCm] Tune gfx1151 unified attention for long prefill | @liminfei-amd | open | 2026-08-18 | 2026-11-26 |
| [#65918](https://github.com/vllm-project/vllm/pull/65918) | [Quark] Rotate MXFP4 MoE activations before both GEMMs | @jimmy-adams | open | 2026-11-05 | 2026-11-26 |
| [#64676](https://github.com/vllm-project/vllm/pull/64676) | [Bugfix][UMBP] Reject DRAM watermarks that keep MORI from ev... | @AndreasKaratzas | open | 2026-10-30 | 2026-11-26 |
| [#67494](https://github.com/vllm-project/vllm/pull/67494) | [Perf][ROCm] AWQ: keep awq_gemm for decode under torch.compi... | @adityaja-amd | open | 2026-11-13 | 2026-11-26 |
| [#62147](https://github.com/vllm-project/vllm/pull/62147) | [CI][AMD] Add queue fallback config for AMD GPU queues | @AndreasKaratzas | draft | 2026-10-17 | 2026-11-26 |
| [#69994](https://github.com/vllm-project/vllm/pull/69994) | [ROCm][Bugfix] Use expandable segments under the extensible ... | @AndreasKaratzas | open | 2026-11-25 | 2026-11-26 |
| [#67354](https://github.com/vllm-project/vllm/pull/67354) | [CI][ROCm] Mirror PP equivalence tests on AMD | @AndreasKaratzas | open | 2026-11-13 | 2026-11-26 |
| [#68189](https://github.com/vllm-project/vllm/pull/68189) | [Bugfix][ROCm][DSv4.1] Fix online fp8_per_channel on gfx942 ... | @frida-andersson | draft | 2026-11-17 | 2026-11-26 |
| [#69674](https://github.com/vllm-project/vllm/pull/69674) | [Bugfix] Apply the logits soft cap in place | @bogdan-petkovic | merged | 2026-11-24 | 2026-11-26 |
| [#69040](https://github.com/vllm-project/vllm/pull/69040) | [ROCm][Perf][MoE] Keep a 32-column BF16 intermediate pad for... | @frida-andersson | draft | 2026-11-20 | 2026-11-26 |
| [#52637](https://github.com/vllm-project/vllm/pull/52637) | [Quantization][ROCm] Fix symmetric GPTQ loading and optimize... | @tangzzycc | open | 2026-08-17 | 2026-11-26 |
| [#67388](https://github.com/vllm-project/vllm/pull/67388) | [ROCm][Perf] W4A16: deferred-scale bf16 kernel for gfx1151 d... | @Koyingtw | open | 2026-11-13 | 2026-11-26 |
| [#70012](https://github.com/vllm-project/vllm/pull/70012) | [ROCm][Bugfix] Mask padding rows after AITER top-k in fused_... | @rjrock | open | 2026-11-26 | 2026-11-26 |
| [#68712](https://github.com/vllm-project/vllm/pull/68712) | [Modeling] Move `load_weights` FSE logic into a `weights_tra... | @fxmarty-amd | open | 2026-11-19 | 2026-11-25 |
| [#63800](https://github.com/vllm-project/vllm/pull/63800) | [ROCm] Route AITER Triton MXFP4 MoE over local experts under... | @Rohan138 | open | 2026-10-26 | 2026-11-25 |
| [#65224](https://github.com/vllm-project/vllm/pull/65224) | [ROCm][Perf][GLM-5.3-Flash] Split decodes off the AITER Flas... | @waqahmed-amd-fi | draft | 2026-11-02 | 2026-11-25 |
| [#69051](https://github.com/vllm-project/vllm/pull/69051) | [Bugfix][Attention] FULL-graph decode follows reloaded KV sc... | @hao-aaron | draft | 2026-11-20 | 2026-11-25 |
| [#69442](https://github.com/vllm-project/vllm/pull/69442) | [ROCm] Bump AITER to ROCm/aiter@2eb9ef3 (v0.1.30 + FlyDSL 0.... | @josh-schell-amd | draft | 2026-11-23 | 2026-11-25 |
| [#68513](https://github.com/vllm-project/vllm/pull/68513) | [ROCm][MoRI-IO] Transfer circular buffers for DSv41 PD | @simondanielsson | draft | 2026-11-18 | 2026-11-25 |
| [#67300](https://github.com/vllm-project/vllm/pull/67300) | [Spec Decode] Hierarchical LM head for drafters | @jiaji-huang | open | 2026-11-12 | 2026-11-25 |
| [#53918](https://github.com/vllm-project/vllm/pull/53918) | [ROCm][Perf] Enable AITER fuse_qk_norm_rope_kvcache for Gemm... | @mpashkovskii | open | 2026-08-26 | 2026-11-25 |
| [#56116](https://github.com/vllm-project/vllm/pull/56116) | [Quantization] Support ModelOpt layer-wise mixed KV cache | @meenchen | open | 2026-09-09 | 2026-11-25 |
| [#65690](https://github.com/vllm-project/vllm/pull/65690) | [Bugfix][ROCm][MoRIIO] READ: fail only the requests of a fai... | @amd-weisun | open | 2026-11-04 | 2026-11-25 |
| [#68723](https://github.com/vllm-project/vllm/pull/68723) | [ROCm][Perf] gfx942: cascade attention over the decode rows ... | @mpashkovskii | draft | 2026-11-19 | 2026-11-25 |
| [#68772](https://github.com/vllm-project/vllm/pull/68772) | [ROCm][DSA] Enable the DCP global-key indexer prefill by def... | @andyluo7 | open | 2026-11-19 | 2026-11-25 |
| [#65696](https://github.com/vllm-project/vllm/pull/65696) | [ROCm] Experiment OCTAVE KV on GFX1151 | @wildwestoat | open | 2026-11-04 | 2026-11-25 |
| [#62190](https://github.com/vllm-project/vllm/pull/62190) | [Bugfix][KV Connector][ROCm] MoRIIO hybrid READ: skip locall... | @whx-sjtu | open | 2026-10-17 | 2026-11-25 |
| [#69950](https://github.com/vllm-project/vllm/pull/69950) | [ROCm][Kernel] Migrate quick reduce to libtorch stable | @giuseppegrossi | open | 2026-11-25 | 2026-11-25 |
| [#59610](https://github.com/vllm-project/vllm/pull/59610) | [ROCm][Perf] Fold the MXFP4 dense activation quant into SiLU... | @reger-men | open | 2026-10-01 | 2026-11-25 |
| [#67381](https://github.com/vllm-project/vllm/pull/67381) | [ROCm][Muse Spark] Online fp8_per_channel dense linears on t... | @mawong-amd | open | 2026-11-13 | 2026-11-25 |
| [#66084](https://github.com/vllm-project/vllm/pull/66084) | [ROCm][Spec Decode] Support int4 draft_lm_head_quantization ... | @weimin023 | open | 2026-11-06 | 2026-11-25 |
| [#58944](https://github.com/vllm-project/vllm/pull/58944) | [ROCm][Perf][Kimi-K3] Accumulate the latent-MoE up-projectio... | @nsusanto | open | 2026-09-27 | 2026-11-25 |
| [#67380](https://github.com/vllm-project/vllm/pull/67380) | [ROCm][Muse Spark] Ulysses sequence parallelism for TEP pref... | @mawong-amd | open | 2026-11-13 | 2026-11-25 |
| [#66128](https://github.com/vllm-project/vllm/pull/66128) | [ROCm][Perf][DSv4.1] Score adaptive-verification decode step... | @maslan-coop26 | open | 2026-11-06 | 2026-11-25 |
| [#63029](https://github.com/vllm-project/vllm/pull/63029) | [ROCm][DSv4.1] Dispatch pre-quantized MXFP4 through MoRI for... | @maslan-coop26 | open | 2026-10-22 | 2026-11-25 |
| [#69935](https://github.com/vllm-project/vllm/pull/69935) | [Bench][ROCm] Support ROCm in vllm bench forward | @mpashkovskii | open | 2026-11-25 | 2026-11-25 |
| [#66484](https://github.com/vllm-project/vllm/pull/66484) | [ROCm][Perf] Tune MXFP4 MoE tiles on gfx942 | @tc3oliver | open | 2026-11-09 | 2026-11-25 |
| [#68751](https://github.com/vllm-project/vllm/pull/68751) | [ROCm][CI] Skip MI355 DP Basic for code its tests never load | @ahsan-ca | open | 2026-11-19 | 2026-11-25 |
| [#69685](https://github.com/vllm-project/vllm/pull/69685) | [ROCm][CI] Run the per-PR MI355 8-GPU steps only on the csrc... | @ahsan-ca | open | 2026-11-24 | 2026-11-25 |
| [#69922](https://github.com/vllm-project/vllm/pull/69922) | [ROCm][DSv41] Mono MoE: LDS mid-result staging for the non-t... | @chao-xu-spec | open | 2026-11-25 | 2026-11-25 |
| [#61962](https://github.com/vllm-project/vllm/pull/61962) | [AMD][DSV4.1Flash] Quantize MoE activations to MXFP8 before ... | @heachary | open | 2026-10-16 | 2026-11-25 |
| [#69684](https://github.com/vllm-project/vllm/pull/69684) | [ROCm][CI] Mirror (B200) Distributed Preload with Mooncake S... | @gchinora | draft | 2026-11-24 | 2026-11-25 |
| [#69447](https://github.com/vllm-project/vllm/pull/69447) | [ROCm][DBO] Don't reserve comm SMs when nothing partitions t... | @amd-maradosa | open | 2026-11-23 | 2026-11-25 |
| [#69892](https://github.com/vllm-project/vllm/pull/69892) | [ROCm][Perf][Kimi-K3.1] Fold q_b_proj and W_UK into one GEMM... | @nsusanto | open | 2026-11-25 | 2026-11-25 |
| [#68758](https://github.com/vllm-project/vllm/pull/68758) | [ROCm][DSA] DCP global-key indexer prefill (opt-in) | @andyluo7 | open | 2026-11-19 | 2026-11-25 |
| [#68759](https://github.com/vllm-project/vllm/pull/68759) | [ROCm] Share the AITER IPC all-gather helper; use it for the... | @andyluo7 | open | 2026-11-19 | 2026-11-25 |
| [#67800](https://github.com/vllm-project/vllm/pull/67800) | [ROCm][Perf][DSv4.1] Fuse the Q RoPE + fp8 cast into the spa... | @Fangzhou-Ai | open | 2026-11-15 | 2026-11-25 |
| [#67368](https://github.com/vllm-project/vllm/pull/67368) | [ROCm][Perf][DSv4.1] Compute the indexer head weights inside... | @okorzh-amd | open | 2026-11-13 | 2026-11-25 |
| [#68993](https://github.com/vllm-project/vllm/pull/68993) | [ROCm][Perf][DSv4.1] Opt-in aiter fused MXFP4 indexer top-51... | @Fangzhou-Ai | draft | 2026-11-20 | 2026-11-25 |
| [#62154](https://github.com/vllm-project/vllm/pull/62154) | [Spec Decode] Fix adaptive verification startup on FA2 GPUs | @waynehacking8 | open | 2026-10-17 | 2026-11-25 |
| [#66734](https://github.com/vllm-project/vllm/pull/66734) | [Bugfix] DBO and async EP fixes: Quark MoE shared experts, t... | @ChengYM5685 | open | 2026-11-10 | 2026-11-25 |
| [#69879](https://github.com/vllm-project/vllm/pull/69879) | [ROCm][Perf] gfx1100: use wvSplitK for decode GEMMs with 6-8... | @Shan2L | open | 2026-11-25 | 2026-11-25 |
| [#59553](https://github.com/vllm-project/vllm/pull/59553) | [ROCm][Perf] Fuse the MXFP4 activation quant into the GDN ga... | @mjkvaak-amd | draft | 2026-10-01 | 2026-11-25 |
| [#69363](https://github.com/vllm-project/vllm/pull/69363) | [ROCm][Qwen4Exp] Skip the folded HC weight when AITER takes ... | @mjkvaak-amd | draft | 2026-11-23 | 2026-11-25 |
| [#69877](https://github.com/vllm-project/vllm/pull/69877) | [ROCm][RDNA] ROCM_RDNA_ATTN: gfx1100 (RDNA3) support with it... | @Shan2L | open | 2026-11-25 | 2026-11-25 |
| [#69878](https://github.com/vllm-project/vllm/pull/69878) | [ROCm][Qwen4Exp] Load the AttnFP8-PLEFP8 checkpoint with MTP | @maeehart | open | 2026-11-25 | 2026-11-25 |
| [#56964](https://github.com/vllm-project/vllm/pull/56964) | [MoE] Generalize unquantized backend fallback for all quanti... | @Aravind0403 | open | 2026-09-15 | 2026-11-25 |
| [#68972](https://github.com/vllm-project/vllm/pull/68972) | [ROCm][RDNA][Perf] Fuse Qwen3.5 GDN in_proj_qkvz + in_proj_b... | @nemanjaudovic | open | 2026-11-20 | 2026-11-25 |
| [#66967](https://github.com/vllm-project/vllm/pull/66967) | [ROCm][Perf] RDNA: head-first grid, vectorized stores and sp... | @Shan2L | open | 2026-11-11 | 2026-11-25 |
| [#69521](https://github.com/vllm-project/vllm/pull/69521) | [Perf][ROCm][Attention] gfx115x: 16-token tiles for wide hea... | @lijipeng787 | open | 2026-11-24 | 2026-11-25 |
| [#57489](https://github.com/vllm-project/vllm/pull/57489) | [ROCm][DSv4.1] Add gfx950 FP4 compressed-KV paged attention | @LiuYinfeng01 | draft | 2026-09-18 | 2026-11-25 |
| [#68505](https://github.com/vllm-project/vllm/pull/68505) | [Refactor][Quantization] Split FP8 and int8 linear kernels i... | @mgoin | open | 2026-11-18 | 2026-11-25 |
| [#65739](https://github.com/vllm-project/vllm/pull/65739) | [Feature][Attention] Add NIXL GPU Device API for MLA DCP A2A | @john-evan08 | draft | 2026-11-04 | 2026-11-25 |
| [#69825](https://github.com/vllm-project/vllm/pull/69825) | [ROCm][Build] Declare every file hipify writes as a byproduc... | @Shan2L | open | 2026-11-25 | 2026-11-25 |
| [#45916](https://github.com/vllm-project/vllm/pull/45916) | [Perf][Kernel][ROCm] Add Triton split-KV paged decode fallba... | @feiyehua | open | 2026-06-17 | 2026-11-25 |
| [#68596](https://github.com/vllm-project/vllm/pull/68596) | [ROCm][Perf] Add gfx950 gluon paged-MQA logits kernel for DS... | @RuibinCheung | open | 2026-11-19 | 2026-11-25 |
| [#56834](https://github.com/vllm-project/vllm/pull/56834) | [ROCm][Perf] Fuse the DeepSeek-V4 MXFP4 indexer K writer int... | @LiuYinfeng01 | open | 2026-09-14 | 2026-11-25 |
| [#67642](https://github.com/vllm-project/vllm/pull/67642) | [ROCm][Perf] gfx942 AITER a16w4 MoE: STRIP16 weight layout a... | @ishara0925 | draft | 2026-11-14 | 2026-11-25 |
| [#68399](https://github.com/vllm-project/vllm/pull/68399) | [ROCm][MiniMax-M3.1] Fuse DBO SDMA all-reduce + add + RMSNor... | @vorapolsiloai | draft | 2026-11-18 | 2026-11-25 |
| [#67036](https://github.com/vllm-project/vllm/pull/67036) | [ROCm][Perf][Qwen4Exp] Optimize QSA prefill scoring and FlyD... | @samremes | open | 2026-11-11 | 2026-11-25 |
| [#64369](https://github.com/vllm-project/vllm/pull/64369) | [ROCm][Qwen4Exp][DeepSeek-V4.1] Add per_token PLE/Engram loo... | @amd-satre | open | 2026-10-28 | 2026-11-25 |
| [#56156](https://github.com/vllm-project/vllm/pull/56156) | [Core] Pack heterogeneous KV-cache pages for hybrid models | @meenchen | open | 2026-09-09 | 2026-11-25 |
| [#67270](https://github.com/vllm-project/vllm/pull/67270) | [ROCm][Perf][Qwen3.8] Use AITER tgemm for tuned shapes on gf... | @frida-andersson | draft | 2026-11-12 | 2026-11-25 |
| [#64325](https://github.com/vllm-project/vllm/pull/64325) | [ROCm][Perf] Defer GDN spec decode state stores in the Trito... | @zhentaocc | open | 2026-10-28 | 2026-11-24 |
| [#67930](https://github.com/vllm-project/vllm/pull/67930) | [Doc][ROCm] DeepSeek-V4.1-Flash: enable adaptive DSpark veri... | @tuukkjs | draft | 2026-11-16 | 2026-11-24 |
| [#69734](https://github.com/vllm-project/vllm/pull/69734) | [ROCm][Perf][MiniMax-M3.1] Mono decode over the NVFP4 K/V an... | @micah-wil | open | 2026-11-24 | 2026-11-24 |
| [#67324](https://github.com/vllm-project/vllm/pull/67324) | [ROCm][Perf][Kimi-K3] KDA speculative verify through AITER F... | @xaguilar-amd | draft | 2026-11-12 | 2026-11-24 |
| [#69716](https://github.com/vllm-project/vllm/pull/69716) | [ROCm][DSv4] Route gfx1151 C4A paged-MQA decode to Triton | @keneoneth | draft | 2026-11-24 | 2026-11-24 |
| [#58258](https://github.com/vllm-project/vllm/pull/58258) | [Kernel][ROCm] change prefix_prefill kenrel num_warps=8 for ... | @jundali77 | open | 2026-09-23 | 2026-11-24 |
| [#66563](https://github.com/vllm-project/vllm/pull/66563) | [ROCm][Perf] Extend default CUDA graph sizes for speculative... | @tuukkjs | open | 2026-11-09 | 2026-11-24 |
| [#59035](https://github.com/vllm-project/vllm/pull/59035) | [ROCm][Perf] Fold Q fp8 quantization into QkNormRopeKvCacheF... | @mpashkovskii | open | 2026-09-28 | 2026-11-24 |
| [#61397](https://github.com/vllm-project/vllm/pull/61397) | [ROCm][Misc] Write GDN prefill output in place for the Trito... | @samremes | open | 2026-10-13 | 2026-11-24 |
| [#69714](https://github.com/vllm-project/vllm/pull/69714) | [ROCm][Model] Beam DFlash2: model-local split-KV sliding-win... | @jamesETsmith | draft | 2026-11-24 | 2026-11-24 |
| [#69719](https://github.com/vllm-project/vllm/pull/69719) | [ROCm][CI] Add an aiter unified attention decode test mirror... | @aarushjain29 | draft | 2026-11-24 | 2026-11-24 |
| [#69718](https://github.com/vllm-project/vllm/pull/69718) | [ROCm][CI] Run aiter unified attention tests on RDNA | @aarushjain29 | draft | 2026-11-24 | 2026-11-24 |
| [#69547](https://github.com/vllm-project/vllm/pull/69547) | [ROCm] Check skinny-GEMM kernel launches instead of returnin... | @fululi12 | open | 2026-11-24 | 2026-11-24 |
| [#64545](https://github.com/vllm-project/vllm/pull/64545) | [ROCm][Kimi-K3] Attention kernel cleanup for mixed Prefill +... | @omuhamma | open | 2026-10-29 | 2026-11-24 |
| [#65716](https://github.com/vllm-project/vllm/pull/65716) | [ROCm] More gfx1250 models, di, and perf fixes  | @jpvillam-amd | open | 2026-11-04 | 2026-11-24 |
| [#69471](https://github.com/vllm-project/vllm/pull/69471) | [ROCm] Lower the extensible KV cache margin floor back to 2 ... | @djramic | draft | 2026-11-23 | 2026-11-24 |
| [#60400](https://github.com/vllm-project/vllm/pull/60400) | [ROCm][Perf] GDN packed decode: tuned launch configs for gfx... | @nemanjaudovic | open | 2026-10-07 | 2026-11-24 |
| [#46676](https://github.com/vllm-project/vllm/pull/46676) | [KERNEL][ROCm]Native HIP MXFP4(Compressed+Quark) (dense + Mo... | @JartX | open | 2026-06-25 | 2026-11-24 |
| [#65410](https://github.com/vllm-project/vllm/pull/65410) | [ROCm][Step5] Give the CSA prefill indexer its own ROCm kern... | @akii96 | open | 2026-11-03 | 2026-11-24 |
| [#67793](https://github.com/vllm-project/vllm/pull/67793) | [ROCm][MiniMax-M3.1] Serve NVFP4 routed experts natively on ... | @sshlyapn | open | 2026-11-15 | 2026-11-24 |
| [#69592](https://github.com/vllm-project/vllm/pull/69592) | [ROCm] Warm Qwen W4A16 and attention Triton kernels on gfx11... | @weimin023 | draft | 2026-11-24 | 2026-11-24 |
| [#61848](https://github.com/vllm-project/vllm/pull/61848) | [ROCm][Distributed] Enable custom all-reduce on RDNA4 over P... | @umarkovi-amd | open | 2026-10-15 | 2026-11-24 |
| [#67878](https://github.com/vllm-project/vllm/pull/67878) | [Bugfix][ROCm][MoRIIO] Recover a WRITE producer from a consu... | @ChengYM5685 | draft | 2026-11-16 | 2026-11-24 |
| [#57369](https://github.com/vllm-project/vllm/pull/57369) | [CI] Relax sharded-sampling divergence bound on ROCm | @khluu | open | 2026-09-17 | 2026-09-24 |
| [#57362](https://github.com/vllm-project/vllm/pull/57362) | [CI] Reclaim GPU memory between model initialization tests | @khluu | merged | 2026-09-17 | 2026-09-19 |
