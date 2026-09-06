"""Tests for the standalone Buildkite webhook routing helpers."""

import io
import json

from vllm.ci import webhook


def test_webhook_accepts_standard_amd_nightly():
    assert webhook.is_nightly_build({"message": "AMD Full CI Run - nightly"}, "amd-ci")


def test_webhook_ignores_therock_amd_nightly():
    assert not webhook.is_nightly_build(
        {"message": "AMD Full CI Run - TheRock nightly (2026-06-15, base 9872921c5)"},
        "amd-ci",
    )


def test_webhook_accepts_standard_upstream_nightly():
    assert webhook.is_nightly_build({"message": "Full CI run - nightly"}, "ci")


def test_perf_eval_nightly_requires_canonical_main_build_message():
    assert webhook.is_perf_eval_nightly_build(
        {
            "branch": "main",
            "message": "Nightly run 2026-08-31: commit 93d8f834dd8a",
        }
    )
    assert not webhook.is_perf_eval_nightly_build(
        {"branch": "main", "message": "ad-hoc perf comparison"}
    )
    assert not webhook.is_perf_eval_nightly_build(
        {
            "branch": "feature/experiment",
            "message": "Nightly run 2026-08-31: commit 93d8f834dd8a",
        }
    )
    assert not webhook.is_perf_eval_nightly_build(
        {
            "branch": "master",
            "message": "Nightly run 2026-08-31: commit 93d8f834dd8a",
        }
    )


def test_perf_eval_non_nightly_finished_build_does_not_dispatch(monkeypatch):
    event = {
        "pipeline": {"slug": webhook.PERF_EVAL_PIPELINE},
        "build": {
            "number": 501,
            "branch": "main",
            "message": "manual perf comparison",
            "state": "passed",
        },
    }
    raw = json.dumps(event).encode()
    handler = object.__new__(webhook.WebhookHandler)
    handler.headers = {
        "Content-Length": str(len(raw)),
        "X-Buildkite-Event": "build.finished",
    }
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    statuses = []
    handler.send_response = statuses.append
    handler.end_headers = lambda: None
    monkeypatch.setattr(webhook, "verify_signature", lambda payload, signature: True)
    dispatches = []
    monkeypatch.setattr(
        webhook,
        "trigger_github_dispatch",
        lambda event_type, payload=None: dispatches.append((event_type, payload)) or True,
    )

    handler.do_POST()

    assert statuses == [200]
    assert dispatches == []
    assert handler.wfile.getvalue() == b"Ignored (not canonical perf-eval nightly)"


def test_perf_eval_master_finished_build_does_not_dispatch(monkeypatch):
    event = {
        "pipeline": {"slug": webhook.PERF_EVAL_PIPELINE},
        "build": {
            "number": 502,
            "branch": "master",
            "message": "Nightly run 2026-08-31: commit 93d8f834dd8a",
            "state": "passed",
        },
    }
    raw = json.dumps(event).encode()
    handler = object.__new__(webhook.WebhookHandler)
    handler.headers = {
        "Content-Length": str(len(raw)),
        "X-Buildkite-Event": "build.finished",
    }
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    statuses = []
    handler.send_response = statuses.append
    handler.end_headers = lambda: None
    monkeypatch.setattr(webhook, "verify_signature", lambda payload, signature: True)
    dispatches = []
    monkeypatch.setattr(
        webhook,
        "trigger_github_dispatch",
        lambda event_type, payload=None: dispatches.append((event_type, payload)) or True,
    )

    handler.do_POST()

    assert statuses == [200]
    assert dispatches == []
    assert handler.wfile.getvalue() == b"Ignored (not canonical perf-eval nightly)"


def _job_event(*, pipeline="amd-ci", queue="amd_mi300_1", job_id="job-1"):
    return {
        "pipeline": {"slug": pipeline},
        "build": {"number": 123},
        "job": {
            "id": job_id,
            "cluster_queue": {"key": queue},
            "state": "scheduled",
        },
    }


def test_queue_job_event_requires_configured_pipeline_and_queue():
    context = webhook.queue_event_context("job.scheduled", _job_event())

    assert context == {
        "pipeline": "amd-ci",
        "queue": "amd_mi300_1",
        "build_number": 123,
        "job_id": "job-1",
        "agent_id": "",
    }
    assert webhook.queue_event_context(
        "job.scheduled", _job_event(pipeline="unrelated-pipeline")
    ) is None
    assert webhook.queue_event_context(
        "job.scheduled", _job_event(queue="unconfigured-queue")
    ) is None
    assert webhook.queue_event_context(
        "job.scheduled", _job_event(queue="amd_mi355B_8")
    ) is None


def test_queue_job_event_reads_rules_and_canonicalizes_case():
    event = _job_event(queue="")
    event["job"].pop("cluster_queue")
    event["job"]["agent_query_rules"] = ["queue=AMD_MI300_1"]

    context = webhook.queue_event_context("job.started", event)

    assert context is not None
    assert context["queue"] == "amd_mi300_1"


def test_agent_event_requires_a_configured_queue_but_not_a_pipeline():
    event = {
        "agent": {
            "id": "agent-1",
            "meta_data": ["queue=amd_mi250_4", "k8s:node=node-a"],
        }
    }

    context = webhook.queue_event_context("agent.connected", event)

    assert context is not None
    assert context["pipeline"] == ""
    assert context["queue"] == "amd_mi250_4"
    assert context["agent_id"] == "agent-1"
    assert webhook.queue_event_context("agent.connected", {"agent": {"id": "agent-2"}}) is None


def test_perf_eval_queue_events_remain_relevant():
    context = webhook.queue_event_context(
        "job.started",
        _job_event(pipeline=webhook.PERF_EVAL_PIPELINE, queue="amd_mi355_1"),
    )

    assert context is not None
    assert context["pipeline"] == webhook.PERF_EVAL_PIPELINE


def test_queue_dispatch_coalescer_debounces_successful_bursts_and_exact_retries():
    now = [100.0]
    coalescer = webhook.QueueDispatchCoalescer(
        60,
        600,
        clock=lambda: now[0],
    )

    assert coalescer.claim("first") == "dispatch"
    coalescer.finish("first", True)
    assert coalescer.claim("first") == "duplicate"
    assert coalescer.claim("second") == "debounced"

    now[0] += 61
    # The exact event suppressed by the successful burst remains deduplicated,
    # while a genuinely new event can trigger the next bounded refresh.
    assert coalescer.claim("second") == "duplicate"
    assert coalescer.claim("third") == "dispatch"


def test_queue_dispatch_coalescer_does_not_hide_failed_dispatches():
    now = [100.0]
    coalescer = webhook.QueueDispatchCoalescer(60, 600, clock=lambda: now[0])

    assert coalescer.claim("event") == "dispatch"
    assert coalescer.claim("other") == "in_flight"
    coalescer.finish("event", False)

    assert coalescer.claim("event") == "dispatch"


def test_queue_event_identity_is_stable_for_equivalent_payload_order():
    left = _job_event()
    right = {
        "job": dict(reversed(list(left["job"].items()))),
        "build": left["build"],
        "pipeline": left["pipeline"],
    }
    context = webhook.queue_event_context("job.scheduled", left)

    assert context is not None
    assert webhook.queue_event_identity("job.scheduled", left, context) == (
        webhook.queue_event_identity("job.scheduled", right, context)
    )
