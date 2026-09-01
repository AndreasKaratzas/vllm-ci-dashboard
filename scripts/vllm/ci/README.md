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

Live Buildkite access is managed through GitHub Actions secrets and a durable
per-attempt request reservation. Use the applicable workflow's
`workflow_dispatch` trigger for an operator-initiated collection. Do not export
a token and invoke a collector directly: every token-reading CLI and shared
client ingress requires all three workflow-created request-guard variables and
exits with status 78 before transport when that evidence is missing or
incomplete. Never commit tokens to the repository.

### Collector CLI forms

These argument forms document what the guarded Data Collection workflow runs;
they are not an unguarded local-token runbook:

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
`operations_v2.json.gz` build input remains private and below the file ceiling.

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

The matrix consumes the exact AMD build roster frozen by `collect_ci.py` in
the same request-bearing attempt. That private, gitignored handoff is an
allowlisted schema-v2 projection with one retained row per source job and
explicit zero-omission retention counts. Its checked-in limit is 64 MiB (the
existing private roster-cache budget and below the 90 MB file boundary); the
producer preflights the complete payload and atomically replaces the file, and
the consumer reads at most that limit from a regular non-symlink file. Missing,
oversized, malformed, non-exhaustive, or wrong-build handoffs cannot produce a
new matrix. Zero-request cooldown runs do not create a handoff and retain the
validated last-known-good matrix instead. This handoff adds no Buildkite API
request.

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

Seven workflows divide canonical publication from focused manual/event collectors:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `hourly-master.yml` | Every two hours + coalesced Buildkite nightly-completion webhooks | Full collection, validation, atomic dashboard-state rotation, and the only scheduled root-site deployment |
| `daily-update.yml` | Manual | Compatibility handoff to the canonical collector; never writes generated data to `main` |
| `ci-collect.yml` | Manual | Tokenless compatibility dispatch into the canonical guarded collector; it cannot run an independent Buildkite refresh |
| `queue-monitor.yml` | Queue webhooks + manual | Queue snapshots and bounded queue issue automation; canonical publication follows via `hourly-master.yml` |
| `queue-lifecycle.yml` | 30-minute recovery checks, two-hour successful cadence + manual | Organization-wide direct job lifecycle observations for the twelve canonical MI250/MI300/MI355 queues |
| `dns-health.yml` | Hourly recovery opportunity + external tick + manual | Request-budgeted observed DNS sampling with an isolated durable state branch, a durable three-hour scan gate, and conditional canonical reconciliation |
| `publication-watchdog.yml` | Queue Monitor, Queue Lifecycle Monitor, DNS Health Monitor, and Site Health Check completions + every 15 minutes + external tick + manual | Validates the durable state identity against Pages and routes bounded collector, DNS, or deploy-only recovery |

All secrets are managed via GitHub Actions encrypted secrets (Settings > Secrets > Actions). The `BUILDKITE_TOKEN` is never exposed in logs — GitHub automatically masks secret values. Rotate credentials whenever exposure is suspected and periodically review that each workflow retains only its required read scopes.

### Durable full-collector request bounds

`hourly-master.yml` and `queue-lifecycle.yml` each own an independent,
single-file, parentless request-bearing-attempt ledger. Every scheduled,
webhook, and manual path reaches the same exact-leased reservation after its
two-stage concurrency gate and before the first step can receive
`BUILDKITE_TOKEN`. Missing, corrupt, ambiguous, non-parentless, or
lease-conflicted state fails closed with zero Buildkite requests. A reservation
survives cancellation and failure for 25 hours. A successful attempt is due
again 120 minutes after its **reservation time** (start-to-start); a failed
attempt can retry after 30 minutes. The 50-minute workflow timeout leaves at
least ten minutes between the last possible request's rolling-24-hour boundary
and reservation expiry, so a 25-hour ledger proves the corresponding bound.

Every token-reading CLI explicitly activates the request guard after its path
setup, and the shared Buildkite client/config ingress covers dormant library
callers such as `create_build`. `scripts/sitecustomize.py`, loaded from one
exact `PYTHONPATH` in workflows, provides process-start defense in depth. The
guard patches `requests.Session.send`, atomically charges the shared local
counter before each HTTPS send to `api.buildkite.com` or
`graphql.buildkite.com`, counts same-origin redirect sends, and blocks
allowance+1 before transport. Requests adapters with hidden internal retry
policies are rejected; application retries are charged individually. Data
Collection reserves at most 800 starts per attempt and at most 16 guarded
attempts in any 25 hours, for a hard rolling-24-hour safety ceiling of 12,800.
The first three completed guarded production samples used 126, 381, and 300
starts (a 269-start mean, or about 3,228/day on the normal two-hour cadence).
The deliberately conservative normal reservation envelope is 9,600/day.
Queue Lifecycle reserves 100 starts per attempt, producing a 1,600
rolling-24-hour hard ceiling and a normal reservation envelope of 1,200/day at
the two-hour cadence. These fixed allowances are safety limits even where a
collector's older theoretical pagination limit is higher; exhausting an
allowance degrades the affected surface to its validated last-known-complete
baseline rather than publishing partial evidence.

