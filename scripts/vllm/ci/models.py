"""Data models for CI dashboard backend."""

from dataclasses import dataclass, field
from typing import Optional


PASS_RATE_CONTRACT_VERSION = 1
TEST_PASS_RATE_BASIS = "pytest_assertions_excluding_skipped"
OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS = (
    "unique logical test-group identities observed in this build; "
    "hardware-specific executions and configured %N shard jobs count once per "
    "normalized group; configured-definition inventories are separate"
)
AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS = (
    "unique logical test-group identities observed in this build; "
    "when its commit matches the pinned AMD definitions, normalized label plus "
    "agent pool resolves the configuration identity family, preserving "
    "topology-distinct routes; hardware-specific executions in one family and "
    "configured %N shard jobs count once per family; without an aligned map "
    "they fall back to the normalized group; configured-definition inventories "
    "are separate"
)


@dataclass
class TestResult:
    """Single test case result from one build."""
    __test__ = False
    test_id: str           # "{classname}::{name}" canonical identifier
    name: str
    classname: str
    status: str            # passed, failed, skipped, error, xfailed, xpassed, canceled
    duration_secs: float
    failure_message: str
    job_name: str
    job_id: str
    step_id: str           # Buildkite step UUID (from job.step.id)
    build_number: int
    pipeline: str          # "amd-ci" or "ci"
    date: str              # ISO date "2026-03-22"
    node: str = ""         # physical CI agent hostname parsed from the job log
                           # "Node:" line (e.g. "chi-mi325x-pod2-032"); "" when
                           # the log did not expose an identifiable node.

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "classname": self.classname,
            "status": self.status,
            "duration_secs": self.duration_secs,
            "failure_message": self.failure_message,
            "job_name": self.job_name,
            "job_id": self.job_id,
            "step_id": self.step_id,
            "build_number": self.build_number,
            "pipeline": self.pipeline,
            "date": self.date,
            "node": self.node,
        }


@dataclass
class BuildSummary:
    """Aggregate metrics for one nightly build."""
    pipeline: str
    build_number: int
    build_url: str
    branch: str
    commit: str
    created_at: str
    state: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0               # pytest errors; a diagnostic subset of failed
    pass_rate: float = 0.0
    duration_secs: float = 0.0
    wall_clock_secs: float = 0.0
    job_count: int = 0
    jobs_passed: int = 0
    jobs_failed: int = 0
    jobs_soft_failed: int = 0      # subset of jobs_failed that are soft-failures
    jobs_running: int = 0          # jobs still in progress
    jobs_waiting: int = 0          # jobs scheduled/waiting
    test_job_count: int = 0        # logical test steps, excluding CI infrastructure
    test_jobs_blocked: int = 0     # test steps that never ran because a dependency failed
    has_test_results: bool = False # at least one parsed test-result row exists
    is_running: bool = False       # True if build still has non-terminal jobs
    test_groups: int = 0           # number of JSONL entries (job-level groups)
    unique_test_groups: int = 0    # observed logical groups (HW routes/shards collapsed)
    test_groups_passing_or: int = 0  # groups passing on ANY hardware (OR logic)
    test_groups_passing_all: int = 0  # groups passing on ALL hardware (strict)
    test_groups_partial: int = 0     # groups that differ across hardware
    by_hardware: dict = field(default_factory=dict)  # per-hardware breakdown
    delta_vs_previous: dict = field(default_factory=dict)

    @property
    def test_pass_rate_pct(self) -> float:
        """Assertion pass rate on a 0-100 scale; skipped tests are excluded."""
        assertions_run = self.passed + self.failed
        return (
            round(self.passed / assertions_run * 100, 2)
            if assertions_run else 0.0
        )

    @property
    def test_pass_rate_basis(self) -> str:
        return TEST_PASS_RATE_BASIS

    @property
    def observed_unique_test_groups_count_basis(self) -> str:
        if str(self.pipeline).strip().casefold() in {"amd", "amd-ci"}:
            return AMD_OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS
        return OBSERVED_UNIQUE_TEST_GROUPS_COUNT_BASIS

    def to_dict(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "build_number": self.build_number,
            "build_url": self.build_url,
            "branch": self.branch,
            "commit": self.commit,
            "created_at": self.created_at,
            "state": self.state,
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errors": self.errors,
            "pass_rate": self.pass_rate,
            "test_pass_rate_pct": self.test_pass_rate_pct,
            "test_pass_rate_basis": self.test_pass_rate_basis,
            "duration_secs": self.duration_secs,
            "wall_clock_secs": self.wall_clock_secs,
            "job_count": self.job_count,
            "jobs_passed": self.jobs_passed,
            "jobs_failed": self.jobs_failed,
            "jobs_soft_failed": self.jobs_soft_failed,
            "jobs_running": self.jobs_running,
            "jobs_waiting": self.jobs_waiting,
            "test_job_count": self.test_job_count,
            "test_jobs_blocked": self.test_jobs_blocked,
            "has_test_results": self.has_test_results,
            "is_running": self.is_running,
            "test_groups": self.test_groups,
            "unique_test_groups": self.unique_test_groups,
            # Explicit alias for consumers that need to distinguish observed
            # runtime identities from configured definition-row counts.
            "observed_unique_test_groups": self.unique_test_groups,
            "observed_unique_test_groups_count_basis": (
                self.observed_unique_test_groups_count_basis
            ),
            "test_groups_passing_or": self.test_groups_passing_or,
            "test_groups_passing_all": self.test_groups_passing_all,
            "test_groups_partial": self.test_groups_partial,
            "by_hardware": self.by_hardware,
            "delta_vs_previous": self.delta_vs_previous,
        }


@dataclass
class TestHealth:
    """Health status of a single test across multiple builds."""
    __test__ = False
    test_id: str
    label: str             # passing, failing, new_failure, fixed, flaky, skipped, new_test
    pass_rate: float
    appearances: int
    last_seen: str
    first_failure: Optional[str] = None
    failure_streak: int = 0
    history: list = field(default_factory=list)
    module: str = ""
    mean_duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "label": self.label,
            "pass_rate": round(self.pass_rate, 4),
            "appearances": self.appearances,
            "last_seen": self.last_seen,
            "first_failure": self.first_failure,
            "failure_streak": self.failure_streak,
            "history": self.history,
            "module": self.module,
            "mean_duration": round(self.mean_duration, 3),
        }


@dataclass
class ParityEntry:
    """One test in the parity comparison."""
    test_id: str
    amd_status: str        # passed, failed, skipped, missing
    upstream_status: str   # passed, failed, skipped, missing
    category: str          # both_pass, both_fail, amd_regression, amd_advantage, amd_only, upstream_only

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "amd_status": self.amd_status,
            "upstream_status": self.upstream_status,
            "category": self.category,
        }
