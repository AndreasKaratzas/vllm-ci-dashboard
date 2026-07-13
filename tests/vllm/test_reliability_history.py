"""Contract tests for bounded all-main pipeline reliability datasets."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from vllm import collect_analytics as ca
from vllm.ci.reliability_history import (
    build_all_main_reliability,
    compact_main_builds,
    compute_nightly_change_history,
    validate_all_main_reliability,
)


GENERATED_AT = "2026-04-24T12:00:00Z"
NIGHTLY_PATTERN = r"AMD Full CI Run - nightly"
BASE = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _job(
    job_id: str,
    name: str,
    state: str = "passed",
    *,
    step_id: str | None = None,
    step_key: str = "shared-step",
    queue: str = "amd_mi300_1",
    minute: int = 0,
    soft_failed: bool = False,
    **extra,
) -> dict:
    runnable = BASE + timedelta(minutes=minute)
    started = runnable + timedelta(minutes=5)
    finished = started + timedelta(minutes=20)
    return {
        "id": job_id,
        "type": "script",
        "name": name,
        "state": state,
        "soft_failed": soft_failed,
        "runnable_at": _iso(runnable),
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "agent_query_rules": [f"queue={queue}"],
        "step": {
            "id": step_id or f"step-{job_id}",
            "key": step_key,
        },
        **extra,
    }


def _build(
    number: int,
    jobs: list[dict],
    *,
    message: str = "merge queue validation",
    branch: str = "main",
    state: str = "passed",
    finished: bool = True,
    hour_offset: int = 0,
) -> dict:
    created = BASE + timedelta(hours=hour_offset)
    return {
        "number": number,
        "branch": branch,
        "state": state,
        "commit": f"commit-{number}",
        "message": message,
        "created_at": _iso(created),
        "started_at": _iso(created + timedelta(minutes=1)),
        "finished_at": _iso(created + timedelta(hours=1)) if finished else None,
        "web_url": f"https://buildkite.com/vllm/amd-ci/builds/{number}",
        "jobs": jobs,
    }


def _dataset(builds: list[dict], pipeline_slug: str = "amd-ci", **kwargs) -> dict:
    return build_all_main_reliability(
        builds,
        pipeline_slug=pipeline_slug,
        window_days=30,
        generated_at=GENERATED_AT,
        nightly_pattern=NIGHTLY_PATTERN,
        **kwargs,
    )


def test_all_main_includes_nightly_and_non_nightly_once_but_rejects_untrusted():
    nightly = _build(
        101,
        [_job("nightly-job", "mi300_1: Nightly Group")],
        message="AMD Full CI Run - nightly",
    )
    non_nightly = _build(
        102,
        [_job("main-job", "mi300_1: Main Group")],
        message="Merge pull request #123",
        state="failed",
        hour_offset=1,
    )
    duplicate = {**non_nightly, "message": "duplicate API page row"}
    feature = _build(103, [_job("feature-job", "mi300_1: Feature")], branch="feature")
    running = _build(104, [_job("running-job", "mi300_1: Running")], state="running")
    unfinished = _build(105, [_job("unfinished-job", "mi300_1: Unfinished")], finished=False)

    result = _dataset([unfinished, feature, non_nightly, nightly, duplicate, running])

    assert [row["number"] for row in result["builds"]] == [102, 101]
    assert result["cohort"]["build_count"] == 2
    assert result["cohort"]["canonical_nightly_build_count"] == 1
    assert result["cohort"]["non_nightly_main_build_count"] == 1
    assert result["cohort"]["includes_canonical_nightlies"] is True
    assert result["denominator"]["eligible_observations"] == 2


def test_group_identity_keeps_gpu_hardware_queue_and_shard_variants_distinct():
    labels = [
        "mi300_4: V1 e2e (4 GPUs)",
        "mi300_4: V1 e2e (4xH100-4xMI300)",
        "mi300_4: Sharded Models 1/2",
        "mi300_4: Sharded Models 2/2",
    ]
    jobs = [
        _job(
            f"variant-{index}",
            label,
            step_key="variant-step",
            queue="amd_mi300_4",
        )
        for index, label in enumerate(labels)
    ]

    groups = _dataset([_build(201, jobs)])["groups"]

    assert len(groups) == 4
    assert len({row["group_id"] for row in groups}) == 4
    assert {row["raw_name"] for row in groups} == set(labels)
    assert {row["name"] for row in groups} == {
        "V1 e2e (4 GPUs)",
        "V1 e2e (4xH100-4xMI300)",
        "Sharded Models 1/2",
        "Sharded Models 2/2",
    }
    assert {row["hardware"] for row in groups} == {"mi300"}
    assert {row["queue"] for row in groups} == {"amd_mi300_4"}


def test_upstream_identity_reports_explicit_and_generic_hardware_without_conflation():
    jobs = [
        _job("h100", "Kernel test (H100)", queue="gpu_4_queue", step_key="kernel"),
        _job("generic", "Generic GPU test", queue="gpu_4_queue", step_key="generic"),
        _job("b200", "Large model test", queue="B200", step_key="large"),
        _job("cpu", "CPU correctness", queue="cpu_queue_postmerge", step_key="cpu"),
    ]

    groups = _dataset([_build(202, jobs)], pipeline_slug="ci")["groups"]

    assert {row["hardware"] for row in groups} == {"h100", "gpu", "b200", "cpu"}
    assert len({row["group_id"] for row in groups}) == 4


def test_upstream_amd_mirrors_keep_amd_hardware_identity():
    jobs = [
        _job(
            "mi250-mirror",
            "AMD: Samplers Test (mi250_1)",
            queue="amd_mi250_1",
            step_key="samplers-mi250",
        ),
        _job(
            "mi325-mirror",
            "AMD: Samplers Test (mi325_1)",
            queue="amd_mi325_1",
            step_key="samplers-mi325",
        ),
        _job(
            "mixed-label",
            "AMD: V1 e2e (4xH100-4xMI300)",
            queue="amd_mi300_4",
            step_key="v1-e2e-mi300",
        ),
    ]

    groups = _dataset([_build(203, jobs)], pipeline_slug="ci")["groups"]

    assert {row["hardware"] for row in groups} == {"mi250", "mi300", "mi325"}
    assert all(row["hardware"] != "unknown" for row in groups)
    mixed = next(row for row in groups if "4xH100" in row["name"])
    assert mixed["hardware"] == "mi300"


def test_reliability_denominator_excludes_non_pass_fail_soft_fail_states():
    jobs = [
        _job("pass", "mi300_1: Denominator", "passed"),
        _job("fail", "mi300_1: Denominator", "failed", minute=1),
        _job("soft", "mi300_1: Denominator", "failed", minute=2, soft_failed=True),
        _job("skip", "mi300_1: Denominator", "skipped", minute=3),
        _job("cancel", "mi300_1: Denominator", "canceled", minute=4),
        _job("unknown", "mi300_1: Denominator", "unknown", minute=5),
        _job("only-skip", "mi300_1: Excluded Only", "skipped", minute=6),
    ]

    result = _dataset([_build(301, jobs, state="failed")])
    group = {row["name"]: row for row in result["groups"]}["Denominator"]

    assert group["denominator"] == 3
    assert (group["passed"], group["failed"], group["soft_failed"]) == (1, 1, 1)
    assert group["incident_rate"] == 66.7
    assert group["excluded_observations"] == 3
    assert group["excluded_by_state"] == {"canceled": 1, "skipped": 1, "unknown": 1}
    assert result["denominator"]["eligible_observations"] == 3
    assert result["denominator"]["excluded_observations"] == 4
    assert result["denominator"]["groups"] == 1
    assert result["denominator"]["catalog_groups"] == 2
    assert result["denominator"]["excluded_only_groups"] == 1
    assert result["denominator"]["unit"] == (
        "terminal job attempts with passed, failed, or soft-fail outcomes"
    )


def test_excluded_mi355b_queues_never_enter_groups_or_denominators():
    jobs = [
        _job(
            "included-mi355",
            "mi355_1: Included Group",
            queue="amd_mi355_1",
        ),
        _job(
            "excluded-mi355b",
            "mi355B_1: Excluded Group",
            queue="amd_mi355B_1",
            retried=True,
            retried_in_job_id="excluded-retry",
        ),
        _job(
            "excluded-mi355b-variant",
            "mi355B_8: Excluded Variant",
            queue="amd_mi355b_8_extra",
        ),
    ]

    result = _dataset([_build(302, jobs)])

    assert [group["name"] for group in result["groups"]] == ["Included Group"]
    assert result["denominator"]["eligible_observations"] == 1
    assert result["denominator"]["groups"] == 1
    assert result["denominator"]["out_of_scope_queue_observations"] == 2
    assert result["summary"]["out_of_scope_queue_observations"] == 2
    assert result["summary"]["retry_evidence_observations"] == 0
    assert sum(len(build["jobs"]) for build in compact_main_builds(result)) == 1
    assert result["provenance"]["queue_scope_source"] == (
        "vllm.constants.is_excluded_queue"
    )


def test_durations_are_typed_and_test_duration_requires_exact_attempt_identity():
    jobs = [
        _job(
            "attempt-a",
            "mi300_1: Duration Group",
            step_id="shared-step-id",
            minute=0,
        ),
        _job(
            "attempt-b",
            "mi300_1: Duration Group",
            step_id="shared-step-id",
            minute=30,
        ),
    ]
    parsed = [{
        "number": 401,
        "jobs": [{
            "job_id": "attempt-a",
            "step_id": "shared-step-id",
            "test_duration_mins": 7.5,
        }],
    }]

    result = _dataset([_build(401, jobs)], test_result_builds=parsed)
    observations = {row["job_id"]: row for row in result["groups"][0]["observations"]}

    assert observations["attempt-a"]["wall_completion_mins"] == 20.0
    assert observations["attempt-a"]["test_duration_mins"] == 7.5
    assert observations["attempt-a"]["queue_wait_mins"] == 5.0
    assert observations["attempt-a"]["end_to_end_mins"] == 25.0
    assert observations["attempt-b"]["test_duration_mins"] is None
    assert result["groups"][0]["duration"]["wall_completion"]["samples"] == 2
    assert result["groups"][0]["duration"]["test_reported"]["samples"] == 1
    assert result["provenance"]["wall_completion_source"] == "job started_at to finished_at"
    assert result["provenance"]["test_duration_source"] == (
        "parsed test-result logs when exact job ID or unique step ID matches"
    )


def test_ambiguous_shared_step_does_not_attach_test_duration_without_job_id():
    raw = _job("", "mi300_1: Ambiguous Duration", step_id="shared-step-id")
    parsed = [{
        "number": 402,
        "jobs": [
            {"job_id": "attempt-a", "step_id": "shared-step-id", "test_duration_mins": 3.0},
            {"job_id": "attempt-b", "step_id": "shared-step-id", "test_duration_mins": 9.0},
        ],
    }]

    observation = _dataset(
        [_build(402, [raw])],
        test_result_builds=parsed,
    )["groups"][0]["observations"][0]

    assert observation["test_duration_mins"] is None


def test_retry_evidence_and_attempt_links_require_explicit_buildkite_fields():
    failed = _job(
        "failed-attempt",
        "mi300_1: Retry Group",
        "failed",
        retried=True,
        retried_in_job_id="passed-attempt",
    )
    passed = _job(
        "passed-attempt",
        "mi300_1: Retry Group",
        "passed",
        minute=30,
        retries_count=1,
        retry_source="manual",
    )
    unrelated_failure = _job(
        "plain-failure",
        "mi300_1: Retry Group",
        "failed",
        minute=60,
    )

    result = _dataset([_build(501, [failed, passed, unrelated_failure], state="failed")])
    observations = {row["job_id"]: row for row in result["groups"][0]["observations"]}
    base = "https://buildkite.com/vllm/amd-ci/builds/501/steps/canvas"

    assert observations["failed-attempt"]["job_url"] == (
        f"{base}?jid=failed-attempt&tab=output"
    )
    assert observations["failed-attempt"]["step_url"] == (
        f"{base}?sid=step-failed-attempt&tab=output"
    )
    assert observations["failed-attempt"]["retry_evidence"]["retried_in_job_url"] == (
        f"{base}?jid=passed-attempt&tab=output"
    )
    assert observations["passed-attempt"]["retry_evidence"]["retries_count"] == 1
    assert "retry_evidence" not in observations["plain-failure"]
    assert result["summary"]["retry_evidence_observations"] == 2


def test_catalog_is_not_top_twenty_truncated_and_group_history_is_bounded():
    many_groups = [
        _job(f"catalog-{index}", f"mi300_1: Catalog Group {index}", step_key=f"group-{index}")
        for index in range(25)
    ]
    catalog = _dataset([_build(601, many_groups)])
    assert len(catalog["groups"]) == 25

    builds = []
    for index in range(65):
        builds.append(_build(
            700 + index,
            [_job(f"history-{index}", "mi300_1: Long History", minute=index)],
            hour_offset=index,
        ))
    history = _dataset(builds)
    group = history["groups"][0]

    assert group["denominator"] == 65
    assert group["observation_count"] == 65
    assert group["retained_observation_count"] == 60
    assert group["observations_truncated"] is True
    assert [row["build_number"] for row in group["observations"][:2]] == [764, 763]
    assert history["denominator"]["eligible_observations"] == 65
    assert len(history["builds"]) == 65
    assert sum(len(build["jobs"]) for build in compact_main_builds(history)) == 60


def test_compact_main_builds_preserve_strict_identity_links_and_duration_types():
    labels = [
        "mi300_4: V1 e2e (4 GPUs)",
        "mi300_4: V1 e2e (4xH100-4xMI300)",
    ]
    source = _dataset([_build(801, [
        _job("four-gpu", labels[0], step_key="v1-four-gpu", queue="amd_mi300_4"),
        _job("cross-hw", labels[1], step_key="v1-cross-hw", queue="amd_mi300_4"),
    ])])

    main_build = compact_main_builds(source)[0]
    jobs = {job["raw_name"]: job for job in main_build["jobs"]}

    assert main_build["branch"] == "main"
    assert main_build["commit"] == "commit-801"
    assert set(jobs) == set(labels)
    assert jobs[labels[0]]["group_id"] != jobs[labels[1]]["group_id"]
    assert jobs[labels[0]]["url"].endswith("?jid=four-gpu&tab=output")
    assert jobs[labels[0]]["step_url"].endswith("?sid=step-four-gpu&tab=output")
    assert jobs[labels[0]]["wall_duration_mins"] == 20.0
    assert jobs[labels[0]]["wait_mins"] == 5.0
    assert jobs[labels[0]]["end_to_end_mins"] == 25.0


def test_collector_exposes_main_builds_with_exact_cohort_provenance():
    source = _dataset([_build(802, [
        _job("main-attempt", "mi300_1: Main Evidence"),
    ])])
    pipeline_data = {}

    ca.attach_main_reliability(pipeline_data, source)

    assert pipeline_data["all_main_reliability"] is source
    assert [build["number"] for build in pipeline_data["main_builds"]] == [802]
    provenance = pipeline_data["main_builds_provenance"]
    assert provenance["schema_version"] == 1
    assert provenance["cohort"] == source["cohort"]
    assert provenance["window"] == {
        "window_days": 30,
        "requested_from": "2026-03-25T12:00:00Z",
        "observed_from": "2026-04-20T09:00:00Z",
        "observed_to": "2026-04-20T09:00:00Z",
    }
    assert provenance["denominator"] == source["denominator"]
    assert provenance["source"] == source["provenance"]
    assert provenance["retention"] == {
        "eligible_observations_in_denominator": 1,
        "eligible_observations_in_main_builds": 1,
        "observation_limit_per_group": 60,
    }
    assert provenance["authoritative_evidence_key"] == "all_main_reliability"


def test_collector_preserves_complete_retry_analysis_when_raw_builds_are_unavailable():
    source = _dataset([_build(803, [
        _job("main-attempt", "mi300_1: Main Evidence"),
    ])], pipeline_slug="ci")
    preserved = {
        "available": True,
        "summary": {
            "builds_evaluated": 30,
            "builds_with_retries": 1,
            "retry_attempt_count": 1,
            "failed_then_passed_recovery_count": 0,
        },
        "retry_attempts": [{
            "build_number": 803,
            "job_id": "older-than-compaction-window",
            "url": "https://buildkite.com/vllm/ci/builds/803/steps/canvas?jid=older-than-compaction-window",
        }],
        "failed_then_passed_recoveries": [],
        "provenance": {
            "source_pipeline": "ci",
            "complete": True,
            "cohort_build_numbers": [803],
        },
    }
    pipeline_data = {}

    ca.attach_main_reliability(
        pipeline_data,
        source,
        retry_builds=None,
        retry_analysis=preserved,
    )

    assert pipeline_data["main_retry_analysis"] is preserved


def test_nightly_fixed_requires_current_pass_and_preserves_both_links():
    def nightly_job(name: str, state: str, suffix: str) -> dict:
        return {
            "name": name,
            "raw_name": name,
            "state": state,
            "step_key": name.lower().replace(" ", "-"),
            "q": "amd_mi300_1",
            "url": f"https://buildkite.com/vllm/amd-ci/builds/{suffix}",
        }

    previous = {
        "number": 901,
        "created_at": "2026-04-20T09:00:00Z",
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/901",
        "jobs": [
            nightly_job("Actually Fixed", "failed", "901/steps/failure"),
            nightly_job("Missing Now", "failed", "901/steps/missing"),
            nightly_job("Indeterminate Now", "failed", "901/steps/unknown"),
        ],
    }
    current = {
        "number": 902,
        "created_at": "2026-04-21T09:00:00Z",
        "web_url": "https://buildkite.com/vllm/amd-ci/builds/902",
        "jobs": [
            nightly_job("Actually Fixed", "passed", "902/steps/pass"),
            nightly_job("Indeterminate Now", "skipped", "902/steps/unknown"),
        ],
    }

    row = compute_nightly_change_history([previous, current])[0]

    assert [item["name"] for item in row["fixed"]] == ["Actually Fixed"]
    assert row["fixed"][0]["url"].endswith("902/steps/pass")
    assert row["fixed"][0]["previous_url"].endswith("901/steps/failure")
    assert [item["name"] for item in row["not_observed"]] == ["Missing Now"]
    assert [item["name"] for item in row["indeterminate"]] == ["Indeterminate Now"]


def test_schema_reports_cohort_window_denominator_source_and_deterministic_order():
    builds = [
        _build(1001, [_job("z-job", "mi355_1: Z Group", queue="amd_mi355_1")]),
        _build(1002, [_job("a-job", "mi300_1: A Group")], hour_offset=1),
    ]

    forward = _dataset(builds)
    reverse = _dataset(list(reversed(builds)))

    assert forward["schema_version"] == 1
    assert forward["cohort"]["id"] == "amd-ci-main-completed-pass-fail"
    assert forward["cohort"]["name"] == (
        "amd-ci branch=main builds with state passed or failed and finished_at"
    )
    assert forward["cohort"]["window_days"] == 30
    assert forward["cohort"]["observed_from"] == "2026-04-20T09:00:00Z"
    assert forward["cohort"]["observed_to"] == "2026-04-20T10:00:00Z"
    assert forward["provenance"]["provider"] == "Buildkite REST API"
    assert forward["provenance"]["query"] == {
        "branch": "main",
        "created_from": "2026-03-25T12:00:00Z",
        "include_retried_jobs": True,
    }
    assert forward["provenance"]["pagination"] == {
        "page_size": 100,
        "max_pages": 50,
        "pages_fetched": None,
        "termination_reason": "provided_builds",
        "exhaustive": True,
        "stop_conditions": ["empty page", "short page", "page adds no build numbers"],
    }
    assert forward["provenance"]["retry_source"] == "explicit Buildkite retry fields only"
    assert [row["name"] for row in forward["groups"]] == ["A Group", "Z Group"]
    assert [row["group_id"] for row in forward["groups"]] == [
        row["group_id"] for row in reverse["groups"]
    ]
    assert [row["number"] for row in forward["builds"]] == [1002, 1001]


def test_cohort_identity_is_pipeline_specific():
    result = _dataset(
        [_build(1101, [_job("upstream-job", "GPU Test")])],
        pipeline_slug="ci",
    )

    assert result["cohort"]["id"] == "ci-main-completed-pass-fail"
    assert result["cohort"]["name"] == (
        "ci branch=main builds with state passed or failed and finished_at"
    )
    assert result["cohort"]["pipeline"] == "ci"
    assert result["provenance"]["pipeline"] == "ci"
    assert result["provenance"]["endpoint"] == (
        "/organizations/vllm/pipelines/ci/builds"
    )


def test_strict_validation_rejects_lookalike_hosts_build_only_jobs_and_foreign_builds():
    source = _dataset(
        [_build(1201, [_job("upstream-job", "GPU Test")])],
        pipeline_slug="ci",
    )
    assert validate_all_main_reliability(source, "ci")

    observation = source["groups"][0]["observations"][0]
    for field, value in (
        (
            "job_url",
            "https://buildkite.com.evil/vllm/ci/builds/1201/steps/canvas?jid=upstream-job",
        ),
        ("job_url", "https://buildkite.com/vllm/ci/builds/1201"),
        ("build_number", 9999),
    ):
        candidate = copy.deepcopy(source)
        candidate["groups"][0]["observations"][0][field] = value
        assert not validate_all_main_reliability(candidate, "ci")
