# vLLM CI Dashboard Backend

Collects nightly CI test data from Buildkite, analyzes test health, and produces JSON files for the project dashboard.

## What It Does

1. **Fetches nightly builds** from two Buildkite pipelines:
   - **AMD** (`amd-ci`): "AMD Full CI Run - nightly" builds (~09:00 UTC / 4 AM Central during daylight time)
   - **Upstream** (`ci`): "Full CI run - nightly" builds (~06:00 UTC / 1 AM Central during daylight time)

2. **Parses pytest output** from job logs to extract test results (pass/fail/skip/error counts + individual failure names from the `short test summary info` section)

3. **Analyzes test health** across builds:
   - Labels each test: `passing`, `failing`, `new_failure`, `fixed`, `flaky`, `skipped`, `new_test`
   - Detects flaky tests (20-80% pass rate over 10-build window)
   - Tracks failure streaks and mean time to fix

4. **Compares AMD vs upstream** (parity analysis):
   - Tests passing on both, failing on both, AMD-only failures, upstream-only, etc.
   - Per-module parity breakdown

5. **Generates dashboard JSON** files consumed by the frontend

## Setup

### Prerequisites

- Python 3.10+
- `requests`, `pyyaml`, and `cryptography` packages
- Buildkite API token with **read_builds**, **read_build_logs**, **read_artifacts**, and
  **read_clusters** scopes. Queue lifecycle collection needs `read_builds` for
  organization-wide build cohorts and `read_clusters` for the exact queue UUID
  allowlist. DNS health collection additionally needs `read_build_logs` so it
  can classify strong DNS signatures without publishing log text.
- If you run `collect_queue_snapshot.py`, the token should also have **Enable GraphQL API Access** so queue-native cluster metrics can be read

### Install

```bash
pip install requests pyyaml cryptography
```

### Environment

The `BUILDKITE_TOKEN` environment variable must be set. This is managed via GitHub Actions secrets — see the repo Settings > Secrets page. Never commit tokens to the repository.

```bash
# For local development only — use a read-only token
export BUILDKITE_TOKEN="$YOUR_TOKEN"
```

### Run

```bash
# Collect last 7 days (default)
python scripts/collect_ci.py --days 7 --output data/vllm/ci/

# Daily incremental
python scripts/collect_ci.py --days 1

# Dry run (preview builds without fetching)
python scripts/collect_ci.py --dry-run

# Single pipeline only
python scripts/collect_ci.py --pipeline amd --days 3

# Skip analysis (only collect raw data)
python scripts/collect_ci.py --days 7 --skip-analysis
```

## Output Files

All files are written to `data/vllm/ci/`:

| File | Description |
|------|-------------|
| `ci_health.json` | Overall health metrics, build summaries, pass rate trends |
| `parity_report.json` | AMD vs upstream test-by-test comparison |
| `flaky_tests.json` | Registry of flaky tests with pass rates and history |
| `failure_trends.json` | Top offenders, new failures, recently fixed, MTTF |
| `dns_failures.json` | Bounded 30-day DNS job-attempt counts and safe Buildkite coordinates |
| `quarantine.json` | Rendered quarantine/allowlist state |
| `test_results/{date}_{pipeline}.jsonl` | Per-test results (one JSON per line) |

### Upstream scheduled-gating contract

The browser-ready contract for scheduled upstream gating is generated at
`data/vllm/ci/operations_v2/gating.json#/gating/upstream_scheduled`. Dashboard
code should consume that projection instead of rebuilding the group-to-job
join or counting raw Buildkite jobs.

The cohort is deliberately narrow: only `ci` pipeline builds on `main` whose
message matches the `Full CI run - nightly` or `Full CI run - daily` marker are
eligible (the classifier permits a whitespace-delimited suffix). Arbitrary
`main` builds are excluded. Only terminal passed/failed builds with retained
configured-group observations are surfaced; older catalog entries whose
bounded observations have expired are omitted instead of being reported as
zero gated. Retry attempts are collapsed before logical test groups are
counted. For each selected build:

- `summary.total` is the number of configured logical AMD mirror groups in
  scope.
- `summary.gated` is the number of those configured groups observed in the
  build.
- `summary.passing` is the number of observed groups whose selected final jobs
  all pass.
- `summary.queue_count` counts queues that gated at least one group in that
  build, while `summary.configured_queue_count` records the full configured
  queue inventory.
- `queue_wait_mins` summarizes wait time from `runnable_at` to `started_at` as
  `p50`, `p95`, `max`, and `sample_count`.
