# Project Dashboard

Auto-updated tracking of AMD GPU ecosystem projects. Last updated: **2026-07-13 04:07 UTC**

## Overview

| Project | Role | Latest Release | Open PRs | Open Issues | Links |
|---------|------|----------------|----------|-------------|-------|
| **vllm** | watch | v0.25.0 | - | 3 | [repo](https://github.com/vllm-project/vllm) / [fork](https://github.com/sunway513/vllm) |

## Live Dashboard

**Signal Desk v2** is an AMD-first CI operations dashboard. It connects nightly regressions, gating coverage, retry and mixed-outcome evidence, group latency, queue capacity, Omni demand, and performance evaluation through one operational interface. Upstream data appears only where it supplies parity context. Perf Eval keeps its dedicated webhook-fed data path while rendering in the shared v2 shell.

Hosted on GitHub Pages — deployed automatically on every push to main.

## Site Layout

- `docs/` — static shell assets (HTML, CSS, JS)
- `data/` — published JSON payloads fetched by the shell at runtime, including `data/site/projects.json`
- `scripts/build_site.py` — assembles `docs/` + `data/` into `_site/` for Pages deploys

## Views

| View | Description |
|------|-------------|
| **Home** | AMD command center, attention queue, nightly movement, and engineering workbench |
| **CI Health** | Current build state, 127-group active target coverage, hardware-cell matrix, and diagnostics |
| **CI Analytics** | AMD nightly regressions, new/recurring/fixed groups, retries, flaky candidates, and completion latency |
| **Queue Monitor** | Queue-native running/waiting counts, official p50/p95, sampled p99, history, and current workloads |
| **CI Workload Trajectory** | AMD demand composition, execution frequency, latency, and failure pressure |
| **Omni** | vLLM-Omni demand, surge thresholds, AMD resource distribution, and active jobs |
| **Perf Eval** | AMD performance and accuracy trends with model/hardware filters, semantic deltas, source-build provenance, and per-metric history |

### Reliability evidence

The dashboard deliberately separates two signals:

- **Mixed-outcome candidate**: the same normalized AMD test group has at least one passing and one hard/soft incident observation in the retained nightly window. The displayed incident rate is incident observations divided by observed group runs; it is not presented as a test-case flake probability.
- **Explicit retry recovery**: Buildkite retry metadata links a failed attempt to a passing retry in the same build. This is stronger retry evidence, but remains distinct from an individual pytest case proven flaky.

Per-run dialogs list every contributing Buildkite build and job-log URL, normalized result, queue, completion time, parsed test count, and retry metadata. Group outcomes combine Buildkite job state with summaries parsed from collected test logs.

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
| `scripts/vllm/collect_analytics.py` | Windowed CI analytics from parsed test-result JSONL plus Buildkite metadata |
| `scripts/vllm/build_operations_snapshot.py` | Compact v2 read model joining nightly transitions, reliability, gating, queues, trajectory, and Omni |
| `scripts/vllm/collect_amd_test_matrix.py` | AMD hardware matrix from upstream `test-amd.yaml`, matched against the latest AMD nightly |
| `scripts/vllm/collect_gating_proposals.py` | Open vLLM PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors |
| `scripts/vllm/collect_gating_target_candidates.py` | Review-only audit of upstream nightly GPU jobs against the canonical AMD gating target list |
| `scripts/vllm/collect_queue_snapshot.py` | Queue-native counts and official p50/p95/max plus sampled active-job percentiles; p99 is never fabricated |
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
python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci --output data/vllm/ci/operations_v2.json
python scripts/vllm/audit_dashboard_data.py
python scripts/render.py
python scripts/build_site.py --cache-bust-index
```

Configure tracked projects in [`config/projects.yaml`](config/projects.yaml).

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
