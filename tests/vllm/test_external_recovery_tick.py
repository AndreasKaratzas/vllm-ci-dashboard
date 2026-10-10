"""The independent clock only wakes guarded recovery and retains retry state."""

import fcntl
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import external_recovery_tick as recovery


NOW = datetime(2026, 10, 10, 5, 30, tzinfo=timezone.utc)
REPOSITORY = recovery.DEFAULT_REPOSITORY


def test_acknowledged_dispatch_survives_restart_and_bounds_tick_frequency(tmp_path):
    state = tmp_path / "state"
    calls = []

    def dispatch(repository, token):
        calls.append((repository, token))
        return 123

    first = recovery.tick(state, REPOSITORY, "secret", now=NOW, dispatch=dispatch)
    assert first["status"] == "dispatched"
    assert first["workflow_run_id"] == 123
    assert (state / "last-dispatch.json").stat().st_mode & 0o777 == 0o600
    assert recovery.tick(
        state, REPOSITORY, "secret", now=NOW + timedelta(seconds=599), dispatch=dispatch
    )["status"] == "cooldown"
    assert len(calls) == 1
    assert recovery.tick(
        state, REPOSITORY, "secret", now=NOW + timedelta(seconds=600), dispatch=dispatch
    )["status"] == "dispatched"
    assert len(calls) == 2


def test_failed_dispatch_does_not_claim_success_or_delay_next_timer_retry(tmp_path):
    state = tmp_path / "state"

    def failed(repository, token):
        raise recovery.RecoveryTickError("transport failed")

    with pytest.raises(recovery.RecoveryTickError, match="transport failed"):
        recovery.tick(state, REPOSITORY, "secret", now=NOW, dispatch=failed)
    assert not (state / "last-dispatch.json").exists()
    assert recovery.tick(state, REPOSITORY, "secret", now=NOW, dispatch=lambda *_: 456)[
        "status"
    ] == "dispatched"


def test_local_overlap_does_not_dispatch_twice(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with (state / "dispatch.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = recovery.tick(
            state, REPOSITORY, "secret", now=NOW,
            dispatch=lambda *_: pytest.fail("overlap must not dispatch"),
        )
    assert result["status"] == "already-running"


@pytest.mark.parametrize(
    "mutation", ["future", "wrong-repository", "wrong-workflow", "corrupt", "symlink"]
)
def test_invalid_local_authority_stops_before_dispatch(tmp_path, mutation):
    state = tmp_path / "state"
    recovery.tick(state, REPOSITORY, "secret", now=NOW, dispatch=lambda *_: 123)
    path = state / "last-dispatch.json"
    payload = json.loads(path.read_text())
    if mutation == "future":
        payload["last_dispatched_at"] = recovery.canonical_time(NOW + timedelta(seconds=601))
        path.write_text(json.dumps(payload))
    elif mutation == "wrong-repository":
        payload["repository"] = "different/repository"
        path.write_text(json.dumps(payload))
    elif mutation == "wrong-workflow":
        payload["workflow"] = "dns-health.yml"
        path.write_text(json.dumps(payload))
    elif mutation == "corrupt":
        path.write_text("{")
    else:
        path.unlink()
        path.symlink_to(tmp_path / "other-file")
    with pytest.raises((recovery.RecoveryTickError, OSError)):
        recovery.tick(
            state, REPOSITORY, "secret", now=NOW + timedelta(minutes=10),
            dispatch=lambda *_: pytest.fail("invalid state must not dispatch"),
        )


@pytest.mark.parametrize("workflow", list(recovery.WORKFLOW_INTERVALS))
def test_dispatch_uses_only_fixed_github_endpoint_main_and_profile(monkeypatch, workflow):
    requests = []

    class Response:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, bound):
            assert bound == recovery.RESPONSE_MAX_BYTES + 1
            return json.dumps({
                "workflow_run_id": 123,
                "run_url": f"https://api.github.com/repos/{REPOSITORY}/actions/runs/123",
                "html_url": f"https://github.com/{REPOSITORY}/actions/runs/123",
            }).encode()

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 30
            return Response()

    monkeypatch.setattr(recovery.urllib.request, "build_opener", lambda *_: Opener())
    assert recovery.dispatch_workflow(REPOSITORY, "secret", workflow) == 123
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == (
        f"https://api.github.com/repos/{REPOSITORY}"
        f"/actions/workflows/{workflow}/dispatches"
    )
    assert request.get_method() == "POST"
    expected = {"ref": "main"}
    if workflow == "deployment-retention.yml":
        expected["inputs"] = {"dry_run": False}
    assert json.loads(request.data) == expected
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("X-github-api-version") == "2026-03-10"