- Each entry in `queues` repeats the logical-group counts, a `used` flag, and
  `queue_wait_mins` for one Buildkite queue, so queue-level gating and latency
  use the same selected job population as the build summary.

`build_operations_snapshot.py` assembles the projection from
`capacity_monitor.json`, which supplies the configured logical-group inventory
and expected queues, and the validated
`analytics.json#/ci/all_main_reliability` aggregate, which supplies retained
build messages plus bounded group observations with outcomes, stable step keys,
retry evidence, and queue timestamps. The public shard is the authoritative
joined contract; the full analytics payload and the monolithic
`operations_v2.json` build input remain private.

`all_main_reliability` schema v2 normalizes its retained observations: build
metadata and base URLs live once in the authoritative `builds` catalog, while
observations retain build/job/step identifiers and exceptional URL overrides.
The Operations builder, audits, and alert watchers bulk-hydrate the legacy
presentation fields before use. This preserves the existing popup payload while
keeping the private monolith under its 64 MiB operating budget. Schema-v1
last-known-good data remains readable during migration.

### Pass-rate contracts

Pass rates carry an explicit percentage and denominator label so consumers do
not have to infer whether a value describes builds, jobs, or assertions:

The producer sets `pass_rate_contract_version: 1` at the `ci_health.json` and
project-root `test_results.json` top levels and in each `analytics.json`
pipeline block. Unversioned payloads are legacy rollout data; the audit warns
but does not require the new fields until a producer has emitted version 1.

- Each `analytics.json` pipeline and window summary publishes
  `build_pass_rate_pct` (0–100) with
  `build_pass_rate_basis: "terminal_build_state_all_green"`. It is the share
  of terminal builds whose final state is fully passed. The legacy `pass_rate`
  remains the same percentage.
- Every build summary in `ci_health.json` publishes `test_pass_rate_pct`
  (0–100) with
  `test_pass_rate_basis: "pytest_assertions_excluding_skipped"`. It is
  `passed / (passed + failed)`; skipped assertions are excluded. The legacy
  `pass_rate` remains the equivalent 0–1 ratio.
- Each platform summary in `data/vllm/test_results.json` uses the same explicit
  assertion basis and records the source counts under `test_assertions`. Its
  legacy `pass_rate` remains the equivalent 0–100 percentage. These assertion
  counts are intentionally separate from the existing job-count fields.

### JSONL Format (test_results)

Each line in a `.jsonl` file is a JSON object:
```json
{"test_id":"tests.test_llm::test_generate","name":"test_generate","classname":"tests.test_llm","status":"passed","duration_secs":12.5,"failure_message":"","job_name":"Basic Correctness","job_id":"abc123","build_number":5500,"pipeline":"amd-ci","date":"2026-03-22"}
```

## Test Health Labels

| Label | Criteria | Meaning |
|-------|----------|---------|
| `passing` | >= 80% pass rate over 10 builds | Reliably passing |
| `failing` | <= 20% pass rate over 10 builds | Consistently failing |
| `new_failure` | Was passing (>80%), now failing | Regression detected |
| `fixed` | Was failing, now passing (>80%) | Recently resolved |
| `flaky` | 20-80% pass rate over 10 builds | Intermittent |
| `skipped` | Always skipped/xfailed | Not executing |
| `new_test` | Appeared in <= 2 builds | Too new to classify |
| `quarantined` | Listed in quarantine.yaml | Excluded from metrics |
| `allowlisted` | Listed in allowlist | Known acceptable failure |

## CI Health best-hardware test-group percentage

The CI Health headline is one best-hardware test-group percentage from
`amd_test_matrix.json`, not a percentage of raw YAML jobs. Generic
cross-architecture replicas share one test group and pass when any represented AMD
architecture passes. A reviewed set of MI355-sensitive model, topology, and
kernel routes remains separate because another architecture cannot prove that
gfx950-specific obligation healthy. The exact rules, reasons, membership, and
commands are published in `best_hardware_policy` and `health_groups`.

The denominator contains every expected test group, including waiting or unobserved
groups; missing signal cannot improve the percentage. Raw hardware cells remain
available as drill-down evidence but do not create a second headline metric.
Commit-pinned AMD/upstream definition parity is a separate source-coverage
inventory and does not affect best-hardware test-group health.

## Managing Quarantine

Edit `config/quarantine.yaml` to quarantine or allowlist tests:

```yaml
quarantine:
  - test_id: "tests.test_module::test_name"
    reason: "Known MI325 memory issue"
    issue: "https://github.com/vllm-project/vllm/issues/12345"
    added: "2026-03-01"
    expires: "2026-04-01"    # auto-removes after this date

allowlist:
  - test_id: "tests.test_other::test_unsupported_op"
    reason: "Uses CUDA-specific op not available on ROCm"
    permanent: true
```

