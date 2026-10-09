"""CRITICAL: Tests that validate the dashboard numbers are CORRECT.

These tests cross-reference current MI data in ci_health.json
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
def _load_shard_bases(request):
    """Load the mutable shard catalog only for live-data audits."""
    if request.node.get_closest_marker("live_data") is None:
        return
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




@pytest.mark.live_data
class TestGroupCountCorrectness:
    """Validate that per-hardware group counts match the raw test results."""

    def test_commit_aligned_definition_families_match_runtime_groups(self):
        """One complete, commit-aligned AMD run must use the source identities.

        Runtime labels do not always include YAML ``num_devices`` metadata.  A
        label-only aggregation can therefore merge two GPU-count-distinct
        definitions that happen to have the same display label.  Reconstruct
        the expected same-build totals from the commit-pinned definition-family
        assignments and exact-build test results so that this cannot silently
        lower either the total or the any-hardware passing count. Matrix job
        states cannot establish test health: a passed job can contain only
        skipped tests.
        """
        health = _load_json("ci_health.json")
        definition_parity = _load_json("config_parity.json")
        matrix = _load_json("amd_test_matrix.json")

        latest = (
            health.get("amd", {}).get("latest_test_signal_build")
            or health.get("amd", {}).get("latest_build")
            or {}
        )
        definition_commit = str(
            definition_parity.get("source", {}).get("commit_sha") or ""
        )
        runtime_commit = str(latest.get("commit") or "")
        if not definition_commit or not runtime_commit:
            pytest.skip("definition/runtime commit provenance is unavailable")
        if not (
            definition_commit.startswith(runtime_commit)
            or runtime_commit.startswith(definition_commit)
        ):
            pytest.skip(
                "definition parity and latest AMD test signal use different commits"
            )

        runtime_build = latest.get("build_number") or latest.get("number")
        matrix_build = matrix.get("source", {}).get("latest_build_number")
        if str(runtime_build or "") != str(matrix_build or ""):
            pytest.skip("AMD matrix and latest test signal use different builds")

        from vllm.ci.analyzer import (
            _AMD_RUNTIME_POOL_SUFFIX_RE,
            _JOB_PREFIX_RE,
            _extract_hardware,
            _normalize_job_name,
            _parse_job_execution_label,
        )

        family_rows = []
        for section in (
            "matches",
            "inline_mirror_variants",
            "additional_variants",
            "amd_only",
        ):
            family_rows.extend(definition_parity.get(section, []))

        all_families = {
            row["amd_identity_family_key"]
            for row in family_rows
            if row.get("amd_identity_family_key")
        }
        published_family_count = definition_parity.get("summary", {}).get(
            "amd_identity_families"
        )
        assert len(all_families) == published_family_count

        families_by_definition = defaultdict(set)
        families_by_runtime_label = defaultdict(set)
        for row in family_rows:
            family = row.get("amd_identity_family_key")
            if not family:
                continue
            labels = (
                row.get("amd_member_labels")
                or row.get("member_labels")
                or [row.get("amd_label") or row.get("label")]
            )
            pools = (
                row.get("amd_member_agent_pools")
                or row.get("member_agent_pools")
                or [row.get("amd_agent_pool") or row.get("agent_pool")]
            )
            assert len(labels) == len(pools), (
                f"Definition-family provenance has {len(labels)} labels but "
                f"{len(pools)} pools for {family}"
            )
            for label, pool in zip(labels, pools):
                normalized = _normalize_job_name(str(label or ""))
                families_by_definition[(str(pool or ""), normalized)].add(family)
                families_by_runtime_label[normalized].add(family)

        ambiguous_runtime_labels = {
            label: sorted(families)
            for label, families in families_by_runtime_label.items()
            if len(families) > 1
        }
        ambiguous_definition_keys = {
            key: sorted(families)
            for key, families in families_by_definition.items()
            if len(families) > 1
        }
        assert not ambiguous_definition_keys, (
            "Agent-pool + normalized-label definition keys are ambiguous: "
            f"{ambiguous_definition_keys}"
        )

        matrix_families = set()
        missing_definition_keys = set()
        for group in matrix.get("health_groups", []):
            for member in group.get("members", []):
                for variant in member.get("variants", []):
                    key = (
                        str(variant.get("agent_pool") or ""),
                        _normalize_job_name(str(variant.get("label") or "")),
                    )
                    families = families_by_definition.get(key, set())
                    if not families:
                        missing_definition_keys.add(key)
                        continue
                    family = next(iter(families))
                    matrix_families.add(family)

        assert not missing_definition_keys, (
            "Matrix variants lack definition-family assignments: "
            f"{sorted(missing_definition_keys)}"
        )
        assert matrix_families == all_families, (
            "Commit-aligned matrix does not cover every AMD identity family; "
            f"missing={sorted(all_families - matrix_families)}, "
            f"extra={sorted(matrix_families - all_families)}"
        )

        states_by_family = defaultdict(lambda: defaultdict(set))
        missing_result_keys = set()
        passing_states = {"passed", "xpassed"}
        failing_states = {"failed", "error"}
        observed_states = passing_states | failing_states | {
            "canceled", "skipped", "xfailed",
        }
        for path in sorted((DATA / "test_results").glob("*_amd.jsonl")):
            with path.open() as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    result = json.loads(line)
                    if str(result.get("build_number")) != str(runtime_build):
                        continue
                    state = result.get("status")
                    if state not in observed_states:
                        continue
                    job_name = str(result.get("job_name") or "")
                    route = _JOB_PREFIX_RE.match(job_name)
                    pool = route.group(1).casefold() if route else ""
                    # Main-CI mirrors retain the physical amd_ queue prefix;
                    # pinned source definitions use its concrete MI pool.
                    pool = pool.removeprefix("amd_")
                    if not pool:
                        # Native main-CI jobs wrap an AMD decorator and retain
                        # the concrete execution pool in a trailing suffix.
                        native_pool = _AMD_RUNTIME_POOL_SUFFIX_RE.search(job_name)
                        _, platform, _ = _parse_job_execution_label(job_name)
                        if platform == "amd" and native_pool:
                            pool = native_pool.group("pool").casefold()
                    key = (
                        pool,
                        _normalize_job_name(job_name),
                    )
                    families = families_by_definition.get(key, set())
                    if not families:
                        missing_result_keys.add(key)
                        continue
                    family = next(iter(families))
                    hardware = _extract_hardware(job_name)
                    states_by_family[family][hardware].add(state)

        assert not missing_result_keys, (
            "AMD test results lack definition-family assignments: "
            f"{sorted(missing_result_keys)}"
        )
        assert set(states_by_family) == all_families, (
            f"AMD build {runtime_build} test results do not cover every identity "
            f"family; missing={sorted(all_families - set(states_by_family))}, "
            f"extra={sorted(set(states_by_family) - all_families)}"
        )

        expected_total = len(all_families)
        expected_passing = sum(
            any(
                states & passing_states and not states & failing_states
                for states in hardware_states.values()
            )
            for hardware_states in states_by_family.values()
        )
        runtime_total = latest.get("unique_test_groups")
        runtime_passing = latest.get("test_groups_passing_or")
        collision_hint = (
            f" Normalized labels spanning multiple source identities: "
            f"{ambiguous_runtime_labels}."
            if ambiguous_runtime_labels
            else ""
        )
        assert runtime_total == expected_total, (
            f"Commit-aligned definition parity has {expected_total} AMD identity "
            f"families but ci_health reports {runtime_total} latest unique test "
            f"groups.{collision_hint}"
        )
        assert runtime_passing == expected_passing, (
            f"AMD build {runtime_build} test results have {expected_passing} "
            f"identity families passing on at least one hardware but ci_health reports "
            f"{runtime_passing}.{collision_hint}"
        )

    def test_ci_health_group_counts_match_jsonl(self):
        """ci_health.json per-HW group counts must match what's in the JSONL."""
        health = _load_json("ci_health.json")
        results, fname = _load_test_results()

        from vllm.ci.analyzer import _extract_hardware, _normalize_job_name

        # Extract per-HW groups from JSONL (same logic as compute_build_summary)
        hw_groups = defaultdict(set)
        for r in results:
            job_name = r.get("job_name", "")
            hw = _extract_hardware(job_name)
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

        from vllm.ci.analyzer import _extract_hardware

        hw_counts = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})

        for r in results:
            job_name = r.get("job_name", "")
            hw = _extract_hardware(job_name)
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


