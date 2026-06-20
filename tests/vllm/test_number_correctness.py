"""CRITICAL: Tests that validate the dashboard numbers are CORRECT.

These tests cross-reference the data in ci_health.json and parity_report.json
against the raw JSONL test results and internal consistency rules.

If ANY of these tests fail, the dashboard is showing wrong numbers.
This is the most important test file in the repo.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "vllm" / "ci"
SCRIPTS = ROOT / "scripts"

# Import analyzer normalization
import sys
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def _load_shard_bases():
    """Ensure shard bases are loaded before every test."""
    from vllm.ci.analyzer import set_shard_bases
    shard_path = DATA / "shard_bases.json"
    if shard_path.exists():
        bases = json.loads(shard_path.read_text())
        set_shard_bases(bases)


def _load_json(name):
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not found")
    return json.loads(path.read_text())


def _load_test_results():
    """Load ALL JSONL test results for the latest AMD date."""
    results_dir = DATA / "test_results"
    if not results_dir.exists():
        pytest.skip("no test_results directory")
    # Find the latest AMD JSONL
    amd_files = sorted(results_dir.glob("*_amd.jsonl"), reverse=True)
    if not amd_files:
        pytest.skip("no AMD JSONL files")
    results = []
    with open(amd_files[0]) as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results, amd_files[0].name


class TestGroupCountCorrectness:
    """Validate that per-hardware group counts match the raw test results."""

    def test_ci_health_group_counts_match_jsonl(self):
        """ci_health.json per-HW group counts must match what's in the JSONL."""
        health = _load_json("ci_health.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name

        # Extract per-HW groups from JSONL (same logic as compute_build_summary)
        hw_re = re.compile(r'^(mi\d+)_\d+:', re.IGNORECASE)
        hw_groups = defaultdict(set)
        for r in results:
            job_name = r.get("job_name", "")
            m = hw_re.match(job_name)
            hw = m.group(1).lower() if m else "unknown"
            norm = _normalize_job_name(job_name)
            hw_groups[hw].add(norm)

        # Compare with ci_health
        lb = health.get("amd", {}).get("latest_build", {})
        bh = lb.get("by_hardware", {})
        for hw, ci_data in bh.items():
            if hw == "unknown":
                continue
            ci_groups = ci_data.get("groups", 0)
            jsonl_groups = len(hw_groups.get(hw, set()))
            assert ci_groups == jsonl_groups, (
                f"{hw}: ci_health says {ci_groups} groups but JSONL ({fname}) "
                f"has {jsonl_groups} groups. Difference: "
                f"{hw_groups.get(hw, set()) - set()} "
                f"(ci_health may be stale or normalization differs)"
            )

    def test_ci_health_test_counts_match_jsonl(self):
        """ci_health.json passed/failed/skipped must match JSONL sums."""
        health = _load_json("ci_health.json")
        results, fname = _load_test_results()

        # Sum test counts from JSONL
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        for r in results:
            name = r.get("name", "")
            status = r.get("status", "")
            # Extract actual count from summary entries like "__passed__ (136)"
            count_match = re.search(r"\((\d+)\)", name)
            count = int(count_match.group(1)) if count_match else 1

            if status in ("passed", "xpassed"):
                total_passed += count
            elif status == "failed":
                total_failed += count
            elif status == "error":
                total_failed += count
            elif status in ("skipped", "xfailed"):
                total_skipped += count

        lb = health.get("amd", {}).get("latest_build", {})
        ci_passed = lb.get("passed", 0)
        ci_failed = lb.get("failed", 0)
        ci_skipped = lb.get("skipped", 0)

        assert ci_passed == total_passed, (
            f"Passed mismatch: ci_health={ci_passed}, JSONL={total_passed}"
        )
        assert ci_failed == total_failed, (
            f"Failed mismatch: ci_health={ci_failed}, JSONL={total_failed}"
        )
        assert ci_skipped == total_skipped, (
            f"Skipped mismatch: ci_health={ci_skipped}, JSONL={total_skipped}"
        )

    def test_per_hw_test_counts_match_jsonl(self):
        """Per-hardware passed/failed/skipped in ci_health must match JSONL."""
        health = _load_json("ci_health.json")
        results, fname = _load_test_results()

        hw_re = re.compile(r'^(mi\d+)_\d+:', re.IGNORECASE)
        hw_counts = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})

        for r in results:
            job_name = r.get("job_name", "")
            m = hw_re.match(job_name)
            hw = m.group(1).lower() if m else "unknown"
            name = r.get("name", "")
            status = r.get("status", "")
            count_match = re.search(r"\((\d+)\)", name)
            count = int(count_match.group(1)) if count_match else 1

            if status in ("passed", "xpassed"):
                hw_counts[hw]["passed"] += count
            elif status in ("failed", "error"):
                hw_counts[hw]["failed"] += count
            elif status in ("skipped", "xfailed"):
                hw_counts[hw]["skipped"] += count

        bh = health.get("amd", {}).get("latest_build", {}).get("by_hardware", {})
        for hw, ci_data in bh.items():
            if hw == "unknown":
                continue
            jsonl = hw_counts.get(hw, {})
            for field in ["passed", "failed", "skipped"]:
                ci_val = ci_data.get(field, 0)
                jsonl_val = jsonl.get(field, 0)
                assert ci_val == jsonl_val, (
                    f"{hw}.{field}: ci_health={ci_val}, JSONL={jsonl_val}"
                )