Queue Lifecycle also stops API work after a 40-minute monotonic collector
budget. Each REST timeout is capped by the remaining budget and retry sleeps
that would cross it are skipped. Exit 75 is accepted only with a validated
incomplete checkpoint after exact request-allowance exhaustion; the distinct
exit 76 accepts a validated incomplete or complete checkpoint after the time
budget. Neither status validates or publishes public lifecycle data. A
44-minute collect-step watchdog and the 50-minute job watchdog remain hard
backstops, while exact request reporting and private cache preparation run on
the workflow's unconditional paths.

Cache loss does not require raising the 800-start safety limit. Core CI records
each completely parsed nightly in the integrity-bound private
`ci-backfill-v1` Actions cache. A capped attempt always validates and saves
monotonic complete shards, the public `ci_core` surface retains its prior
complete generation, and the next guarded attempt restores and skips those
shards. Each checkpoint shard is limited to 4 MiB and the checkpoint aggregate
shares the test-result store's 25,100,288-byte (about 24 MiB) ceiling. It keeps
at most the newest 16 shards, dropping oldest whole UTC days until both the
count and byte limits fit; an irreducible newest day fails closed. The cache is
gitignored and never enters the dashboard-state or Pages tree, so it cannot
create a repository blob near GitHub's 90 MB sync limit.

Neither workflow bootstraps its ledger. At rollout, disable the corresponding
producer, inventory every legacy run in the preceding 25 hours whose token step
could have executed, and use the run's `created_at` as the conservative
`reserved_at`. Successful rows need the exact durable commit pushed by that
run; failed/cancelled rows keep all success fields null. Unknown legacy request
counts remain null and `request_start_bound_proven` must be false. For example:

```json
[
  {
    "workflow_run_id": "123456789",
    "workflow_run_attempt": 1,
    "event_name": "schedule",
    "reserved_at": "2026-09-01T00:00:00Z",
    "request_start_bound_proven": false,
    "succeeded_at": "2026-09-01T00:25:00Z",
    "durable_ref": "<full-40-character-git-commit-sha>",
    "actual_request_starts": null
  },
  {
    "workflow_run_id": "123456790",
    "workflow_run_attempt": 1,
    "event_name": "workflow_dispatch",
    "reserved_at": "2026-09-01T01:00:00Z",
    "request_start_bound_proven": false,
    "succeeded_at": null,
    "durable_ref": null,
    "actual_request_starts": null
  }
]
```

Save the complete, current array outside the repository and initialize exactly
once while the producer remains disabled:

```bash
python scripts/vllm/request_bearing_attempt_budget.py \
  --config config/data_collection_attempt_budget.json initialize \
  --seed-file /secure/path/data-collection-seeds.json

python scripts/vllm/request_bearing_attempt_budget.py \
  --config config/queue_lifecycle_attempt_budget.json initialize \
  --seed-file /secure/path/queue-lifecycle-seeds.json
```

Initialization refuses an existing branch. During cutover, at most 16 guarded
runtime attempts and at most 19 total legacy-plus-runtime attempts may coexist.
This bounded overlap permits one initial guarded Data Collection run when 18
legacy rows are still active, then releases further slots exactly as those rows
age out; it never weakens the post-cutover 16-attempt bound. Do not store or
initialize from the illustrative rows above—rebuild the inventory immediately
before rollout and validate the resulting parentless refs before enabling the
workflows.

### Bounded dashboard-state snapshots

Generated dashboard data is no longer appended to `main`. The canonical
collector creates a parentless exact snapshot and atomically rotates two refs
configured in `config/dashboard_state.json`:

- `dashboard-state` is the current tested source-and-generated tree.
- `dashboard-state-previous` is the immediately preceding tested tree. On the
  first publication both refs point to the same root commit.

