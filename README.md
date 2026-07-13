# Project Dashboard

Auto-updated tracking of AMD GPU ecosystem projects. Last updated: **2026-07-13 07:44 UTC**

## Overview

| Project | Role | Latest Release | Open PRs | Open Issues | Links |
|---------|------|----------------|----------|-------------|-------|
| **vllm** | watch | v0.25.0 | - | 3 | [repo](https://github.com/vllm-project/vllm) / [fork](https://github.com/sunway513/vllm) |

## Live Dashboard

Interactive AMD CI operations dashboard with linked nightly movement, all-main
test-group reliability, queue and workload history, performance evaluation,
and authenticated operational controls.

Hosted on GitHub Pages — deployed automatically on every push to main.

## Site Layout

- `docs/` — static shell assets (HTML, CSS, JS)
- `data/` — published JSON payloads fetched by the shell at runtime, including `data/site/projects.json`
- `scripts/build_site.py` — assembles `docs/` + `data/` into `_site/` for Pages deploys

## Views

| View | Description |
|------|-------------|
| **Home** | Current AMD signals, nightly movement, queue pressure, and linked engineering work |
| **CI Health** | Reviewed gating plan, latest AMD evidence, hardware coverage, and nightly failure history |
| **CI Analytics** | All completed AMD `branch=main` builds for group reliability, plus a separate nightly regression and retry lifecycle |
| **Perf Eval** | Artifact-backed AMD performance and accuracy series with build and commit provenance |
| **Queue Monitor** | Source-labeled current counts and waits, exact active jobs, and 30 days of retained queue history |
| **CI Workload Trajectory** | Historical AMD group execution, latency, incident pressure, and exact group drilldowns |
| **Omni** | vLLM-Omni resource use across the fleet, split between AMD and non-AMD queues |
| **Test Build / Ready / Admin** | Controlled test builds, current and stale ticket evidence, and authenticated access administration |

## Evidence Semantics

- Reliability uses every completed `vllm/amd-ci` build on `branch=main` in the
  retained window. Canonical nightlies are identified separately for
  new/recurring/fixed comparisons.
- A mixed-outcome group means it has both passing and incident observations.
  It is a candidate for investigation, not a measured test-case flake
  probability. Confirmed retry recoveries require explicit Buildkite retry
  metadata and a failed-attempt-to-passing-attempt edge.
- Hardware, queue, raw label, and step identity remain part of a group key.
  Similar names such as `V1 e2e (4 GPUs)` and
  `V1 e2e (4xH100-4xMI300)` are not merged.
- Group observations retain exact Buildkite build, job, and step links. Queue
  aggregates link to their retained source data and current jobs link to exact
  Buildkite output.
- Queue `p50`/`p95` values prefer Buildkite queue-native metrics. Sampled
  values are labeled as samples; unsupported `p99` and connected-agent values
  are shown as unavailable, never as zero.
- Queue history is retained for 30 days. The retired `amd_mi355B*` queue family
  is excluded at collection, aggregation, audit, and presentation boundaries.
- Upstream CI is used only on explicit parity surfaces; it is not mixed into
  AMD reliability or readiness counts.

## Markdown Dashboards

- [PR Tracker](dashboards/pr-tracker.md) — all tracked PRs across projects
- [Weekly Digest](dashboards/weekly-digest.md) — weekly summary of releases, PRs, and issues
- [Dashboard Audit](dashboards/dashboard-audit.md) — source-of-truth map and hidden-bug checklist

## Data Collection

The main data path is `.github/workflows/hourly-master.yml`, which runs every 30 minutes and serializes every Pages writer behind the shared `gh-pages-deploy` lock.

| Script | Purpose |
|--------|---------|
| `scripts/collect.py` | vLLM PRs, project #39 issues, linked CI PR tags, releases |
| `scripts/collect_ci.py` | Buildkite nightly test results, CI health, parity, flaky/failure data |
| `scripts/vllm/collect_analytics.py` | Paginated all-main AMD reliability, exact attempt evidence, retries, latency, and separate nightly comparisons |
| `scripts/vllm/collect_amd_test_matrix.py` | AMD hardware matrix from upstream `test-amd.yaml`, matched against the latest AMD nightly |
| `scripts/vllm/collect_gating_proposals.py` | Open vLLM PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors |
| `scripts/vllm/collect_gating_target_candidates.py` | Review-only audit of upstream nightly GPU jobs against the canonical AMD gating target list |
| `scripts/vllm/collect_queue_snapshot.py` | Source-aware queue metrics, exact active jobs, and merge-safe 30-day history |
| `scripts/vllm/build_operations_snapshot.py` | Versioned read model shared by the v2 operational views |
| `scripts/vllm/collect_capacity_monitor.py` | AMD queue capacity limits plus mirror test-group dependency projections |
| `scripts/vllm/audit_dashboard_data.py` | Cross-surface audit for data totals, frontend assumptions, links, and deploy safety |
| `scripts/render.py` | Generate markdown dashboards and site data |
| `scripts/build_site.py` | Assemble `docs/` and `data/` into `_site/` for Pages |

To run manually:

```bash
pip install requests pyyaml
python scripts/collect.py
python scripts/collect_ci.py --days 8 --pipeline both --output data/vllm/ci/
python scripts/vllm/collect_analytics.py --days 30 --output data/vllm/ci/
python scripts/vllm/collect_amd_test_matrix.py --output data/vllm/ci/
python scripts/vllm/collect_gating_proposals.py --output data/vllm/ci/
python scripts/vllm/collect_gating_target_candidates.py --output data/vllm/ci/
python scripts/vllm/collect_capacity_monitor.py --output data/vllm/ci/
python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci/ --output data/vllm/ci/operations_v2.json
python scripts/vllm/audit_dashboard_data.py
python scripts/render.py
python scripts/build_site.py --cache-bust-index
```

Configure tracked projects in [`config/projects.yaml`](config/projects.yaml).

Private Buildkite collection requires `BUILDKITE_TOKEN` in the environment.
The static dashboard itself does not embed that token. Queue fields remain
explicitly unavailable when the API does not return their authoritative
source.

## Local Preview

```bash
python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci/ --output data/vllm/ci/operations_v2.json
python scripts/build_site.py --cache-bust-index
python -m http.server 8765 --bind 0.0.0.0 --directory _site
```

Open `http://127.0.0.1:8765/#projects`. The same server exposes every view;
for example, `#ci-health`, `#ci-analytics`, and `#ci-queue`.

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