@pytest.fixture
def aligned_definition_audit(tmp_path, monkeypatch):
    """Keep source identities fixed while varying exact-build test evidence."""
    from vllm.ci import analyzer

    monkeypatch.setattr(sys.modules[__name__], "DATA", tmp_path)
    monkeypatch.setattr(analyzer, "_SHARD_BASES", ["shared"])
    latest = {
        "build_number": 500,
        "commit": "a" * 40,
        "unique_test_groups": 2,
        "test_groups_passing_or": 1,
    }
    routes = [
        ("mi300_1", ":amd: (MI300) Shared %N", "shared (1 gpu)"),
        ("mi355_dpx", ":amd: (MI355 DPX) Shared %N", "shared (1 gpu)"),
        ("mi300_2", ":amd: (MI300) Shared %N", "shared (2 gpus)"),
    ]
    payloads = {
        "ci_health": {"amd": {"latest_test_signal_build": latest}},
        "config_parity": {
            "source": {"commit_sha": "a" * 40},
            "summary": {"amd_identity_families": 2},
            "amd_only": [
                {"agent_pool": pool, "label": label,
                 "amd_identity_family_key": family}
                for pool, label, family in routes
            ],
        },
        "amd_test_matrix": {
            "source": {"latest_build_number": 500},
            "health_groups": [{"members": [{"variants": [
                {"agent_pool": pool, "label": label, "state": "passed"}
                for pool, label, _family in routes
            ]}]}],
        },
    }
    results = [
        {"build_number": 500, "job_name": f"{pool}: Shared {shard}",
         "status": status}
        for pool, shard, status in (
            ("mi300_1", 1, "passed"),
            ("mi300_1", 2, "passed"),
            ("mi355_dpx", 1, "skipped"),
            ("mi300_2", 1, "skipped"),
        )
    ]
    results_dir = tmp_path / "test_results"
    results_dir.mkdir()
    (results_dir / "2026-10-01_amd.jsonl").write_text(json.dumps({
        "build_number": 501, "job_name": "mi300_1: Unrelated", "status": "passed",
    }) + "\n")

    def audit():
        for name, payload in payloads.items():
            (tmp_path / f"{name}.json").write_text(json.dumps(payload))
        (results_dir / "2026-09-30_amd.jsonl").write_text(
            "".join(json.dumps(result) + "\n" for result in results)
        )
        validator = TestGroupCountCorrectness()
        validator.test_commit_aligned_definition_families_match_runtime_groups()

    return latest, results, audit


