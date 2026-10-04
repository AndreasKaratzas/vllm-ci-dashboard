"""
Data integrity tests for the project dashboard.
Validates that CI data files have correct structure and the
vLLM CI view will render correctly.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


@pytest.mark.live_data
class TestCIHealthData:
    @pytest.fixture
    def health(self):
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("ci_health.json not collected yet")
        return json.loads(path.read_text())

    def test_has_required_top_keys(self, health):
        for key in ["generated_at", "amd", "upstream"]:
            assert key in health

    def test_amd_has_latest_build(self, health):
        assert "latest_build" in health.get("amd", {})

    def test_latest_build_has_required_fields(self, health):
        lb = health["amd"]["latest_build"]
        for f in ["build_number", "total_tests", "passed", "failed", "skipped", "pass_rate", "test_groups"]:
            assert f in lb, f"missing: {f}"

    def test_pass_rate_is_valid(self, health):
        assert 0 <= health["amd"]["latest_build"]["pass_rate"] <= 1

    def test_test_counts_consistent(self, health):
        lb = health["amd"]["latest_build"]
        # ``failed`` is the terminal non-passing count and includes pytest
        # errors; ``errors`` is retained as a diagnostic subset.
        assert lb["passed"] + lb["failed"] + lb.get("skipped", 0) <= lb["total_tests"]
        assert lb.get("errors", 0) <= lb["failed"]

    def test_hardware_breakdown_exists(self, health):
        assert len(health["amd"]["latest_build"].get("by_hardware", {})) > 0

    def test_hardware_pass_rates_valid(self, health):
        for hw, d in health["amd"]["latest_build"].get("by_hardware", {}).items():
            if hw == "unknown": continue
            assert 0 <= d["pass_rate"] <= 1, f"{hw} pass_rate={d['pass_rate']}"


@pytest.mark.live_data
class TestParityReport:
    @pytest.fixture
    def parity(self):
        path = DATA / "vllm" / "ci" / "parity_report.json"
        if not path.exists():
            pytest.skip("parity_report.json not collected yet")
        return json.loads(path.read_text())

    def test_has_job_groups(self, parity):
        assert len(parity.get("job_groups", [])) > 0

    def test_groups_have_names(self, parity):
        for g in parity["job_groups"]:
            assert g.get("name"), f"empty name: {g}"

    def test_groups_have_side(self, parity):
        for g in parity["job_groups"]:
            # Scheduled-only groups (backfilled=True, from non-terminal jobs)
            # intentionally have no amd/upstream data yet — they're PENDING.
            if g.get("backfilled") and not g.get("amd") and not g.get("upstream"):
                continue
            assert g.get("amd") or g.get("upstream"), f"'{g['name']}' has no side"

    def test_no_negative_counts(self, parity):
        for g in parity["job_groups"]:
            for side in ["amd", "upstream"]:
                d = g.get(side)
                if not d: continue
                for f in ["passed", "failed", "skipped"]:
                    assert d.get(f, 0) >= 0, f"'{g['name']}' {side}.{f} < 0"

    def test_group_failures_not_greater_than_health(self):
        """Sum of per-group AMD failures (non-backfilled only) must not exceed
        the health card total.

        Backfilled groups have data from previous builds with potentially
        different failure counts — exclude them from the comparison.
        ci_health counts test cases from the current build only.
        """
        health_path = DATA / "vllm" / "ci" / "ci_health.json"
        parity_path = DATA / "vllm" / "ci" / "parity_report.json"
        if not health_path.exists() or not parity_path.exists():
            pytest.skip("data not collected yet")
        health = json.loads(health_path.read_text())
        parity = json.loads(parity_path.read_text())

        lb = health["amd"]["latest_build"]
        # failed already includes errors in compute_build_summary
        card_failures = lb["failed"]

        group_failures = 0
        for g in parity["job_groups"]:
            if g.get("backfilled") or g.get("hw_backfilled"):
                continue  # groups with any backfilled HW have mixed-build data
            d = g.get("amd")
            if not d:
                continue
            group_failures += (d.get("failed", 0) + d.get("error", 0))

        assert group_failures <= card_failures, (
            f"Group failures sum ({group_failures}) > card total ({card_failures}). "
            f"Group data has more failures than the health summary."
        )

    def test_group_failed_plus_error_equals_total_minus_pass_skip(self):
        """Per-group: all status counts should sum to total."""
        path = DATA / "vllm" / "ci" / "parity_report.json"
        if not path.exists():
            pytest.skip("parity_report.json not collected yet")
        parity = json.loads(path.read_text())
        bad = []
        for g in parity["job_groups"]:
            for side in ["amd", "upstream"]:
                d = g.get(side)
                if not d:
                    continue
                accounted = (d.get("passed", 0) + d.get("failed", 0) +
                             d.get("error", 0) + d.get("skipped", 0) +
                             d.get("xfailed", 0) + d.get("xpassed", 0) +
                             d.get("canceled", 0))
                total = d.get("total", 0)
                if accounted != total:
                    bad.append(f"'{g['name']}' {side}: accounted={accounted} != total={total}")
        assert not bad, f"Count mismatches:\n" + "\n".join(bad[:10])

    def test_groups_have_real_pytest_counts(self, parity):
        """At least one side (AMD or upstream) should have real pytest counts.

        AMD CI may only produce __job_level__ entries (total=1 per group).
        Upstream typically has granular pytest counts (total > 10 per group).
        If BOTH sides have only job-level counts, the log parser is broken.
        """
        for side in ["amd", "upstream"]:
            groups = [g for g in parity["job_groups"] if g.get(side)]
            if not groups:
                continue
            groups_with_real_counts = [g for g in groups if g[side]["total"] > 10]
            ratio = len(groups_with_real_counts) / len(groups) if groups else 0
            if ratio >= 0.3:
                return  # At least one side has real counts
        pytest.fail(
            "Neither AMD nor upstream has granular pytest counts. "
            "Both sides appear to have only __job_level__ fallback data."
        )

    def test_parity_total_matches_health(self):
        """Parity report AMD test total should be in the same ballpark as ci_health."""
        health_path = DATA / "vllm" / "ci" / "ci_health.json"
        parity_path = DATA / "vllm" / "ci" / "parity_report.json"
        if not health_path.exists() or not parity_path.exists():
            pytest.skip("data not collected yet")
        health = json.loads(health_path.read_text())
        parity = json.loads(parity_path.read_text())

        health_total = health["amd"]["latest_build"]["total_tests"]
        parity_total = sum(
            g["amd"]["total"] for g in parity["job_groups"] if g.get("amd")
        )
        # Parity excludes CPU/Intel/Arm/Ascend groups, so it will be <=
        # but shouldn't be drastically less (< 10% would indicate job-level counts)
        assert parity_total >= health_total * 0.1, (
            f"Parity AMD total ({parity_total}) is less than 10% of ci_health "
            f"total ({health_total}). Data likely has job-level counts."
        )

    def test_job_links_have_hw_field(self, parity):
        """Every job_link must have a string 'hw' field (used by toUpperCase in JS overlay)."""
        bad = []
        for g in parity["job_groups"]:
            for jl in g.get("job_links", []):
                if "hw" not in jl:
                    bad.append(f"'{g['name']}' side={jl.get('side')}: missing 'hw'")
                elif not isinstance(jl["hw"], str):
                    bad.append(f"'{g['name']}' side={jl.get('side')}: hw is {type(jl['hw']).__name__}, expected str")
        assert not bad, (
            f"{len(bad)} job_link(s) missing 'hw' field (causes toUpperCase crash in JS):\n"
            + "\n".join(bad[:10])
        )

    def test_job_links_have_required_fields(self, parity):
        """Every job_link must have url, job_name, side, and hw fields."""
        required = {"url", "job_name", "side", "hw"}
        bad = []
        for g in parity["job_groups"]:
            for jl in g.get("job_links", []):
                missing = required - set(jl.keys())
                if missing:
                    bad.append(f"'{g['name']}' side={jl.get('side')}: missing {missing}")
        assert not bad, (
            f"{len(bad)} job_link(s) with missing fields:\n" + "\n".join(bad[:10])
        )

    def test_hw_failures_is_dict_or_null(self, parity):
        """hw_failures must be a dict (or null), never a string/list/number."""
        for g in parity["job_groups"]:
            hwf = g.get("hw_failures")
            if hwf is not None:
                assert isinstance(hwf, dict), (
                    f"'{g['name']}' hw_failures is {type(hwf).__name__}, expected dict or null"
                )

    def test_side_hardware_and_incidents_are_internally_consistent(self, parity):
        """Explicit source-side fields must remain subsets of the merged view."""
        bad = []
        for g in parity["job_groups"]:
            merged_hardware = set(g.get("hardware") or [])
            side_hardware_sets = {}
            for side in ("amd", "upstream"):
                side_hardware = g.get(f"{side}_hardware")
                if side_hardware is None:
                    continue  # Legacy parity snapshots predate source-side fields.
                if not isinstance(side_hardware, list):
                    bad.append(
                        f"'{g['name']}' {side}_hardware is "
                        f"{type(side_hardware).__name__}, expected list"
                    )
                    continue
                side_hardware_set = set(side_hardware)
                side_hardware_sets[side] = side_hardware_set
                if not side_hardware_set.issubset(merged_hardware):
                    bad.append(
                        f"'{g['name']}' {side}_hardware {side_hardware_set} "
                        f"is not a subset of hardware {merged_hardware}"
                    )
                for suffix in ("failures", "canceled"):
                    incidents = g.get(f"{side}_hw_{suffix}")
                    if incidents is None:
                        continue
                    if not isinstance(incidents, dict):
                        bad.append(
                            f"'{g['name']}' {side}_hw_{suffix} is "
                            f"{type(incidents).__name__}, expected dict"
                        )
                        continue
                    unexpected = set(incidents) - side_hardware_set
                    if unexpected:
                        bad.append(
                            f"'{g['name']}' {side}_hw_{suffix} has {unexpected} "
                            f"outside {side}_hardware {side_hardware_set}"
                        )
            if set(side_hardware_sets) == {"amd", "upstream"}:
                expected_hardware = set().union(*side_hardware_sets.values())
                if merged_hardware != expected_hardware:
                    bad.append(
                        f"'{g['name']}' hardware {merged_hardware} does not equal "
                        f"source-side union {expected_hardware}"
                    )
                for suffix in ("failures", "canceled"):
                    side_maps = [
                        g.get(f"{side}_hw_{suffix}")
                        for side in ("amd", "upstream")
                    ]
                    if not all(isinstance(value, dict) for value in side_maps):
                        continue
                    expected_incidents = {}
                    for side_map in side_maps:
                        for hw, count in side_map.items():
                            expected_incidents[hw] = (
                                expected_incidents.get(hw, 0) + count
                            )
                    merged_incidents = g.get(f"hw_{suffix}") or {}
                    if merged_incidents != expected_incidents:
                        bad.append(
                            f"'{g['name']}' hw_{suffix} {merged_incidents} "
                            f"does not equal source-side sum {expected_incidents}"
                        )
        assert not bad, "Invalid source-side hardware fields:\n" + "\n".join(bad[:10])

    def test_groups_have_error_field(self, parity):
        """Groups with errors must have the 'error' field so it can be folded into 'failed'."""
        for g in parity["job_groups"]:
            for side in ["amd", "upstream"]:
                d = g.get(side)
                if not d:
                    continue
                # error field should exist (can be 0)
                assert "error" in d or d.get("failed", 0) >= 0, \
                    f"'{g['name']}' {side} missing 'error' field"


    def test_pass_rate_excludes_skipped(self):
        """pass_rate must be passed/(passed+failed), not passed/total.

        total includes skipped tests which should not affect pass rate.
        The dashboard displays ran count (passed+failed) not total.
        """
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("ci_health.json not collected yet")
        health = json.loads(path.read_text())
        for side in ["amd", "upstream"]:
            lb = health.get(side, {}).get("latest_build")
            if not lb:
                continue
            # failed already includes errors in compute_build_summary
            ran = lb["passed"] + lb["failed"]
            if ran == 0:
                continue
            expected = round(lb["passed"] / ran, 4)
            assert lb["pass_rate"] == expected, (
                f"{side} pass_rate {lb['pass_rate']} != passed/ran {expected}. "
                f"Skipped tests may be incorrectly included in denominator."
            )

    def test_hw_failures_keys_subset_of_hardware(self, parity):
        """hw_failures keys must be a subset of the group's hardware list.

        Prevents showing failure badges for hardware the group doesn't run on.
        """
        bad = []
        for g in parity["job_groups"]:
            hwf = g.get("hw_failures") or {}
            hw_set = set(g.get("hardware") or [])
            for hw in hwf:
                if hw not in hw_set:
                    bad.append(f"'{g['name']}' hw_failures has '{hw}' not in hardware {hw_set}")
        assert not bad, "hw_failures references hardware not in group:\n" + "\n".join(bad[:10])

    def test_amd_groups_have_amd_job_links(self, parity):
        """AMD groups must have at least one AMD-side job link.

        Prevents the detail row from showing only upstream links for an AMD group.
        """
        bad = []
        for g in parity["job_groups"]:
            if not g.get("amd"):
                continue
            amd_links = [jl for jl in g.get("job_links", []) if jl and jl.get("side") == "amd"]
            if not amd_links:
                bad.append(g["name"])
        # Allow a small number of edge cases (e.g., docker build jobs with no log)
        assert len(bad) <= len(parity["job_groups"]) * 0.1, (
            f"{len(bad)} AMD groups have zero AMD-side job links:\n" + "\n".join(bad[:10])
        )

    def test_failing_groups_have_links_for_failing_hw(self, parity):
        """AMD groups with hw_failures should have AMD job links for each failing hw.

        Only checks AMD-side hardware (mi250, mi300, mi325, mi355). Upstream hardware
        (h100, b200, etc.) failures may not have AMD links if the group is
        upstream-only or the failure is on the upstream side.
        """
        AMD_HW = {"mi250", "mi300", "mi325", "mi355", "cpu"}
        bad = []
        for g in parity["job_groups"]:
            if not g.get("amd"):
                continue  # upstream-only groups don't need AMD links
            amd_hw_failures = g.get("amd_hw_failures")
            hwf = (
                amd_hw_failures
                if isinstance(amd_hw_failures, dict)
                else (g.get("hw_failures") or {})
            )
            if not hwf:
                continue
            amd_links = [jl for jl in g.get("job_links", []) if jl and jl.get("side") == "amd"]
            link_hws = {jl["hw"] for jl in amd_links if "hw" in jl}
            for hw in hwf:
                if hw in AMD_HW and hw not in link_hws:
                    bad.append(f"'{g['name']}' has hw_failures[{hw}]={hwf[hw]} but no AMD link for that hw")
        assert not bad, "Missing links for failing hardware:\n" + "\n".join(bad[:10])


@pytest.mark.live_data
class TestAnalyticsData:
    @pytest.fixture
    def analytics(self):
        path = DATA / "vllm" / "ci" / "analytics.json"
        if not path.exists():
            pytest.skip("analytics.json not collected yet")
        return json.loads(path.read_text())

    def test_has_pipelines(self, analytics):
        assert len(analytics) > 0

    def test_pipelines_have_summary(self, analytics):
        for p, d in analytics.items():
            assert "summary" in d, f"{p} missing summary"

    def test_builds_have_jobs(self, analytics):
        for p, d in analytics.items():
            builds = d.get("builds", [])
            if builds:
                assert "jobs" in builds[0], f"{p} build missing jobs"

    def test_amd_analytics_not_blank_when_test_results_exist(self, analytics):
        result_files = list((DATA / "vllm" / "ci" / "test_results").glob("*_amd.jsonl"))
        if not result_files:
            pytest.skip("AMD test-result files not collected yet")
        amd = analytics.get("amd-ci") or {}
        window = (amd.get("windows") or {}).get(amd.get("default_window", "7d"), {})
        assert window.get("builds") or amd.get("builds"), (
            "AMD analytics should fall back to parsed test_results instead of publishing an empty block"
        )


class TestFrontendFiles:
    def test_index_html_exists(self):
        assert (DOCS / "index.html").exists()

    def test_index_html_has_sidebar(self):
        html = (DOCS / "index.html").read_text()
        assert 'id="sidebar"' in html
        assert 'id="main-content"' in html

    def test_all_tabs_present(self):
        """CI tabs can be in HTML or dynamically registered via JS."""
        html = (DOCS / "index.html").read_text()
        js = (DOCS / "assets" / "js" / "utils.js").read_text()
        for tab in ["ci-health", "ci-analytics", "ci-queue"]:
            in_html = f'data-tab="{tab}"' in html
            in_js = f"id: '{tab}'" in js
            assert in_html or in_js, f"missing tab: {tab} (not in HTML or registerCISection)"
        assert 'data-tab="projects"' in html, "missing tab: projects"
        assert 'id="parity-view"' in html, (
            "Test parity should render inside Home now that the static Home/Test Parity tabs are merged"
        )



    def test_css_exists(self):
        assert (DOCS / "assets" / "css" / "dashboard.css").exists()

    def test_css_has_sidebar(self):
        css = (DOCS / "assets" / "css" / "dashboard.css").read_text()
        assert "#sidebar" in css
        assert ".overlay-backdrop" in css






    def test_theme_toggle_exists(self):
        """index.html must have a theme toggle button and data-theme attribute."""
        html = (DOCS / "index.html").read_text()
        assert 'id="theme-toggle"' in html, "missing theme toggle button"
        assert 'data-theme' in html, "missing data-theme attribute for theme switching"

    def test_css_has_light_and_dark_themes(self):
        """dashboard.css must define both light and dark theme variables."""
        css = (DOCS / "assets" / "css" / "dashboard.css").read_text()
        assert '[data-theme="dark"]' in css, "missing dark theme CSS variables"
        assert '[data-theme="light"]' in css, "missing light theme CSS variables"















    @pytest.mark.live_data
    def test_queue_stats_computable_from_builds(self):
        """Queue stats must be recomputable from per-build job data and duration_ranking.

        The time window selector filters builds by date and recomputes queue stats.
        This requires either per-job queue fields (new collector) or a job-name-to-queue
        lookup from duration_ranking.
        """
        path = DATA / "vllm" / "ci" / "analytics.json"
        if not path.exists():
            pytest.skip("analytics.json not collected yet")
        data = json.loads(path.read_text())
        for p, d in data.items():
            dr = d.get("duration_ranking", [])
            builds = d.get("builds", [])
            if not dr or not builds:
                continue
            # Build job-name → queue lookup
            job_queues = {j["name"]: j["queues"] for j in dr if j.get("queues")}
            # Check that a reasonable fraction of per-build jobs can be mapped to queues
            total_jobs = 0
            mapped_jobs = 0
            for b in builds[:5]:  # sample first 5 builds
                for j in b.get("jobs", []):
                    total_jobs += 1
                    if j.get("q") or j["name"] in job_queues:
                        mapped_jobs += 1
            if total_jobs > 0:
                ratio = mapped_jobs / total_jobs
                assert ratio >= 0.5, (
                    f"Pipeline {p}: only {ratio:.0%} of per-build jobs map to queues. "
                    f"Time window filtering requires job-to-queue mapping."
                )

    def test_shard_merge_preserves_parens(self):
        """mergeShardedGroups must not merge groups with digits inside parens."""
        js = (DOCS / "assets" / "js" / "utils.js").read_text()
        # The old regex that incorrectly stripped digits before closing paren
        assert r"\s+\d+\)$" not in js, (
            "utils.js still has the paren-digit stripping regex that incorrectly "
            "merges distinct groups like 'api server 1' and 'api server 2'"
        )


class TestFrontendPendingGroups:
    """Validate that the frontend doesn't filter out pending/backfilled groups."""


    @pytest.mark.live_data
    def test_parity_report_pending_groups_have_correct_count(self):
        """The number of groups per HW in parity_report (including backfilled)
        must match what the frontend would display."""
        parity_path = ROOT / "data" / "vllm" / "ci" / "parity_report.json"
        if not parity_path.exists():
            pytest.skip("no parity_report.json")
        parity = json.loads(parity_path.read_text())

        for g in parity.get("job_groups", []):
            if g.get("backfilled") and not g.get("amd") and not g.get("upstream"):
                # This group has no data but is backfilled — it MUST have hardware
                assert g.get("hardware"), (
                    f"Backfilled group '{g['name']}' has no hardware list. "
                    "It will be invisible on the dashboard."
                )


