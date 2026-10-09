# Dashboard view retirement and current CI authority

The dashboard measures only current AMD MI GPU workloads in Buildkite `ci`.
Legacy `amd-ci` runs are excluded from current health, parity percentages, and
latency. Physical node and DNS history remain independent infrastructure evidence.

## Removed modules and artifacts

| Removed surface | Deleted producers / code | Deleted artifacts |
| --- | --- | --- |
| CI Workload Trajectory | `collect_hotness.py`, `collect_group_changes.py`, trajectory renderers and snapshot projections, unused matrix hotness fallback/index-merge helpers | `hotness.json`, `group_changes.json`, `operations_v2/trajectory.json` |
| Target Health | `collect_gating_targets.py`, `collect_gating_target_candidates.py`, `collect_gating_proposals.py`, `write_gating_nightlies`, target renderers and joins | `vllm_amd_gating_targets.json`, `gating_targets.json`, `gating_target_candidates.json`, `gating_proposals.json`, `gating_nightlies.json`, `operations_v2/gating.json` |
| Flake / Retry Comparison | comparison tables, historical comparison projections, retry-only comparison helpers | `operations_v2/comparison_retry_evidence.json` |
| CUDA execution comparison | CUDA nightly cohort/selector, counterpart matching, ratios/deltas, comparison charts/drawers, the uncalled generic history browser, and legacy utils parity/link/overlay graph | Raw runtime `parity_report.json` and its mirrored root file are not public inputs |
| Duplicate AMD main alert | Duplicate scheduled reconciliation and state projection; shared retry and issue-state helpers remain in use by the canonical MI CI watcher | `open_amd_main_failure_issues.json` |
| Queue Monitor page | queue page renderer, page-only helpers, navigation, controls, CSS, and the obsolete `test_dashboard_trends_data.py` chart contracts | No independent collector retirement: shared observations still serve Omni and infrastructure automation |

Exclusive tests, workflow steps, public manifest entries, storage allocations,
source-audit rules, and CSS were retired with their consumers. Old deep links
redirect to supported views. The still-used hardware identity normalizer moved
from the retired candidate collector into `ci/group_identity.py`.
Latency publication audits use the shared bundle allocation; the historical
flake/retry comparison limits and their exclusive live-data checks are removed.

## Retained contracts

| Capability | Authority and maintenance rule |
| --- | --- |
| Command Center and Upstream parity | Immutable current upstream `main` CI configuration; classification policy contains explicit unsupported groups, never frozen coverage totals |
| AMD mirrors | Inline and native AMD routes from the same current-main source pin; required, optional, and soft-fail source flags stay distinct |
| Runtime AMD health and hardware | AMD MI GPU jobs in `ci`; definitions pinned to the exact observed nightly commit; any-hardware and all-hardware logical counts are explicit |
| AMD nightly latency | Global latest five completed relevant `ci` / `main` nightlies; AMD MI jobs only; maximum complete parallel-shard wall minutes per nightly, then median; sample count, dates, and exact jobs shown; no older backfill |
| Reliability / retry drilldowns | Current AMD MI `ci` cohorts and exact retained attempt evidence; these are still used by incident investigation |
| Omni and infrastructure automation | Shared current AMD MI queue, capacity, workload, lifecycle, physical agent health, and DNS evidence; retained independently of removed pages |

## Publication and Actions stability

Bundle v3 publishes exactly eleven lazy sections. Its health readers retain
strict bounded support for immutable v1/v2 publications, deployed before writer
activation. Publication surface contract v6 validates complete v5 restore proofs
before removing retired domains and their clocks. Canonical Actions purge retired
artifacts only after validated selection; the public allowlist also forbids them.

Current source parity refreshes without a Buildkite token. Runtime collection
fetches `ci` once and retains only MI GPU execution. Private roster caches are invalidated
by the schema-v3 current-CI handoff. Request guards, durable attempt budgets,
resumable caches, exact source pins, atomic bounded writes, live audits, browser
checks, publication verification, and synthetic site probes remain enforced.
The canonical job allows seventy-five minutes for collection and publication;
its cross-process request guard still stops Buildkite sends fifty minutes from
before durable reservation. Slow ledger handoffs consume that request window.
This preserves the existing 25-hour ledger, sixteen-attempt limit, 800-start
allowance, and retry backoff while leaving time for final validation and deploy.
Collector logs record exact guarded transport starts per surface and the running
total, so quota regressions can be traced to a producer without exposing source
requests or identities.

Historical attempt-ledger evidence retains its exact known retired surface names
so immutable request accounting remains readable. New evidence and retry requests
accept only active collectors. Workflow audits enforce the retained producer steps
and reject references to deleted collectors.