@pytest.mark.parametrize(("statuses", "passing"), [
    (("passed", "passed", "skipped"), 1),
    (("passed", "error", "skipped"), 0),
    (("passed", "failed", "xpassed"), 1),
    (("canceled", "skipped", "xfailed"), 0),
])
def test_definition_audit_uses_test_signal_instead_of_job_passes(
    aligned_definition_audit, statuses, passing,
):
    """Skip-only jobs cannot pass; failed shards need another passing hardware."""
    latest, results, audit = aligned_definition_audit
    latest["test_groups_passing_or"] = passing
    for result, status in zip(results, statuses):
        result["status"] = status
    audit()


@pytest.mark.parametrize(("field", "value", "message"), [
    ("unique_test_groups", 1, "latest unique test groups"),
    ("test_groups_passing_or", 2, "ci_health reports 2"),
])
def test_definition_audit_rejects_incorrect_runtime_counts(
    aligned_definition_audit, field, value, message,
):
    latest, _results, audit = aligned_definition_audit
    latest[field] = value
    with pytest.raises(AssertionError, match=message):
        audit()


@pytest.mark.parametrize(("field", "value", "message"), [
    ("build_number", 501, "do not cover every identity family"),
    ("job_name", "mi300_8: Shared 1", "lack definition-family assignments"),
])
def test_definition_audit_requires_exact_build_and_source_routes(
    aligned_definition_audit, field, value, message,
):
    _latest, results, audit = aligned_definition_audit
    results[-1][field] = value
    with pytest.raises(AssertionError, match=message):
        audit()