Every candidate is rejected before publication if it has more than 10,000
files, more than 256 MiB of logical tree data, or any blob larger than 85 MiB
(89,128,960 bytes, which is below 90,000,000 decimal bytes). Symlinks,
submodules, non-canonical or traversing paths, malformed manifests, and a
generated file-set/hash mismatch also fail closed. Every established state must
also contain a hash-bound, canonical, semantically valid private projection
attestation of at most 4 KiB. Site assembly, projection attestations, remote
proofs, and health proofs all use the same 85 MiB per-file and 256
MiB/10,000-file public-tree limits. The private
`data/vllm/ci/dashboard_state.json` manifest and
`public_projection_attestation.json` never enter Pages. The public
`publication_manifest.json` describes every canonical root file with its mode,
size, SHA-256, and Git object ID; only `pr-preview/` is outside that root
attestation. `_site/publication_generation.json` binds the manifest digest,
file count, and total bytes to the exact generation, state commit, state tree,
code commit, and timestamp identity. Post-deploy and watchdog verification use
the Git tree object IDs, so they fetch only the small manifest and marker rather
than every large public blob.

One validation-only rollover rule keeps both state slots usable across a limit
reduction. After a state code commit has been proven to be an ancestor different
from trusted `main`, the trusted current validator may accept its hash-attested
historical manifest when the declared tree ceiling is an integer at least 256
MiB, its blob and file-count ceilings are no weaker than today's 85 MiB/10,000-
file policy, and the bound inventory plus metadata itself still fits today's
256 MiB tree ceiling. Manifest creation cannot select this rule, and proof for a
state whose code SHA equals trusted `main` remains strict. Synthetic health uses
the same read-only rule so a safe historical generation does not become a false
outage during rollover. Every new collector generation writes exact current
limits; after its first rotation, the historical previous slot remains a bounded
failover until it ages out normally.

Synthetic public health additionally fetches and digest/length verifies the
Operations manifest and every bounded lazy section except `reliability`; those
thirteen JSON canaries are also strict-parsed. Together with the shell,
publication metadata, and six required shell assets, each of the three quorum
probes has 11 bounded identity/control resources and at most 22 HTTP starts
after the single transient retry. The modal-generation probe additionally
strict-parses all thirteen eager Operations canaries and streams the complete
`reliability.json` route through SHA-256 without retaining its body; if the
middle probe fails before route discovery, probe three owns that proof. Eager
canaries have a 20-second bounded attempt and the stream has one retry, a
150-second total deadline per attempt, and at most one final 10-second
blocking-read overrun. The exact confirmation ceiling is therefore 94 HTTP
starts, 1,500 transport/deadline seconds, and 1,507 elapsed seconds including
quorum delays. The two lightweight healthy probes must identify the same exact
projection generation as the full proof; failure of that full proof fails the
complete health invocation regardless of the ordinary 2-of-3 result.

The Operations manifest is capped at 2 MiB, each canary at 12 MiB, their
combined probe bundle at 32 MiB, and the separately streamed reliability route
at 64 MiB. Bundle generation and the full data audit enforce that shared
contract before publication, while the remote checker uses every hash-attested
descriptor size as its exact read bound. A bundle that outgrows the browser or
monitor budget therefore leaves the preceding generation intact instead of
creating a partial or unreadable replacement.

Canonical publishers preserve previews without preserving stale canonical
files: they server-prove the exact old Pages tree before fetching preview
blobs, copy only whole `pr-preview/pr-N` cohorts into the newly assembled
`_site`, prune older cohorts to a 112 MiB preview envelope, and recheck the
combined 256 MiB/10,000-file Pages envelope before an orphan replacement.
Individual preview cohorts are also limited to 112 MiB and 2,000 files. The
privileged preview build removes only its redundant
`data/vllm/ci/queue_timeseries.jsonl` fallback after trusted site assembly and
before nesting; the live queue branch remains authoritative and the compact
`queue_history_chart.json` stays in the preview. This keeps the current preview
near 91 MiB and the current canonical-plus-preview tree near 226 MiB. The
expected preview inventory digest is certified after the initial deploy and any
corruption redeploy. Ambiguous proof/ref movement aborts before Pages mutation;
a definitively unsafe old Pages tree is recovered without carrying its previews.

State manifest schema 2 binds every generated descriptor to its Git object ID
as well as mode, byte count, and SHA-256. It also records a content summary
(excluding the self-referential manifest) whose file count, total bytes, and
largest blob are recomputed by full validation. The watchdog fetches state with
an 8 MiB blob limit and the declared code commit with `blob:none`, then runs
`validate-ref-metadata --expected-code-sha`. That mode reads only the canonical
state manifest and the canonical projection attestation. It verifies generated
paths/modes/OIDs, declared storage bounds, and exact non-generated source-tree
OID/mode identity without hashing or lazily fetching the large data blobs.
Collectors and deploy-only recovery continue to use full `validate-ref`, which
also reads generated bytes and verifies every SHA-256 descriptor.