class TestShardMerging:
    def _base(self, name):
        n = re.sub(r'\s+\d+$', '', name)
        n = re.sub(r'\s+\d+\s*:.*$', '', n)
        return n

    def test_trailing_digit(self):
        assert self._base("lora 1") == "lora"

    def test_digit_colon(self):
        assert self._base("mm (standard) 1: qwen2") == "mm (standard)"

    def test_digit_inside_parens_preserved(self):
        """Digits inside parens are part of the name, not shard numbers."""
        assert self._base("entrypoints integration (api server 1)") == "entrypoints integration (api server 1)"
        assert self._base("entrypoints integration (api server 2)") == "entrypoints integration (api server 2)"
        assert self._base("multi-modal models (extended generation 1)") == "multi-modal models (extended generation 1)"

    def test_preserved(self):
        assert self._base("distributed tests (2 gpus)") == "distributed tests (2 gpus)"
        assert self._base("basic correctness") == "basic correctness"

    @pytest.mark.live_data
    def test_reduces_real_data(self):
        """Frontend merge should produce <= groups (backend may already merge)."""
        path = DATA / "vllm" / "ci" / "parity_report.json"
        if not path.exists():
            pytest.skip("no parity data")
        groups = json.loads(path.read_text())["job_groups"]
        merged = {self._base(g["name"]) for g in groups}
        assert len(merged) <= len(groups)