@pytest.mark.parametrize("label_style", ["ci_queue", "native_amd"])
def test_definition_audit_assigns_current_ci_execution_routes(
    aligned_definition_audit, label_style,
):
    """Current CI queue/wrapper annotations still bind exact source families."""
    _latest, results, audit = aligned_definition_audit
    for result in results:
        pool, label = result["job_name"].split(": ", 1)
        hardware = pool.split("_", 1)[0].upper()
        if label_style == "ci_queue":
            result["job_name"] = f"amd_{pool}: :amd: ({hardware}) {label}"
        else:
            result["job_name"] = f"AMD: :amd: ({hardware}) {label} ({pool})"
    audit()


@pytest.mark.parametrize("job_name", [
    "amd_mi300_8: :amd: (MI300) Shared 1",
    "AMD: :amd: (MI300) Shared 1 (mi300_8)",
    "AMD: :amd: (MI300) Shared 1",
    ":nvidia: (H100) Shared 1 (mi300_2)",
    "amd_mi300_2: :amd: (MI300) Unknown 1",
])
def test_definition_audit_rejects_unassigned_current_ci_execution_routes(
    aligned_definition_audit, job_name,
):
    """Queue normalization cannot excuse missing or foreign source routes."""
    _latest, results, audit = aligned_definition_audit
    results[-1]["job_name"] = job_name
    with pytest.raises(AssertionError, match="lack definition-family assignments"):
        audit()




@pytest.mark.live_data
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




@pytest.mark.live_data
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




class TestExtractHardwareFunction:
    """Unit tests for _extract_hardware() covering all naming patterns."""

    def test_amd_prefix(self):
        from vllm.ci.analyzer import _extract_hardware
        assert _extract_hardware("mi250_1: Some Test") == "mi250"
        assert _extract_hardware("mi325_4: Another Test") == "mi325"
        assert _extract_hardware("mi355_2: Test (B200-MI355)") == "mi355"
        assert _extract_hardware("mi355_dpx: Test (B200-MI355)") == "mi355"

    def test_standardized_platform_decorator(self):
        from vllm.ci.analyzer import _extract_hardware

        assert _extract_hardware(":amd: (MI300) Some Test") == "mi300"
        assert _extract_hardware(":amd: (MI355) Some Test") == "mi355"
        assert _extract_hardware(":amd: (MI355 DPX) Some Test") == "mi355"
        assert _extract_hardware(":computer: (CPU) Some Test") == "cpu"

    def test_standardized_nvidia_decorator(self):
        from vllm.ci.analyzer import _extract_hardware

        assert _extract_hardware(":nvidia: (H200) Basic Correctness") == "h200"
        assert _extract_hardware(":nvidia: (L4) Distributed Models") == "l4"
        assert _extract_hardware(":nvidia: (GH200) Some Test") == "gh200"
        assert _extract_hardware(":nvidia: (MITHRIL) Some Test") == "mithril"
        assert _extract_hardware(
            ":nvidia: (H200 MIG 18GB) Basic Correctness"
        ) == "h200 mig 18gb"
        assert _extract_hardware(
            "gpu_1: :nvidia: (H200 MIG 35GB) Basic Correctness"
        ) == "h200 mig 35gb"
        assert _extract_hardware(
            "gpu_1: :nvidia: (H200) Basic Correctness"
        ) == "h200"

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