Current CI health, matrix, and source parity are checked before the longer
analytics fetch. Failed preflight or publication selection retains a bounded
diagnostic artifact with initial candidate findings and later fallback findings.
The artifact excludes private caches, agent identities, and arbitrary logs.
Collector failures also produce that early artifact when a raw audit cannot run.
The matrix selects its observed nightly from fresh CI health before consulting
retained analytics; older analytics metadata cannot reject a new frozen roster.
When a nightly resumes in an active retry, the active head remains visible while
the completed runtime signal uses its exact matching result shard, frozen matrix,
commit, and terminal analytics build. A strictly validated current completed
transaction may publish; only a failed transaction requires baseline recovery.

Current analytics cache writes remove the retired `amd-ci` partition. If the
active cache reaches its existing 256 MiB cap, it keeps a complete recent
interval with an exact coverage boundary; omitted older history is fetched
before publishing the full window. Older GPU rosters without queue metadata
refresh once before their cached results are reused.

Current runtime validation requires concrete MI GPU queue routing and excludes
CPU-only source steps, including those scheduled on an MI queue. Hardware
labels come from verified MI execution. Physical AMD MI node-health percentages
also use `ci` by default. Mixed legacy rollups are
replaced by the freshly collected CI window when their pipeline scope cannot
be separated; subsequent CI-only generations retain their scoped history.

Normal physical node health measures terminal AMD runs from current `ci` builds
created in the selected UTC window. Whole UTC creation-day slices exhaust this
finite cohort across every branch and trigger. Version-two proof explicitly
names its build-creation eligibility and day basis. Both rollups and failure
membership use the parent's creation day; actual start/finish timestamps remain
unchanged for timelines and co-failure analysis. Pending/running executions do
not enter this terminal denominator. A canceled run without its own finish
still requires a final unblocked parent completion bound. The table names this
cohort and shows the exact interval; it does not claim to cover recent jobs
belonging to older builds. Both private ledgers reset when their pipeline or
day basis changes, so old start-day buckets cannot become creation-day counts.
Every search must paginate completely before publication; invalid timestamps,
incomplete pages, or request failures preserve the previous generation.
Exact failure percentages require a current cohort coverage proof for the
selected days. Normal Actions refresh the default seven-day view in bounded
daily slices. Retained observations from earlier refreshes remain available,
but cannot establish an exhaustive sixty-day denominator on their own.
Explicit start-day REST collection remains available with strict version-one
started-job proof: older finished and active builds are
searched separately for each supported state,
through overlapping creation-time partitions with no oldest-age cutoff.
Every page retains the original filters and immediately projects compact
observations. Equal-time boundaries overlap, and invalid filters, ambiguous
ordering, non-progressing pagination, or request limits preserve prior data.
Bounded phase/page counts make slow collection diagnosable without agent or
job identities; queued daily searches are cancelled after the first failure.
Control jobs (`waiter`, `manual`, and `trigger`) do not enter physical run counts.
Failed command jobs explicitly reported as never run (`signature_rejected`,
`agent_incompatible`, or `stack_error`) with no start timestamp also stay outside
that started-job denominator. A supplied start timestamp still requires validation.
Ambiguous command execution timestamps still block the source proof and retain
the prior generation when returned by a queried cohort; failure diagnostics
contain bounded operational metadata.

Private resumable CI result shards survive failed publication. After hydrating
the current frozen job roster, collection reuses a private shard only when its
declared integrity, parser, source, and exact current job identities validate.
This prevents an older published shard for the same build and parser from
forcing hundreds of repeat log downloads after job retries. Cache size limits,
public source audits, and canonical publication selection remain unchanged.

Regressions cover CPU/legacy exclusion, global cohort selection, missing-group
and incomplete-shard behavior, exact five-nightly links, observed agent routing,
current source coverage, retired navigation, malformed legacy restore proofs,
and the complete retained public bundle.

The latency projection retains exact job evidence in a declared column format
when needed to fit its 7 MiB public allocation. A 225-group, five-nightly,
MI-only, eight-shard stress case preserves every sample and round-trips
the evidence exactly; the whole eager bundle still fits its 32 MiB envelope.


## Immutable runtime source verification

`ci/analytics_cache.py` retains `runtime-source-indexes-v1` separately from
runtime build metadata. Each compact index binds a full observed commit to its
verified Git definition tree and CPU-only routes. `main_ci_definitions.py`
verifies Git tree/blob identities before deriving or reusing the index; a cached
source flag is reapplied to the exact job roster rather than treated as routing
proof on its own. The private checkpoint is bounded to 8 MiB and 4,096 pins and
is never part of the public projection. It carries no runtime freshness:
collection timestamps and complete runtime source windows still determine what
may publish.

The manual [Runtime Source Verification workflow](../.github/workflows/runtime-source-warmup.yml)
restores authenticated private CI metadata and this immutable checkpoint, then
runs `collect_analytics.py --prewarm-source-indexes`. It verifies only exact
historical commits already present in the private inventory, makes no Buildkite
requests, and writes no public data. Proved pins are checkpointed independently
as progress is made; an incomplete pass fails visibly while retaining that
bounded progress. Rerun the helper until its completeness output is true before
a cold full refresh. A successful warmup does not advance dashboard clocks or
establish current agent-health coverage.