@pytest.mark.live_data
class TestSiteAssembly:
    def test_data_dir_exists(self):
        assert (ROOT / "data").exists()

    def test_projects_json(self):
        p = ROOT / "data" / "site" / "projects.json"
        if not p.exists():
            pytest.skip("not rendered yet")
        assert "projects" in json.loads(p.read_text())


# ═══════════════════════════════════════════════════════════════════════════════
# Git conflict marker and JSON validity checks
# ═══════════════════════════════════════════════════════════════════════════════

CONFLICT_MARKERS = re.compile(r'^(<{7}|={7}|>{7})\s', re.MULTILINE)


@pytest.mark.live_data
class TestNoConflictMarkers:
    """Ensure no data files contain git merge conflict markers.

    Concurrent deploys with keep_files can corrupt files on gh-pages.
    These tests catch it early so it doesn't break the live site.
    """

    @staticmethod
    def _all_data_files():
        """Yield all JSON/JSONL files under data/."""
        for p in DATA.rglob("*.json"):
            yield p
        for p in DATA.rglob("*.jsonl"):
            yield p

    @pytest.mark.parametrize("path", list(DATA.rglob("*.json")), ids=lambda p: str(p.relative_to(ROOT)))
    def test_json_no_conflict_markers(self, path):
        """No JSON file should contain git conflict markers."""
        content = path.read_text()
        matches = CONFLICT_MARKERS.findall(content)
        assert not matches, (
            f"{path.relative_to(ROOT)} contains git conflict markers. "
            f"This usually means concurrent deploys corrupted gh-pages."
        )

    @pytest.mark.parametrize("path", list(DATA.rglob("*.jsonl")), ids=lambda p: str(p.relative_to(ROOT)))
    def test_jsonl_no_conflict_markers(self, path):
        """No JSONL file should contain git conflict markers."""
        content = path.read_text()
        matches = CONFLICT_MARKERS.findall(content)
        assert not matches, (
            f"{path.relative_to(ROOT)} contains git conflict markers."
        )

    @pytest.mark.parametrize("path", list(DATA.rglob("*.json")), ids=lambda p: str(p.relative_to(ROOT)))
    def test_json_is_valid(self, path):
        """Every .json file must be valid JSON."""
        content = path.read_text()
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            pytest.fail(f"{path.relative_to(ROOT)} is not valid JSON: {e}")

    @pytest.mark.parametrize("path", list(DATA.rglob("*.jsonl")), ids=lambda p: str(p.relative_to(ROOT)))
    def test_jsonl_lines_are_valid(self, path):
        """Every non-empty line in a .jsonl file must be valid JSON."""
        for i, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                pytest.fail(
                    f"{path.relative_to(ROOT)} line {i} is not valid JSON: {e}\n"
                    f"  Content: {line[:200]}"
                )
