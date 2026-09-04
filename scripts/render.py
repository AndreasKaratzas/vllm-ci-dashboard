#!/usr/bin/env python3
"""Render Markdown dashboards from collected JSON data."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from vllm.github_home_bundle import publish_projects

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "projects.yaml"
DATA = ROOT / "data"
DASHBOARDS = ROOT / "dashboards"
SITE_DATA = ROOT / "data" / "site"


def load_json(path):
    """Load JSON file, return empty dict if missing."""
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def load_all_data(config):
    """Load all project data."""
    data = {}
    for name in config["projects"]:
        project_dir = DATA / name
        data[name] = {
            "prs": load_json(project_dir / "prs.json"),
            "issues": load_json(project_dir / "issues.json"),
            "releases": load_json(project_dir / "releases.json"),
        }
    return data


def render_readme(config, data):
    """Generate README.md overview dashboard."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Project Dashboard",
        "",
        f"Auto-updated tracking of AMD GPU ecosystem projects. Last updated: **{now}**",
        "",
        "## Overview",
        "",
        "| Project | Role | Latest Release | Open PRs | Open Issues | Links |",
        "|---------|------|----------------|----------|-------------|-------|",
    ]

    for name, cfg in config["projects"].items():
        d = data.get(name, {})
        role = "dev" if cfg["role"] == "active_dev" else "watch"

        # Latest release
        releases = d.get("releases", {}).get("releases", [])
        latest = releases[0]["tag_name"] if releases else "-"

        # Open PRs count
        prs = d.get("prs", {}).get("prs", [])
        open_prs = sum(1 for p in prs if p["state"] == "open")
        pr_str = str(open_prs) if open_prs else "-"

        # Open issues count
        issues = d.get("issues", {}).get("issues", [])
        open_issues = len(issues)
        issue_str = str(open_issues) if open_issues else "-"

        # Links
        repo_url = f"https://github.com/{cfg['repo']}"
        links = f"[repo]({repo_url})"
        if cfg.get("fork"):
            links += f" / [fork](https://github.com/{cfg['fork']})"

        lines.append(
            f"| **{name}** | {role} | {latest} | {pr_str} | {issue_str} | {links} |"
        )

    lines += [
        "",
        "## Live Dashboard",
        "",
        "Interactive dashboard with a **Home** view for PRs, project #39 issues, and test parity, plus CI operations views.",
        "",
        "Hosted on GitHub Pages. Pushes to `main` run CI; the production site and fresh",
        "operational data are published by the scheduled/dispatch",
        "`.github/workflows/hourly-master.yml` workflow or the manual Pages workflow.",
        "",
        "## Site Layout",
        "",
        "- `docs/` — static shell assets (HTML, CSS, JS)",
        "- `data/` — collector inputs and generated payloads; the site assembler publishes only the explicit public manifest",
        "- `scripts/build_site.py` — assembles `docs/` + `data/` into `_site/` for Pages deploys",
        "",
        "## Views",
        "",
        "| View | Description |",
        "|------|-------------|",
        "| **Home** | PRs, project #39 issues, and ROCm vs upstream test parity |",
        "| **CI Health** | Visual AMD runtime overview, main-only upstream parity, build-pinned logical AMD target health, live current-main AMD mirror inventory, separate reviewed-plan mapping diagnostics, and architecture drilldowns |",
        "| **CI Analytics** | AMD test health, precomputed 30-day flake/retry/latency comparison, nightlies, DNS, and agent health |",
        "| **Queue Monitor** | Buildkite queue workload, wait-time charts, active job overlays, and AMD capacity projections |",
        "| **Hotness / Omni** | Workload trajectories; exact Omni active-job evidence, 1h–3d queue windows, queued-age bands, daily deltas, and explicit partial-attribution labels |",
        "",
        "## Markdown Dashboards",
        "",
        "- [PR Tracker](dashboards/pr-tracker.md) — all tracked PRs across projects",
        "- [Weekly Digest](dashboards/weekly-digest.md) — weekly summary of releases, PRs, and issues",
        "- [Dashboard Audit](dashboards/dashboard-audit.md) — source-of-truth map and hidden-bug checklist",
        "",
        "## Data Collection",
        "",
        "The main data path is `.github/workflows/hourly-master.yml`, which targets a two-hour cadence. A 15-minute publication watchdog begins recovery at 95 minutes of publication age, leaving bounded execution headroom before the three-hour site-health limit if a scheduled run is delayed or dropped. Queue evidence is collected independently on a best-effort 10-minute GitHub Actions schedule and published to the dedicated `queue-data` branch; the Queue UI reads the freshest of that live feed and the canonical Pages snapshot. Unrelated dashboard audits and full-site deployment locks therefore cannot discard or delay queue observations.",
        "",
        "Buildkite-native p50/p95 remain the site-comparable queue series. Percentiles reconstructed from the separately fetched scheduled-job population are retained and charted with their own labels and n/N coverage; they never silently replace native values.",
        "",
        "| Script | Purpose |",
        "|--------|---------|",
        "| `scripts/collect.py` | vLLM PRs, project #39 issues, linked CI PR tags, releases |",
        "| `scripts/collect_ci.py` | Buildkite nightly test results, CI health, parity, flaky/failure data |",
        "| `scripts/vllm/collect_analytics.py` | Windowed CI analytics from parsed test-result JSONL plus Buildkite metadata |",
        "| `scripts/vllm/collect_amd_test_matrix.py` | AMD hardware matrix from upstream `test-amd.yaml`, matched against the latest AMD nightly |",
        "| `scripts/vllm/collect_ownership_parity.py` | Build a source-area parity map from the exact vLLM commit used by the latest AMD nightly |",
        "| `scripts/vllm/build_test_group_parity.py` | Validate and publish the reviewed upstream CUDA-to-ROCm logical test-group inventory |",
        "| `scripts/vllm/collect_gating_proposals.py` | Open vLLM PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors |",
        "| `scripts/vllm/collect_gating_targets.py` | Regenerate the canonical AMD parity target snapshot from `config/vllm_amd_gating_targets.json` |",
        "| `scripts/vllm/collect_gating_target_candidates.py` | Review-only audit of upstream nightly GPU jobs against the canonical AMD parity target list, including authorized `%N` shard expansion |",
        "| `scripts/vllm/collect_queue_snapshot.py` | Queue timeseries, workload-attributed counts, and the exact active-job ledger |",
        "| `scripts/vllm/collect_capacity_monitor.py` | AMD queue capacity limits plus mirror test-group dependency projections |",
        "| `scripts/vllm/build_operations_snapshot.py` | Build the versioned operations manifest and lazy CI Health, Queue, and Omni read-model shards |",
        "| `scripts/vllm/build_queue_section.py` | Build only the live Queue read-model shard for the independent queue publisher |",
        "| `scripts/vllm/ci_area_regression_watcher.py` | Reconcile one dashboard-repository issue per regressing test area using the ranked owner chain, regional working hours, and exact AMD evidence |",
        "| `scripts/vllm/sync_ci_operations_project.py` | Add open managed dashboard issues to the single AMD CI Operations Project by workstream |",
        "| `scripts/vllm/ensure_ci_operations_labels.py` | Ensure the managed-issue and Project workstream labels exist before any watcher runs |",
        "| `scripts/vllm/audit_dashboard_data.py` | Cross-surface audit for data totals, frontend assumptions, links, and deploy safety |",
        "| `scripts/render.py` | Generate markdown dashboards and site data |",
        "| `scripts/build_site.py` | Assemble `docs/` and `data/` into `_site/` for Pages |",
        "",
        "To run manually:",
        "",
        "```bash",
        "pip install requests pyyaml",
        "python scripts/collect.py",
        "python scripts/collect_ci.py --days 8 --pipeline both --output data/vllm/ci/",
        "python scripts/vllm/collect_analytics.py --days 30 --output data/vllm/ci/",
        "python scripts/vllm/collect_amd_test_matrix.py --output data/vllm/ci/",
        "python scripts/vllm/collect_ownership_parity.py --input-dir data/vllm/ci --output data/vllm/ci",
        "python scripts/vllm/build_test_group_parity.py --output data/vllm/ci/",
        "python scripts/vllm/collect_gating_proposals.py --output data/vllm/ci/",
        "python scripts/vllm/collect_gating_targets.py --output data/vllm/ci/",
        "python scripts/vllm/collect_gating_target_candidates.py --output data/vllm/ci/",
        "python scripts/vllm/collect_queue_snapshot.py",
        "python scripts/vllm/collect_capacity_monitor.py --output data/vllm/ci/",
        "python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci --output data/vllm/ci/operations_v2.json.gz",
        "python scripts/vllm/build_queue_section.py",
        "python scripts/vllm/ci_area_regression_watcher.py",
        "python scripts/vllm/sync_ci_operations_project.py",
        "python scripts/vllm/audit_dashboard_data.py --strict-warnings",
        "python scripts/render.py",
        "python scripts/build_site.py --cache-bust-index",
        "```",
        "",
        "Configure tracked projects in [`config/projects.yaml`](config/projects.yaml).",
        "The authoritative AMD parity target configuration is",
        "`config/vllm_amd_gating_targets.json`; `gating_targets.json` is regenerated",
        "from it on every canonical run. `operations_v2.json.gz` is a private, bounded build input,",
        "while the allowlisted operations manifest and lazy shards are generated public",
        "artifacts. Canonical deployments replace `gh-pages`, so retired artifacts are",
        "purged instead of surviving indefinitely. Do not hand-edit or delete generated",
        "data solely because its commit timestamp is old; the dashboard audit validates",
        "that every high-value input still has a producer and consumer.",
        "",
        "The reviewed upstream CUDA-to-ROCm test-group inventory lives in",
        "`config/vllm_upstream_test_group_parity.json`. Its validated public",
        "projection is `data/vllm/ci/test_group_parity.json`, with physical and logical",
        "upstream totals, applicable coverage on the reviewed main snapshot, separate",
        "ROCm physical/logical inventory counts, area",
        "summaries, and all reviewed group rows kept distinct. It does not track a",
        "hard-coded pull request or expose matcher-link bookkeeping as parity.",
        "",
        "Organization-wide OSS rollups should consume the versioned",
        "[`org_summary.json`](https://andreaskaratzas.github.io/vllm-ci-dashboard/data/vllm/ci/org_summary.json)",
        "artifact. It keeps observed logical test groups, exact job variants, the",
        "reviewed upstream parity inventory, best-hardware test-group health, scheduled",
        "cohorts, reviewed parity targets, and queue activity as separate populations.",
        "Schema v6 exposes these as `test_groups`, `test_group_parity`, `health_checks`,",
        "`scheduled_cohorts`, and `parity_targets`; it does not combine them under a",
        "generic gating count. `queues.daily_served_job_waits.days` remains the compact",
        "UTC-day index. Its `source` object points to the exact vectors already published in",
        "`queue_lifecycle.json`; consumers should follow `source.path`, `source.key`, and",
        "`source.vector_key`. This replaces schema v3's duplicated inline vectors without",
        "sampling them or reducing them to a daily average or percentile.",
        "",
        "Runtime target results follow a fail-closed identity chain: exact build-pinned",
        "AMD matrix labels first, then current upstream-to-AMD definition-parity aliases.",
        "Only reviewed labels ending in `%N` may absorb numbered runtime shards; unrelated",
        "numeric suffixes and GPU counts remain distinct. Colliding matrix rows are merged",
        "with hard/soft incidents taking precedence over passes, while retaining every",
        "exact Buildkite link. A target with no selected result is classified separately",
        "as lacking a one-to-one AMD definition, mapping review, ambiguous, or defined but not observed;",
        "the CI Health drawer shows that reason, the matched AMD labels, and source-commit",
        "alignment.",
        "",
        "### CI ownership and regression issues",
        "",
        "[`config/vllm_ci_ownership.json`](config/vllm_ci_ownership.json) is the",
        "authoritative 31-area ranked routing map. Each area has one to three active",
        "owners. The hourly workflow evaluates every exact AMD matrix definition,",
        "attributes each definition through",
        "a parity snapshot pinned to that nightly's exact vLLM commit, and reconciles one state-owned issue",
        "per area in `AndreasKaratzas/vllm-ci-dashboard`. Current regressions, exact",
        "Buildkite evidence, upstream parity gaps, the ranked chain, and the actual",
        "GitHub assignees are shown on the managed area issues and AMD CI Operations project.",
        "",
        "The ownership config carries two shared, DST-aware working-hours profiles.",
        "They are operational shifts, not claims about an engineer's home location. EU",
        "uses Serbia time (`Europe/Belgrade`), while NA uses Chicago time",
        "(`America/Chicago`):",
        "",
        "| Profile | Local hours | Time zone | Engineers |",
        "|---|---|---|---|",
        "| EU | Monday–Friday, 09:00–17:00 | `Europe/Belgrade` (Serbia) | `gchinora`, `stefankoncarevic`, `fxmarty-amd` |",
        "| NA | Monday–Friday, 09:00–17:00 | `America/Chicago` | `aarushjain29`, `divakar-amd`, `micah-wil`, `mawong-amd`, `AndreasKaratzas` |",
        "",
        "Assignment uses only these committed regional schedules. The watcher walks",
        "each area's configured ranks in ascending order and selects the first owner",
        "currently inside that profile's working",
        "hours. If every ranked owner is outside working hours, or a schedule cannot be",
        "evaluated safely, assignment falls back to the CI lead. The watcher also verifies",
        "that the selected login can be assigned in this repository; otherwise it",
        "assigns the CI lead. If neither account is verifiably assignable, the watcher",
        "refuses to open an unassigned issue. Each regression issue tags the selected",
        "owner and verified assignee, then CCs every remaining ranked area owner exactly",
        "once. No issue can be opened outside the dashboard repository.",
        "",
        "Use one GitHub Project, **AMD CI Operations**, with label-backed views instead",
        "of three separate projects: `workstream:infra`, `workstream:dashboard-ci`, and",
        "`workstream:dev`. This keeps one lifecycle per incident while still providing",
        "the requested Infra, dashboard CI, and development queues. The Project sync",
        "requires a `PROJECTS_WRITE_TOKEN` Actions secret with Projects V2 write access;",
        "for a classic PAT this is the `project` scope, and the token owner must be able",
        "to update Andreas Karatzas's Project. The repository-scoped `GITHUB_TOKEN`",
        "cannot update a user-owned Project. If the secret is absent, issue",
        "reconciliation and dashboard deployment continue while Project synchronization",
        "reports a safe no-op.",
        "",
        "During credential rotation, the guarded Project-sync step accepts the existing",
        "`PROJECTS" + "_TOKEN` only as a fallback when `PROJECTS_WRITE_TOKEN` is absent. The",
        "fallback is confined to the repository/project-validated add-item script;",
        "install the scoped replacement and remove the legacy secret when rotation is",
        "complete.",
        "",
        "The hourly GitHub collector reads public `vllm-project` Project #39 and refreshes",
        "`project_items.json` as a read-only fallback for the Home issue list.",
        "`PROJECTS_READ_TOKEN` is optional and only raises the API rate limit. Dashboard",
        "automation never creates or updates Project #39 issues, comments, or fields.",
        "",
        "The canonical operational queue is the **AMD CI Operations** project and its managed area issues. Project #39",
        "supplies read-only issue evidence, while area issues provide the exact latest-nightly",
        "ownership queue. The existing `amd-main-failure` issue remains a broad all-main",
        "rollup; these evidence scopes are intentionally distinct.",
        "",
        "## Local development (Nix)",
        "",
        "A Nix flake pins Python, Node, and every CLI the collectors / linters",
        "need, so you do not have to manage a venv or a global `npm i -g`.",
        "",
        "```bash",
        "# One-time: enable flakes + nix-command if you haven't already.",
        "nix develop            # or: direnv allow  (with .envrc)",
        "```",
        "",
        "The default `devShells.default` (`dashboard`) gives you Python 3.12",
        "(`uv`-managed), Node 22, `prettier`, `cspell`, `gh`, `git-lfs`, `jq`,",
        "`yq-go`, `shellcheck`, `yamllint`, `actionlint`, and `act` for running",
        "workflows locally. The shell hook wires up shortcut functions:",
        "",
        "| Function | What it does |",
        "|----------|--------------|",
        "| `dash-collect` | Run the local collector pipeline (`collect.py`, `collect_activity.py`, `collect_ci.py`) |",
        "| `dash-render` | Regenerate `data/site/projects.json` and markdown dashboards |",
        "| `dash-test` | Run the pytest suite |",
        "| `dash-clean` | Remove generated artifacts (`_site/`, caches) |",
        "| `dash-lint-js` / `dash-fmt-js` | `cspell` + `prettier` over `docs/assets/js` |",
        "| `dash-lint-workflows` | `actionlint` + `yamllint` over `.github/workflows` |",
        "| `dash-lint-shell` | `shellcheck` over tracked shell scripts |",
        "| `dash-lint-spell` | `cspell` over docs, scripts, tests, and workflows |",
        "",
        "For a minimal shell with only Python + the collector deps, use",
        "`nix develop .#minimal`.",
        "",
    ]

    readme_path = ROOT / "README.md"
    readme_path.write_text("\n".join(lines))
    print(f"Generated {readme_path}")


