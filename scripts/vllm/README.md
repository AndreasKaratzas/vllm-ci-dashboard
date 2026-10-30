# vLLM Dashboard Scripts

Additional data collection scripts specific to the vLLM CI dashboard.

## Scripts

| Script | Purpose | Trigger |
|--------|---------|---------|
| `collect_queue_snapshot.py` | Captures current Buildkite queue-native metrics every permitted poll and refreshes the complete active-job overlay at most hourly; incomplete detail pagination retains the last complete overlay with explicit timestamps/status | Every 10 min via `queue-monitor.yml` (detail at most hourly) |
| `collect_queue_lifecycle.py` | Collects seven-day privacy-minimized queue lifecycle events through resumable exhaustive disjoint parent-created query units without publishing partial generations | 30-minute recovery checks with a two-hour successful cadence via `queue-lifecycle.yml` |
| `collect_docker_builds.py` | Collects 30 days of ROCm docker build jobs (`refresh-rocm-base-amd`, `image-build-amd`, `ensure-ci-base-amd`) on `ci` and `amd-ci` through paced, resumable GraphQL pages into the compact public `docker_builds.json`; branch names and URLs are never stored | 30-minute recovery checks with a two-hour successful cadence via `docker-builds.yml` |
| `collect_analytics.py` | Builds failure rankings, duration rankings, queue wait stats | Every two hours via `hourly-master.yml` |
| `collect_amd_test_matrix.py` | Derives inline and native AMD GPU main CI routes at the exact observed nightly commit | Every reserved full collection via `hourly-master.yml`; cooldown runs retain the validated matrix |
| `collect_perf_eval_artifacts.py` | Ingests AMD nightly perf-eval results from Buildkite artifacts newest-first, persisting what fits the guarded request allowance and deferring the rest to the next run | Every reserved full collection via `hourly-master.yml` |
| `merge_perf_eval_events.py` | Strictly merges a validated durable baseline with candidate perf-eval JSONL through the bounded atomic writer | Every canonical perf-eval seed sync |
| `build_operations_snapshot.py` | Builds the private operations input and bundle-v5 manifest with eleven retained lazy sections (including the compact all-main-builds `main_ci` section), current runtime GPU cohorts, source parity, and exact five-nightly latency | Every canonical collection and Pages assembly |
| `build_queue_section.py` | Builds only the compact public Queue shard from queue-owned inputs | Every independent queue-monitor run |
| `ci_main_failure_watcher.py` | Tracks confirmed `ci`/`main` AMD MI test-group incidents and retains bisect candidate bounds per strict group | Hourly after analytics collection |
| `main_pulse.py` | Lists `ci` branch=main builds finished since its durable cursor and applies their AMD MI jobs through the main-failure watcher's transitions on top of the published watcher state; `export-overlay` hands its newer observations to the collection watcher | Every `queue-monitor.yml` trigger, admitted at most every ten minutes by its own ledger |
| `audit_dashboard_data.py` | Cross-checks generated data, frontend assumptions, and deploy workflow ordering before publishing | Every two hours via `hourly-master.yml` + local debugging |
| `check_site_health.py` | Probes the deployed shell and bounded publication-status contract, emitting JSON and Markdown evidence | Hourly at :57 UTC-minute plus manual `health-check.yml` runs |
| `plan_dns_publication_reconcile.py` | Decides whether a successful live DNS publish must wake the canonical publisher and verifies that queued work still has an unacknowledged DNS generation | After every successful `dns-health.yml` collection and before a DNS-targeted canonical run |
| `plan_queue_publication_reconcile.py` | Wakes the canonical publisher only for an invalid status or an affected Queue surface, then verifies that the requested queue generation reached Pages; the workflow separately confirms its exact durable source ref | After validated queue publication or a fresh zero-request durable retry, and before a queue-targeted canonical run |
| `dns_request_budget.py` | Validates the parentless DNS request ledger and durably reserves a complete per-scan allowance for 25 hours before any Buildkite call, bounding actual starts below 1,000 per rolling day | Every `dns-health.yml` run, plus controlled one-time ledger initialization |
| `request_bearing_attempt_budget.py` | Gates full Data Collection, Queue Lifecycle and Docker Builds through independent parentless 25-hour attempt ledgers, including start-to-start success cadence, bounded failure retry, migration overlap, and read-only webhook/watchdog observation | Before every possible full-collector token exposure, plus controlled one-time initialization |
| `buildkite_request_guard.py` | Enforces the fixed per-attempt allowance across processes by charging every exact Buildkite `requests.Session.send` before transport and rejecting hidden adapter retries | Explicitly at every token-reading CLI/shared client ingress, with `scripts/sitecustomize.py` as process-start defense in depth |
| `ci/backfill_checkpoint.py` | Integrity-validates complete per-nightly CI shards so cache-loss recovery makes monotonic progress across repeated 800-request caps without publishing partial data | Restored and saved even after collector failure by guarded Data Collection runs |
| `docker_builds_bootstrap.py` | Classifies the docker builds first collection as pending, over budget, failed, unknown or collected from the data branch, the docker attempt ledger and the latest completed run, for the publication audit | `hourly-master.yml` before publication selection |
| `plan_publication_watchdog.py` | Plans proactive canonical recovery and suppresses active/recent duplicates; durable full-collection due state comes from the separate attempt ledger rather than general publication timestamps | `publication-watchdog.yml` |
| `dashboard_state.py` | Fully validates/materializes bounded parentless snapshots, provides OID-only metadata validation for watchdogs, creates tested root commits, writes the public marker, rotates refs, and atomically repairs a single valid slot | Canonical collection, deploy-only recovery, watchdog checks, and state rollback |
| `public_projection.py` | Creates the state-bound SHA-256 manifest for every canonical public file and verifies local or remote Git trees without reading large deployed blobs | Before and after every canonical or deploy-only Pages publication, plus the watchdog |
| `verify_published_operations_bundle.py` | Verifies that every deployed Operations shard exists and matches the public manifest byte contract | After every canonical or manual Pages deployment |
| `select_publication_surfaces.py` | Validates collected source transactions, restores failed generated surfaces from the durable dashboard-state baseline while comparing source files with the immutable candidate code, then rebuilds and re-audits the combined snapshot | Every canonical `hourly-master.yml` run before tests |
| `config_parity.py` | Compares AMD vs NVIDIA CI config (commands, test lists) | Part of `collect_ci.py` |
| `pipelines.py` | Pipeline definitions (slug, name patterns, build filters) | Imported by other scripts |