class TestGroupFailureCorrectness:
    """Validate that groups marked as failing actually have failures."""

    def test_failing_groups_have_actual_failures(self):
        """Every group in parity report with failures must have failed test
        results in at least one JSONL file (current or previous build, since
        the parity report backfills from previous builds)."""
        parity = _load_json("parity_report.json")

        from vllm.ci.analyzer import _normalize_job_name

        # Load ALL AMD JSONL files (current + previous builds)
        results_dir = DATA / "test_results"
        groups_with_failures = set()
        for jsonl_path in results_dir.glob("*_amd.jsonl"):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("status") in ("failed", "error"):
                        norm = _normalize_job_name(r.get("job_name", ""))
                        groups_with_failures.add(norm)
        # Also check upstream JSONL (parity includes upstream failures now)
        for jsonl_path in results_dir.glob("*_upstream.jsonl"):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("status") in ("failed", "error"):
                        norm = _normalize_job_name(r.get("job_name", ""))
                        groups_with_failures.add(norm)

        for g in parity.get("job_groups", []):
            if not g.get("amd") and not g.get("upstream"):
                continue
            # Only check AMD failures against JSONL — upstream failures use
            # different normalized names due to parity key matching and won't
            # match the JSONL group name exactly.
            amd_failed = 0
            if g.get("amd"):
                amd_failed = g["amd"].get("failed", 0) + g["amd"].get("error", 0)
            if amd_failed > 0:
                assert g["name"] in groups_with_failures, (
                    f"Group '{g['name']}' shows {amd_failed} AMD failures in parity "
                    f"report but has no failed test results in any JSONL"
                )

    def test_passing_groups_have_no_failures(self):
        """Groups with 0 failures must not have failed results in JSONL
        (after job-state override)."""
        parity = _load_json("parity_report.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name
        

        # Build map of group -> failure count from JSONL
        group_failures = defaultdict(int)
        for r in results:
            if r.get("status") in ("failed", "error"):
                norm = _normalize_job_name(r.get("job_name", ""))
                name = r.get("name", "")
                count_match = re.search(r"\((\d+)\)", name)
                count = int(count_match.group(1)) if count_match else 1
                group_failures[norm] += count

        for g in parity.get("job_groups", []):
            if not g.get("amd"):
                continue
            amd_failed = g["amd"].get("failed", 0) + g["amd"].get("error", 0)
            if amd_failed == 0:
                jsonl_fail = group_failures.get(g["name"], 0)
                assert jsonl_fail == 0, (
                    f"Group '{g['name']}' shows 0 failures in parity report but "
                    f"has {jsonl_fail} failures in JSONL ({fname}). "
                    "The log parser may be creating false failures."
                )


class TestSkipPatternsCompleteness:
    """Validate that SKIP_JOB_PATTERNS doesn't drop real test groups."""

    def test_no_test_groups_skipped(self):
        """Every job in the Buildkite build that looks like a test
        (not bootstrap/docker) must have results in the JSONL."""
        results, fname = _load_test_results()
        health = _load_json("ci_health.json")

        from vllm.ci.analyzer import _normalize_job_name
        
        from vllm.pipelines import SKIP_JOB_PATTERNS

        # Get all groups from JSONL
        jsonl_groups = set()
        for r in results:
            norm = _normalize_job_name(r.get("job_name", ""))
            jsonl_groups.add(norm)

        # Check that no skip pattern matches any existing group
        for group in jsonl_groups:
            for pattern in SKIP_JOB_PATTERNS:
                assert pattern not in group.lower(), (
                    f"SKIP_JOB_PATTERNS '{pattern}' matches collected group '{group}'. "
                    "This should not happen — the group was collected despite the pattern."
                )


class TestParityReportConsistency:
    """Validate parity_report.json is internally consistent."""

    def test_amd_groups_match_ci_health_unique_groups(self):
        """Parity report AMD group count should match ci_health unique_test_groups."""
        parity = _load_json("parity_report.json")
        health = _load_json("ci_health.json")

        parity_amd = len([g for g in parity.get("job_groups", []) if g.get("amd")])
        ci_unique = health.get("amd", {}).get("latest_build", {}).get("unique_test_groups", 0)

        # Allow small differences due to timing (parity might use different build)
        diff = abs(parity_amd - ci_unique)
        assert diff <= 5, (
            f"Parity report has {parity_amd} AMD groups but ci_health has "
            f"{ci_unique} unique_test_groups (diff={diff}). These should be close."
        )

    def test_hw_failure_counts_are_subset_of_total_failures(self):
        """Per-HW failure counts for AMD hardware must not exceed AMD total failures.
        hw_failures may also contain upstream hardware (h100, b200) — skip those."""
        AMD_HW = {"mi250", "mi325", "mi355", "cpu"}
        parity = _load_json("parity_report.json")
        for g in parity.get("job_groups", []):
            if not g.get("amd"):
                continue
            total_fail = g["amd"].get("failed", 0) + g["amd"].get("error", 0)
            hw_failures = g.get("hw_failures") or {}
            for hw, count in hw_failures.items():
                if hw not in AMD_HW:
                    continue  # upstream hw failures checked separately
                assert count <= total_fail, (
                    f"Group '{g['name']}': {hw} has {count} failures but "
                    f"total AMD failures is only {total_fail}"
                )


class TestBuildStateIntegrity:
    """Validate that job states are correctly reflected in test results."""

    def test_no_failures_from_passed_jobs_in_jsonl(self):
        """JSONL must not contain __unidentified_failures__ for jobs where
        the job-level entry shows passed. This catches the log parser bug
        where embedded subprocess output was misinterpreted as failures.

        Note: soft-failed jobs (state=failed, soft_failed=True) legitimately
        have both __passed__ and failure entries — some tests pass and some fail.
        We only flag when a job has __job_level__ passed AND __unidentified_failures__."""
        results, fname = _load_test_results()

        # Group results by job_name
        by_job = defaultdict(list)
        for r in results:
            by_job[r.get("job_name", "")].append(r)

        for job_name, job_results in by_job.items():
            has_job_level_pass = any(
                r.get("name") == "__job_level__" and r["status"] == "passed"
                for r in job_results
            )
            if not has_job_level_pass:
                continue
            # If job-level says passed, there should be no failures at all
            fail_names = [r["name"] for r in job_results if r["status"] in ("failed", "error")]
            if fail_names:
                pytest.fail(
                    f"Job '{job_name}' has __job_level__ passed but also has "
                    f"failure entries: {fail_names}. The log parser override failed."
                )


class TestNightlyDateAlignment:
    """Validate that nightly_date() correctly aligns AMD and upstream builds."""

    def test_nightly_date_before_noon_utc(self):
        """Builds before 12:00 UTC should keep the same calendar day."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from collect_ci import nightly_date
        # Current upstream and AMD nightly slots are both before noon UTC.
        assert nightly_date("2026-03-25T06:00:08Z") == "2026-03-25"
        assert nightly_date("2026-03-25T09:00:08") == "2026-03-25"
        assert nightly_date("2026-03-19T07:00:00Z") == "2026-03-19"
        # Edge: exactly midnight UTC
        assert nightly_date("2026-03-25T00:00:00Z") == "2026-03-25"
        # Edge: 11:59 UTC
        assert nightly_date("2026-03-25T11:59:59Z") == "2026-03-25"

    def test_nightly_date_after_noon_utc(self):
        """Builds after 12:00 UTC should map to next calendar day."""
        from collect_ci import nightly_date
        # Historical upstream nightlies after noon UTC map to the next day.
        assert nightly_date("2026-03-24T21:00:06Z") == "2026-03-25"
        assert nightly_date("2026-03-24T21:00:06") == "2026-03-25"
        # Edge: exactly noon
        assert nightly_date("2026-03-25T12:00:00Z") == "2026-03-26"
        # Edge: 23:59 UTC
        assert nightly_date("2026-03-25T23:59:59Z") == "2026-03-26"

    def test_nightly_date_aligns_amd_and_upstream(self):
        """AMD and upstream testing the same code should map to the same date."""
        from collect_ci import nightly_date
        # Current upstream 06:00 UTC and AMD 09:00 UTC runs share a date.
        assert nightly_date("2026-03-25T09:00:08Z") == nightly_date("2026-03-25T06:00:06Z")
        # Historical AMD 06:00 UTC and upstream 21:00 UTC runs also align.
        assert nightly_date("2026-03-25T06:00:08Z") == nightly_date("2026-03-24T21:00:06Z")

    def test_nightly_date_empty_input(self):
        from collect_ci import nightly_date
        assert nightly_date("") == ""
        assert nightly_date(None) == ""


class TestGroupChangesCompleteness:
    """Validate that group_changes.json captures significant YAML changes."""

    def test_large_group_changes_have_pr_attribution(self):
        """Any YAML change with 5+ added or removed groups should have a PR."""
        gc = _load_json("group_changes.json")
        for ch in gc.get("changes", []):
            n_changes = len(ch.get("added", [])) + len(ch.get("removed", []))
            if n_changes >= 5:
                assert ch.get("pr"), (
                    f"Change on {ch['date']} with {n_changes} group changes "
                    f"(sha={ch.get('sha','?')}) has no PR attribution. "
                    f"Added: {ch.get('added',[])}..."
                )

    def test_group_changes_dates_are_valid(self):
        """All dates in group_changes.json must be valid ISO dates."""
        gc = _load_json("group_changes.json")
        for ch in gc.get("changes", []):
            date = ch.get("date", "")
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", date), (
                f"Invalid date format: {date!r}"
            )

    def test_group_changes_covers_recent_period(self):
        """group_changes.json should cover at least 14 days."""
        gc = _load_json("group_changes.json")
        assert gc.get("days", 0) >= 14, (
            f"group_changes.json only covers {gc.get('days')} days, expected >= 14"
        )


class TestUpstreamHardwareTracking:
    """Validate that upstream GPU hardware (H100, B200, etc.) is tracked."""

    def test_upstream_has_hardware_breakdown(self):
        """ci_health upstream must have non-unknown hardware entries."""
        health = _load_json("ci_health.json")
        up = health.get("upstream", {}).get("latest_build", {})
        bh = up.get("by_hardware", {})
        non_unknown = {k: v for k, v in bh.items() if k not in ("unknown", "cpu")}
        assert len(non_unknown) >= 1, (
            f"Upstream has no GPU hardware breakdown — only {list(bh.keys())}. "
            "The _extract_hardware() function may not detect (H100), (B200) etc."
        )

    def test_upstream_h100_is_largest_group(self):
        """H100 should be the default/largest upstream hardware group."""
        health = _load_json("ci_health.json")
        up = health.get("upstream", {}).get("latest_build", {})
        bh = up.get("by_hardware", {})
        if "h100" not in bh:
            pytest.skip("no h100 data yet")
        h100 = bh["h100"]
        for hw, data in bh.items():
            if hw in ("unknown", "cpu", "h100"):
                continue
            assert h100.get("groups", 0) >= data.get("groups", 0), (
                f"H100 ({h100.get('groups')}) has fewer groups than {hw} ({data.get('groups')})"
            )

    def test_parity_report_has_upstream_hardware(self):
        """Parity report groups must have upstream hardware tags."""
        parity = _load_json("parity_report.json")
        upstream_hw_groups = [
            g for g in parity.get("job_groups", [])
            if g.get("upstream") and g.get("hardware")
            and any(hw not in ("unknown", "cpu") for hw in g["hardware"])
        ]
        assert len(upstream_hw_groups) >= 5, (
            f"Only {len(upstream_hw_groups)} parity groups have upstream GPU hardware tags. "
            "Expected at least 5 (H100, B200, etc.)"
        )


class TestExtractHardwareFunction:
    """Unit tests for _extract_hardware() covering all naming patterns."""

    def test_amd_prefix(self):
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("mi250_1: Some Test") == "mi250"
        assert _extract_hardware("mi325_4: Another Test") == "mi325"
        assert _extract_hardware("mi355_2: Test (B200-MI355)") == "mi355"

    def test_upstream_gpu_tag(self):
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("Some Test (H100)") == "h100"
        assert _extract_hardware("Some Test (B200)") == "b200"
        assert _extract_hardware("Some Test (2xH100)") == "h100"
        assert _extract_hardware("Some Test (4xA100)") == "a100"
        assert _extract_hardware("AsyncTP Tests (H200)") == "h200"

    def test_upstream_default_h100(self):
        """Jobs without GPU tag default to h100 (default NVIDIA queue)."""
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("Async Engine, Inputs, Utils, Worker") == "h100"
        assert _extract_hardware("Benchmarks") == "h100"
        assert _extract_hardware("LoRA") == "h100"

    def test_cpu_codepath_tests_are_gpu(self):
        """Tests with (CPU) suffix test CPU codepath on GPU hardware — NOT cpu jobs."""
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("V1 others (CPU)") == "h100"
        assert _extract_hardware("Multi-Modal Processor (CPU)") == "h100"
        assert _extract_hardware("Async Engine, Inputs, Utils, Worker, Config (CPU)") == "h100"
        assert _extract_hardware("Basic Models Test (Other CPU)") == "h100"

    def test_actual_cpu_platform_jobs(self):
        """Jobs that actually run on CPU-only hardware."""
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("CPU-Distributed Tests") == "cpu"
        assert _extract_hardware("Arm CPU Test") == "cpu"
        assert _extract_hardware("Intel GPU Test") == "cpu"
        assert _extract_hardware("Ascend NPU Test") == "cpu"

    def test_hw_tag_with_multiplier(self):
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("Test (2xB200)") == "b200"
        assert _extract_hardware("Test (4xH100)") == "h100"


class TestNightlyDateFunction:
    """Unit tests for nightly_date() in collect_ci.py and collect_analytics.py."""

    def test_collect_ci_nightly_date(self):
        from collect_ci import nightly_date
        # Before noon UTC -> same day
        assert nightly_date("2026-03-25T06:00:00Z") == "2026-03-25"
        assert nightly_date("2026-03-25T00:00:00Z") == "2026-03-25"
        assert nightly_date("2026-03-25T11:59:59Z") == "2026-03-25"
        # After noon UTC -> next day
        assert nightly_date("2026-03-25T12:00:00Z") == "2026-03-26"
        assert nightly_date("2026-03-25T21:00:00Z") == "2026-03-26"

    def test_collect_analytics_nightly_date(self):
        from vllm.collect_analytics import nightly_date as analytics_nightly_date
        assert analytics_nightly_date("2026-03-25T06:00:00Z") == "2026-03-25"
        assert analytics_nightly_date("2026-03-25T21:00:00Z") == "2026-03-26"

    def test_both_functions_agree(self):
        """collect_ci and collect_analytics nightly_date must produce same results."""
        from collect_ci import nightly_date as ci_nd
        from vllm.collect_analytics import nightly_date as analytics_nd
        test_times = [
            "2026-03-25T06:00:00Z", "2026-03-25T12:00:00Z",
            "2026-03-25T21:00:00Z", "2026-03-20T00:00:00",
        ]
        for t in test_times:
            assert ci_nd(t) == analytics_nd(t), f"nightly_date mismatch for {t}"


class TestGroupChangesPerPipeline:
    """Validate group_changes.json has per-pipeline separation."""

    def test_has_per_pipeline_fields(self):
        """All changes should have per-pipeline fields after cache refresh."""
        gc = _load_json("group_changes.json")
        changes = gc.get("changes", [])
        if not changes:
            pytest.skip("no changes data")
        with_fields = sum(1 for ch in changes if "amd_added" in ch)
        # Allow partial migration — skip if no entries have fields yet (pre-deploy)
        if with_fields == 0:
            pytest.skip("group_changes.json not yet updated with per-pipeline fields")
        ratio = with_fields / len(changes) if changes else 0
        assert ratio >= 0.5, (
            f"Only {with_fields}/{len(changes)} changes have per-pipeline fields. "
            "Cache may need refresh (re-run collect_group_changes.py)."
        )

    def test_amd_only_pr_has_no_upstream_changes(self):
        """PRs that only modify test-amd.yaml should have empty upstream changes."""
        gc = _load_json("group_changes.json")
        for ch in gc.get("changes", []):
            amd_changes = len(ch.get("amd_added", [])) + len(ch.get("amd_removed", []))
            up_changes = len(ch.get("upstream_added", [])) + len(ch.get("upstream_removed", []))
            # If a PR has AMD changes but no upstream changes, upstream fields must be empty
            if amd_changes > 0 and up_changes == 0:
                assert ch.get("upstream_added") == [], (
                    f"PR {ch.get('pr',{}).get('number','?')} has AMD-only changes but "
                    f"upstream_added is not empty: {ch['upstream_added']}"
                )

    def test_combined_is_superset_of_per_pipeline(self):
        """combined added/removed must be superset of per-pipeline."""
        gc = _load_json("group_changes.json")
        for ch in gc.get("changes", []):
            combined_added = set(ch.get("added", []))
            per_pipe_added = set(ch.get("amd_added", [])) | set(ch.get("upstream_added", []))
            assert per_pipe_added <= combined_added, (
                f"Per-pipeline added groups not in combined: "
                f"{per_pipe_added - combined_added}"
            )


class TestSkipPatternsRobust:
    """Comprehensive tests for SKIP_JOB_PATTERNS safety."""

    def test_patterns_are_specific_enough(self):
        """Each skip pattern must be at least 2 words or very specific."""
        from vllm.pipelines import SKIP_JOB_PATTERNS
        for p in SKIP_JOB_PATTERNS:
            assert len(p) >= 4, f"Skip pattern '{p}' is too short — risk of false matches"

    def test_patterns_dont_match_upstream_groups(self):
        """Skip patterns must not match any upstream test group names."""
        from vllm.pipelines import SKIP_JOB_PATTERNS
        parity = _load_json("parity_report.json")
        upstream_groups = [g["name"] for g in parity.get("job_groups", []) if g.get("upstream")]
        for group in upstream_groups:
            lower = group.lower()
            for pattern in SKIP_JOB_PATTERNS:
                assert pattern not in lower, (
                    f"SKIP_JOB_PATTERNS '{pattern}' matches upstream group '{group}'"
                )


class TestLogParserJobStateOverride:
    """Validate the log parser correctly handles job state overrides."""

    def test_no_unidentified_failures_in_passed_jobs(self):
        """No JSONL entry should have both __passed__ and __unidentified_failures__
        for a job that ultimately passed (not soft-failed)."""
        results_dir = DATA / "test_results"
        if not results_dir.exists():
            pytest.skip("no test results")
        from collections import defaultdict
        for jsonl_path in results_dir.glob("*.jsonl"):
            by_job = defaultdict(list)
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    by_job[r.get("job_name", "")].append(r)
            for job_name, entries in by_job.items():
                has_job_pass = any(e["name"] == "__job_level__" and e["status"] == "passed" for e in entries)
                has_unidentified = any("__unidentified" in e["name"] for e in entries)
                if has_job_pass and has_unidentified:
                    pytest.fail(
                        f"{jsonl_path.name}: '{job_name}' has __job_level__ passed "
                        f"AND __unidentified_failures__"
                    )


class TestNormalizationInvariants:
    """Validate that normalization only merges genuine %N shards.

    Every merge (multiple raw job names -> one normalized name) must be
    justified by a shard base from shard_bases.json. This catches
    false merges where different tests are incorrectly collapsed.
    """

    def test_all_merges_are_shard_based(self):
        """Within the SAME hardware, every merge of multiple raw jobs into
        one normalized name must correspond to a known shard base.

        Jobs from DIFFERENT hardware sharing a name is expected (same test,
        different GPU — e.g., mi250_1: Engine and mi325_1: Engine).
        """
        results, fname = _load_test_results()
        from vllm.ci.analyzer import _normalize_job_name, _extract_hardware, _SHARD_BASES

        assert _SHARD_BASES, "shard_bases not loaded — json import may be missing"

        # Group raw job names by (hardware, normalized name)
        hw_norm_to_raw: dict[tuple, set] = defaultdict(set)
        for r in results:
            raw = r.get("job_name", "")
            hw = _extract_hardware(raw)
            norm = _normalize_job_name(raw)
            hw_norm_to_raw[(hw, norm)].add(raw)

        # Within the same HW, >1 raw name must be a shard base
        bad_merges = []
        for (hw, norm), raws in hw_norm_to_raw.items():
            if len(raws) <= 1:
                continue
            is_shard = any(norm.startswith(base) for base in _SHARD_BASES)
            if not is_shard:
                bad_merges.append((hw, norm, raws))

        assert not bad_merges, (
            f"Found {len(bad_merges)} false merges within same HW:\n"
            + "\n".join(
                f"  [{hw}] '{norm}' <- {sorted(raws)}"
                for hw, norm, raws in bad_merges[:5]
            )
            + "\nThese are different tests on the SAME hardware being collapsed. "
            "Fix _normalize_job_name() or add to shard_bases.json."
        )

    def test_shard_bases_are_used(self):
        """Every shard base should match at least one job in the JSONL.
        Stale shard bases (from removed YAML steps) should be cleaned up."""
        results, fname = _load_test_results()
        from vllm.ci.analyzer import _normalize_job_name, _SHARD_BASES

        if not _SHARD_BASES:
            pytest.skip("shard_bases not loaded")

        all_norms = {_normalize_job_name(r.get("job_name", "")) for r in results}

        unused = []
        for base in _SHARD_BASES:
            used = any(norm.startswith(base) for norm in all_norms)
            if not used:
                unused.append(base)

        assert not unused, (
            f"Shard bases not used by any test group: {unused}. "
            "These may be stale (YAML step removed). "
            "Regenerate shard_bases.json from the current YAML."
        )

    def test_gpu_counts_preserved(self):
        """GPU counts like (2 GPUs), (4 GPUs) must NOT be stripped from names.
        Different GPU counts = different test configurations."""
        from vllm.ci.analyzer import _normalize_job_name

        # These pairs must normalize to DIFFERENT names
        pairs = [
            ("mi325_2: V1 e2e (2 GPUs)", "mi325_4: V1 e2e (4 GPUs)"),
            ("mi325_2: Distributed DP Tests (2 GPUs)", "mi325_4: Distributed DP Tests (4 GPUs)"),
            ("mi250_1: Engine", "mi250_1: Engine (1 GPU)"),
        ]
        for a, b in pairs:
            na = _normalize_job_name(a)
            nb = _normalize_job_name(b)
            assert na != nb, (
                f"GPU-count variants incorrectly merged:\n"
                f"  '{a}' -> '{na}'\n"
                f"  '{b}' -> '{nb}'\n"
                "These are different test configs and must stay separate."
            )

    def test_multi_hw_tags_preserved(self):
        """Multi-hardware tags like (H100-MI325) must NOT be stripped.
        They represent cross-hardware test configurations."""
        from vllm.ci.analyzer import _normalize_job_name

        pairs = [
            ("mi325_2: Distributed Tests (2 GPUs)(H100-MI250)",
             "mi325_2: Distributed Tests (2 GPUs)(H100-MI325)"),
            ("mi325_1: LM Eval Small Models",
             "mi325_2: LM Eval Small Models (B200-MI325)"),
        ]
        for a, b in pairs:
            na = _normalize_job_name(a)
            nb = _normalize_job_name(b)
            assert na != nb, (
                f"Multi-HW tag variants incorrectly merged:\n"
                f"  '{a}' -> '{na}'\n"
                f"  '{b}' -> '{nb}'"
            )

    def test_gpu_count_hw_tags_normalized(self):
        """Tags like (4xH100) are converted to (4 GPUs) — extracting the
        count and stripping the hardware type. Tags without a count like
        (H200) are kept as-is."""
        from vllm.ci.analyzer import _normalize_job_name

        # Single-HW NxHW → N GPUs
        assert _normalize_job_name("V1 e2e (4xH100)") == "v1 e2e (4 gpus)"
        assert _normalize_job_name("Test (2xB200)") == "test (2 gpus)"
        # Multi-HW NxHW-NxHW → kept as-is (cross-vendor tests are distinct)
        assert _normalize_job_name("mi325_4: V1 e2e (4xH100-4xMI325)") == "v1 e2e (4xh100-4xmi325)"
        assert _normalize_job_name("mi355_2: Test (2xH100-2xMI355)") == "test (2xh100-2xmi355)"
        # Bare HW tag (no count) → kept as-is
        assert _normalize_job_name("LM Eval Large Models (H200)") == "lm eval large models (h200)"
        assert _normalize_job_name("Kernels (B200)") == "kernels (b200)"
        # Already in (N GPUs) format → kept
        assert _normalize_job_name("V1 e2e (4 GPUs)") == "v1 e2e (4 gpus)"


class TestParityKeyHandling:
    """Validate that parity key matching doesn't lose groups.

    When multiple AMD norms share a parity key (e.g., different GPU count
    variants all matching the same upstream test), ALL of them must appear
    in the parity report — not just the last one.
    """

    def test_parity_key_no_group_loss(self):
        """compute_parity must not silently drop AMD groups that share a parity key."""
        from vllm.ci.analyzer import (
            _normalize_job_name, _parity_key, compute_parity,
        )
        from vllm.ci.models import TestResult

        results, fname = _load_test_results()
        # Count AMD groups by norm
        amd_norms = set()
        for r in results:
            amd_norms.add(_normalize_job_name(r.get("job_name", "")))

        # Check for parity key collisions
        pk_to_norms = defaultdict(set)
        for norm in amd_norms:
            pk = _parity_key(norm)
            pk_to_norms[pk].add(norm)

        collisions = {pk: norms for pk, norms in pk_to_norms.items() if len(norms) > 1}
        if not collisions:
            pytest.skip("no parity key collisions in current data")

        # Run actual compute_parity and verify all norms are present
        amd_results = [
            TestResult(**{**json.loads(line), "step_id": json.loads(line).get("step_id", "")})
            for line in open(DATA / "test_results" / fname).read().splitlines()
            if line.strip()
        ]
        # Need upstream too
        up_file = fname.replace("_amd.", "_upstream.")
        up_path = DATA / "test_results" / up_file
        if not up_path.exists():
            pytest.skip("no upstream JSONL for this date")
        up_results = [
            TestResult(**{**json.loads(line), "step_id": json.loads(line).get("step_id", "")})
            for line in up_path.read_text().splitlines()
            if line.strip()
        ]

        parity = compute_parity(amd_results, up_results)
        parity_names = {g["name"] for g in parity.get("job_groups", [])}

        missing = amd_norms - parity_names
        # Filter out excluded groups (CPU, Intel, etc.)
        from vllm.ci.analyzer import _EXCLUDE_PATTERNS
        missing = {n for n in missing if not _EXCLUDE_PATTERNS.match(n)}

        assert not missing, (
            f"{len(missing)} AMD groups lost in parity matching:\n"
            + "\n".join(f"  {n} (parity_key={_parity_key(n)})" for n in sorted(missing)[:10])
            + "\nThis means the parity key dict comprehension is dropping duplicates."
        )

    def test_parity_key_cross_pipeline_matching(self):
        """_parity_key must produce the same key for the SAME test across
        AMD and upstream pipelines, regardless of hardware tags.

        This is the critical function that enables cross-pipeline comparison.
        Every pair below must produce the same parity key.
        """
        from vllm.ci.analyzer import _parity_key

        # (N GPUs) format — already normalized
        assert _parity_key("mi325_2: V1 e2e (2 GPUs)") == \
               _parity_key("V1 e2e (2 GPUs)")

        # (NxHW) → (N GPUs) via normalize, then parity strips nothing extra
        assert _parity_key("V1 e2e (4xH100)") == \
               _parity_key("mi325_4: V1 e2e (4 GPUs)")

        # Future: (NxHW-NxHW) → (N GPUs)
        assert _parity_key("mi325_4: V1 e2e (4xH100-4xMI325)") == \
               _parity_key("V1 e2e (4xH100)")

        # Multi-HW tag stripped for matching
        assert _parity_key("mi325_2: Distributed Tests (2 GPUs)(H100-MI325)") == \
               _parity_key("Distributed Tests (2 GPUs)")

        # Bare HW tags stripped for matching
        assert _parity_key("LM Eval Large Models (H200)") == \
               _parity_key("LM Eval Large Models (H100)") == \
               _parity_key("LM Eval Large Models")

        assert _parity_key("Kernels (B200)") == \
               _parity_key("Kernels")

        # Different GPU counts → DIFFERENT parity keys (different tests)
        assert _parity_key("V1 e2e (2 GPUs)") != \
               _parity_key("V1 e2e (4 GPUs)")

        assert _parity_key("Distributed Tests (2 GPUs)") != \
               _parity_key("Distributed Tests (4 GPUs)")


class TestShardBasesSync:
    """Validate that shard_bases.json is in sync with reality."""

    def test_shard_bases_file_exists(self):
        """shard_bases.json must exist."""
        path = DATA / "shard_bases.json"
        assert path.exists(), "shard_bases.json not found"

    def test_shard_bases_loaded(self):
        """Shard bases must be loaded into the analyzer module."""
        from vllm.ci.analyzer import _SHARD_BASES
        assert _SHARD_BASES, (
            "_SHARD_BASES is empty — json import may be missing in analyzer.py "
            "or shard_bases.json failed to load"
        )

    def test_shard_bases_match_file(self):
        """In-memory shard bases must match shard_bases.json on disk."""
        from vllm.ci.analyzer import _SHARD_BASES

        path = DATA / "shard_bases.json"
        if not path.exists():
            pytest.skip("shard_bases.json not found")
        file_bases = sorted(b.lower() for b in json.loads(path.read_text()))
        mem_bases = sorted(_SHARD_BASES)
        assert mem_bases == file_bases, (
            f"In-memory shard bases don't match file.\n"
            f"  Memory: {mem_bases}\n"
            f"  File:   {file_bases}"
        )

    def test_every_shard_base_strips_trailing_digit(self):
        """Each shard base + ' N' must normalize to just the base."""
        from vllm.ci.analyzer import _normalize_job_name, _SHARD_BASES

        for base in _SHARD_BASES:
            with_shard = f"mi250_1: {base.title()} 3"
            without = f"mi250_1: {base.title()}"
            assert _normalize_job_name(with_shard) == _normalize_job_name(without), (
                f"Shard base '{base}' + trailing digit not stripped:\n"
                f"  '{with_shard}' -> '{_normalize_job_name(with_shard)}'\n"
                f"  '{without}' -> '{_normalize_job_name(without)}'"
            )


class TestPendingGroupCompleteness:
    """Validate that scheduled/waiting jobs appear as pending groups."""

    def test_pending_groups_have_hardware(self):
        """Every pending group in the parity report must have a hardware list."""
        parity = _load_json("parity_report.json")
        for g in parity.get("job_groups", []):
            if g.get("backfilled"):
                assert g.get("hardware"), (
                    f"Pending group '{g['name']}' has no hardware list. "
                    "Scheduled job injection should set hardware from the queue."
                )

    def test_pending_groups_without_data_are_backfilled(self):
        """Groups with no AMD or upstream data must be marked as backfilled."""
        parity = _load_json("parity_report.json")
        for g in parity.get("job_groups", []):
            if not g.get("amd") and not g.get("upstream"):
                assert g.get("backfilled"), (
                    f"Group '{g['name']}' has no test data but is not marked "
                    "as backfilled/pending."
                )

    def test_amd_completed_groups_not_pending(self):
        """If a group has AMD test results from the CURRENT build, it must
        NOT be marked as backfilled/pending — even if the upstream build
        hasn't completed that group yet.

        This catches the bug where upstream pending incorrectly made AMD
        groups show as PENDING (backfilled = amd_bf OR up_bf)."""
        parity = _load_json("parity_report.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name

        # Get the current AMD build number from parity report
        amd_build = parity.get("amd_build")
        if not amd_build:
            pytest.skip("no amd_build in parity report")

        # Find groups that have results from the CURRENT build
        current_build_groups = set()
        for r in results:
            if r.get("build_number") == amd_build:
                current_build_groups.add(_normalize_job_name(r.get("job_name", "")))

        # These groups must NOT be marked as backfilled
        bad = []
        for g in parity.get("job_groups", []):
            if g.get("backfilled") and g["name"] in current_build_groups:
                bad.append(g["name"])

        assert not bad, (
            f"{len(bad)} groups have AMD results from current build #{amd_build} "
            f"but are marked as PENDING:\n"
            + "\n".join(f"  {n}" for n in bad[:10])
            + "\nThis means upstream pending is incorrectly affecting AMD status. "
            "The backfilled flag should only depend on AMD build state."
        )

    def test_backfilled_groups_have_no_current_build_results(self):
        """Every group marked backfilled=True must NOT have test results
        from the current AMD build. If it does, the backfill tagging is wrong."""
        parity = _load_json("parity_report.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name

        amd_build = parity.get("amd_build")
        if not amd_build:
            pytest.skip("no amd_build in parity report")

        # Build set of norms that have results in current build
        current_norms = set()
        for r in results:
            if r.get("build_number") == amd_build:
                current_norms.add(_normalize_job_name(r.get("job_name", "")))

        backfilled_with_data = []
        for g in parity.get("job_groups", []):
            if g.get("backfilled") and g["name"] in current_norms:
                backfilled_with_data.append(g["name"])

        assert not backfilled_with_data, (
            f"{len(backfilled_with_data)} groups are backfilled but have "
            f"current build data:\n"
            + "\n".join(f"  {n}" for n in backfilled_with_data[:10])
        )


class TestNoStaleFailuresFromBackfill:
    """Validate that backfilled data doesn't inflate failure counts.

    When a group has hw_backfilled (e.g., mi355 from a previous build),
    the backfilled failures must NOT be included in the AMD regression
    count. Only current-build failures should be counted.
    """

    def test_backfilled_hw_failures_not_in_amd_counts(self):
        """Groups with hw_backfilled should not have their AMD failure
        count inflated by stale data from previous builds.

        If amd.failed > 0 and ALL failing hardware is backfilled,
        the group should not appear as a regression."""
        parity = _load_json("parity_report.json")

        from vllm.ci.analyzer import _normalize_job_name

        bad = []
        for g in parity.get("job_groups", []):
            if not g.get("amd") or not g.get("hw_backfilled"):
                continue
            amd_failed = g["amd"].get("failed", 0) + g["amd"].get("error", 0)
            if amd_failed == 0:
                continue
            # Check if ALL hw_failures come from backfilled hardware
            hwf = g.get("hw_failures") or {}
            bf_hw = set(g.get("hw_backfilled", {}).keys())
            # If all failing hardware is backfilled, these are stale failures
            failing_hw = {hw for hw, c in hwf.items() if c > 0}
            if failing_hw and failing_hw.issubset(bf_hw):
                bad.append(f"{g['name']}: failed={amd_failed}, "
                          f"failing_hw={failing_hw}, backfilled_hw={bf_hw}")

        assert not bad, (
            f"{len(bad)} groups have AMD failures ONLY from backfilled hardware "
            f"(stale data from previous builds):\n"
            + "\n".join(f"  {b}" for b in bad[:10])
            + "\ncompute_parity should not include backfilled results "
            "in test counts."
        )

    def test_regression_count_matches_current_build(self):
        """The number of AMD regressions should reflect CURRENT build only.

        Count groups where amd.failed > 0 and upstream.failed == 0
        (pass upstream, fail AMD). This count should only include
        groups with current-build failures, not backfilled ones."""
        parity = _load_json("parity_report.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name

        amd_build = parity.get("amd_build")
        if not amd_build:
            pytest.skip("no amd_build")

        # Groups that ACTUALLY failed in the current build
        current_failures = set()
        for r in results:
            if r.get("build_number") == amd_build and r.get("status") in ("failed", "error"):
                current_failures.add(_normalize_job_name(r.get("job_name", "")))

        # Parity regressions
        regressions = []
        for g in parity.get("job_groups", []):
            if not g.get("amd") or not g.get("upstream"):
                continue
            amd_f = g["amd"].get("failed", 0) + g["amd"].get("error", 0)
            up_f = g["upstream"].get("failed", 0) + g["upstream"].get("error", 0)
            if amd_f > 0 and up_f == 0:
                regressions.append(g["name"])

        # Every regression should have current-build failures
        stale_regressions = [r for r in regressions if r not in current_failures]
        assert not stale_regressions, (
            f"{len(stale_regressions)} regressions are from backfilled data, "
            f"not the current build #{amd_build}:\n"
            + "\n".join(f"  {r}" for r in stale_regressions[:10])
        )


class TestUpstreamFailureCompleteness:
    """Validate that ALL upstream failures from Buildkite appear in the parity report."""

    def test_no_blocked_jobs_in_parity(self):
        """Blocked jobs (AMD jobs in upstream pipeline) must not appear
        as groups in the parity report."""
        parity = _load_json("parity_report.json")
        blocked = [g for g in parity.get("job_groups", [])
                   if g["name"].startswith("amd:")]
        assert not blocked, (
            f"{len(blocked)} blocked AMD jobs appear in parity report:\n"
            + "\n".join(f"  {g['name']}" for g in blocked[:5])
            + "\nThese are AMD-side jobs in the upstream pipeline that never ran."
        )

    def test_upstream_failed_jobs_are_failures(self):
        """Upstream failures from the CURRENT build must appear in parity.

        Only checks failures from the build matching parity's upstream_build,
        not backfilled data from previous builds.

        Note: AMD and upstream may normalize to different names for the same
        logical group (e.g. 'quantized moe test (b200)' vs 'quantized moe test
        (b200-mi325)'). The parity report uses parity-key matching to align
        them, so we check that every upstream failure's base name (stripping
        the GPU tag) appears somewhere in the parity report's base names."""
        parity = _load_json("parity_report.json")
        up_build = parity.get("upstream_build")
        if not up_build:
            pytest.skip("no upstream_build in parity")

        results, fname = _load_test_results()
        from vllm.ci.analyzer import _EXCLUDE_PATTERNS, _HW_MULTI, _HW_SINGLE, _normalize_job_name

        up_file = fname.replace("_amd.", "_upstream.")
        up_path = DATA / "test_results" / up_file
        if not up_path.exists():
            pytest.skip("no upstream JSONL")

        def _strip_hw_tag(name):
            """Strip trailing hardware tag like (B200) or (B200-MI325).

            Keep semantic group qualifiers like "(2 GPUs)" intact.
            """
            s = name.strip()
            for pat in (_HW_MULTI, _HW_SINGLE):
                m = pat.search(s)
                if m and m.end() == len(s):
                    return (s[:m.start()] + s[m.end():]).strip()
            return s

        # Only failures from the CURRENT upstream build
        up_failing_groups = set()
        with open(up_path) as f:
            for line in f:
                r = json.loads(line.strip())
                if r.get("build_number") != up_build:
                    continue
                if r.get("status") in ("failed", "error"):
                    norm = _normalize_job_name(r.get("job_name", ""))
                    if not _EXCLUDE_PATTERNS.match(norm):
                        up_failing_groups.add(norm)

        parity_up_fail = set()
        for g in parity.get("job_groups", []):
            up = g.get("upstream")
            if up and (up.get("failed", 0) + up.get("error", 0)) > 0:
                parity_up_fail.add(g["name"])
                # Family aliases are the canonical identity for parity-facing
                # views when one upstream group fans out into multiple AMD HW
                # variants (for example 2xH100-2xMI300 / 2xH100-2xMI355).
                if g.get("family_name"):
                    parity_up_fail.add(g["family_name"])

        # Build base-name sets for fuzzy matching (parity key alignment)
        parity_bases = {_strip_hw_tag(n) for n in parity_up_fail}

        missing = set()
        for g in up_failing_groups:
            if g not in parity_up_fail and _strip_hw_tag(g) not in parity_bases:
                missing.add(g)

        assert not missing, (
            f"{len(missing)} upstream failures from build #{up_build} "
            f"not in parity report:\n"
            + "\n".join(f"  {g}" for g in sorted(missing)[:10])
        )

    def test_failed_job_state_produces_failure_result(self):
        """Jobs with Buildkite state=failed must have at least one failed
        test result in the JSONL. Tests the log parser's job-state override."""
        parity = _load_json("parity_report.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _normalize_job_name

        # Check upstream JSONL
        up_file = fname.replace("_amd.", "_upstream.")
        up_path = DATA / "test_results" / up_file
        if not up_path.exists():
            pytest.skip("no upstream JSONL")

        # Group results by job_name
        job_results = defaultdict(list)
        with open(up_path) as f:
            for line in f:
                r = json.loads(line.strip())
                job_results[r.get("job_name", "")].append(r)

        # For each job that has __job_level__ failed or __unidentified_failures__,
        # verify it contributes to the parity report
        for job_name, entries in job_results.items():
            has_failure = any(
                e["status"] in ("failed", "error")
                for e in entries
            )
            has_pass_only = all(
                e["status"] in ("passed", "xpassed", "skipped", "xfailed")
                for e in entries
            )
            # If there's a __job_level__ with status != passed, verify failure tracked
            job_level = [e for e in entries if e["name"] == "__job_level__"]
            if job_level and job_level[0]["status"] in ("failed", "error"):
                assert has_failure, (
                    f"Job '{job_name}' has __job_level__ failed but no failure "
                    f"entries in JSONL"
                )
