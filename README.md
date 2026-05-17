# Project Dashboard

Auto-updated tracking of AMD GPU ecosystem projects. Last updated: **2026-05-17 00:06 UTC**

## Overview

| Project | Role | Latest Release | Open PRs | Open Issues | Links |
|---------|------|----------------|----------|-------------|-------|
| **vllm** | watch | v0.21.0 | 5 | 16 | [repo](https://github.com/vllm-project/vllm) / [fork](https://github.com/sunway513/vllm) |

## Live Dashboard

Interactive dashboard with a **Home** view for PRs, project #39 issues, and test parity, plus CI operations views.

Hosted on GitHub Pages — deployed automatically on every push to main.

## Site Layout

- `docs/` — static shell assets (HTML, CSS, JS)
- `data/` — published JSON payloads fetched by the shell at runtime, including `data/site/projects.json`
- `scripts/build_site.py` — assembles `docs/` + `data/` into `_site/` for Pages deploys

## Views

| View | Description |
|------|-------------|
| **Home** | PRs, project #39 issues, and ROCm vs upstream test parity |
| **CI Health** | Latest Buildkite nightly health, parity details, failures, flakes, and links |
| **CI Analytics** | Nightly build comparison, recent builds, group trends, AMD hardware matrix, queue comparison |
| **Queue Monitor** | Buildkite queue workload, wait-time charts, active job overlays, and admin triage |
| **Hotness / Omni / Ready / Admin** | Focused operational views for workload spikes, Omni queues, ready tickets, and dashboard admin tasks |

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
| `scripts/vllm/collect_amd_test_matrix.py` | AMD hardware matrix from upstream `test-amd.yaml`, matched against the latest AMD nightly |
| `scripts/vllm/collect_queue_snapshot.py` | Queue timeseries and active job overlays |
| `scripts/vllm/audit_dashboard_data.py` | Cross-surface audit for data totals, frontend assumptions, links, and deploy safety |
| `scripts/render.py` | Generate markdown dashboards and site data |
| `scripts/build_site.py` | Assemble `docs/` and `data/` into `_site/` for Pages |

To run manually:

```bash
pip install requests pyyaml
python scripts/collect.py
python scripts/collect_ci.py --days 8 --pipeline both --output data/vllm/ci/
python scripts/vllm/collect_analytics.py --days 14 --output data/vllm/ci/
python scripts/vllm/collect_amd_test_matrix.py --output data/vllm/ci/
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