## Environment

Live Buildkite collectors receive `BUILDKITE_TOKEN` or
`BUILDKITE_API_TOKEN` only from GitHub Actions after the workflow has durably
reserved that attempt's request allowance. Every token-reading CLI and shared
client ingress activates the guard before it can construct or send a request;
a token with missing or incomplete guard variables exits with status 78.
Exporting a token alone is therefore not a supported local run mode. Use the
corresponding workflow's `workflow_dispatch` trigger, and never hardcode tokens
in source files.

The dashboard publishes CI data only: no script opens, assigns, or routes
issues for CI incidents, and no team roster is kept here. Ticketing is tracked
outside this repository.

The project snapshot reads upstream vLLM Project #39 as public, read-only
evidence; it does not mutate that Project's issues, comments, or fields.

For queue monitoring specifically, the token needs Buildkite GraphQL access so `collect_queue_snapshot.py` can read cluster queue metrics and scheduled jobs. A dedicated replacement token should be read-only (`read_builds` and `read_clusters`; plus GraphQL access). The production workflow does not fall back to per-queue GraphQL or REST scans: current metrics fail closed, while an incomplete bounded detail read retains the last complete job overlay.

Buildkite's queue-native p50/p95 remain the site-comparable primary values whenever they are available. The fully paginated scheduled-job reconstruction is stored and charted separately, with exact non-zombie n/N coverage, because equal counts do not prove that two sequential reads contain the same jobs or use the same percentile estimator. Queue history keeps every poll for 48 hours, then retains one actual snapshot plus every queue's primary and reconstructed p50/p95/p99 peaks and exact observation times per UTC hour for the remainder of the 30-day window.

The history writer is atomic and capped at 46 MiB. Its compact chart is capped
at 6 MiB, and the pair has an exact shared 52 MiB allocation. If unusually wide
queue schemas exceed that budget, it progressively coarsens only older UTC
buckets while preserving the newest live snapshot and each retained bucket's
exact peak envelopes; it never makes another Buildkite request to compact
storage.

The frequent collector force-publishes a single-commit `queue-data` branch containing only queue-owned evidence and a compact chart feed. The browser compares its current snapshot with the canonical Pages shard, uses the newer one, and falls back to the Pages history if the dedicated feed becomes stale. The verbose JSONL remains available as drill-down evidence but is not reparsed on every chart refresh.