def render_pr_tracker(config, data):
    """Generate PR tracker dashboard."""
    lines = [
        "# PR Tracker",
        "",
        "All tracked PRs across projects, grouped by project.",
        "",
    ]

    for name, cfg in config["projects"].items():
        d = data.get(name, {})
        prs = d.get("prs", {}).get("prs", [])
        collected = d.get("prs", {}).get("collected_at", "unknown")

        role_label = "Active Development" if cfg["role"] == "active_dev" else "Upstream Watch"
        lines.append(f"## {name} ({role_label})")
        lines.append(f"Repo: `{cfg['repo']}` | Last collected: {collected}")
        lines.append("")

        if not prs:
            lines.append("_No tracked PRs._")
            lines.append("")
            continue

        lines.append("| # | Title | Author | Status | Created | Updated |")
        lines.append("|---|-------|--------|--------|---------|---------|")

        for pr in prs:
            num = pr["number"]
            title = pr["title"][:60]
            if len(pr["title"]) > 60:
                title += "..."
            author = pr["author"]
            url = pr["html_url"]

            if pr.get("merged"):
                status = "merged"
            elif pr["state"] == "closed":
                status = "closed"
            elif pr.get("draft"):
                status = "draft"
            else:
                status = "open"

            created = pr["created_at"][:10]
            updated = pr["updated_at"][:10]

            lines.append(
                f"| [#{num}]({url}) | {title} | @{author} | {status} | {created} | {updated} |"
            )

        lines.append("")

    DASHBOARDS.mkdir(parents=True, exist_ok=True)
    path = DASHBOARDS / "pr-tracker.md"
    path.write_text("\n".join(lines))
    print(f"Generated {path}")