def test_multiword_nvidia_decorators_keep_distinct_hardware_buckets():
    from vllm.ci import analyzer
    from vllm.ci.models import TestResult

    job_names = (
        ":nvidia: (H100) Basic Correctness",
        ":nvidia: (H200 MIG 18GB) Basic Correctness",
        "gpu_1: :nvidia: (H200 MIG 35GB) Basic Correctness",
    )
    results = [
        TestResult(
            test_id=f"group-{index}",
            name="__passed__ (1)",
            classname="group",
            status="passed",
            duration_secs=1,
            failure_message="",
            job_name=job_name,
            job_id=f"job-{index}",
            step_id=f"step-{index}",
            build_number=500,
            pipeline="ci",
            date="2026-08-31",
        )
        for index, job_name in enumerate(job_names, 1)
    ]

    summary = analyzer.compute_build_summary(
        {
            "number": 500,
            "state": "passed",
            "jobs": [
                {"name": job_name, "state": "passed"}
                for job_name in job_names
            ],
        },
        results,
        "upstream",
    )

    assert {
        hardware: row["groups"]
        for hardware, row in summary.by_hardware.items()
    } == {
        "h100": 1,
        "h200 mig 18gb": 1,
        "h200 mig 35gb": 1,
    }


@pytest.mark.parametrize(("mi355_prefix", "suite_suffix"), [
    ("mi355_1: :amd: (MI355)", ""),
    ("mi355_dpx: :amd: (MI355 DPX)", ""),
    (":amd: (MI355 DPX)", ""),
    ("mi355_dpx: :amd: (MI355 DPX)", " (MI355 suite)"),
])
def test_standardized_decorators_collapse_logical_groups_and_shards(
    monkeypatch, mi355_prefix, suite_suffix,
):
    from vllm.ci import analyzer
    from vllm.ci.models import TestResult

    normalized_label = f"attention kernels shard{suite_suffix.lower()}"
    monkeypatch.setattr(
        analyzer, "_SHARD_BASES", ["attention kernels shard", normalized_label],
    )
    mi300_shard_1 = f"mi300_1: :amd: (MI300) Attention Kernels Shard 1{suite_suffix}"
    mi300_shard_2 = f"mi300_1: :amd: (MI300) Attention Kernels Shard 2{suite_suffix}"
    mi355 = f"{mi355_prefix} Attention Kernels Shard 1{suite_suffix}"

    assert analyzer._normalize_job_name(mi300_shard_1) == normalized_label
    assert analyzer._normalize_job_name(mi300_shard_2) == normalized_label
    assert analyzer._normalize_job_name(mi355) == normalized_label
    assert analyzer._normalize_job_name(
        f"{mi355_prefix} Attention Kernels Shard %N{suite_suffix}"
    ) == normalized_label
    assert analyzer._normalize_job_name(
        "Attention Kernels Shard 2 (other suite)"
    ) == "attention kernels shard 2 (other suite)"
    assert analyzer._normalize_job_name(
        ":amd: (MI355) Attention Kernels Shard %N"
    ) == "attention kernels shard"
    assert analyzer._normalize_job_name(
        ":computer: (CPU) Attention Kernels Shard"
    ) == "attention kernels shard"
    assert analyzer._normalize_job_name(
        "gpu_1: :nvidia: (H200) Attention Kernels Shard 1"
    ) == "attention kernels shard"
    assert analyzer._normalize_job_name(
        "gpu_1: :nvidia: (H200 MIG 35GB) Attention Kernels Shard 1"
    ) == "attention kernels shard"
    assert analyzer._normalize_job_name(
        ":nvidia: (L4) Attention Kernels Shard %N"
    ) == "attention kernels shard"

    results = [
        TestResult(
            test_id=f"group-{index}",
            name="__passed__ (1)",
            classname="group",
            status="passed",
            duration_secs=1,
            failure_message="",
            job_name=job_name,
            job_id=f"job-{index}",
            step_id=f"step-{index}",
            build_number=500,
            pipeline="ci",
            date="2026-08-20",
        )
        for index, job_name in enumerate((mi300_shard_1, mi300_shard_2, mi355), 1)
    ]
    summary = analyzer.compute_build_summary(
        {
            "number": 500,
            "state": "passed",
            "jobs": [
                {"name": mi300_shard_1, "state": "passed"},
                {"name": mi300_shard_2, "state": "passed"},
                {"name": mi355, "state": "passed"},
            ],
        },
        results,
        "amd",
    )

    assert summary.unique_test_groups == 1
    assert summary.test_groups_passing_or == 1
    assert summary.test_groups_passing_all == 1
    assert summary.by_hardware["mi300"]["groups"] == 1
    assert summary.by_hardware["mi355"]["groups"] == 1



