# Project Dashboard

Auto-updated tracking of AMD GPU ecosystem projects. Last updated: **2026-09-01 09:04 UTC**

## Overview

| Project | Role | Latest Release | Open PRs | Open Issues | Links |
|---------|------|----------------|----------|-------------|-------|
| **vllm** | watch | v0.28.0 | - | 3 | [repo](https://github.com/vllm-project/vllm) / [fork](https://github.com/sunway513/vllm) |

## Live Dashboard

Interactive dashboard with a **Home** view for PRs, project #39 issues, and test parity, plus CI operations views.

Hosted on GitHub Pages. Pushes to `main` run CI; the production site and fresh
operational data are published by the scheduled/dispatch
`.github/workflows/hourly-master.yml` workflow or the manual Pages workflow.

## Site Layout

- `docs/` — static shell assets (HTML, CSS, JS)
- `data/` — collector inputs and generated payloads; the site assembler publishes only the explicit public manifest
- `scripts/build_site.py` — assembles `docs/` + `data/` into `_site/` for Pages deploys

## Views

| View | Description |
|------|-------------|
| **Home** | PRs, project #39 issues, and configured AMD coverage against upstream GPU source definitions |
| **CI Health** | Current main CI AMD MI runtime health, configured upstream GPU coverage, AMD mirror inventory, and MI architecture drilldowns |
| **CI Analytics** | AMD MI test health, median latency over the latest five completed relevant main CI nightlies, reliability, DNS, and agent health |
| **Omni** | Current Omni MI active-job evidence, MI queue windows, queued-age bands, and daily deltas |

## Markdown Dashboards

- [PR Tracker](dashboards/pr-tracker.md) — all tracked PRs across projects
- [Weekly Digest](dashboards/weekly-digest.md) — weekly summary of releases, PRs, and issues
- [Dashboard Audit](dashboards/dashboard-audit.md) — source-of-truth map and hidden-bug checklist

## Data Collection

The main data path is `.github/workflows/hourly-master.yml`, which targets a two-hour cadence. A 15-minute publication watchdog begins recovery at 95 minutes of publication age, leaving bounded execution headroom before the three-hour site-health limit if a scheduled run is delayed or dropped. Queue evidence is collected independently on a best-effort 10-minute GitHub Actions schedule and published to the dedicated `queue-data` branch; Omni reads the freshest of that live feed and the canonical Pages snapshot. Unrelated dashboard audits and full-site deployment locks therefore cannot discard or delay queue observations.

Generated operational data is kept out of `main`. A weekly, tokenless scheduler
keepalive checks the protected branch age and, only after 30 days without a
code commit, advances the single bounded `.github/scheduler-activity.txt`
heartbeat. This preserves long-lived scheduled-workflow eligibility without
allowing collector output or cache history to grow the repository.
The deliberate liveness tradeoff is at most one tiny default-branch commit per
30 inactive days; its one-line file is replaced rather than appended, while
all high-volume state remains in bounded rotating refs and caches.

All remote
Actions are pinned to immutable commit SHAs; Python is fixed to 3.12.13 and all
workflow installs use the checked-in `constraints.txt` lock.

Buildkite-native p50/p95 remain the site-comparable queue series. Percentiles reconstructed from the separately fetched scheduled-job population are retained and charted with their own labels and n/N coverage; they never silently replace native values.

| Script | Purpose |
|--------|---------|
| `scripts/collect.py` | vLLM PRs, project #39 issues, linked CI PR tags, releases |
| `scripts/collect_ci.py` | AMD MI test results from current main CI, CI health, and flake/failure data |
| `scripts/vllm/collect_analytics.py` | Windowed CI analytics from parsed test-result JSONL plus Buildkite metadata |
| `scripts/vllm/collect_amd_test_matrix.py` | AMD GPU routes from the exact main CI definitions used by the latest nightly |
| `scripts/vllm/collect_ownership_parity.py` | Build a source-area parity map from the exact vLLM commit used by the latest AMD nightly |
| `scripts/vllm/build_test_group_parity.py` | Derive configured upstream GPU source groups, AMD MI routes, and required gate coverage |
| `scripts/vllm/collect_queue_snapshot.py` | Queue timeseries, workload-attributed counts, and the exact active-job ledger |
| `scripts/vllm/collect_capacity_monitor.py` | AMD queue capacity limits plus mirror test-group dependency projections |
| `scripts/vllm/build_operations_snapshot.py` | Build the versioned operations manifest and lazy CI Health, Queue, and Omni read-model shards |
| `scripts/vllm/build_queue_section.py` | Build only the live Queue read-model shard for the independent queue publisher |
| `scripts/vllm/ci_area_regression_watcher.py` | Reconcile one dashboard-repository issue per regressing test area using the ranked owner chain, regional working hours, and exact AMD evidence |
| `scripts/vllm/sync_ci_operations_project.py` | Add open managed dashboard issues to the single AMD CI Operations Project by workstream |
| `scripts/vllm/ensure_ci_operations_labels.py` | Ensure the managed-issue and Project workstream labels exist before any watcher runs |
| `scripts/vllm/audit_dashboard_data.py` | Cross-surface audit for data totals, frontend assumptions, links, and deploy safety |
| `scripts/render.py` | Generate markdown dashboards and site data |
| `scripts/build_site.py` | Assemble `docs/` and `data/` into `_site/` for Pages |

Tokenless collection, transformation, and validation steps can be run manually:

Live Buildkite collectors are intentionally excluded from this list. Trigger
the guarded **Data Collection**, **Queue Monitor**, **Queue Lifecycle Monitor**,
or **DNS Health Monitor** workflow with `workflow_dispatch` instead. A direct
process that can see `BUILDKITE_TOKEN` or `BUILDKITE_API_TOKEN` exits with
status 78 unless the workflow has supplied a complete durable request-guard
reservation; exporting a token alone is not a supported local run mode.

DNS Health discovers current AMD jobs before its daily active-build sweep.
Each scan keeps the 110-request limit, reserves 40 request starts for log
classification, and gives discovery half the collection time budget. When
discovery reaches its budget, validated observations are saved as an encrypted
checkpoint with `discovery.complete: false`; the public panel continues to
show incomplete coverage and pending jobs. A failed scan before any validated
discovery page preserves the prior generation.

```bash
pip install requests pyyaml
python scripts/collect.py
python scripts/vllm/collect_amd_test_matrix.py --output data/vllm/ci/
python scripts/vllm/collect_ownership_parity.py --input-dir data/vllm/ci --output data/vllm/ci
python scripts/vllm/build_test_group_parity.py --output data/vllm/ci/
python scripts/vllm/collect_capacity_monitor.py --output data/vllm/ci/
python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci --output data/vllm/ci/operations_v2.json.gz
python scripts/vllm/build_queue_section.py
python scripts/vllm/ci_area_regression_watcher.py
python scripts/vllm/sync_ci_operations_project.py
python scripts/vllm/audit_dashboard_data.py --strict-warnings
python scripts/render.py
python scripts/build_site.py --cache-bust-index
```

Configure tracked projects in [`config/projects.yaml`](config/projects.yaml).
Current CI execution metrics use only AMD MI GPU jobs from the `ci` pipeline.
Non-MI execution and legacy `amd-ci` data do not contribute to current CI health, latency, or reliability.
Parity and AMD mirrors use current upstream `main` GPU YAML as an immutable configuration benchmark; they do not track upstream GPU execution.
`config/vllm_upstream_test_group_parity.json` stores explicit unsupported-group
classification policy, rather than a frozen coverage inventory. Coverage and
required blocking gates are distinct counts, with optional and soft-fail routes
shown separately. Runtime definitions remain pinned to the observed nightly.

Latency uses the global latest five completed main CI nightlies with MI GPU execution. Each AMD group
contributes at most one sample per nightly: maximum wall time of the complete
parallel shard group, followed by the median across available nightly samples.
Missing groups are unavailable; older nightlies never fill missing samples.

The private `operations_v2.json.gz` build input produces bundle v3 with eleven
allowlisted lazy sections. Canonical publication replaces the Pages tree,
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

The daily [deployment retention workflow](.github/workflows/deployment-retention.yml)
keeps the newest twenty GitHub deployment records and removes proven superseded
Pages records. Its [retention policy](scripts/vllm/deployment-retention.md) preserves
the serving deployment and active work; manual runs default to a preview.

### CI ownership and regression issues

[`config/vllm_ci_ownership.json`](config/vllm_ci_ownership.json) is the
authoritative 31-area ranked routing map. Each area has one to three active
owners. The hourly workflow evaluates every exact AMD matrix definition,
attributes each definition through
a parity snapshot pinned to that nightly's exact vLLM commit, and reconciles one state-owned issue
per area in `AndreasKaratzas/vllm-ci-dashboard`. Current regressions, exact
Buildkite evidence, upstream parity gaps, the ranked chain, and the actual
GitHub assignees are shown on the managed area issues and AMD CI Operations project.

The ownership config carries two shared, DST-aware working-hours profiles.
They are operational shifts, not claims about an engineer's home location. EU
uses Serbia time (`Europe/Belgrade`), while NA uses Chicago time
(`America/Chicago`):

| Profile | Local hours | Time zone | Engineers |
|---|---|---|---|
| EU | Monday–Friday, 09:00–17:00 | `Europe/Belgrade` (Serbia) | `gchinora`, `stefankoncarevic`, `fxmarty-amd` |
| NA | Monday–Friday, 09:00–17:00 | `America/Chicago` | `aarushjain29`, `divakar-amd`, `micah-wil`, `mawong-amd`, `AndreasKaratzas` |

Assignment uses only these committed regional schedules. The watcher walks
each area's configured ranks in ascending order and selects the first owner
currently inside that profile's working
hours. If every ranked owner is outside working hours, or a schedule cannot be
evaluated safely, assignment falls back to the CI lead. The watcher also verifies
that the selected login can be assigned in this repository; otherwise it
assigns the CI lead. If neither account is verifiably assignable, the watcher
refuses to open an unassigned issue. Each regression issue tags the selected
owner and verified assignee, then CCs every remaining ranked area owner exactly
once. No issue can be opened outside the dashboard repository.

Use one GitHub Project, **AMD CI Operations**, with label-backed views instead
of three separate projects: `workstream:infra`, `workstream:dashboard-ci`, and
`workstream:dev`. This keeps one lifecycle per incident while still providing
the requested Infra, dashboard CI, and development queues. The Project sync
requires a `PROJECTS_WRITE_TOKEN` Actions secret with Projects V2 write access;
for a classic PAT this is the `project` scope, and the token owner must be able
to update Andreas Karatzas's Project. The repository-scoped `GITHUB_TOKEN`
cannot update a user-owned Project. If the secret is absent, issue
reconciliation and dashboard deployment continue while Project synchronization
reports a safe no-op.

During credential rotation, the guarded Project-sync step accepts the existing
`PROJECTS_TOKEN` only as a fallback when `PROJECTS_WRITE_TOKEN` is absent. The
fallback is confined to the repository/project-validated add-item script;
install the scoped replacement and remove the legacy secret when rotation is
complete.

The hourly GitHub collector reads public `vllm-project` Project #39 and refreshes
`project_items.json` as a read-only fallback for the Home issue list.
`PROJECTS_READ_TOKEN` is optional and only raises the API rate limit. Dashboard
automation never creates or updates Project #39 issues, comments, or fields.

The canonical operational queue is the **AMD CI Operations** project and its managed area issues. Project #39
supplies read-only issue evidence, while area issues provide the exact latest-nightly
ownership queue. The existing `amd-main-failure` issue remains a broad all-main
rollup; these evidence scopes are intentionally distinct.

## Local development (Nix)

A Nix flake pins Python, Node, and every CLI the collectors / linters
need, so you do not have to manage a venv or a global `npm i -g`.

```bash
# One-time: enable flakes + nix-command if you haven't already.
nix develop            # or: direnv allow  (with .envrc)
```

The default `devShells.default` (`dashboard`) gives you Python 3.12
(`uv`-managed), Node 22, `prettier`, `cspell`, `gh`, `git-lfs`, `jq`,
`yq-go`, `shellcheck`, `yamllint`, `actionlint`, and `act` for running
workflows locally. The shell hook wires up shortcut functions:

| Function | What it does |
|----------|--------------|
| `dash-collect` | Run the local collector pipeline (`collect.py`, `collect_activity.py`, `collect_ci.py`) |
| `dash-render` | Regenerate `data/site/projects.json` and markdown dashboards |
| `dash-test` | Run the pytest suite |
| `dash-clean` | Remove generated artifacts (`_site/`, caches) |
| `dash-lint-js` / `dash-fmt-js` | `cspell` + `prettier` over `docs/assets/js` |
| `dash-lint-workflows` | `actionlint` + `yamllint` over `.github/workflows` |
| `dash-lint-shell` | `shellcheck` over tracked shell scripts |
| `dash-lint-spell` | `cspell` over docs, scripts, tests, and workflows |

For a minimal shell with only Python + the collector deps, use
`nix develop .#minimal`.
