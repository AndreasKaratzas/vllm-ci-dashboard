#!/usr/bin/env python3
"""Render Markdown dashboards from collected JSON data."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

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
        "| **CI Health** | Latest exact AMD job variants by architecture, separately reviewed runtime targets, definition parity, diagnostics, and evidence links |",
        "| **CI Analytics** | Nightly build comparison, recent builds, group trends, AMD hardware matrix, queue comparison |",
        "| **Queue Monitor** | Buildkite queue workload, wait-time charts, active job overlays, admin triage, and AMD capacity projections |",
        "| **Hotness / Omni / Ready / Admin** | Workload trajectories; exact Omni active-job evidence, 1h–3d queue windows, queued-age bands, daily deltas, and explicit partial-attribution labels; ready tickets; and admin tasks |",
        "",
        "## Markdown Dashboards",
        "",
        "- [PR Tracker](dashboards/pr-tracker.md) — all tracked PRs across projects",
        "- [Weekly Digest](dashboards/weekly-digest.md) — weekly summary of releases, PRs, and issues",
        "- [Dashboard Audit](dashboards/dashboard-audit.md) — source-of-truth map and hidden-bug checklist",
        "",
        "## Data Collection",
        "",
        "The main data path is `.github/workflows/hourly-master.yml`, which runs every 30 minutes and serializes every Pages writer behind the shared `gh-pages-deploy` lock.",
        "",
        "| Script | Purpose |",
        "|--------|---------|",
        "| `scripts/collect.py` | vLLM PRs, project #39 issues, linked CI PR tags, releases |",
        "| `scripts/collect_ci.py` | Buildkite nightly test results, CI health, parity, flaky/failure data |",
        "| `scripts/vllm/collect_analytics.py` | Windowed CI analytics from parsed test-result JSONL plus Buildkite metadata |",
        "| `scripts/vllm/collect_amd_test_matrix.py` | AMD hardware matrix from upstream `test-amd.yaml`, matched against the latest AMD nightly |",
        "| `scripts/vllm/collect_gating_proposals.py` | Open vLLM PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors |",
        "| `scripts/vllm/collect_gating_targets.py` | Regenerate the canonical AMD gating target snapshot from `config/vllm_amd_gating_targets.json` |",
        "| `scripts/vllm/collect_gating_target_candidates.py` | Review-only audit of upstream nightly GPU jobs against the canonical AMD gating target list |",
        "| `scripts/vllm/collect_queue_snapshot.py` | Queue timeseries, workload-attributed counts, and the exact active-job ledger |",
        "| `scripts/vllm/collect_capacity_monitor.py` | AMD queue capacity limits plus mirror test-group dependency projections |",
        "| `scripts/vllm/build_operations_snapshot.py` | Build the versioned operations manifest and lazy CI Health, Queue, and Omni read-model shards |",
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
        "python scripts/vllm/collect_gating_proposals.py --output data/vllm/ci/",
        "python scripts/vllm/collect_gating_targets.py --output data/vllm/ci/",
        "python scripts/vllm/collect_gating_target_candidates.py --output data/vllm/ci/",
        "python scripts/vllm/collect_queue_snapshot.py",
        "python scripts/vllm/collect_capacity_monitor.py --output data/vllm/ci/",
        "python scripts/vllm/build_operations_snapshot.py --input-dir data/vllm/ci --output data/vllm/ci/operations_v2.json",
        "python scripts/vllm/audit_dashboard_data.py --strict-warnings",
        "python scripts/render.py",
        "python scripts/build_site.py --cache-bust-index",
        "```",
        "",
        "Configure tracked projects in [`config/projects.yaml`](config/projects.yaml).",
        "The authoritative AMD gating target configuration is",
        "`config/vllm_amd_gating_targets.json`; `gating_targets.json` is regenerated",
        "from it on every canonical run. `operations_v2.json` is a private build input,",
        "while the allowlisted operations manifest and lazy shards are generated public",
        "artifacts. Canonical deployments replace `gh-pages`, so retired artifacts are",
        "purged instead of surviving indefinitely. Do not hand-edit or delete generated",
        "data solely because its commit timestamp is old; the dashboard audit validates",
        "that every high-value input still has a producer and consumer.",
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
    path.write_text(json.dumps(out, indent=2) + "\n")
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