Quarantined tests are still collected and tracked, but excluded from failure counts and health metrics.

## GitHub Actions Integration

Six workflows divide canonical publication from focused manual/event collectors:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `hourly-master.yml` | Hourly + Buildkite nightly-completion webhooks | Full collection, validation, and the only scheduled root-site deployment |
| `daily-update.yml` | Manual | Focused GitHub-data refresh committed to `main` |
| `ci-collect.yml` | Manual | Validation-only focused Buildkite CI refresh; never commits or publishes |
| `queue-monitor.yml` | Queue webhooks + manual | Queue snapshots and bounded queue issue automation; canonical publication follows via `hourly-master.yml` |
| `queue-lifecycle.yml` | Hourly + manual | Organization-wide direct job lifecycle observations for the twelve canonical MI250/MI300/MI355 queues |
| `dns-health.yml` | Hourly + external tick + manual | Incremental full-log DNS classification with an isolated durable state branch and conditional canonical reconciliation |

All secrets are managed via GitHub Actions encrypted secrets (Settings > Secrets > Actions). The `BUILDKITE_TOKEN` is never exposed in logs — GitHub automatically masks secret values. Rotate credentials whenever exposure is suspected and periodically review that each workflow retains only its required read scopes.

### Webhook-Triggered Updates

For build-completion updates, `hourly-master.yml` receives the
`buildkite_build_finished` repository dispatch and performs a complete,
validated publication.

Buildkite queue freshness now uses those job-level webhook events (`job.scheduled`, `job.started`, `job.finished`) plus agent events (`agent.connected`, `agent.disconnected`, `agent.lost`, `agent.stopping`) to dispatch the lightweight `queue-monitor.yml` workflow. This keeps queue counts and zombie-job alerts fresher without forcing the heavier CI collectors to run on every queue change.

Queue analytics intentionally exclude waiting or running jobs older than 4 hours. Those jobs are treated as zombies, surfaced separately in `queue_jobs.json`, and tracked via `queue_zombie_watcher.py` so the main queue charts stay conservative.

Canonical AMD lifecycle analytics are intentionally narrower than the general
queue monitor. `collect_queue_lifecycle.py` queries only the twelve standard
`amd_mi250_*`, `amd_mi300_*`, and `amd_mi355_*` queues at widths 1, 2, 4, and
8. MI325, MI355B, CPU, partner, perf-eval, and router queues are excluded from
these totals. The collector records three event-time facts independently:
incoming work at REST `build.jobs[].runnable_at`, served work at
`build.jobs[].started_at`, and completions at `build.jobs[].finished_at`. It
derives queue wait and runtime only when both required source
timestamps are present, and publishes exact observed rolling two-hour counts
plus UTC hour buckets in `queue_lifecycle.json`. It also publishes one
`daily_wait_times.days` entry for every UTC date intersecting the seven-day
retention window. Each entry contains the sorted vector of individual
served-job wait durations in seconds, attributed to the date of the direct
`started_at` timestamp; empty observed days remain present with an empty
vector, and the first or last date is marked partial when its observed bounds
do not cover a complete calendar day. The supported organization
Builds REST endpoint does not filter job event timestamps directly. The
collector unions builds finished inside the source window, builds created
inside it, and active-state builds created inside the bounded parent-build
horizon, then filters jobs by the twelve direct cluster-queue UUIDs. Every
retained value is therefore an exact direct job observation, while the
aggregate separately declares residual population limits such as page-number
drift and jobs attached to parent builds created before that horizon.

The reconciled, deduplicated seven-day job-observation ledger lives only on the
`queue-lifecycle-data` branch as daily files under `queue_lifecycle_jobs/`; it
is neither committed to `main` nor published to Pages. Each compact row contains a hashed
job identity, its canonical queue, direct event timestamps, derived durations,
outcome, and retry flags. UTC-day segmentation lets unchanged days reuse their
existing Git objects instead of rewriting the whole ledger; a late start or
completion can still update the segment containing that job's earliest retained
event. The ledger deliberately omits labels, URLs, branches,
commits, pipeline names, and other build metadata. Per-segment and total-size
guards prevent GitHub blob-limit and repository-pressure failures.