When canonical Queue health is affected, the monitor dispatches a dedicated
`queue_generation` repair. That lane imports only `queue-data`, refreshes only
the Queue publication transaction, preserves Queue Lifecycle and every other
surface, and installs a deny-all Buildkite guard with an exact zero-request
report. Interval- or capacity-gated monitor runs may retry a lost dispatch from
all four files of one durable queue commit, but only while its generation is
less than five hours old. A normal healthy queue poll never triggers a Pages
rebuild. Operational queue degradation and recovery do not need that rebuild:
the browser fetches both the live queue section and its Pages fallback every
five minutes and selects the newer embedded snapshot timestamp. The targeted
planner therefore treats `Queue health` in `publication_status.json` strictly
as publication-integrity state; ordinary live queue pressure remains on the
ten-minute branch feed, while the canonical fallback keeps the normal
two-hour Data Collection cadence.

Every queue trigger, including webhooks and manual dispatch, first acquires an
exact lease on the parentless one-file `queue-request-budget` branch. Metrics
reserve two request starts no more than once per ten minutes; active-job detail
reserves twelve additional starts no more than hourly. At current volume the
normal cost is about 432 requests/day (144 one-page metric reads plus 24
twelve-page detail scans). The 25-hour ledger permits at most 650 outstanding starts, and
the workflow has a 20-minute timeout, leaving more than the one-hour cushion
needed to bound actual request starts in every rolling 24 hours. Missing,
corrupt, ambiguous, or lease-conflicted budget state exposes no Buildkite token.

### Bounded queue lifecycle recovery

Queue Lifecycle has an independent 100-start guarded attempt and never treats
an API retry as free. Its organization-build queries are pairwise-disjoint:
recent parents use `[query_start, query_end)`, while older active and
older-finished parents use `[active_parent_start, query_start)`. Every request
asks only for page one of an exact half-open created-time unit. A full 100-row
response is not accepted or paginated; the unit is atomically replaced by two
adjacent children. Only a short, therefore exhaustive, response completes a
unit. This avoids both overlapping cohorts and an unstable page-number cursor
when recovery spans workflow attempts.

Completed units and their observations are stored in the private
`data/vllm/ci/.cache/queue-lifecycle-wip-v1/checkpoint.json.gz` Actions cache.
The strict checkpoint is capped at 64 MiB compressed, contains only hashed job
identities and the same privacy projection as the durable ledger, and binds its
content digest, exact canonical branch commit, canonical ledger digest, target
queue-map digest, and frozen query/watermark. Missing, malformed,
future-horizon, wrong-base, or wrong-queue checkpoints are discarded and
restarted without feeding bytes into the canonical ledger. A dense interval
that cannot be split records a terminal failure so retries make no further
Buildkite requests. The
750-leaf work-tree ceiling requires at most 1,499 build responses; including
one normal queue-discovery response per retry, it fits within sixteen
100-start attempts (transport retries consume that same hard allowance).
Each collector invocation has a monotonic 40-minute work budget. REST request
timeouts shrink to the remaining budget and a retry delay is rejected before
it could cross the deadline. The workflow also applies a 44-minute step
watchdog beneath its 50-minute job watchdog. A deadline yield is green only
after the checkpoint is re-read and verified against the canonical baseline;
recovery progress remains private and cannot be published as a complete
generation.

Guard exhaustion leaves the interrupted unit pending and exits 75 without
touching `queue_lifecycle.json` or `queue_lifecycle_jobs`. A wall-clock yield
exits 76 and may retain a complete checkpoint when the deadline lands after the
last unit; its next attempt can publish without repeating any build query. The
workflow uses an `always()` cache-save path with a unique immutable run key,
restores the newest namespace entry, and retains only the eight newest entries.
Once every unit is complete, the collector merges against the exact canonical
generation at the original frozen query horizon and atomically builds both
canonical artifacts.
The private job-segment directory has a 16 MiB aggregate compressed cap and a
512 MiB aggregate uncompressed cap. Seven days remains the target window; an
overflow deterministically removes the oldest whole latest-event UTC cohorts,
then (only when necessary) the oldest whole observations in the remaining
boundary day. The public 5 MiB summary is rebuilt exclusively from the retained
rows and publishes the exact shortened-scope and byte-limited attestation.
Incremental recovery cannot erase that disclosure; a full reconciliation or
natural aging beyond the configured window is required. This local compaction
does not issue or enable additional Buildkite requests.
Only after validation, exact guarded-request reporting, and a verified durable
branch push is the WIP deleted; a small cached tombstone prevents an older
completed checkpoint from resurfacing.

The workflow checks its durable gate every 30 minutes while recovery is in
progress. An incomplete attempt may reserve again only after 30 minutes; after
a successful publication, the same ledger permits no new Buildkite-bearing
attempt for two hours. Thus recovery can consume its existing 16-attempt,
1,600-start rolling hard cap without increasing the normal two-hour request
cadence, and a designed request-bound checkpoint is reported as successful
bounded progress rather than a dashboard workflow failure.

