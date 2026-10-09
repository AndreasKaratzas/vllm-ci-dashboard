from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from vllm import buildkite_request_guard as guard
from vllm.ci import log_parser


@pytest.fixture(autouse=True)
def restore_requests_guard():
    session_type = requests.sessions.Session
    original_send = session_type.send
    sentinel = object()
    original_identity = getattr(session_type, "_vllm_buildkite_request_guard", sentinel)
    yield
    session_type.send = original_send
    if original_identity is sentinel:
        try:
            delattr(session_type, "_vllm_buildkite_request_guard")
        except AttributeError:
            pass
    else:
        session_type._vllm_buildkite_request_guard = original_identity


def initialize(tmp_path: Path, *, allowance: int = 2) -> Path:
    path = tmp_path / "guard.json"
    guard.initialize(path, attempt_id="data-100-1", allowance=allowance)
    return path


def prepared(url: str) -> requests.PreparedRequest:
    return requests.Request("GET", url).prepare()


def test_counter_is_atomic_and_blocks_allowance_plus_one_before_transport(
    tmp_path: Path,
) -> None:
    path = initialize(tmp_path, allowance=10)

    def charge(_: int) -> str:
        try:
            guard.consume(path, attempt_id="data-100-1", allowance=10)
            return "sent"
        except guard.BuildkiteRequestGuardError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(pool.map(charge, range(20)))
    assert outcomes.count("sent") == 10
    assert outcomes.count("blocked") == 10
    assert guard.read_count(path, attempt_id="data-100-1", allowance=10) == 10


def test_zero_allowance_is_a_valid_deny_all_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = initialize(tmp_path, allowance=0)
    transported: list[str] = []
    monkeypatch.setattr(
        requests.sessions.Session,
        "send",
        lambda session, request, *args, **kwargs: transported.append(request.url),
    )
    guard.install(path, attempt_id="data-100-1", allowance=0)
    session = requests.Session()

    session.send(prepared("https://github.com/example"))
    with pytest.raises(
        guard.BuildkiteRequestAllowanceExhausted, match="blocked before transport"
    ):
        session.send(prepared("https://api.buildkite.com/v2/builds"))

    assert transported == ["https://github.com/example"]
    assert guard.read_count(path, attempt_id="data-100-1", allowance=0) == 0


def test_transport_deadline_blocks_without_charge_and_reports_after_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10_000_000_000]
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: clock[0])
    path = tmp_path / "guard.json"
    guard.initialize(
        path, attempt_id="data-100-1", allowance=2, max_duration_seconds=1,
    )
    transported: list[str] = []
    monkeypatch.setattr(
        requests.sessions.Session, "send",
        lambda session, request, *args, **kwargs: transported.append(request.url),
    )
    guard.install(path, attempt_id="data-100-1", allowance=2)
    session = requests.Session()
    clock[0] = 10_999_999_999
    session.send(prepared("https://api.buildkite.com/v2/builds"))
    before_expiry = path.read_bytes()
    clock[0] = 11_000_000_000
    with pytest.raises(guard.BuildkiteRequestWindowExpired, match="before transport"):
        session.send(prepared("https://api.buildkite.com/v2/builds"))
    assert path.read_bytes() == before_expiry
    session.send(prepared("https://github.com/example"))
    assert len(transported) == 2
    assert guard.read_count(path, attempt_id="data-100-1", allowance=2) == 1


def test_reservation_handoff_delay_consumes_the_transport_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: 2_000_000_000)
    path = tmp_path / "guard.json"
    guard.initialize(
        path, attempt_id="data-100-1", allowance=2, max_duration_seconds=2,
        started_monotonic_ns=1_000_000_000,
    )
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: 3_000_000_000)
    with pytest.raises(guard.BuildkiteRequestWindowExpired):
        guard.consume(path, attempt_id="data-100-1", allowance=2)
    assert guard.read_count(path, attempt_id="data-100-1", allowance=2) == 0