Lifecycle collection runs independently once per hour so an API or schema
failure cannot delay the ten-minute point-in-time queue monitor. Organization-
wide finished, created, and active-build cohorts use bounded, verified REST
pagination, and the public aggregate includes the exact source window, cohort
filters, query coverage, and provenance. Incomplete pagination, an unreadable
established ledger, or a failed Buildkite query causes collection to fail
instead of silently publishing a partial window. Workflows pass the existing
`BUILDKITE_TOKEN` secret to the collector as
`BUILDKITE_API_TOKEN`; tokens must never be placed in source, generated data,
logs, or dashboard URLs.

### DNS health observations

`collect_dns_failures.py` discovers terminal script-job attempts across the
`amd-ci` and `ci` pipelines, including retries and passing jobs, then scans each
new bounded log for strong DNS signatures. The first successful collection
exhaustively bootstraps a 24-hour discovery horizon so it fits the shared API
quota. Each later run re-queries a two-hour overlap and extends the contiguous
coverage start; a gap longer than 24 hours resets to a fresh 24-hour bootstrap.
The configured 30-day value remains the target retention horizon. Until enough
contiguous observations accrue, longer windows are explicitly partial and the
UI renders their values as lower bounds. This expected, explicitly quantified
partial coverage is a DNS-panel warning rather than a site-wide publication
degradation. After three hours the DNS panel labels the observations stale and
the publication audit declares the DNS surface degraded site-wide. A DNS
dataset that is not collected, malformed, or internally inconsistent takes the
same strict degradation or fail-closed publication path.

GitHub Actions schedules are best-effort and may be delayed or dropped. The
workflow therefore also accepts the dedicated `dns_health_tick`
`repository_dispatch` event for a scheduler outside GitHub Actions. After a
successful durable DNS publish, a separate least-privileged reconciliation job
checks the public canonical status. It dispatches `hourly-master.yml` only when
DNS remains affected or the canonical snapshot is older than three hours, so a
fresh producer clears stale publication state without duplicating healthy
hourly collections. The dispatch carries the exact DNS generation. A lightweight
preflight skips queued work only once Pages contains that generation, its full
DNS contract validates, DNS is no longer affected, and the publication is still
inside the three-hour freshness window. Every required generation is queued;
the generation-aware preflight safely deduplicates it after a newer canonical
run succeeds. A targeted master verifies the same postcondition after deployment
and fails visibly if Pages did not acknowledge it. Every workflow that writes
`gh-pages` uses the shared `queue: max`
lock, so a later preview or manual deploy cannot replace an already-pending DNS
reconciliation. A strict three-hour producer SLA requires configuring that
external tick; an in-repository GitHub cron cannot guarantee its own recovery.
The canonical collector has a 60-minute timeout so a hung run cannot retain the
shared deployment lock indefinitely.

Each scheduled run gives the whole collection a 25-minute budget with a
separate finalization reserve. Unvisited log work remains pending for the next
overlap instead of being reported as a complete zero. Pending work is scanned
in a deterministic oldest/newest alternation, starting with the oldest job, so
a steady stream of new jobs cannot monopolize the bounded request budget while
every other request still samples the freshest observations. Its public
`dns_failures.json` dataset covers the trailing 720 observed hours. “Observed”
is deliberate: API, rate-limit, oversized-log, and pending-job gaps remain
explicit in each window's coverage block, so an incomplete scan cannot be
displayed as a complete zero.

The primary histogram count is the number of distinct affected Buildkite job
attempts. Retries have different job UUIDs and therefore count independently.
The separate episode count clusters matching lines within five seconds; stack
trace repetition cannot inflate the affected-job count. Evidence rows retain
only safe Buildkite coordinates and fixed classifier enums. Free-form
Buildkite job names are excluded entirely because a blacklist cannot prove
that arbitrary labels contain no credentials. Evidence never contains log
snippets, raw-log URLs, headers, environment values, branches, commits,
authors, or arbitrary target hostnames.

A DNS observation is independent of the final Buildkite outcome. Passing jobs
remain in scope because a retry or cache fallback can recover from genuine
resolver trouble while leaving useful node-level infrastructure evidence. The
public outcome contract therefore reconciles every affected-job total into
`passed_jobs`, `soft_failed_jobs`, and `hard_failed_jobs`. The dashboard labels
these as final outcomes after the DNS observation and reserves failure styling
for non-passing outcomes. It does not infer that DNS caused a retry, or describe
resolver-line clusters as CI incidents.

The repository and its force-orphan `dns-health-data` branch are publicly
readable. Plaintext scanner state therefore exists only at the gitignored
`dns_health/scan_state.json.gz` path inside an ephemeral Actions runner. The
branch stores authenticated Fernet ciphertext at
`dns_health/scan_state.fernet`; it never stores the plaintext gzip. The
workflow obtains `DNS_STATE_ENCRYPTION_KEY` only from an encrypted Actions
secret, decrypts before collection, validates the new gzip and aggregate,
re-encrypts to a temporary file, deletes the runner plaintext, and only then
replaces the branch. Cryptographic failures are reported generically without
printing keys, ciphertext, or state content.

