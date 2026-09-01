# vLLM Dashboard Scripts

Additional data collection scripts specific to the vLLM CI dashboard.

## Scripts

| Script | Purpose | Trigger |
|--------|---------|---------|
| `collect_queue_snapshot.py` | Captures current Buildkite queue-native metrics every permitted poll and refreshes the complete active-job overlay at most hourly; incomplete detail pagination retains the last complete overlay with explicit timestamps/status | Every 10 min via `queue-monitor.yml` (detail at most hourly) |
| `collect_queue_lifecycle.py` | Resumably collects seven-day privacy-minimized queue lifecycle events through exhaustive disjoint parent-created query units without publishing partial generations | 30-minute recovery checks with a two-hour successful cadence via `queue-lifecycle.yml` |
| `collect_analytics.py` | Builds failure rankings, duration rankings, queue wait stats | Every two hours via `hourly-master.yml` |
| `collect_amd_test_matrix.py` | Normalizes upstream `test-amd.yaml` into a dynamic per-architecture coverage matrix, matched against the latest AMD nightly | Every reserved full collection via `hourly-master.yml`; cooldown runs retain the validated matrix |
| `collect_ownership_parity.py` | Builds the ownership routing map from the exact vLLM commit referenced by the latest AMD matrix | Hourly after matrix collection |
| `collect_gating_targets.py` | Regenerates `gating_targets.json` from the authoritative `config/vllm_amd_gating_targets.json` | Every canonical `hourly-master.yml` run |
| `collect_gating_proposals.py` | Finds recent open PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors, then follows cached proposal PRs until they stop adding mirrors | Every two hours via `hourly-master.yml` |
| `collect_gating_target_candidates.py` | Builds a review-only audit of upstream nightly GPU jobs vs the canonical AMD gating target list, including likely duplicates, exclusions, new candidates, and explicit `%N` shard aggregation | Every two hours via `hourly-master.yml` |
| `merge_perf_eval_events.py` | Strictly merges a validated durable baseline with candidate perf-eval JSONL through the bounded atomic writer | Every canonical perf-eval seed sync |
| `build_operations_snapshot.py` | Builds the private v2 operations input plus its public manifest and lazy section shards; runtime targets resolve through exact matrix aliases and definition parity with explicit unresolved reasons | Every canonical collection and Pages assembly |
| `build_queue_section.py` | Builds only the compact public Queue shard from queue-owned inputs | Every independent queue-monitor run |
| `ci_main_failure_watcher.py` | Reconciles one upstream `ci`/`main` failure issue and retains bisect candidate bounds per strict group | Hourly after analytics collection |
| `ci_area_regression_watcher.py` | Maps every exact AMD matrix definition to its owned test-area rotation and reconciles one state-owned dashboard issue per area with confirmed incidents | Hourly after matrix collection |
| `ensure_ci_operations_labels.py` | Ensures managed and workstream labels exist before issue watchers run | Every canonical collection |
| `sync_ci_operations_project.py` | Adds open managed dashboard issues to the linked AMD CI Operations Project, split by workstream labels | Hourly after issue reconciliation |
| `audit_dashboard_data.py` | Cross-checks generated data, frontend assumptions, and deploy workflow ordering before publishing | Every two hours via `hourly-master.yml` + local debugging |
| `check_site_health.py` | Probes the deployed shell and bounded publication-status contract, emitting JSON and Markdown evidence | Hourly at :57 UTC-minute plus manual `health-check.yml` runs |
| `plan_dns_publication_reconcile.py` | Decides whether a successful live DNS publish must wake the canonical publisher and verifies that queued work still has an unacknowledged DNS generation | After every successful `dns-health.yml` collection and before a DNS-targeted canonical run |
| `plan_queue_publication_reconcile.py` | Wakes the canonical publisher only for an invalid status or an affected Queue surface, then verifies that the requested queue generation reached Pages; the workflow separately confirms its exact durable source ref | After validated queue publication or a fresh zero-request durable retry, and before a queue-targeted canonical run |
| `dns_request_budget.py` | Validates the parentless DNS request ledger and durably reserves a complete per-scan allowance for 25 hours before any Buildkite call, bounding actual starts below 1,000 per rolling day | Every `dns-health.yml` run, plus controlled one-time ledger initialization |
| `request_bearing_attempt_budget.py` | Gates full Data Collection and Queue Lifecycle through independent parentless 25-hour attempt ledgers, including start-to-start success cadence, bounded failure retry, migration overlap, and read-only webhook/watchdog observation | Before every possible full-collector token exposure, plus controlled one-time initialization |
| `buildkite_request_guard.py` | Enforces the fixed per-attempt allowance across processes by charging every exact Buildkite `requests.Session.send` before transport and rejecting hidden adapter retries | Explicitly at every token-reading CLI/shared client ingress, with `scripts/sitecustomize.py` as process-start defense in depth |
| `ci/backfill_checkpoint.py` | Integrity-validates complete per-nightly CI shards so cache-loss recovery makes monotonic progress across repeated 800-request caps without publishing partial data | Restored and failure-survivingly saved by guarded Data Collection runs |
| `plan_publication_watchdog.py` | Plans proactive canonical recovery and suppresses active/recent duplicates; durable full-collection due state comes from the separate attempt ledger rather than general publication timestamps | `publication-watchdog.yml` and generation-targeted recovery runs |
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