Deploy-only recovery treats buildability as part of slot validity. If the
current fully validated state deterministically fails operations reconstruction,
the dashboard audit, site assembly, marker creation, or exact local projection,
the same current state is first rebuilt without network access in a clean
temporary worktree using its already installed state-pinned environment. A
successful retry publishes current. Only the same stage failing twice with
healthy disk/inode/memory headroom and non-signal exit status authorizes trying the
previous slot; a different-stage or infrastructure-class failure leaves both
refs untouched. The previous slot then uses its own pinned `constraints.txt`
and is promoted under exact leases only after all gates pass. Dependency/network
ambiguity or two failing candidates mutates neither Pages nor the rollback refs.
Manifest policy changes may accept only the current and
same-shape N-1 schema, and both the producer-declared and current hard limits
remain enforced.

`dashboard_state.py rotate` pushes both refs in one Git atomic transaction with
exact `--force-with-lease` observations. A lease rejection or server rejection
updates neither ref. After the first generation, each successful rotation sets
current to the new parentless commit and previous to the old current commit, so
only two state generations remain reachable. A network failure during the
post-push verification can be reported after the server accepted the atomic
transaction; always re-read both remote refs before retrying. State rotation
precedes Pages deployment by design. If deployment then fails, the public marker
does not match current state and the watchdog requests an exact deploy-only
recovery without repeating Buildkite collection.

#### One-time bootstrap and lockout

`bootstrap_allowed` starts as `true` only for the migration in which both state
refs are definitively absent. The first canonical Data Collection run passes
`--current-sha absent --previous-sha absent`; `rotate` creates both refs at the
same validated root or creates neither. Workflows refuse frozen-main bootstrap
if only one ref exists, remote discovery is ambiguous, or validation/fetch
fails.

Health checks have a separate, temporary migration authority in
`config/dashboard_bootstrap.json`. They accept the legacy projection only
before `2026-09-02T00:00:00Z` and only when fresh, canonical evidence from the
GitHub Git Refs API says that both state refs are absent. A present ref,
ambiguous API response, stale or malformed evidence, or the deadline expiring
makes missing state-backed metadata unhealthy. This evidence is generated on
the runner and is never accepted from the public site.

Before restore, recovery validates each observed slot independently and binds
every usable slot's declared code SHA to the exact trusted `main` SHA with a
fail-closed GitHub Compare API ancestry proof. If both slots are valid,
`repair-slots` is a no-op. If exactly one is valid, it atomically copies that
root to both refs under exact leases without a Buildkite request. If neither is
valid, it fails rather than treating missing or corrupt state as bootstrap
authority.

After the first successful run, verify and fully validate the two identical
slots:

```bash
git ls-remote --refs origin \
  refs/heads/dashboard-state \
  refs/heads/dashboard-state-previous
git fetch origin \
  +refs/heads/dashboard-state:refs/remotes/origin/dashboard-state \
  +refs/heads/dashboard-state-previous:refs/remotes/origin/dashboard-state-previous \
  --depth=1
CURRENT_STATE_SHA=$(git rev-parse refs/remotes/origin/dashboard-state)
PREVIOUS_STATE_SHA=$(git rev-parse refs/remotes/origin/dashboard-state-previous)
test "$CURRENT_STATE_SHA" = "$PREVIOUS_STATE_SHA"
python scripts/vllm/dashboard_state.py validate-ref --ref "$CURRENT_STATE_SHA"
STATE_CODE_SHA=$(git show \
  "$CURRENT_STATE_SHA:data/vllm/ci/dashboard_state.json" | \
  python -c 'import json,sys; print(json.load(sys.stdin)["code_sha"])')
git fetch origin "$STATE_CODE_SHA" --depth=1
python scripts/vllm/dashboard_state.py validate-ref \
  --ref "$CURRENT_STATE_SHA" --expected-code-sha "$STATE_CODE_SHA"
```

Then change `config/dashboard_state.json` to `"bootstrap_allowed": false` in
the next reviewed `main` commit. This is a required post-bootstrap step, not an
automatic state-branch mutation. Once disabled, deletion of both refs is a hard
recovery condition rather than permission to rebuild durable state from the
frozen generated files on `main`.

#### Explicit rollback

Rollback swaps the two already validated slots; it never creates history and
makes no Buildkite request. Run it while no canonical publisher is active. Fetch
both refs and their declared code commits, validate them using the same
two-pass workflow contract, then use the exact observed SHAs:

```bash
git fetch origin \
  +refs/heads/dashboard-state:refs/remotes/origin/dashboard-state \
  +refs/heads/dashboard-state-previous:refs/remotes/origin/dashboard-state-previous \
  --depth=1
CURRENT_STATE_SHA=$(git rev-parse refs/remotes/origin/dashboard-state)
PREVIOUS_STATE_SHA=$(git rev-parse refs/remotes/origin/dashboard-state-previous)
for STATE_SHA in "$CURRENT_STATE_SHA" "$PREVIOUS_STATE_SHA"; do
  python scripts/vllm/dashboard_state.py validate-ref --ref "$STATE_SHA"
  STATE_CODE_SHA=$(git show \
    "$STATE_SHA:data/vllm/ci/dashboard_state.json" | \
    python -c 'import json,sys; print(json.load(sys.stdin)["code_sha"])')
  git fetch origin "$STATE_CODE_SHA" --depth=1
  python scripts/vllm/dashboard_state.py validate-ref \
    --ref "$STATE_SHA" --expected-code-sha "$STATE_CODE_SHA"
done
python scripts/vllm/dashboard_state.py rotate \
  --new-state "$PREVIOUS_STATE_SHA" \
  --current-sha "$CURRENT_STATE_SHA" \
  --previous-sha "$PREVIOUS_STATE_SHA" \
  --remote origin
gh workflow run deploy-pages.yml --ref main
```

The exact leases reject a concurrent advance. The successful command makes the
old previous state current and retains the displaced state in the previous
slot. Existing historical objects on `main` are intentionally not rewritten;
this design stops future hourly reachable-history growth. Git hosts may retain
superseded unreachable objects until normal server-side garbage collection.

### Webhook-Triggered Updates

For build-completion updates, `hourly-master.yml` receives the
`buildkite_build_finished` repository dispatch and performs a complete,
validated publication.

Buildkite queue freshness now uses those job-level webhook events (`job.scheduled`, `job.started`, `job.finished`) plus agent events (`agent.connected`, `agent.disconnected`, `agent.lost`, `agent.stopping`) to wake the lightweight `queue-monitor.yml` workflow. A webhook is never a cadence bypass: the parentless `queue-request-budget` ledger coalesces every trigger, including manual runs, before the token-bearing step. Queue-native metrics reserve two starts at most every ten minutes; the complete active-job overlay reserves twelve additional starts at most hourly. Missing, corrupt, ambiguous, or lease-conflicted budget state fails closed with zero Buildkite requests.

The normal current-volume cost is about 432 requests/day: 144 one-page metric
reads plus 24 twelve-page detail scans. The exact 25-hour ledger caps outstanding
reservations at 650 starts. Queue Monitor times out after 20 minutes, so every
actual start remains inside the ledger's extra one-hour hold and the same 650
ceiling applies to every rolling 24-hour window. If the active-job connection
needs more than twelve pages or errors, the collector never publishes the
partial rows. It retains `queue_jobs.json` from the last exhaustive scan,
preserves `details_observed_at`, and records a `retained_due_to_page_cap` or
`retained_due_to_error` status alongside independently current
`metrics_observed_at`.

The workflow never creates the budget branch. At cutover, an operator must
account for every successful legacy queue run still inside the preceding 25
hours (use its conservative maximum request-start charge, not its observed
returned rows) and initialize once while queue publication is disabled:

```bash
python scripts/vllm/queue_request_budget.py initialize \
  --now 2026-09-01T12:00:00Z \
  --seed 2026-08-31T12:02:00Z=101 \
  --seed 2026-08-31T12:12:00Z=101
```

Supply all verified rows; the example is intentionally incomplete. An
over-650 seed becomes migration debt and blocks new request permits until
enough 25-hour reservations expire. Initialization refuses an existing branch,
and runtime updates are parentless replacements protected by exact leases.

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
do not cover a complete calendar day. The public aggregate has a 5 MiB hard
ceiling. If pathological job volume would exceed it, the oldest whole-day
vectors are replaced deterministically by exact count/min/p50/p95/max/average
blocks and explicit omitted-sample coverage; the manifest-bound sharded ledger
continues to retain every underlying observation in its explicitly attested
published scope. The summary is always recomputed from that exact ledger scope;
it never reports an observation removed by the ledger's independent byte
retention. The supported organization
Builds REST endpoint does not filter job event timestamps directly. The
collector unions builds finished inside the source window, builds created
inside it, and active-state builds created inside the bounded parent-build
horizon, then filters jobs by the twelve direct cluster-queue UUIDs. Every
retained value is therefore an exact direct job observation, while the
aggregate separately declares residual population limits such as page-number
drift and jobs attached to parent builds created before that horizon.