The 1,600-start ceiling remains conservative by charging the full 100-start
reservation even when a wall-clock yield used fewer starts. Consequently,
persistently slow responses can consume sixteen partial reservations before
the 1,499-response work tree finishes. Collection then waits until older
25-hour reservations expire and continues from the validated checkpoint; it
does not raise the ceiling, discard completed units, or publish partial data.
There is deliberately no elapsed-age eviction for a still context-valid WIP,
so arbitrarily slow but progressing recovery survives as many rolling-cap
waits as it needs. Future query horizons remain invalid.

### Perf-eval retention and artifact deduplication

Both `data/vllm/perf_eval/events.jsonl` and its derived `perf_eval.json` have an
exact 4 MiB writer limit inside a shared 8 MiB state allocation. Writers
serialize in memory, reject an oversized candidate, and atomically replace the
previous file only after the candidate passes that byte check. This remains
substantially below the dashboard's 90 MB sync limit.

The normal event history is a rolling 180 days with at least the latest 30
complete nightlies. If unusually wide results reach the byte limit first, the
store deterministically removes whole oldest nightly cohorts through smaller
retention tiers, never individual rows from a retained nightly and never the
latest two nightlies needed for deltas. The derived payload applies the same
whole-nightly rule. If the irreducible latest-two payload cannot fit, the write
fails before replacement instead of publishing an oversized or partial file.

Result pruning does not make an artifact eligible for another download. Exact
Buildkite artifact IDs (or the conservative build/job/path/SHA-1 fallback) are
folded into a compact identity index retained for 45 days. The artifact
collector enforces a maximum 30-day Buildkite lookback, so the index always
outlives every artifact the next run can discover. Compaction changes only
local storage; it adds no Buildkite or GitHub API requests.

Canonical startup strictly validates and bounds the private event store restored
from the current `dashboard-state` snapshot before any collector mutates it.
Only the one-time, explicitly gated bootstrap uses the frozen generated roots in
an immutable `main` checkout. The store is an explicitly private build input and
is never expected to exist on gh-pages.
`perf_eval.json` is rebuilt from those validated events instead of copied from
the public site. The shared non-canceling deployment lock serializes every
collector run, while exact source-tree validation and an atomic leased state-ref
rotation prevent a concurrent publication from bypassing validation. This keeps
derived timestamps non-regressing without an additional token, GitHub request,
or Buildkite request. The merge helper still supports strict source-neutral reconciliation
for migrations and repairs: it orders stable result identities by validated
ingestion generation, unions equal-generation disjoint metrics/tasks, and
fails closed on conflicting equal-generation values.

## Data Flow

```
Buildkite API
    |
    v
collect_queue_snapshot.py --> data/vllm/ci/queue_timeseries.jsonl
                          --> data/vllm/ci/queue_jobs.json
build_queue_section.py    --> data/vllm/ci/operations_v2/queue.json
                          --> data/vllm/ci/queue_history_chart.json
                          --> queue-data branch (live browser feed)
collect_capacity_monitor.py --> data/vllm/ci/capacity_monitor.json
collect_analytics.py      --> data/vllm/ci/analytics.json (private full input)
                           --> ci_main_failure_watcher.py,
                               amd_duration_regression_watcher.py
                           --> open_ci_main_failure_issues.json,
                               open_amd_duration_regression_issues.json
                               (private state)
private analytics.json    --> ci/public_analytics.py during build_site.py
                           --> bounded _site/data/vllm/ci/analytics.json
collect_amd_test_matrix.py --> data/vllm/ci/amd_test_matrix.json
config_parity.py          --> data/vllm/ci/config_parity.json
raw operational inputs    --> build_operations_snapshot.py
                           --> operations_v2.json.gz (private bounded build input)
                           --> operations_v2_manifest.json + operations_v2/*.json
                           --> docs/assets/js/ops-v2.js
audit_dashboard_data.py   --> validates data/ + docs/assets/js + workflows
dashboard_state.py        --> dashboard-state (current parentless snapshot)
                           --> dashboard-state-previous (one rollback snapshot)
deployed Pages + publication_status.json
                         --> check_site_health.py
                         --> bounded workflow artifact + marker-owned issue
```

The gh-pages analytics file is the bounded browser projection, not a
last-known-good reliability input. The two-hour collector deliberately retains
the full private artifact from the validated `dashboard-state` snapshot until
collection refreshes it and never seeds it from gh-pages. During the one-time
state bootstrap only, the frozen `main` copy is the seed. This keeps public
evidence from feeding back into incident history, watcher state, or the next
projection.

### Private analytics build cache

