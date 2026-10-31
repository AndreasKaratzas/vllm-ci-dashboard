"""Tests that validate all data files required by dashboard rendering exist
and have the correct structure. If these tests fail, the dashboard tabs will
be empty or show errors.

These catch the class of bugs where:
- A data file is missing or malformed
- A field the JS renderer expects is null/missing
- Data files are out of sync with each other
"""

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_data

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
PROJECTS_JSON = DATA / "site" / "projects.json"


@pytest.fixture
def projects():
    return json.loads(PROJECTS_JSON.read_text())["projects"]


class TestProjectsTab:
    """Tests for the Projects tab (renderCards)."""

    def test_projects_json_exists(self):
        assert PROJECTS_JSON.exists()

    def test_projects_json_has_projects(self, projects):
        assert len(projects) >= 1, f"Expected at least 1 project, got {len(projects)}"

    def test_all_projects_have_repo(self, projects):
        for name, cfg in projects.items():
            assert "repo" in cfg, f"{name} missing 'repo'"

    def test_all_projects_have_data_dir(self, projects):
        for name in projects:
            assert (DATA / name).is_dir(), f"Missing data/{name}/ directory"

    @pytest.mark.parametrize("required_file", ["prs.json", "issues.json"])
    def test_core_data_files_exist(self, projects, required_file):
        """These files feed the active Operations home view."""
        missing = []
        for name in projects:
            if not (DATA / name / required_file).exists():
                missing.append(name)
        assert not missing, f"{required_file} missing for: {missing}"

    def test_prs_json_has_prs_array(self, projects):
        for name in projects:
            path = DATA / name / "prs.json"
            if not path.exists():
                continue
            d = json.loads(path.read_text())
            assert "prs" in d or "items" in d, f"{name}/prs.json missing 'prs' key"




class TestVLLMCIData:
    """Tests that vLLM CI data is complete and consistent."""

    def test_ci_health_has_amd_build(self):
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("no ci_health data")
        d = json.loads(path.read_text())
        assert d.get("amd", {}).get("latest_build"), "ci_health missing amd.latest_build"

    def test_ci_health_build_has_required_fields(self):
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("no ci_health data")
        d = json.loads(path.read_text())
        assert "upstream" not in d
        assert d["amd"].get("source_pipeline") == "ci"
        assert d["amd"].get("hardware_scope") == "amd_mi_gpu"
        lb = d["amd"]["latest_build"]
        for field in ["build_number", "passed", "failed", "pass_rate", "by_hardware"]:
            assert field in lb, f"latest_build missing '{field}'"
        assert all(re.fullmatch(r"mi\d{3,4}", str(hardware).lower()) for hardware in lb["by_hardware"])


    def test_test_results_synced_to_project_root(self):
        """collect_ci.py should generate data/vllm/test_results.json."""
        path = DATA / "vllm" / "test_results.json"
        assert path.exists(), "test_results.json not generated"
        d = json.loads(path.read_text())
        assert d.get("rocm"), "test_results.json missing rocm data"

    def test_shard_bases_exists(self):
        path = DATA / "vllm" / "ci" / "shard_bases.json"
        assert path.exists(), "shard_bases.json missing (YAML shard detection not run)"
        bases = json.loads(path.read_text())
        assert isinstance(bases, list), "shard_bases.json should be a list"
        assert len(bases) >= 3, f"Expected at least 3 shard bases, got {len(bases)}"


class TestJSRenderingSafety:
    """Tests that data fields used by JS .toFixed() / .toLocaleString() etc.
    are actually numbers, not null/undefined. Catches the class of bug where
    the JS renderer crashes because a numeric field is missing."""


    def test_pass_rate_is_number_in_ci_health(self):
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("no ci_health")
        d = json.loads(path.read_text())
        for section in ["amd"]:
            lb = (d.get(section) or {}).get("latest_build")
            if not lb:
                continue
            pr = lb.get("pass_rate")
            assert isinstance(pr, (int, float)), f"{section}.latest_build.pass_rate is {type(pr)}"
            bh = lb.get("by_hardware", {})
            for hw, data in bh.items():
                hpr = data.get("pass_rate")
                assert isinstance(hpr, (int, float)), f"{section}.by_hardware.{hw}.pass_rate is {type(hpr)}"

    def test_test_results_pass_rate_is_number(self):
        """The retained ROCm compatibility summary has numeric assertion rates."""
        for path in DATA.glob("*/test_results.json"):
            d = json.loads(path.read_text())
            for platform in ["rocm"]:
                pd = d.get(platform)
                if not pd or not pd.get("summary"):
                    continue
                pr = pd["summary"].get("pass_rate")
                if pr is not None:
                    assert isinstance(pr, (int, float)), (
                        f"{path}: {platform}.summary.pass_rate is {type(pr).__name__}"
                    )

    def test_ci_health_builds_have_group_rate_fields(self):
        """Current AMD cards use typed logical group rates."""
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("no ci_health")
        d = json.loads(path.read_text())
        for section in ["amd"]:
            builds = (d.get(section) or {}).get("builds", [])
            for b in builds:
                utg = b.get("unique_test_groups")
                tgp = b.get("test_groups_passing_or")
                if utg is not None:
                    assert isinstance(utg, int), f"Build #{b.get('build_number')} unique_test_groups is {type(utg)}"
                if tgp is not None:
                    assert isinstance(tgp, int), f"Build #{b.get('build_number')} test_groups_passing_or is {type(tgp)}"

    def test_ci_health_group_pass_math_is_self_consistent(self):
        """Passing-any-HW is strict all-HW plus partial groups."""
        path = DATA / "vllm" / "ci" / "ci_health.json"
        if not path.exists():
            pytest.skip("no ci_health")
        d = json.loads(path.read_text())
        for section in ["amd"]:
            builds = (d.get(section) or {}).get("builds", [])
            for b in builds:
                total = b.get("unique_test_groups") or 0
                passing_or = b.get("test_groups_passing_or") or 0
                passing_all = b.get("test_groups_passing_all") or 0
                partial = b.get("test_groups_partial") or 0
                assert passing_or == passing_all + partial, (
                    f"{section} build #{b.get('build_number')} has inconsistent group pass math"
                )
                assert 0 <= passing_or <= total, (
                    f"{section} build #{b.get('build_number')} passing groups out of range"
                )



class TestNoJobStateMismatch:
    """Tests that passed jobs are not reported as failed."""

    def test_no_failures_from_passed_jobs(self):
        """If a Buildkite job passed (state=passed), it must not appear
        as failed in test results. This catches the bug where the log parser
        extracted pytest failures from embedded subprocess output."""
        ci_health = DATA / "vllm" / "ci" / "ci_health.json"
        if not ci_health.exists():
            pytest.skip("no CI data")

        # Load ci_health to get job states
        health = json.loads(ci_health.read_text())
        lb = health.get("amd", {}).get("latest_build", {})
        # Can't check individual jobs from ci_health alone,
        # but we can check that total failures <= jobs_failed
        total_test_failures = lb.get("failed", 0)
        jobs_failed = lb.get("jobs_failed", 0)
        # Soft-failed jobs count as failures
        jobs_soft_failed = lb.get("jobs_soft_failed", 0)
        # Test failures should not massively exceed job failures
        # (some jobs have multiple test failures, so test_failures > jobs_failed is OK,
        # but test_failures shouldn't exist for jobs that passed)
        if jobs_failed == 0 and jobs_soft_failed == 0:
            assert total_test_failures == 0, (
                f"ci_health shows {total_test_failures} test failures but "
                f"0 jobs failed — log parser may be reporting false failures"
            )