The reconciled, deduplicated job-observation ledger lives only on the
`queue-lifecycle-data` branch under `queue_lifecycle_jobs/`; it is neither
committed to `main` nor published to Pages. Each compact row contains a hashed
job identity, its canonical queue, direct event timestamps, derived durations,
outcome, and retry flags. An ordinary UTC day is stored as
`YYYY-MM-DD.jsonl.gz`. If that day's actual deterministic gzip would exceed the
16 MiB per-file ceiling, rows sorted by job identity are recursively bisected
into contiguous ranges named
`YYYY-MM-DD.part-NNNNNNNNN-of-NNNNNNNNN.jsonl.gz`; part numbers start at one,
have nine digits, are contiguous, and all declare the same total, so a missing
final part is detectable without a manifest. No plain daily file
accompanies a part set. Existing daily-only manifests remain valid, while newly
written manifests declare the additive `utc_day_or_adaptive_part_v1` naming
contract. Unchanged, unsplit days therefore retain their exact filename and
bytes. A late start or completion can still update the segment containing that
job's earliest retained event.

Seven days is the configured target horizon, while the manifest's additive
`retention` object attests the exact scope that was actually published. The
complete compressed segment directory is capped at 16 MiB (16,777,216 bytes),
giving roughly 3.5x headroom over the current approximately 4.5 MiB ledger and
leaving ample distance from GitHub's 90 MB warning boundary even alongside the
separately capped 5 MiB summary. If a candidate exceeds the compressed or 512
MiB uncompressed aggregate ceiling, the writer deterministically removes whole
oldest UTC cohorts based on each observation's **latest** retained lifecycle
event. This keeps a long-running job whose completion is recent. If the one
remaining boundary day is itself too dense, the writer retains a deterministic
newest whole-observation prefix from that day. It records exact input,
published, and omitted counts, omitted whole latest-event days, any partial
boundary day, actual published event bounds, and `byte_limited` completeness.
Incremental runs carry a still-relevant omission forward; only a full
reconciliation or the omitted event-days aging outside the configured window
can clear it. Publication therefore fails for volume only when one canonical
row cannot fit either hard ceiling. Compaction itself makes no Buildkite API
request and does not change the guarded request topology. The ledger
deliberately omits labels, URLs, branches, commits, pipeline names, and other
build metadata.

Lifecycle collection checks its durable recovery gate every 30 minutes, while
successful request-bearing attempts remain two hours apart, so an API or schema
failure cannot delay the ten-minute point-in-time queue monitor. Organization-
wide finished, created, and active-build cohorts use bounded, verified REST
pagination, and the public aggregate includes the exact source window, cohort
filters, query coverage, and provenance. Incomplete pagination, an unreadable
established ledger, or a failed Buildkite query causes collection to fail
instead of silently publishing a partial window. Workflows pass the existing
`BUILDKITE_TOKEN` secret to the collector as
`BUILDKITE_API_TOKEN`; tokens must never be placed in source, generated data,
logs, or dashboard URLs.

A wall-clock-yielding attempt may use fewer than its reserved 100 request
starts, but the durable ledger still charges all 100. Under persistently slow
responses, sixteen partial attempts can therefore reach the 1,600-start ceiling
before the bounded query tree is complete. Progress pauses until an older
25-hour reservation ages out, then resumes from the exact frozen checkpoint;
the collector never spends beyond the ceiling to recover faster. A WIP that
still matches its content digest, canonical baseline/ref, frozen query and
watermark, and queue identity has no elapsed-age eviction, so arbitrarily slow
but progressing recovery survives repeated rolling-cap waits. Future query
horizons remain invalid.

### DNS health observations

`collect_dns_failures.py` discovers terminal script-job attempts across the
`amd-ci` and `ci` pipelines, including retries and passing jobs, then scans each
bounded log sample for strong DNS signatures. The workflow gets an hourly
recovery opportunity and retains a three-hour scanner interval, but the hard
cross-run limit comes from the separate parentless `dns-request-budget` branch.
Before an eligible attempt receives a Buildkite token, the workflow atomically
reserves its complete 110-request allowance for 25 hours. Retries are included
in that per-run allowance. A failed, canceled, or interrupted scan keeps the
whole reservation, so lack of a new DNS generation cannot make the next retry
forget traffic already started. A new reservation is rejected while the
retained total plus 110 would exceed 990. Because the request-bearing step has
less than one hour of wall-clock headroom after reservation, the extra hour of
retention guarantees at most 990 actual request starts in every rolling 24
hours, below the 1,000-request target.
The 500-log limit remains a secondary safety bound. Hourly, external, or manual
invocations inside the scanner interval validate the established budget ledger
but do not move it, reserve capacity, or make a Buildkite request.