The canonical workflow persists only
`data/vllm/ci/.cache/analytics-builds-v1` through GitHub Actions cache storage.
Its immutable key is versioned and changes once per UTC day. The first
successful run of a day saves that day's snapshot; later runs restore the same
snapshot and refetch the rolling 24-hour overlap instead of trying to mutate an
existing cache key. On the next UTC day, the prior-day key is the first restore
fallback; a final versioned namespace fallback retains the newest valid cache
through a multi-day Actions outage. The collector validates and prunes any
restored cache, then forces a full reconciliation when its `generated_at` UTC
date differs from the collection date. It also
forces a full reconciliation once `last_full_at` is at least 24 hours old.
This ensures the first cache saved under each immutable daily key is fully
reconciled instead of freezing an incremental snapshot for that day.

Cache restore or save transport failures are non-fatal optimizations: the
collector continues against Buildkite. If analytics collection itself fails,
the `ci_analytics` publication surface falls back to its validated baseline and the
workflow does not save a cache for the new day, so the next run retries from
the prior safe snapshot. The directory is gitignored, covered by the public
manifest's never-publish policy, never staged, and never seeded from gh-pages.

The private reliability ledger uses schema v2: each retained observation keeps
stable build/job/step identifiers while build URL, commit, message, and creation
time are stored once in the build catalog. Server-side consumers hydrate the
legacy presentation fields before building Operations data or watcher evidence,
so browser cards and popups retain the same contract. The writer is atomic and
enforces a 56 MiB normal operating budget (well below GitHub's 100 MiB blob
limit), reporting per-component bytes and any emergency evidence compaction.
An incremental cache projection that grows by both at least 20% and 8 MiB is
discarded and reconciled once from the exhaustive source before it can replace
the cache.

The immutable dashboard-state tree has a checked 256 MiB ceiling. Its shared
allocation policy now assigns 222 MiB, leaving 34 MiB of global headroom, and
includes a separate 18 MiB envelope for unclassified code/config assets.
Generated operational data has disjoint aggregate groups: Operations control
files (4 MiB), watcher state (3.25 MiB), GitHub Home (768 KiB), and small fixed
operational files (256 KiB). Retired gating, hotness, and group-change data have
no active allocation. The final staged-index guard checks
every group before commit; bounded evidence writers compact whole units and
attest source/published/omitted counts.

The `open_*_issues.json` watcher ledgers compose inside that allocation
through fixed producer envelopes: upstream-main 1 MiB and duration 256 KiB are
written; the retired AMD-main, CI-area, queue-latency, queue-zombie,
agent-health, Omni and flakiness ledgers keep their envelopes so historical
state stays readable. The shared atomic writer preserves every confirmed/pending
incident. When needed, it removes only refetchable row detail, inactive ordering
fences, clear area signals, and retired cache rows, recording exact
source/published/omitted counts in `publication_retention`.

The GitHub Home allocation is also compositional: `projects.json` and
`releases.json` receive 32 KiB each, `prs.json` and `issues.json` receive
320 KiB each, and the Project #39 fallback receives 64 KiB. Writers serialize
and bound candidates before atomically replacing the last-known-good files.
Under byte pressure they remove refetchable body/query detail before retaining
a newest-first prefix of whole rows, retain exact source/published/omitted
accounting, and mark the browser population as a lower bound. Retained
same-repository issue and PR references are reconciled so compaction cannot
create dangling links.

## Bounded last-known-good publication

The canonical workflow separates core health/matrix/current-source parity from
private analytics/reliability. Queue observations, capacity, workload, Omni,
lifecycle, agent health, GitHub home, and perf evaluation have independent
transactions. Contract v9 validates historical v5, v6, v7 and v8 restore proofs
before dropping retired ownership. The v5 gating, group-change, and hotness domains and
their clocks are retired; v6 runtime `parity_report.json` files are verified in
full before their paths are removed. The v7 optional runtime parity override
file is also verified in full before retirement, as are the v8 ticketing files
(`ownership_config_parity.json`, `ci_ownership.json` and the queue and Omni issue
ledgers). Configuration parity remains supported. The
current main inventory refresh uses an immutable source SHA and needs no
Buildkite request. A routed degradation keeps fresh
candidate bytes and publishes an explicit warning. A collector failure or hard
routed audit error instead rejects that surface's entire candidate transaction.
The selector restores the whole failed surface from the validated durable state
snapshot captured before collection, rebuilds the derived Operations data, and
runs the complete audit again. Its separate immutable candidate-code ref proves
that fallback cannot replace source or workflow files with an older state copy.
Unknown findings, code or workflow defects, an invalid baseline, and any
post-restore error remain hard deployment stops.

