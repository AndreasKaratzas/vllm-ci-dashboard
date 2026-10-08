"""Both runtime populations use main ci with disjoint hardware scopes."""

import pytest
from vllm.pipelines import PIPELINES, _job_queue, is_amd_ci_job, is_upstream_cuda_ci_job


@pytest.mark.parametrize(
    "job,expected",
    [
        ({"agent_query_rules": ["queue=amd_mi355_dpx"], "name": ":amd: (MI355 DPX) Test"}, "amd"),
        (
            {
                "agent": {"meta_data": ["queue=amd_mi300_1"]},
                "agent_query_rules": ["queue=gpu_1"],
                "name": ":nvidia: (H100) Test",
            },
            "amd",
        ),
        ({"q": "gpu_1", "name": "Undecorated test"}, "cuda"),
        ({"q": "amd_mi355_dpx", "raw_name": ":amd: (MI355 DPX) Test", "name": "Test"}, "amd"),
        ({"name": ":amd: (MI355) Test"}, "amd"),
        ({"name": "mi300_1: Test"}, "amd"),
        ({"name": ":nvidia: (B200) Test"}, "cuda"),
        ({"q": "cpu", "name": ":nvidia: (H100) Conflicting label"}, "neither"),
        ({"q": "amd_cpu", "name": ":amd: (CPU) Test"}, "neither"),
        ({"q": "intel_gpu", "name": ":intel: (ARC) Test"}, "neither"),
        ({"name": "Upload pipeline"}, "neither"),
        ({"type": "trigger", "name": ":amd: (MI355) Trigger"}, "neither"),
    ],
)
def test_job_scopes_are_disjoint_and_use_observed_queues_first(job, expected):
    assert is_amd_ci_job(job) is (expected == "amd")
    assert is_upstream_cuda_ci_job(job) is (expected == "cuda")


def test_both_current_populations_have_the_same_authority_and_nightly_filter():
    assert PIPELINES["amd"]["slug"] == PIPELINES["upstream"]["slug"] == "ci"
    assert PIPELINES["amd"]["name_pattern"] == PIPELINES["upstream"]["name_pattern"]
    assert _job_queue({"agent_queue": "amd_mi300_1", "q": "gpu_1"}) == "amd_mi300_1"