def test_dispatch_refuses_cross_origin_redirect_without_forwarding_token():
    with pytest.raises(recovery.RecoveryTickError, match="redirect refused"):
        recovery.NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere/")


@pytest.mark.parametrize("token", ["", "secret\nheader", " " * 2, "x" * 4097])
def test_bad_token_never_enters_http_transport(monkeypatch, token):
    monkeypatch.setattr(
        recovery.urllib.request, "build_opener", lambda *_: pytest.fail("no transport allowed")
    )
    with pytest.raises(recovery.RecoveryTickError, match="GH_TOKEN"):
        recovery.dispatch_workflow(REPOSITORY, token)


def test_failure_is_redacted_and_does_not_store_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GH_TOKEN", "sensitive-value")
    monkeypatch.setattr(
        recovery, "tick", lambda *_, **__: (_ for _ in ()).throw(
            recovery.RecoveryTickError("GitHub dispatch returned HTTP 401")
        )
    )
    monkeypatch.setattr("sys.argv", ["tick", "--state-directory", str(tmp_path)])
    assert recovery.main() == 1
    output = capsys.readouterr().out
    assert "sensitive-value" not in output
    assert "HTTP 401" in output


def test_host_timer_is_independent_bounded_and_keeps_collector_tokens_out():
    root = Path(__file__).resolve().parents[2]
    units = root / "deploy/recovery-tick"
    timer = (units / "vllm-dashboard-recovery@.timer").read_text()
    service = (units / "vllm-dashboard-recovery@.service").read_text()
    assert "OnUnitActiveSec=10min" in timer
    assert "OnBootSec=90s" in timer
    assert "DynamicUser=yes" in service
    assert "StateDirectoryMode=0700" in service
    assert "TimeoutStartSec=60" in service
    assert "--workflow %i.yml" in service
    assert "--state-directory /var/lib/vllm-dashboard-recovery-%i/dispatch" in service
    assert "BUILDKITE" not in service


def test_systemd_private_parent_symlink_with_real_dispatch_child_succeeds(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    visible = tmp_path / "visible"
    visible.symlink_to(private, target_is_directory=True)
    result = recovery.tick(
        visible / "dispatch", REPOSITORY, "secret", now=NOW, dispatch=lambda *_: 123
    )
    assert result["status"] == "dispatched"
    assert (private / "dispatch/last-dispatch.json").is_file()
    with pytest.raises(recovery.RecoveryTickError, match="must not be a symlink"):
        recovery.tick(visible, REPOSITORY, "secret", now=NOW, dispatch=lambda *_: 123)


@pytest.mark.parametrize("workflow,interval", list(recovery.WORKFLOW_INTERVALS.items()))
def test_profile_cadence_survives_restart_without_changing_collector_limits(
    tmp_path, workflow, interval, monkeypatch
):
    calls = []

    def dispatch(repository, token, selected):
        calls.append((repository, token, selected))
        return 123

    monkeypatch.setattr(recovery, "dispatch_workflow", dispatch)
    state = tmp_path / "state"
    assert recovery.tick(state, REPOSITORY, "secret", workflow=workflow, now=NOW)[
        "status"
    ] == "dispatched"
    assert recovery.tick(
        state, REPOSITORY, "secret", workflow=workflow,
        now=NOW + timedelta(seconds=interval - 1),
    )["status"] == "cooldown"
    assert calls == [(REPOSITORY, "secret", workflow)]
    assert recovery.tick(
        state, REPOSITORY, "secret", workflow=workflow,
        now=NOW + timedelta(seconds=interval),
    )["status"] == "dispatched"
    assert len(calls) == 2


def test_arbitrary_workflow_cannot_dispatch_or_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        recovery.urllib.request, "build_opener", lambda *_: pytest.fail("no transport allowed")
    )
    with pytest.raises(recovery.RecoveryTickError, match="fixed recovery profiles"):
        recovery.dispatch_workflow(REPOSITORY, "secret", "arbitrary.yml")
    with pytest.raises(recovery.RecoveryTickError, match="fixed recovery profiles"):
        recovery.tick(tmp_path / "state", REPOSITORY, "secret", workflow="arbitrary.yml")
    assert not (tmp_path / "state").exists()