@pytest.mark.parametrize("mi355_pool", ["mi355_2", "mi355_dpx"])
def test_aligned_amd_route_map_preserves_topology_distinct_groups(
    monkeypatch, mi355_pool,
):
    from vllm.ci import analyzer
    from vllm.ci.models import TestResult

    source_commit = "a" * 40
    label = "qwen3 sync eplb accuracy"
    mi300 = "mi300_4: :amd: (MI300) Qwen3 Sync EPLB Accuracy"
    mi355 = f"{mi355_pool}: :amd: (MI355) Qwen3 Sync EPLB Accuracy"
    assert analyzer._normalize_job_name(mi300) == label
    assert analyzer._normalize_job_name(mi355) == label

    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEY_COMMIT", "")
    monkeypatch.setattr(analyzer, "_AMD_RUNTIME_GROUP_KEYS", {})
    analyzer.set_amd_runtime_group_key_map(
        source_commit,
        {
            (label, "mi300_4"): "qwen3 sync eplb accuracy (4 gpus)",
            (label, mi355_pool): "qwen3 sync eplb accuracy (2 gpus)",
        },
    )

    results = [
        TestResult(
            test_id=f"group-{index}",
            name="__passed__ (1)" if status == "passed" else "__skipped__ (1)",
            classname="group",
            status=status,
            duration_secs=1,
            failure_message="",
            job_name=job_name,
            job_id=f"job-{index}",
            step_id=f"step-{index}",
            build_number=501,
            pipeline="ci",
            date="2026-08-21",
        )
        for index, (job_name, status) in enumerate(
            ((mi300, "passed"), (mi355, "skipped")),
            1,
        )
    ]
    aligned = analyzer.compute_build_summary(
        {"number": 501, "commit": source_commit, "state": "passed", "jobs": []},
        results,
        "amd",
    )
    stale = analyzer.compute_build_summary(
        {"number": 501, "commit": "b" * 40, "state": "passed", "jobs": []},
        results,
        "amd",
    )

    assert aligned.unique_test_groups == 2
    assert aligned.test_groups_passing_or == 1
    assert aligned.test_groups_passing_all == 1
    assert aligned.test_groups_partial == 0
    assert stale.unique_test_groups == 1
    assert stale.test_groups_passing_or == 1
    assert stale.test_groups_passing_all == 0
    assert stale.test_groups_partial == 1


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