def test_v2_window_expires_across_processes_and_final_report_remains_readable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guard.json"
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    env = os.environ.copy()
    for name in (*guard.GUARD_ENV_NAMES, *guard.TOKEN_ENV_NAMES):
        env.pop(name, None)
    env["PYTHONPATH"] = str(scripts)
    subprocess.run(
        [sys.executable, "-m", "vllm.buildkite_request_guard", "initialize",
         "--file", str(path), "--attempt-id", "data-100-1", "--allowance", "2",
         "--max-duration-seconds", "1"],
        env=env, check=True, capture_output=True, text=True,
    )
    env.update({
        "BUILDKITE_REQUEST_GUARD_FILE": str(path),
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "data-100-1",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "2",
    })
    code = """
import json
import time
import requests
from vllm import buildkite_request_guard as guard
transported = []
def transport(adapter, request, **kwargs):
    transported.append(request.url)
    response = requests.Response()
    response.status_code = 200
    response._content = b'{}'
    response.request = request
    return response
requests.adapters.HTTPAdapter.send = transport
requests.get('https://api.buildkite.com/v2/builds')
time.sleep(1.05)
try:
    requests.get('https://api.buildkite.com/v2/builds')
except guard.BuildkiteRequestWindowExpired:
    print(json.dumps({'transported': len(transported), 'expired': True}))
else:
    raise AssertionError('expired transport was not blocked')
"""
    child = subprocess.run(
        [sys.executable, "-c", code], env=env, check=True,
        capture_output=True, text=True,
    )
    assert json.loads(child.stdout) == {"transported": 1, "expired": True}
    report = subprocess.run(
        [sys.executable, "-m", "vllm.buildkite_request_guard", "report",
         "--file", str(path), "--attempt-id", "data-100-1", "--allowance", "2"],
        env=env, check=True, capture_output=True, text=True,
    )
    assert report.stdout.strip() == "actual_request_starts=1"


@pytest.mark.parametrize("start", [True, -1, 3_000_000_000, "0"])
def test_invalid_request_window_anchor_cannot_create_a_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: object,
) -> None:
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: 2_000_000_000)
    path = tmp_path / "guard.json"
    with pytest.raises(guard.BuildkiteRequestGuardError):
        guard.initialize(
            path, attempt_id="data-100-1", allowance=2, max_duration_seconds=2,
            started_monotonic_ns=start,
        )
    assert not path.exists()


@pytest.mark.parametrize("duration", [True, 0, -1, 3001, "50"])
def test_invalid_request_duration_cannot_create_a_guard(
    tmp_path: Path, duration: object,
) -> None:
    path = tmp_path / "guard.json"
    with pytest.raises(guard.BuildkiteRequestGuardError):
        guard.initialize(
            path, attempt_id="data-100-1", allowance=2, max_duration_seconds=duration,
        )
    assert not path.exists()


def test_expired_initialization_and_clock_rollback_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: 2_000_000_000)
    path = tmp_path / "guard.json"
    with pytest.raises(guard.BuildkiteRequestWindowExpired):
        guard.initialize(
            path, attempt_id="data-100-1", allowance=2, max_duration_seconds=1,
            started_monotonic_ns=1_000_000_000,
        )
    assert not path.exists()
    guard.initialize(path, attempt_id="data-100-1", allowance=2, max_duration_seconds=1)
    before = path.read_bytes()
    monkeypatch.setattr(guard.time, "monotonic_ns", lambda: 1_999_999_999)
    with pytest.raises(guard.BuildkiteRequestWindowExpired):
        guard.consume(path, attempt_id="data-100-1", allowance=2)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "extended", "boolean", "float-version"])
def test_malformed_v2_request_window_is_rejected(tmp_path: Path, change: str) -> None:
    path = tmp_path / "guard.json"
    guard.initialize(path, attempt_id="data-100-1", allowance=2, max_duration_seconds=1)
    value = json.loads(path.read_text())
    if change == "missing":
        del value["request_window"]
    elif change == "extended":
        value["request_window"]["deadline_monotonic_ns"] += 3000 * 1_000_000_000
    elif change == "boolean":
        value["request_window"]["started_monotonic_ns"] = True
    else:
        value["schema_version"] = 2.0
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    with pytest.raises(guard.BuildkiteRequestGuardError):
        guard.consume(path, attempt_id="data-100-1", allowance=2)


def test_send_level_patch_counts_redirect_hops_and_ignores_non_buildkite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = initialize(tmp_path, allowance=2)
    transported: list[str] = []

    def fake_send(session, request, *args, **kwargs):
        transported.append(request.url)
        if request.url.endswith("/first"):
            return session.send(prepared("https://api.buildkite.com/v2/second"))
        return object()

    monkeypatch.setattr(requests.sessions.Session, "send", fake_send)
    guard.install(path, attempt_id="data-100-1", allowance=2)
    session = requests.Session()
    session.send(prepared("https://github.com/example"))
    session.send(prepared("https://api.buildkite.com/v2/first"))

    assert transported == [
        "https://github.com/example",
        "https://api.buildkite.com/v2/first",
        "https://api.buildkite.com/v2/second",
    ]
    assert guard.read_count(path, attempt_id="data-100-1", allowance=2) == 2
    with pytest.raises(guard.BuildkiteRequestGuardError, match="blocked before transport"):
        session.send(prepared("https://graphql.buildkite.com/v1"))
    assert len(transported) == 3