Attested fallback state is committed privately in
`data/vllm/ci/publication_state.json`; restore paths and hashes never enter the
public site. The state distinguishes fresh degraded surfaces from restored
fallback surfaces. Only restored data has a 36-hour hard limit; a fresh
degradation remains publishable and keeps its fingerprinted CI incident open.
`build_site.py` emits a sanitized `publication_status.json` so the dashboard
can identify fresh degradation, fallback, mixed, and blocked states without
exposing private restore metadata. Next to the `affected_surfaces` labels it
publishes their machine `surface_ids`, the `pending_surface_ids` that are not
yet collected but progressing within their bound, and each surface's
`stale_minutes` from `config/data_freshness_targets.json`; schema version 1 is
unchanged and Site Health accepts a status without these three fields. The
docker builds seed is pending, not degraded, while `docker_builds_bootstrap.py`
shows no `docker-builds-data` branch, a latest completed run that saved
resumable progress, and fewer unpublished attempts than the ledger's 16 per 25
hours; any other bootstrap state stays degraded. Repeated runs of the same
incident do not post duplicate comments. Collector failures carry a bounded typed reason into
the incident fingerprint; a first transient network/HTTP failure uses the
validated baseline without opening a ticket, while deterministic failures and
two consecutive transient failures alert. General incident recovery requires
two distinct eligible healthy canonical publication states, each bound to its
exact dashboard-state and code SHAs. Repeated validation of the same state
does not add recovery credit. Matching DNS-only or queue-only incidents require
one validated targeted source reconciliation, bound to its exact state and code
SHAs. A separate Site Health path can confirm an already credited full
publication: normalized evidence must show a current healthy publication with
no fallback or degraded surfaces, at least two healthy probes out of three,
and at least two probes matching the complete projection for that same state
and code identity.

The legacy manual `ci-collect.yml` workflow is validation-only. It can exercise
the focused collectors and show the resulting workspace changes, but its token
is read-only and it cannot commit, push, or publish around the selector and its
private attestation state.

Browser authentication, user signup, Test Build, Ready Tickets, and Admin have
been retired end-to-end. They have no public route, collector, publication
surface, or mutation workflow.

Runtime target matching is intentionally conservative. Reviewed upstream YAML
keys resolve through the exact nightly definition snapshot; standalone AMD
targets use execution identities that include commands, working directory,
dependencies, and hardware topology. Matrix entries retain those identities,
so same-label executions on different pools cannot exchange results. Display
names follow the current collected definitions while runtime evidence remains
pinned to its build. Older unlinked targets retain exact matrix-label and
definition-parity matching. Only an explicit
trailing `%N` target can aggregate numbered shards. Duplicate matrix identities
merge incident-first and retain each job URL. Unmatched targets publish a
resolution status (`no_amd_definition`, `stale_target_alias`, `ambiguous`, or
`not_observed`) instead of presenting every identity failure as missing runtime
signal.

`reviewed_definition_labels.py` projects names without changing reviewed
coverage or gating decisions. Configured step keys are explicit YAML `key`
values, never list positions. Renames preserve their links; missing keys,
splits, incomplete snapshots, and ambiguous evidence publish a resolution
status. The parity drawer shows replacement definitions and scope notes where
available. Historical review names and source commits remain available for
comparison.

The AMD matrix derives generic cross-hardware aliases from conservative
execution evidence, including equivalent pytest directory spellings. An
explicit, narrow policy covers the reviewed Mamba dependency-revision
difference. Hardware-sensitive rules remain reviewed policy with exact
execution evidence for renames, and the output contains only applicable
rules. Tests cover both historical and renamed configurations.

`scripts/build_site.py --cache-bust-index` assembles `docs/` and `data/`
into `_site/` using `config/public_data_manifest.json`; unlisted collector
state and the compatibility monolith are not published. The canonical Pages
deployment replaces `gh-pages`, which removes retired artifacts. State-backed
publications also pass the stable generation ID with `--cache-bust-value`, so
deploy-only recovery reproduces the tested site byte-for-byte.
Before each root replacement, the publisher size-proves the existing Pages
tree and overlays only bounded whole `pr-preview/pr-N` cohorts. The combined
tree is re-bounded and its preview inventory digest is verified after deploy,
so retired canonical files disappear without silently deleting valid previews.

A state commit carries the `.github/workflows` tree of its pinned code, and
GitHub refuses a `GITHUB_TOKEN` push that introduces a new commit whose
workflow files differ from `main`. So `dashboard_state.py rotate
--default-branch main` compares the candidate's tree with the fetched `main`
tip before pushing (an unreachable `main` skips this check), and treats
GitHub's exact "without `workflows` permission" rejection the same way. Either
case exits 3
without moving a ref: the run warns, skips Pages and the incident issue, and
re-dispatches the same inputs on `main`. A full run first marks its ledger row
`superseded_at`. The row keeps its slot in the 25-hour cap but no longer gates
the next run or extends the failure backoff. One run dispatches at most one
replacement; duplicate dispatches coalesce in the full-collection concurrency
group.