class TestSkipPatternsRobust:
    """Comprehensive tests for SKIP_JOB_PATTERNS safety."""

    def test_patterns_are_specific_enough(self):
        """Each skip pattern must be at least 2 words or very specific."""
        from vllm.pipelines import SKIP_JOB_PATTERNS
        for p in SKIP_JOB_PATTERNS:
            assert len(p) >= 4, f"Skip pattern '{p}' is too short — risk of false matches"

    def test_docker_metadata_test_is_not_filtered_as_infrastructure(self):
        from vllm.pipelines import SKIP_JOB_PATTERNS

        test_name = "mi250_1: Docker Build Metadata (ROCm)".lower()
        infra_name = "AMD: :docker: ensure ci_base".lower()
        legacy_infra_name = "AMD: Docker build test image and artifacts".lower()

        assert not any(pattern in test_name for pattern in SKIP_JOB_PATTERNS)
        assert any(pattern in infra_name for pattern in SKIP_JOB_PATTERNS)
        assert any(pattern in legacy_infra_name for pattern in SKIP_JOB_PATTERNS)



@pytest.mark.parametrize(("summary_text", "expected"), [
    ("1 xfailed, 2 xpassed, 34 warnings", {"xfailed": 1, "xpassed": 2}),
    ("2 xpassed", {"xpassed": 2}),
    ("3 passed, 2 xpassed", {"passed": 3, "xpassed": 2}),
    ("1 failed, 2 xpassed", {"xpassed": 2}),
    ("2 xfailed", {"xfailed": 2}),
])
def test_pytest_summary_preserves_xpass_counts(summary_text, expected):
    """The build 13954 XPASS summary must retain real passing test evidence."""
    from vllm.ci.analyzer import compute_build_summary
    from vllm.ci.log_parser import parse_job_results

    job = {
        "id": "job",
        "name": "mi300_2: :amd: (MI300) Model Runner V2 Distributed",
        "state": "passed",
    }
    results = parse_job_results(
        job, 13954, "ci", "2026-10-01",
        log_text=(
            "\x1b_bk;t=1790848161565\x07\x1b[33m==== "
            f"{summary_text} in 354.38s (0:05:54) ====\x1b[0m"
        ),
    )
    assert sorted((result.status, result.name) for result in results) == sorted(
        (status, f"__{status}__ ({count})") for status, count in expected.items()
    )
    summary = compute_build_summary({"number": 13954, "jobs": [job]}, results, "amd")
    assert summary.passed == expected.get("passed", 0) + expected.get("xpassed", 0)
    assert summary.skipped == expected.get("skipped", 0) + expected.get("xfailed", 0)
    assert summary.unique_test_groups == 1
    assert summary.test_groups_passing_or == int(summary.passed > 0)
    if summary.passed:
        assert summary.duration_secs == 354.4


@pytest.mark.live_data
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

    @pytest.mark.live_data
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


@pytest.mark.live_data
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








@pytest.mark.live_data
class TestCurrentMiExecutionScope:
    """Keep published health and exact parsed evidence in the same MI cohort."""

    def test_current_health_has_only_mi_execution_and_no_cuda_role(self):
        from vllm.pipelines import is_amd_ci_job
        health = _load_json("ci_health.json")
        assert health.get("hardware_scope") == "amd_mi_gpu"
        assert "upstream" not in health
        results, _ = _load_test_results()
        assert results
        assert all(row.get("pipeline") == "ci" and is_amd_ci_job(row) for row in results)
        assert not list((DATA / "test_results").glob("*_upstream.jsonl"))

    def test_latest_mi_assertion_counts_match_exact_current_shard(self):
        from vllm.ci.analyzer import _actual_count
        from vllm.ci.models import TestResult
        health = _load_json("ci_health.json")
        latest = (health.get("amd") or {}).get("latest_test_signal_build") or {}
        rows, filename = _load_test_results()
        current = [TestResult(**row) for row in rows if row.get("build_number") == latest.get("build_number")]
        assert current, f"latest MI signal has no exact records in {filename}"
        passing = sum(_actual_count(row) for row in current if row.status in {"passed", "xpassed"})
        failing = sum(_actual_count(row) for row in current if row.status in {"failed", "error"})
        assert latest["passed"] == passing
        assert latest["failed"] == failing
        assert latest["total_tests"] == passing + failing + latest["skipped"]