Canonical Pages workflows import only the validated `dns_failures.json`.
Neither plaintext nor encrypted scanner state is committed to `main` or
published to Pages. Authentication, decryption, encryption, or total
collection failure therefore preserves the last encrypted branch commit and
validated DNS aggregate without rolling back unrelated CI or queue surfaces.
Keep the encryption key stable across runs. Rotate it through a controlled
decrypt-and-re-encrypt migration, retaining the old key until the durable
ciphertext has been replaced successfully.

### Managed Alert Issues

The unified hourly Data Collection workflow also reconciles four bounded
umbrella issues in this repository:

- AMD main test-group failures use the exhaustive amd-ci branch=main reliability
  cohort. The latest retry attempt in a build wins; a later pass resolves the
  same strict label + step + hardware + queue identity. Hard failures confirm
  immediately. Soft failures remain visible as pending observations and require
  two distinct eligible completed builds before becoming incidents. Missing or
  indeterminate observations neither advance nor resolve the signal.
- Upstream CI main test-group failures use the exhaustive ci branch=main
  reliability cohort and the same strict retry-aware identity. Each active
  incident retains the last known passing commit and first failing commit as a
  candidate range for later ancestry validation and automated git bisection.
- AMD main duration regressions compare the median wall completion time of the
  latest three successful final attempts with the preceding six to twelve runs.
  Queue wait is excluded. A 15% increase opens the alert, and the baseline stays
  fixed until the latest-three median recovers below that threshold.
- CI agent health uses the dashboard's infra-suspect definition, excludes
  canceled builds and unidentified nodes, and alerts when a six-hour window
  contains a three-hour co-failure cluster with at least three logical failures
  across at least two groups on one physical AMD node.

State lives in open_amd_main_failure_issues.json,
open_ci_main_failure_issues.json, open_amd_duration_regression_issues.json, and
open_agent_health_issues.json.
Each watcher can update or close only the issue number in its own state file; it
never searches for issues by label, and it verifies a watcher-specific ownership
marker before any update or close. When a signal recovers, its watcher comments
with the recovery rule and automatically closes that tracked issue. Manually
closing an active alert suppresses reopening until that signal first recovers.

## Architecture

```
scripts/
  collect_ci.py              # Entry point / orchestrator
  ci/
    config.py                # Constants, thresholds, pipeline definitions
    models.py                # Dataclasses: TestResult, BuildSummary, TestHealth, ParityEntry
    buildkite_client.py      # Buildkite REST API client
    log_parser.py            # Pytest log output parser (extracts test results from job logs)
    junit_parser.py          # JUnit XML parser (fallback if artifacts are available)
    analyzer.py              # Health labeling, parity, trends, quarantine
    reporter.py              # JSON/JSONL output generation
    webhook.py               # Standalone Buildkite webhook receiver
```

## Troubleshooting

**"BUILDKITE_TOKEN not set"**: Ensure the token is configured in GitHub Actions secrets or exported in your local environment.

**No nightly builds found**: The script filters by build name pattern. Check that the pipeline has builds matching "AMD Full CI Run - nightly" or "Full CI run - nightly".

**Rate limiting (429)**: The script retries on 429 with exponential backoff using the `Retry-After` header. For large fetches (30+ days), run in smaller batches: `--days 7`.

**Cached data**: The analytics collector's sanitized Buildkite history cache
lives in `data/vllm/ci/.cache/analytics-builds-v1`. The hourly workflow keeps
one immutable cache key per UTC day in GitHub Actions cache storage, restores
the prior day when the new key is not populated, and still refetches the recent
overlap on every run. The collector fully reconciles when the restored cache's
`generated_at` UTC date differs from the current collection date, or when
cached `last_full_at` reaches 24 hours old. This guarantees that the first
snapshot saved under each immutable daily key is complete. A failed analytics
collection does not save the new daily key; cache transport failures are
non-fatal and collection continues from Buildkite.
An incremental materialization that grows by at least 20% and 8 MiB is treated
as suspicious and receives one exhaustive reconciliation before any cache
replacement. Analytics and the independently consumed gating-nightly seed are
written atomically; gating is written first so a later analytics budget failure
does not withhold valid fresh gating evidence.
This directory is private, gitignored, never published, and never restored
from gh-pages. Delete the local directory to force a cache-free fetch.