## Current main CI and view retirement

Current CI execution metrics use only AMD MI GPU jobs from the `ci` pipeline.
CUDA, NVIDIA, Intel, CPU, unknown routes, and legacy `amd-ci` executions do not
contribute to current runtime coverage, health, reliability, or latency.
Parity and AMD mirrors are derived from an immutable upstream `main` commit;
`config/vllm_upstream_test_group_parity.json` stores explicit unsupported-group
classification policy, rather than a frozen coverage inventory. Coverage and
required blocking gates are distinct counts, with optional and soft-fail routes
shown separately. The AMD test-group inventory (`mirror_inventory`) lists every
AMD YAML definition on that commit, one row per definition with a `kind` of
`gpu_mirror`, `cpu_mirror` (mirror.amd with `no_gpu`), or `native` (native AMD
step); only GPU mirrors feed coverage. Runtime definitions remain pinned to the observed nightly.
An MI queue is physical routing evidence; it cannot override an exact source
step marked `no_gpu`. Compact private execution indexes bind these exclusions
to each full runtime commit and its verified Git definition tree. The manual
`runtime-source-warmup.yml` workflow checkpoints bounded immutable source
verification without Buildkite access or a new data clock. Incomplete warmups
resume from their proved indexes before the guarded full collection proceeds.

Latency uses the global latest five completed main CI nightlies. Each AMD MI group
contributes at most one sample per nightly: maximum wall time of the complete
parallel shard group, followed by the median across available nightly samples.
Missing groups are unavailable; older nightlies never fill missing samples.

The private `operations_v2.json.gz` build input produces bundle v5 with eleven
allowlisted lazy sections; v5 retires the `ownership` section. Readers still
accept the v3 and v4 inventories, so a deployed older generation stays valid
until the next publication replaces it. Canonical publication replaces the Pages tree,
validates historical restore proofs, and purges retired artifacts. The removal
inventory is tracked in [Dashboard Cleanup](dashboards/dashboard-cleanup.md).