@pytest.mark.parametrize(
    "url",
    [
        "http://api.buildkite.com/v2/builds",
        "https://api.buildkite.com.evil.example/v2/builds",
        "https://user@api.buildkite.com/v2/builds",
        "https://api.buildkite.com:444/v2/builds",
        "https://buildkite.com/v2/builds",
    ],
)
def test_only_exact_https_api_hosts_are_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    path = initialize(tmp_path, allowance=1)
    monkeypatch.setattr(requests.sessions.Session, "send", lambda *args, **kwargs: object())
    guard.install(path, attempt_id="data-100-1", allowance=1)
    requests.Session().send(prepared(url))
    assert guard.read_count(path, attempt_id="data-100-1", allowance=1) == 0


def test_nonzero_adapter_retry_policy_fails_before_charge_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = initialize(tmp_path, allowance=1)
    transported: list[str] = []
    monkeypatch.setattr(
        requests.sessions.Session,
        "send",
        lambda session, request, *args, **kwargs: transported.append(request.url),
    )
    guard.install(path, attempt_id="data-100-1", allowance=1)
    session = requests.Session()
    session.mount("https://api.buildkite.com", requests.adapters.HTTPAdapter(max_retries=1))
    with pytest.raises(guard.BuildkiteRequestGuardError, match="hidden transport retries"):
        session.send(prepared("https://api.buildkite.com/v2/builds"))
    assert transported == []
    assert guard.read_count(path, attempt_id="data-100-1", allowance=1) == 0


def test_different_identity_is_rejected_without_displacing_the_first_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    guard.initialize(first, attempt_id="data-first-1", allowance=2)
    guard.initialize(second, attempt_id="data-second-1", allowance=2)
    transported: list[str] = []
    monkeypatch.setattr(
        requests.sessions.Session,
        "send",
        lambda session, request, *args, **kwargs: transported.append(request.url),
    )

    guard.install(first, attempt_id="data-first-1", allowance=2)
    with pytest.raises(guard.BuildkiteRequestGuardError, match="different guard identity"):
        guard.install(second, attempt_id="data-second-1", allowance=2)

    requests.Session().send(prepared("https://api.buildkite.com/v2/builds"))
    assert transported == ["https://api.buildkite.com/v2/builds"]
    assert guard.read_count(first, attempt_id="data-first-1", allowance=2) == 1
    assert guard.read_count(second, attempt_id="data-second-1", allowance=2) == 0


def test_missing_corrupt_or_replaced_state_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(guard.BuildkiteRequestGuardError, match="unavailable"):
        guard.read_count(missing, attempt_id="data-100-1", allowance=1)

    path = initialize(tmp_path, allowance=1)
    path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
    with pytest.raises(guard.BuildkiteRequestGuardError, match="strict UTF-8 JSON"):
        guard.read_count(path, attempt_id="data-100-1", allowance=1)
    with pytest.raises(guard.BuildkiteRequestGuardError, match="replace existing"):
        guard.initialize(path, attempt_id="data-100-1", allowance=1)


def test_log_parser_propagates_the_guard_class_instead_of_returning_lower_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Exhausted:
        def get(self, *args, **kwargs):
            raise guard.BuildkiteRequestGuardError("exhausted")

    monkeypatch.setattr(log_parser, "_get_session", lambda: Exhausted())
    monkeypatch.setattr(log_parser.cfg, "BK_TOKEN", "present")
    with pytest.raises(guard.BuildkiteRequestGuardError, match="exhausted"):
        log_parser.fetch_job_log({"raw_log_url": "https://api.buildkite.com/v2/log"})


def test_sitecustomize_and_collectors_share_one_module_and_exception_identity(
    tmp_path: Path,
) -> None:
    path = initialize(tmp_path, allowance=1)
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(scripts),
            "BUILDKITE_REQUEST_GUARD_FILE": str(path),
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "data-100-1",
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "1",
        }
    )
    code = """
import json
import sys
from vllm import buildkite_request_guard as canonical
from vllm.ci import log_parser
print(json.dumps({
    'same_exception': canonical.BuildkiteRequestGuardError is log_parser.BuildkiteRequestGuardError,
    'top_level_loaded': 'buildkite_request_guard' in sys.modules,
    'canonical_loaded': 'vllm.buildkite_request_guard' in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        check=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)
    assert result == {
        "same_exception": True,
        "top_level_loaded": False,
        "canonical_loaded": True,
    }


def test_incomplete_guard_environment_terminates_python_before_script(tmp_path: Path) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(scripts),
            "BUILDKITE_REQUEST_GUARD_FILE": str(tmp_path / "missing.json"),
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "",
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "print('must-not-run')"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 78
    assert "must-not-run" not in completed.stdout
    assert "fatal Buildkite request guard activation error" in completed.stderr


def test_present_but_empty_guard_environment_fails_closed() -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(scripts),
            "BUILDKITE_REQUEST_GUARD_FILE": "",
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "",
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", "print('must-not-run')"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 78
    assert "must-not-run" not in completed.stdout
    assert "fatal Buildkite request guard activation error" in completed.stderr