The CI ownership watcher reads its only availability input from the committed
regional working-hour profiles in `config/vllm_ci_ownership.json`. EU follows
09:00–17:00 Serbia time (`Europe/Belgrade`) and NA follows 09:00–17:00 Chicago
time (`America/Chicago`), Monday through Friday. Assignment walks each area's
configured ranks in ascending order and falls back to the CI lead when no ranked
owner is in hours or the schedule cannot be evaluated safely.

The project snapshot reads upstream vLLM Project #39 as public, read-only
evidence; it does not mutate that Project's issues, comments, or fields. Its
sole writable summary surface is one automation-owned managed comment on dashboard
issue #255, created or updated with the repository-scoped `GITHUB_TOKEN` and
`issues: write`.

Project synchronization uses the repository `GITHUB_TOKEN` only to list the
dashboard's managed issues. The separate `PROJECTS_WRITE_TOKEN` secret is used
only for the `addProjectV2ItemById` mutation against the configured Project.
Missing project credentials are a safe no-op; the script never removes Project
items, edits issue content, or targets another repository.

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
                           --> ci_main_failure_watcher.py
                           --> open_ci_main_failure_issues.json (private state)
private analytics.json    --> ci/public_analytics.py during build_site.py
                           --> bounded _site/data/vllm/ci/analytics.json
collect_amd_test_matrix.py --> data/vllm/ci/amd_test_matrix.json
collect_ownership_parity.py --> data/vllm/ci/ownership_config_parity.json
collect_gating_targets.py --> data/vllm/ci/gating_targets.json
collect_gating_proposals.py --> data/vllm/ci/gating_proposals.json
collect_gating_target_candidates.py --> data/vllm/ci/gating_target_candidates.json
config_parity.py          --> data/vllm/ci/config_parity.json
amd_test_matrix.json + config_parity.json + ownership_config_parity.json
                         + config/vllm_ci_ownership.json
                           --> ci_area_regression_watcher.py
                           --> ci_ownership.json
                           --> open_ci_area_regression_issues.json (private state)
managed dashboard issues  --> sync_ci_operations_project.py
                           --> AMD CI Operations Project
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
allocation policy reserves only 240 MiB, guaranteeing 16 MiB of global
headroom, and gives unclassified code/config assets a separate 16 MiB envelope.
Generated operational data is never charged to that code reserve: gating
control (6 MiB), Operations control files (5 MiB), watcher state (3 MiB),
GitHub Home (768 KiB), group changes (1 MiB), and small fixed operational files
(256 KiB) have disjoint aggregate groups. The final staged-index guard checks
every group before commit; bounded evidence writers compact whole units and
attest source/published/omitted counts.

The eight `open_*_issues.json` watcher ledgers compose inside that 3 MiB
allocation through fixed producer envelopes: upstream-main 1 MiB; AMD-main and
CI-area 512 KiB each; duration, queue-latency, and queue-zombie 256 KiB each;
agent-health and Omni 128 KiB each. Their shared atomic writer preserves every
open issue, suppression identity, and confirmed/pending incident. When needed,
it removes only refetchable row detail, inactive ordering fences, clear area
signals, and retired cache rows, recording exact
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

The canonical workflow splits CI into five atomic publication surfaces: core
health/matrix/ownership, private analytics/reliability, gating configuration
and nightly evidence, test-group changes, and workload hotness. Queue,
lifecycle, agent-health, GitHub-home, and perf-eval inputs remain separate
surfaces. An analytics capacity failure can therefore
retain reliability history without rolling back unrelated health, matrix,
ownership, or gating data; if the analytics command fails before producing a
fresh nightly seed, gating is quarantined too. A routed degradation keeps fresh
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
exposing private restore metadata. Repeated runs of the same incident do not
post duplicate comments. Collector failures carry a bounded typed reason into
the incident fingerprint; a first transient network/HTTP failure uses the
validated baseline without opening a ticket, while deterministic failures and
two consecutive transient failures alert. Six consecutive healthy canonical
publications are required before an incident closes and rearms.

The legacy manual `ci-collect.yml` workflow is validation-only. It can exercise
the focused collectors and show the resulting workspace changes, but its token
is read-only and it cannot commit, push, or publish around the selector and its
private attestation state.

Browser authentication, user signup, Test Build, Ready Tickets, and Admin have
been retired end-to-end. They have no public route, collector, publication
surface, or mutation workflow.

Runtime target matching is intentionally conservative. Exact build-pinned matrix
labels win; definition-parity aliases are the fallback. Only an explicit
trailing `%N` target can aggregate numbered shards. Duplicate matrix identities
merge incident-first and retain each job URL. Unmatched targets publish a
resolution status (`no_amd_definition`, `stale_target_alias`, `ambiguous`, or
`not_observed`) instead of presenting every identity failure as missing runtime
signal.

The area incident watcher uses all exact matrix definition rows, not the
smaller reviewed runtime-target plan. Area attribution prefers commit-pinned
definition-parity source files, then reviewed aliases/overrides. Ambiguous or
unmapped rows remain unassigned and visible; they are never routed through a
lossy category guess. A hard result confirms immediately, while the same soft
result must recur on two distinct completed builds; pending soft evidence stays
visible without opening an issue. Issue assignment walks each configured rank in
ascending order, verifies repository assignability, and falls back to the CI lead.
Every confirmed-incident issue tags the selected owner and verified assignee,
then CCs each remaining ranked area owner once. The shared issue client rejects
any repository other than the dashboard.

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