def render_weekly_digest(config, data):
    """Generate weekly digest dashboard."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d")
    now_str = now.strftime("%Y-%m-%d")

    lines = [
        "# Weekly Digest",
        "",
        f"Week of {week_ago_str} to {now_str}",
        "",
    ]

    # New releases this week
    lines.append("## New Releases")
    lines.append("")
    any_releases = False
    for name, cfg in config["projects"].items():
        d = data.get(name, {})
        releases = d.get("releases", {}).get("releases", [])
        for r in releases:
            pub = r.get("published_at", "")
            if pub and pub[:10] >= week_ago_str:
                tag = r["tag_name"]
                url = r.get("html_url", "")
                link = f"[{tag}]({url})" if url else tag
                lines.append(f"- **{name}**: {link}")
                any_releases = True
    if not any_releases:
        lines.append("_No new releases this week._")
    lines.append("")

    # PRs opened/merged this week
    lines.append("## PRs This Week")
    lines.append("")
    any_prs = False
    for name, cfg in config["projects"].items():
        d = data.get(name, {})
        prs = d.get("prs", {}).get("prs", [])

        opened = [p for p in prs if p["created_at"][:10] >= week_ago_str]
        merged = [
            p
            for p in prs
            if p.get("merged") and p["updated_at"][:10] >= week_ago_str
        ]

        if opened or merged:
            any_prs = True
            lines.append(f"### {name}")
            for p in opened:
                lines.append(
                    f"- Opened: [#{p['number']}]({p['html_url']}) {p['title'][:60]} (@{p['author']})"
                )
            for p in merged:
                if p not in opened:
                    lines.append(
                        f"- Merged: [#{p['number']}]({p['html_url']}) {p['title'][:60]} (@{p['author']})"
                    )
            lines.append("")

    if not any_prs:
        lines.append("_No PR activity this week._")
        lines.append("")

    # New issues this week
    lines.append("## New Issues This Week")
    lines.append("")
    any_issues = False
    for name, cfg in config["projects"].items():
        d = data.get(name, {})
        issues = d.get("issues", {}).get("issues", [])
        recent = [i for i in issues if i["created_at"][:10] >= week_ago_str]
        if recent:
            any_issues = True
            lines.append(f"### {name}")
            for i in recent:
                lines.append(
                    f"- [#{i['number']}]({i['html_url']}) {i['title'][:60]} (@{i['author']})"
                )
            lines.append("")

    if not any_issues:
        lines.append("_No new tracked issues this week._")
        lines.append("")

    DASHBOARDS.mkdir(parents=True, exist_ok=True)
    path = DASHBOARDS / "weekly-digest.md"
    path.write_text("\n".join(lines))
    print(f"Generated {path}")


def render_site_data(config):
    """Generate data/site/projects.json for the GitHub Pages dashboard."""
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    out = {"projects": {}}
    for name, cfg in config["projects"].items():
        out["projects"][name] = {
            "repo": cfg["repo"],
            "role": cfg["role"],
        }
        if cfg.get("fork"):
            out["projects"][name]["fork"] = cfg["fork"]
        if "depends_on" in cfg:
            out["projects"][name]["depends_on"] = cfg["depends_on"]
        if "build_workflows" in cfg:
            out["projects"][name]["build_workflows"] = cfg["build_workflows"]
    path = SITE_DATA / "projects.json"
    publish_projects(path, out)
    print(f"Generated {path}")


def main():
    with open(CONFIG) as f:
        config = yaml.safe_load(f)

    data = load_all_data(config)

    render_readme(config, data)
    render_pr_tracker(config, data)
    render_weekly_digest(config, data)
    render_site_data(config)

    print("Rendering complete.")


if __name__ == "__main__":
    main()