The budget branch is one exact, single-file, parentless commit. Its JSON is
limited to 32 timestamp/count reservations and 64 KiB; it contains no token,
Buildkite response, URL, job identity, or log evidence. Every update force-pushes
one new root with an exact lease under the same non-canceling
`dns-health-data-publish` concurrency group. Missing, malformed, non-parentless,
oversized, or concurrently changed established state fails before Buildkite
access. A push response lost after server acceptance also stops the run before
collection; the next run re-reads the durable ref.

The workflow never bootstraps a missing request ledger. Before enabling it,
initialize the branch once with verified request-start telemetry from every
attempt still inside the 25-hour hold window. The September 1 migration uses the
following deliberately exact legacy debt (570 + 568 + 110 + 10 + 110 = 1,368
starts):

```bash
python scripts/vllm/dns_request_budget.py initialize \
  --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --seed 2026-08-31T07:33:07Z=570 \
  --seed 2026-08-31T15:20:21Z=568 \
  --seed 2026-08-31T23:00:56Z=110 \
  --seed 2026-09-01T05:20:34Z=10 \
  --seed 2026-09-01T05:33:17Z=110 \
  --remote origin
```

Initialization is the only command allowed to record bounded over-cap migration
debt. Runtime validation accepts that debt but cannot extend it: no scan is
permitted until expired entries leave enough room for a full 110-start
reservation, and the first permitted runtime update clears the migration flag.
At the exact half-open boundary `2026-09-01T08:33:07Z`, the 570-start entry has
expired, 798 starts remain, and the next 110-start scan is permitted with 908
starts reserved. If initialization occurs after any listed entry has already
reached its 25-hour boundary, omit that entry only after verifying its
expiration; the initializer rejects out-of-window seeds rather than silently
changing telemetry.

The eligible scan receives a 20-minute wall-clock budget so large active-parent
payloads can finish their fail-closed discovery pass. Wall-clock headroom does
not expand API traffic: the independent 110-request-start ceiling remains
authoritative within the separately reserved rolling allowance and includes
retries. The budget decision timestamp is passed unchanged into the collector,
so crossing the three-hour interval boundary between workflow steps cannot turn
a zero-reservation republish into a request-bearing scan.

The configured 30-day value is the target retention horizon, not a claim of an
exhaustive census. Unvisited jobs remain explicitly pending, longer windows stay
partial, and the UI renders observed values as lower bounds. This expected,
quantified partial coverage is a DNS-panel warning rather than a site-wide
publication degradation. Both the DNS panel and publication audit declare the
source stale after 12 hours. This documented window tolerates delayed or dropped
GitHub cron events while 15-minute recovery opportunities reduce the normal delay;
a DNS dataset that is not collected, malformed, or internally inconsistent
takes the same strict degradation or fail-closed publication path.

GitHub Actions schedules are best-effort and may be delayed or dropped. The
DNS workflow therefore accepts `dns_health_tick`, while the dedicated
publication watchdog accepts `publication_watchdog_tick` from a scheduler
outside GitHub Actions. The watchdog also runs after the trusted Queue Monitor,
Queue Lifecycle Monitor, DNS Health Monitor, or Site Health Check completes,
regardless of its conclusion, and has a 15-minute cron as one more
best-effort opportunity. It first validates the parentless current state and
its exact code tree, then compares the state identity with the public Pages
marker. Definitively uninitialized state routes to the canonical collector;
state/Pages mismatch routes to deploy-only recovery; a fresh DNS-only
degradation routes to the DNS collector; and other stale/blocked publication
state routes to canonical collection. Ambiguous discovery or an invalid
established state fails closed instead of dispatching speculative recovery.
The canonical collector is due every 120 minutes, while proactive recovery
starts at 95 minutes of publication age. Active-run suppression prevents a
duplicate when the normal two-hour run is already queued or running, and a
15-minute retry cooldown bounds repeated failed attempts.