Organization rollups consume
[`org_summary.json`](https://andreaskaratzas.github.io/vllm-ci-dashboard/data/vllm/ci/org_summary.json).
Schema v7 keeps current observed AMD logical groups and exact job variants,
current configuration parity, and queue activity in distinct populations.
`queues.daily_served_job_waits` references the independently bounded lifecycle
vectors through `source.path`, `source.key`, and `source.vector_key`.

Buildkite collection runs only through the guarded **Data Collection**,
**Queue Monitor**, **Queue Lifecycle Monitor**, and **DNS Health Monitor**
GitHub Actions workflows. Local tests and rendering use fixtures without a
Buildkite token. Request allowances and successful collection cadence remain
bounded by the durable ledgers.

## Main branch monitoring and detection latency

`main` is the source of truth. `collect_analytics.py` keeps every completed
`ci` branch=main build for 30 days; each catalog build records its final AMD MI
job outcomes (`amd_jobs`) and each strict group records its per-build final
outcome (`outcome_history`, with `r` marking a pass reached only by retrying a
hard failure, and `wall_mins` holding each passing final attempt's wall time).
From that one cohort:

- `ci_main_failure_watcher.py` confirms incidents (hard immediately, soft after
  two builds), keeps `good_commit`/`bad_commit` bisect bounds, and records
  opened/resolved transitions in `timeline` inside its state file.
- `amd_duration_regression_watcher.py` lists test steps whose wall time rose
  by a sustained step since a change (rules in `scripts/vllm/ci/README.md`).
- Flaky groups and retry hotspots use the thresholds in
  `config/ci_flakiness_thresholds.json`.

Both watchers need no GitHub token: every fresh run advances `last_run`, which
`main_ci` publishes as `watcher_last_run`. The retired `issue` and
`suppressed` keys stay in `main_ci` as `null` and `false` whatever the watcher
state holds, and the web app does not read them; the main pulse overlay keeps
`issue`/`issue_basis` only as `null`/`""`.
- `operations_v2/main_ci.json` (bounded to 1.5 MiB) publishes per-build
  summaries, the watchers' incidents, timeline and slowdowns, flaky groups,
  retry hotspots and daily/hourly pass-rate series. `shell.sources.main_ci` carries the cohort's
  own collection time, because `analytics.json` advances even when an incomplete
  Buildkite scan preserves the previous cohort. `core.main_derived_sources` in
  `config/data_freshness_targets.json` lists that clock, so Site Health reports
  the case as stale core data and the watchdog labels its recovery
  `core-data-stale`.

These watchers run in the same Data Collection run that refreshed analytics,
before the Operations snapshot is rebuilt, so a new hard failure reaches
`main_ci` in one generation. They leave their state untouched whenever the
`ci_analytics` surface is degraded or the cohort is older than three hours. For example, from 2026-10-17 16:31 to 2026-10-19
00:34 UTC every analytics collection failed (`RuntimeSourceError`
schema-drift), so no main alert could fire; the degraded publication was
reported through the hourly validation incident (#568) instead.

### What bounds detection latency

Without the main pulse below, worst case is roughly the 120-minute success
interval in `config/data_collection_attempt_budget.json` plus one 15-35 minute
collection.
The publication watchdog checks every 15 minutes and dispatches Data
Collection as soon as that ledger is due. It starts Docker Build Monitor the
same way whenever the docker ledger is due and no run is active. A Buildkite
webhook cannot start collection earlier: `cadence-preflight` observes the same ledger and drops
early dispatches. Lowering latency means lowering `success_interval_minutes`
(and raising `max_request_bearing_attempts`, currently 16 per 25 hours with 800
request starts each), which is a Buildkite API budget decision.

### Main pulse: detection between collections

`main_pulse.py` runs as the `main-pulse` job of `queue-monitor.yml`, so every
cron tick, watchdog completion and owner-hosted hourly tick wakes it. Its
parentless `main-pulse-request-budget` ledger (`config/main_pulse.json`)
admits one reservation of five Buildkite request starts (one 100-build
`finished_from` page plus its four retries) per ten minutes and at most 750
per 25 hours; other wake-ups expose no token and make zero requests. Normal
spend is one request per admitted run, about 144 a day.

An admitted run reads the published `open_ci_main_failure_issues.json` from
`dashboard-state` and its own overlay from `main-pulse-data` (cursor, the
strict groups whose watermark is newer than that state, and its timeline
events), lists builds finished since the cursor (clamped to three hours; a
truncated page keeps the cursor), and publishes the updated overlay. It needs
no GitHub token; the overlay's `issue` is always `null`. Data Collection
exports the overlay with its state commit before `ci_main_failure_watcher.py`
runs: a group is taken only while the overlay's watermark is newer, so the
collection stays the authority.

Expected latency from a build finishing to its hard failure reaching the overlay
is the wait for the next admitted run plus about two minutes of job time:
typically 5-20 minutes while the ten-minute cron fires, up to about 25 minutes
on the 15-minute watchdog chain alone, and about 65 minutes at worst on the
owner-hosted hourly tick alone.
Soft failures still need a second build. Rehearse without tokens with
`main_pulse.py run --authority-state <state> --state-file <overlay>
--recorded-builds <builds.json>`.

### Making the `buildkite_build_finished` webhook work

Only one such dispatch has ever reached `hourly-master.yml` (2026-03-26) and
`queue-monitor.yml` has received none, so no relay is deployed today. What it
needs:

1. A relay. Buildkite webhooks post Buildkite JSON with Buildkite headers, while
   `POST /repos/AndreasKaratzas/vllm-ci-dashboard/dispatches` needs a GitHub
   bearer token and an `event_type` body, so Buildkite cannot call GitHub
   directly. `scripts/vllm/ci/webhook.py` is that relay: an HTTP server on
   `WEBHOOK_PORT` (default 8080) that must run on an always-on host reachable
   over HTTPS (for example behind a reverse proxy).
2. Relay secrets: `GITHUB_TOKEN`, a fine-grained token scoped to this
   repository with **Contents: write** (required for repository dispatch), and
   `BUILDKITE_WEBHOOK_SECRET`, the token of the Buildkite webhook. The relay
   accepts Buildkite's `X-Buildkite-Signature: timestamp=...,signature=...`
   HMAC (body signed as `<timestamp>.<body>`, 5-minute skew) or the plain
   `X-Buildkite-Token` mode.
3. A Buildkite webhook notification service on the `vllm` organization
   (**Organization Settings > Notification Services > Webhook**) pointing at the
   relay, with events `build.finished` (plus `job.*`/`agent.*` for the queue
   monitor), pipeline `ci` (and `perf-eval`), branch filter `main`. This needs a
   Buildkite organization administrator of `vllm`; a pipeline-level `notify`
   would instead need an upstream change to the `ci` pipeline definition. The
   dashboard owner can deploy the relay and token but cannot do this step
   without that access.
4. The relay dispatches `buildkite_build_finished` only for canonical nightly
   builds today. Because of the attempt ledger above, dispatching every main
   build would not shorten latency until the success interval is lowered.