A watchdog dispatch carries the exact generation it observed. The
three workflow groups independently retain only one pending routine, targeted
DNS, and targeted publication-recovery wake-up. A routine burst therefore cannot
replace a pending repair. Each surviving job then joins the FIFO
`gh-pages-deploy` writer queue, so it cannot replace an already-pending
deploy-only recovery or trusted preview publication. Preview synchronization
bursts are first coalesced per PR, and close events never enqueue a full-tree
Pages rewrite, so untrusted PR churn cannot starve canonical recovery. After it
acquires that writer lock, a
preflight skips it if another run already advanced Pages. A separate 120-minute
automated-trigger preflight coalesces a wake-up that arrived behind a recent publication.
DNS keeps
its stronger generation acknowledgement: a targeted run is skipped only once
Pages contains that DNS generation, its full contract validates, DNS is no
longer affected, and publication remains fresh. The canonical collector has a
50-minute timeout so a hung run cannot retain the lock indefinitely. Excluding
time already held by another bounded Pages writer, the first-attempt bound is
`95 + 15 + 50 = 160` minutes (trigger age, detection interval, timeout), twenty
minutes before the three-hour site-health freshness
limit. At the normal 25-minute runtime, one failed attempt plus its 15-minute
cooldown and retry is bounded by `95 + 15 + 25 + 15 + 25 = 175` minutes,
retaining five minutes of margin. GitHub schedules remain best-effort, so the
independent external 15-minute tick is still required for the timing guarantee
when Actions cron is delayed or dropped.

Declaring a `repository_dispatch` trigger is not an independent scheduler. To
make publication recovery enforceable independently of GitHub's scheduler,
configure a scheduler outside GitHub Actions to POST the following event every
15 minutes (the recovery timing contract assumes no longer interval):

```http
POST https://api.github.com/repos/AndreasKaratzas/vllm-ci-dashboard/dispatches
Accept: application/vnd.github+json
Authorization: Bearer <external-heartbeat-token>

{"event_type":"publication_watchdog_tick"}
```

Use a dedicated fine-grained token restricted to this repository with
**Contents: write**, store it only in the external scheduler, and rotate it
normally. Monitor the `Publication Recovery Watchdog` run history and alert if
no `repository_dispatch` event arrives for 30 minutes. In-repository cron and
`workflow_run` triggers materially improve recovery odds but cannot guarantee
recovery from their own scheduler failure domain.

Each eligible run gives collection a 20-minute budget with a separate
finalization reserve. Unvisited log work remains pending for a later sample
instead of being reported as a complete zero. Pending work is ordered newest
first, distributed round-robin across pipeline/queue/node coordinates, and allocated in
a prefix-stable 60/40 passed/non-passing mix. That preserves the passing-job
signals that outcome-only filtering would miss without letting one busy fleet
consume the bounded request budget. Its public
`dns_failures.json` dataset covers the trailing 720 observed hours. “Observed”
is deliberate: API, rate-limit, oversized-log, and pending-job gaps remain
explicit in each window's coverage block, so an incomplete scan cannot be
displayed as a complete zero.

This request cap is transitional. The lossless target is agent-side
classification: scan the already-local job log in a Buildkite lifecycle hook
and emit only a compact, privacy-safe positive DNS record. Consuming those
records from job-finished events or filtered build metadata removes the need to
download every negative job log while retaining passing-job observations.

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

The repository and its force-orphan `dns-health-data` and
`dns-request-budget` branches are publicly readable. Plaintext scanner state
therefore exists only at the gitignored
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

The unified two-hour Data Collection workflow also reconciles four bounded
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

State lives in `open_amd_main_failure_issues.json`,
`open_ci_main_failure_issues.json`, `open_amd_duration_regression_issues.json`,
`open_agent_health_issues.json`, `open_ci_area_regression_issues.json`,
`open_omni_surge_issues.json`, `open_queue_issues.json`, and
`open_queue_zombie_issues.json`. Fixed per-ledger producer limits sum exactly to
the dashboard state's 3 MiB watcher allocation. Every replacement is atomic;
bounded compaction keeps all actionable issue and incident mappings, prefers
dropping refetchable or retired cache detail, and attests any omission through
`publication_retention` counts.
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

**"BUILDKITE_TOKEN not set"**: Ensure the token is configured as a GitHub
Actions secret and trigger the appropriate guarded workflow. Exporting a token
alone is intentionally rejected with exit status 78.

**No nightly builds found**: The script filters by build name pattern. Check that the pipeline has builds matching "AMD Full CI Run - nightly" or "Full CI run - nightly".

**Rate limiting (429)**: The script retries on 429 with exponential backoff using the `Retry-After` header. For large fetches (30+ days), run in smaller batches: `--days 7`.

**Cached data**: The analytics collector's sanitized Buildkite history cache
lives in `data/vllm/ci/.cache/analytics-builds-v1`. The canonical workflow keeps
one immutable cache key per UTC day in GitHub Actions cache storage, restores
the prior day when the new key is not populated, and finally restores the newest
versioned cache after a multi-day Actions outage. It still refetches the recent
overlap on every run. The collector validates/prunes the restored cache and
fully reconciles when its
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
