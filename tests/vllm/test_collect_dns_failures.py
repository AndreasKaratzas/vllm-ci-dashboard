"""DNS health collector contract, privacy, and boundary regression tests."""

from __future__ import annotations

# cspell:ignore crsuse gaierror Rlcl xoxb

import gzip
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from vllm import collect_dns_failures as collector
from vllm.ci import dns_failures as dns


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def test_private_state_budget_stays_below_encrypted_git_blob_ceiling():
    assert dns.MAX_COMPRESSED_STATE_BYTES == 63 * 1024 * 1024
    assert dns.MAX_COMPRESSED_STATE_BYTES < 90_000_000


def test_state_writer_rejects_oversize_compressed_output(monkeypatch):
    monkeypatch.setattr(dns, "MAX_COMPRESSED_STATE_BYTES", 1)
    with pytest.raises(dns.StateValidationError, match="compressed state exceeds"):
        dns.state_bytes(dns.empty_state(NOW, NOW - timedelta(hours=720)))


def test_state_writer_rejects_oversize_decompressed_output(monkeypatch):
    monkeypatch.setattr(dns, "MAX_DECOMPRESSED_STATE_BYTES", 1)
    with pytest.raises(dns.StateValidationError, match="decompressed state exceeds"):
        dns.state_bytes(dns.empty_state(NOW, NOW - timedelta(hours=720)))


def test_state_reader_uses_bounded_streaming_decompression(monkeypatch):
    state = dns.empty_state(NOW, NOW - timedelta(hours=720))
    compressed = dns.state_bytes(state)

    def reject_unbounded_decompression(_compressed: bytes) -> bytes:
        pytest.fail("state reader used unbounded gzip.decompress")

    monkeypatch.setattr(dns.gzip, "decompress", reject_unbounded_decompression)

    assert dns.state_from_bytes(compressed) == state


def _timestamp(*, hours: float = 0, seconds: float = 0) -> str:
    return dns.iso_timestamp(NOW + timedelta(hours=hours, seconds=seconds))


def _uuid(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012x}"


def _metadata(
    index: int,
    *,
    pipeline: str = "amd-ci",
    finished_hours: float = -0.5,
    started_hours: float | None = None,
    state: str = "passed",
    queue: str = "amd_mi355_1",
    node: str = "crsuse2-m2m-295",
) -> dict:
    return {
        "pipeline": pipeline,
        "build_number": 12000 + index,
        "job_id": _uuid(index),
        "queue": queue,
        "node": node,
        "hardware": dns.queue_hardware(queue),
        "state": state,
        "started_at": _timestamp(
            hours=started_hours if started_hours is not None else finished_hours - 1
        ),
        "finished_at": _timestamp(hours=finished_hours),
    }


def _classification(
    episode_hours: float,
    *,
    matches: int = 1,
    targets: tuple[str, ...] = ("unknown",),
) -> dns.DnsClassification:
    return dns.DnsClassification(
        match_count=matches,
        episode_times=(_timestamp(hours=episode_hours),),
        signature_ids=("temporary_name_resolution",),
        target_categories=targets,
        time_basis="log_timestamp",
    )


def _positive_record(
    index: int,
    *,
    episode_hours: float = -0.25,
    finished_hours: float = -0.1,
    started_hours: float | None = None,
    state: str = "passed",
    targets: tuple[str, ...] = ("unknown",),
) -> dict:
    return dns.scan_record(
        _metadata(
            index,
            finished_hours=finished_hours,
            started_hours=started_hours,
            state=state,
        ),
        _classification(episode_hours, targets=targets),
        attempted_at=_timestamp(),
    )


def _negative_record(index: int, *, finished_hours: float = -0.5) -> dict:
    return dns.scan_record(
        _metadata(index, finished_hours=finished_hours),
        dns.DnsClassification(0, (), (), (), "job_finished_at"),
        attempted_at=_timestamp(),
    )


def _state(rows: list[dict], *, discovery_hours: float = 720) -> dict:
    payload = dns.empty_state(NOW, NOW - timedelta(hours=discovery_hours))
    payload["jobs"] = dns.sort_state_jobs(rows)
    return dns.validate_state(payload)


def _job(
    index: int,
    *,
    state: str = "passed",
    soft_failed: bool = False,
    queue: str = "amd_mi355_1",
    finished_hours: float = -0.5,
    name: str | None = None,
    node: str = "crsuse2-m2m-295",
) -> dict:
    return {
        "id": _uuid(index),
        "type": "script",
        "state": state,
        "soft_failed": soft_failed,
        "name": name or f"test group {index}",
        "started_at": _timestamp(hours=finished_hours - 1),
        "finished_at": _timestamp(hours=finished_hours),
        "agent_query_rules": [f"queue={queue}"],
        "agent": {"meta_data": [f"queue={queue}", f"k8s:node={node}"]},
        "raw_log_url": "https://should-never-be-retained.invalid/private",
        "env": {"SECRET": "should-never-be-retained"},
    }


def _build(number: int, jobs: list[dict]) -> dict:
    return {
        "number": number,
        "branch": "private-contributor-branch",
        "commit": "deadbeef",
        "author": {"email": "private@example.com"},
        "jobs": jobs,
    }


def test_classifier_collapses_sixteen_dns_lines_into_one_five_second_episode():
    base_ms = int((NOW - timedelta(minutes=10)).timestamp() * 1000)
    lines = [
        f"\x1b_bk;t={base_ms + index * 250}\x07 socket.gaierror: "
        "Temporary failure in name resolution while reading "
        "vllm-public-assets.s3.us-west-2.amazonaws.com"
        for index in range(16)
    ]

    result = dns.classify_dns_log(
        "\n".join(lines),
        job_finished_at=_timestamp(),
        job_started_at=_timestamp(hours=-1),
    )

    assert result.match_count == 16
    assert len(result.episode_times) == 1
    assert result.signature_ids == ("temporary_name_resolution",)
    assert result.target_categories == ("vllm_public_assets", "aws_s3")
    assert result.time_basis == "log_timestamp"


def test_buildkite_timestamp_parser_accepts_milliseconds_and_nanoseconds():
    epoch_seconds = int((NOW - timedelta(minutes=5)).timestamp())
    millisecond = dns.classify_dns_log(
        f"\x1b_bk;t={epoch_seconds * 1000} getaddrinfo failed",
        job_finished_at=_timestamp(),
        job_started_at=_timestamp(hours=-1),
    )
    nanosecond = dns.classify_dns_log(
        f"\x1b_bk;t={epoch_seconds * 1_000_000_000} getaddrinfo failed",
        job_finished_at=_timestamp(),
        job_started_at=_timestamp(hours=-1),
    )

    expected = dns.iso_timestamp(datetime.fromtimestamp(epoch_seconds, timezone.utc))
    assert millisecond.episode_times == (expected,)
    assert nanosecond.episode_times == (expected,)


def test_plain_or_out_of_range_timestamp_text_cannot_spoof_an_incident_window():
    spoofed_seconds = int((NOW - timedelta(hours=12)).timestamp() * 1000)
    plain = dns.classify_dns_log(
        f"_bk;t={spoofed_seconds} getaddrinfo failed",
        job_started_at=_timestamp(hours=-1),
        job_finished_at=_timestamp(),
    )
    control_but_out_of_range = dns.classify_dns_log(
        f"\x1b_bk;t={spoofed_seconds} getaddrinfo failed",
        job_started_at=_timestamp(hours=-1),
        job_finished_at=_timestamp(),
    )

    for result in (plain, control_but_out_of_range):
        assert result.episode_times == (_timestamp(),)
        assert result.time_basis == "job_finished_at"


def test_fractional_nanosecond_marker_projects_as_whole_second():
    base_seconds = int((NOW - timedelta(minutes=30)).timestamp())
    result = dns.classify_dns_log(
        f"\x1b_bk;t={base_seconds * 1_000_000_000 + 123_456_000} getaddrinfo failed",
        job_started_at=_timestamp(hours=-1),
        job_finished_at=_timestamp(),
    )
    assert result.episode_times == (
        dns.iso_timestamp(datetime.fromtimestamp(base_seconds, timezone.utc)),
    )
    row = dns.scan_record(
        _metadata(99, finished_hours=-0.1),
        result,
        attempted_at=_timestamp(),
    )
    output = dns.build_public_output(_state([row]))
    item = output["evidence"]["items"][0]
    assert "." not in item["first_at"]
    assert item["first_at"] == item["window_metrics"]["720h"]["first_at"]


def test_classifier_requires_strong_dns_evidence_and_uses_only_target_enums():
    generic_failures = "\n".join(
        [
            "MaxRetryError: too many retries",
            "ReadTimeout: request timed out",
            "SSLError: TLS handshake failed",
            "HTTP 429 Too Many Requests",
            "ConnectionError: connection reset",
        ]
    )
    assert not dns.classify_dns_log(generic_failures, job_finished_at=_timestamp()).positive

    classified = dns.classify_dns_log(
        "curl: (6) Could not resolve host: packages.unfamiliar.example",
        job_finished_at=_timestamp(),
    )
    assert classified.positive
    assert classified.target_categories == ("unknown",)
    assert "packages.unfamiliar.example" not in repr(classified)


def test_episode_gap_is_inclusive_at_five_seconds_and_splits_after_it():
    base = int((NOW - timedelta(hours=1)).timestamp() * 1000)
    text = "\n".join(
        [
            f"\x1b_bk;t={base} no such host",
            f"\x1b_bk;t={base + 5_000} no such host",
            f"\x1b_bk;t={base + 10_001} no such host",
        ]
    )
    result = dns.classify_dns_log(
        text,
        job_finished_at=_timestamp(),
        job_started_at=_timestamp(hours=-2),
    )
    assert result.match_count == 3
    assert len(result.episode_times) == 2


def test_discovery_includes_passed_soft_hard_and_distinct_retry_uuids():
    jobs = [
        _job(1, state="passed"),
        _job(2, state="failed", soft_failed=True),
        _job(3, state="failed"),
        _job(4, state="failed", name="same retried step"),
        _job(5, state="passed", name="same retried step"),
        _job(6, state="canceled"),
        _job(7, state="passed", queue="cpu_queue_postmerge"),
        _job(8, state="passed", queue="amd_mi355b_1"),
    ]

    rows = collector.discover_job_metadata(
        {"amd-ci": [_build(12112, jobs)], "ci": []}
    )

    assert {row["job_id"] for row in rows} == {_uuid(i) for i in range(1, 6)}
    assert {row["state"] for row in rows} == {"passed", "soft", "hard"}
    assert all("job_name" not in row for row in rows)
    assert all("branch" not in row and "commit" not in row and "author" not in row for row in rows)
    assert all("raw_log_url" not in row and "env" not in row for row in rows)


def test_discovery_normalizes_fractional_job_timestamps_to_whole_seconds():
    job = _job(1, state="passed")
    job["started_at"] = "2026-08-16T22:00:00.987654Z"
    job["finished_at"] = "2026-08-16T23:00:00.123456Z"

    [row] = collector.discover_job_metadata(
        {"amd-ci": [_build(12112, [job])], "ci": []}
    )

    assert row["started_at"] == "2026-08-16T22:00:00Z"
    assert row["finished_at"] == "2026-08-16T23:00:00Z"


def test_discovery_fails_closed_when_build_job_inventory_is_malformed():
    with pytest.raises(collector.CollectionError, match="invalid_response"):
        collector.discover_job_metadata({"amd-ci": [{"number": 1}], "ci": []})
    malformed_job = _job(1)
    malformed_job["id"] = "not-a-buildkite-uuid"
    with pytest.raises(collector.CollectionError, match="invalid_response"):
        collector.discover_job_metadata(
            {"amd-ci": [_build(1, [malformed_job])], "ci": []}
        )


def test_arbitrary_job_names_never_enter_state_or_public_evidence():
    synthetic_buildkite_token = "bkua_" + "1" * 20
    synthetic_github_token = "ghp_" + "2" * 24
    synthetic_slack_token = "xoxb-" + "3" * 32
    unsafe = (
        "Traceback owner.person@private.example at https://private.example/path "
        f"token=supersecretvalue {synthetic_buildkite_token} "
        f"{synthetic_github_token} {synthetic_slack_token}\x00"
    )
    job = _job(1, name=unsafe)
    [metadata] = collector.discover_job_metadata(
        {"amd-ci": [_build(12112, [job])], "ci": []}
    )
    assert "job_name" not in metadata
    pending = dns.pending_record(metadata)
    assert "job_name" not in pending

    positive = dns.scan_record(
        metadata,
        _classification(-0.75),
        attempted_at=_timestamp(),
    )
    public = dns.build_public_output(_state([positive]))
    serialized = json.dumps(public)
    assert "job_name" not in public["evidence"]["items"][0]
    for secret in (synthetic_buildkite_token, synthetic_github_token, synthetic_slack_token):
        assert secret not in serialized

    bad_row = dict(pending, job_name=unsafe)
    bad = dns.empty_state(NOW, NOW - timedelta(hours=720))
    bad["jobs"] = [bad_row]
    with pytest.raises(dns.StateValidationError, match="unexpected keys"):
        dns.validate_state(bad)


def test_state_gzip_is_deterministic_strict_and_contains_no_raw_evidence(tmp_path: Path):
    metadata = _metadata(1)
    raw_log = (
        "\x1b_bk;t=1786966200000 Temporary failure in name resolution "
        "https://huggingface.co/private/model?token=supersecretvalue"
    )
    row = dns.scan_record(
        metadata,
        dns.classify_dns_log(
            raw_log,
            job_finished_at=metadata["finished_at"],
            job_started_at=metadata["started_at"],
        ),
        attempted_at=_timestamp(),
    )
    state = _state([row])

    first = dns.state_bytes(state)
    second = dns.state_bytes(state)
    assert first == second
    decoded = gzip.decompress(first).decode()
    public = json.dumps(dns.build_public_output(state))
    for forbidden in (
        "huggingface.co",
        "private.example",
        "supersecretvalue",
        "Temporary failure in name resolution",
        "https://",
        "bkua_",
    ):
        assert forbidden not in decoded
        assert forbidden not in public

    path = tmp_path / "state.json.gz"
    dns.write_state(path, state)
    assert dns.load_state(path) == state
    with pytest.raises(dns.StateValidationError):
        dns.state_from_bytes(b"not gzip")
    malformed = dict(state)
    malformed["unexpected"] = True
    with pytest.raises(dns.StateValidationError, match="unexpected keys"):
        dns.state_bytes(malformed)


class _LogClient:
    def __init__(self, responses: dict[str, str | Exception]):
        self.responses = responses
        self.calls: list[str] = []

    def fetch_job_log(
        self,
        metadata: dict,
        *,
        deadline: float | None = None,
    ) -> tuple[str, int]:
        self.calls.append(metadata["job_id"])
        result = self.responses[metadata["job_id"]]
        if isinstance(result, Exception):
            raise result
        return result, len(result.encode())


def test_scan_cache_skips_final_positive_negative_and_oversize_records():
    positive = _positive_record(1, finished_hours=-1)
    negative = _negative_record(2, finished_hours=-2)
    oversize = dns.oversize_record(
        _metadata(3, finished_hours=-3),
        dns.MAX_LOG_BYTES + 1,
        attempted_at=_timestamp(),
    )
    pending = dns.pending_record(_metadata(4, finished_hours=-0.25))
    client = _LogClient({_uuid(4): "ordinary successful output"})

    rows = collector.scan_records(
        [positive, negative, oversize, pending],
        client=client,
        attempted_at=_timestamp(),
        max_logs=10,
    )

    assert client.calls == [_uuid(4)]
    statuses = {row["job_id"]: row["status"] for row in rows}
    assert statuses == {
        _uuid(1): "positive",
        _uuid(2): "negative",
        _uuid(3): "oversize",
        _uuid(4): "negative",
    }


def test_pending_scan_prioritizes_the_freshest_prefix_with_a_hard_limit():
    pending = [
        dns.pending_record(_metadata(index, finished_hours=-index / 10))
        for index in range(1, 7)
    ]
    client = _LogClient(
        {row["job_id"]: "ordinary output" for row in pending}
    )

    rows = collector.scan_records(
        pending,
        client=client,
        attempted_at=_timestamp(),
        max_logs=4,
    )

    assert client.calls == [_uuid(1), _uuid(2), _uuid(3), _uuid(4)]
    assert [row["status"] for row in rows].count("negative") == 4
    assert [row["status"] for row in rows].count("pending") == 2


def test_pending_order_has_a_stratified_60_40_prefix_and_rotates_coordinates():
    pending = [
        dns.pending_record(
            _metadata(1, finished_hours=-0.01, state="passed")
        ),
        dns.pending_record(
            _metadata(2, finished_hours=-0.02, state="hard")
        ),
        dns.pending_record(
            _metadata(3, finished_hours=-0.03, state="passed")
        ),
        dns.pending_record(
            _metadata(
                4,
                finished_hours=-0.04,
                state="soft",
                queue="amd_mi300_1",
                node="crsuse2-m2m-296",
            )
        ),
        dns.pending_record(
            _metadata(
                5,
                pipeline="ci",
                finished_hours=-0.05,
                state="passed",
                queue="amd_mi325_1",
                node="crsuse2-m2m-297",
            )
        ),
        dns.pending_record(
            _metadata(
                6,
                finished_hours=-0.06,
                state="hard",
                queue="amd_mi300_1",
                node="crsuse2-m2m-296",
            )
        ),
    ]

    ordered = collector._fair_pending_order(pending)

    assert [row["job_id"] for row in ordered[:5]] == [
        _uuid(1),
        _uuid(2),
        _uuid(5),
        _uuid(4),
        _uuid(3),
    ]
    assert [row["state"] for row in ordered[:5]] == [
        "passed",
        "hard",
        "passed",
        "soft",
        "passed",
    ]
    assert sum(row["state"] == "passed" for row in ordered[:5]) == 3


def test_scan_uses_the_configured_bounded_rolling_fetch_window():
    candidate_count = collector.MAX_CONCURRENT_LOG_FETCHES + 3
    pending = [
        dns.pending_record(_metadata(index, finished_hours=-index / 10))
        for index in range(1, candidate_count + 1)
    ]

    class BlockingClient:
        def __init__(self):
            self.lock = threading.Lock()
            self.release = threading.Event()
            self.window_started = threading.Event()
            self.calls: list[str] = []
            self.active = 0
            self.peak_active = 0

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            with self.lock:
                self.calls.append(metadata["job_id"])
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
                if self.active == collector.MAX_CONCURRENT_LOG_FETCHES:
                    self.window_started.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("rolling scanner did not release blocked fetches")
            with self.lock:
                self.active -= 1
            return "ordinary successful output", 26

    client = BlockingClient()
    result: dict[str, object] = {}

    def run_scan() -> None:
        try:
            result["rows"] = collector.scan_records(
                pending,
                client=client,
                attempted_at=_timestamp(),
                max_logs=candidate_count,
            )
        except BaseException as exc:  # make worker failures visible to the test
            result["error"] = exc

    scanner = threading.Thread(target=run_scan)
    scanner.start()
    try:
        assert client.window_started.wait(timeout=5)
        with client.lock:
            assert len(client.calls) == collector.MAX_CONCURRENT_LOG_FETCHES
            assert client.peak_active == collector.MAX_CONCURRENT_LOG_FETCHES
    finally:
        client.release.set()
        scanner.join(timeout=5)

    assert not scanner.is_alive()
    assert "error" not in result
    assert len(client.calls) == candidate_count
    assert client.peak_active == collector.MAX_CONCURRENT_LOG_FETCHES
    assert (
        [row["status"] for row in result["rows"]].count("negative")
        == candidate_count
    )


def test_concurrent_log_window_has_an_explicit_raw_memory_bound():
    assert collector.MAX_CONCURRENT_LOG_FETCHES == 8
    assert collector.MAX_IN_FLIGHT_RAW_LOG_BYTES == 128 * 1024 * 1024


def test_concurrent_client_admission_stays_at_thirty_request_starts_per_minute():
    clock = {"value": 0.0}
    clock_lock = threading.Lock()

    def monotonic() -> float:
        with clock_lock:
            return clock["value"]

    def sleeping(seconds: float) -> None:
        with clock_lock:
            clock["value"] += seconds

    class ConcurrentSession:
        def __init__(self):
            self.headers: dict[str, str] = {}
            self.starts: list[float] = []

        def request(self, method: str, url: str, **kwargs):
            with clock_lock:
                self.starts.append(clock["value"])
            return _FakeResponse(
                200,
                body=b"ordinary output",
                headers={"Content-Type": "text/plain"},
            )

    session = ConcurrentSession()
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeping,
        monotonic=monotonic,
    )
    with ThreadPoolExecutor(max_workers=collector.MAX_CONCURRENT_LOG_FETCHES) as pool:
        futures = [
            pool.submit(client.fetch_job_log, _metadata(index))
            for index in range(1, 32)
        ]
        assert all(future.result()[0] == "ordinary output" for future in futures)

    starts = sorted(session.starts)
    assert starts[0] == 0
    assert starts[-1] == 60
    assert all(later - earlier >= 2 for earlier, later in zip(starts, starts[1:]))


def test_retry_after_blocks_other_concurrent_requesters():
    clock = {"value": 0.0}
    clock_lock = threading.Lock()
    backoff_started = threading.Event()
    release_retry = threading.Event()
    second_request_started = threading.Event()
    sleep_lock = threading.Lock()
    first_backoff_sleep = {"pending": True}

    def monotonic() -> float:
        with clock_lock:
            return clock["value"]

    def sleeping(seconds: float) -> None:
        hold_retry = False
        with sleep_lock:
            if seconds == 6 and first_backoff_sleep["pending"]:
                first_backoff_sleep["pending"] = False
                hold_retry = True
        if hold_retry:
            backoff_started.set()
            if not release_retry.wait(timeout=5):
                raise AssertionError("concurrent requester did not observe shared backoff")
            return
        with clock_lock:
            clock["value"] += seconds

    class RetrySession:
        def __init__(self):
            self.headers: dict[str, str] = {}
            self.lock = threading.Lock()
            self.starts: list[float] = []

        def request(self, method: str, url: str, **kwargs):
            with self.lock, clock_lock:
                self.starts.append(clock["value"])
                request_number = len(self.starts)
            if request_number == 1:
                return _FakeResponse(429, headers={"Retry-After": "6"})
            second_request_started.set()
            return _FakeResponse(
                200,
                body=b"ordinary output",
                headers={"Content-Type": "text/plain"},
            )

    session = RetrySession()
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeping,
        monotonic=monotonic,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        retrying = pool.submit(client.fetch_job_log, _metadata(1))
        assert backoff_started.wait(timeout=5)
        concurrent = pool.submit(client.fetch_job_log, _metadata(2))
        try:
            assert second_request_started.wait(timeout=5)
        finally:
            release_retry.set()
        assert retrying.result()[0] == "ordinary output"
        assert concurrent.result()[0] == "ordinary output"

    assert session.starts[0] == 0
    assert session.starts[1] >= 6
    assert all(
        later - earlier >= 2
        for earlier, later in zip(sorted(session.starts), sorted(session.starts)[1:])
    )


def test_shared_deadline_stops_rolling_submission_after_in_flight_requests():
    candidate_count = collector.MAX_CONCURRENT_LOG_FETCHES + 3
    pending = [
        dns.pending_record(_metadata(index, finished_hours=-index / 10))
        for index in range(1, candidate_count + 1)
    ]
    clock = {"value": 0.0}
    clock_lock = threading.Lock()
    started = threading.Barrier(collector.MAX_CONCURRENT_LOG_FETCHES)

    def monotonic() -> float:
        with clock_lock:
            return clock["value"]

    class DeadlineClient:
        def __init__(self):
            self.calls: list[str] = []

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            with clock_lock:
                assert clock["value"] < deadline
                self.calls.append(metadata["job_id"])
            started.wait(timeout=5)
            with clock_lock:
                clock["value"] = deadline
            return "ordinary successful output", 26

    client = DeadlineClient()
    rows = collector.scan_records(
        pending,
        client=client,
        attempted_at=_timestamp(),
        max_logs=candidate_count,
        deadline=10.0,
        monotonic=monotonic,
    )

    expected_first_window = {
        row["job_id"]
        for row in collector._fair_pending_order(pending)[
            : collector.MAX_CONCURRENT_LOG_FETCHES
        ]
    }
    assert set(client.calls) == expected_first_window
    assert len(client.calls) == collector.MAX_CONCURRENT_LOG_FETCHES
    assert (
        [row["status"] for row in rows].count("negative")
        == collector.MAX_CONCURRENT_LOG_FETCHES
    )
    assert [row["status"] for row in rows].count("pending") == 3


def test_budget_exhaustion_never_starts_a_candidate_beyond_the_active_window():
    candidate_count = collector.MAX_CONCURRENT_LOG_FETCHES + 3
    pending = [
        dns.pending_record(_metadata(index, finished_hours=-index / 10))
        for index in range(1, candidate_count + 1)
    ]
    fair_order = collector._fair_pending_order(pending)
    exhausting_job = fair_order[0]["job_id"]
    initial_window = {
        row["job_id"]
        for row in fair_order[: collector.MAX_CONCURRENT_LOG_FETCHES]
    }
    exhausted = threading.Event()

    class ExhaustingClient:
        def __init__(self):
            self.lock = threading.Lock()
            self.calls: list[str] = []

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            with self.lock:
                self.calls.append(metadata["job_id"])
            if metadata["job_id"] == exhausting_job:
                exhausted.set()
                raise collector.BudgetExhausted()
            if not exhausted.wait(timeout=5):
                raise AssertionError("oldest candidate did not exhaust the budget")
            return "ordinary successful output", 26

    client = ExhaustingClient()
    rows = collector.scan_records(
        pending,
        client=client,
        attempted_at=_timestamp(),
        max_logs=candidate_count,
    )

    assert exhausting_job in client.calls
    assert set(client.calls) <= initial_window
    assert len(client.calls) <= collector.MAX_CONCURRENT_LOG_FETCHES
    assert [row["status"] for row in rows].count("pending") >= 4


def test_deadline_shortened_scan_starts_with_freshest_work():
    pending = [
        dns.pending_record(_metadata(index, finished_hours=-index / 10))
        for index in range(1, 5)
    ]
    elapsed = [0.0]

    class DeadlineClient(_LogClient):
        def fetch_job_log(
            self,
            metadata: dict,
            *,
            deadline: float | None = None,
        ) -> tuple[str, int]:
            result = super().fetch_job_log(metadata, deadline=deadline)
            elapsed[0] += 1.0
            return result

    client = DeadlineClient(
        {row["job_id"]: "ordinary output" for row in pending}
    )

    collector.scan_records(
        pending,
        client=client,
        attempted_at=_timestamp(),
        max_logs=4,
        deadline=2.0,
        monotonic=lambda: elapsed[0],
    )

    assert client.calls == [_uuid(1), _uuid(2)]


def test_unavailable_is_retried_after_new_pending_work():
    unavailable = dns.unavailable_record(
        _metadata(1, finished_hours=-0.01),
        "network_error",
        attempted_at=_timestamp(hours=-1),
    )
    newest = dns.pending_record(_metadata(2, finished_hours=-0.1))
    client = _LogClient(
        {
            _uuid(1): "getaddrinfo failed",
            _uuid(2): "ordinary output",
        }
    )

    limited = collector.scan_records(
        [unavailable, newest],
        client=client,
        attempted_at=_timestamp(),
        max_logs=1,
    )
    assert client.calls == [_uuid(2)]
    assert {row["job_id"]: row["status"] for row in limited} == {
        _uuid(1): "unavailable",
        _uuid(2): "negative",
    }

    retry_client = _LogClient({_uuid(1): "getaddrinfo failed"})
    retried = collector.scan_records(
        [unavailable],
        client=retry_client,
        attempted_at=_timestamp(),
        max_logs=1,
    )
    assert retried[0]["status"] == "positive"
    assert retried[0]["attempts"] == 2


def test_unavailable_and_oversize_are_never_cached_as_negative():
    pending_network = dns.pending_record(_metadata(1))
    pending_large = dns.pending_record(_metadata(2, finished_hours=-0.25))
    client = _LogClient(
        {
            _uuid(1): collector.LogUnavailable("rate_limited"),
            _uuid(2): collector.OversizeLog(dns.MAX_LOG_BYTES + 10),
        }
    )
    rows = collector.scan_records(
        [pending_network, pending_large],
        client=client,
        attempted_at=_timestamp(),
        max_logs=10,
    )
    assert {row["status"] for row in rows} == {"unavailable", "oversize"}
    assert not any(row["status"] == "negative" for row in rows)


def test_missing_agent_node_is_recovered_from_safe_full_log_banner():
    metadata = _metadata(1, node="unidentified")
    pending = dns.pending_record(metadata)
    client = _LogClient(
        {
            _uuid(1): (
                "=== Pod: buildkite-amd-abc | Node: crsuse2-m2m-295 | now ===\n"
                "ordinary successful output"
            )
        }
    )
    rows = collector.scan_records(
        [pending],
        client=client,
        attempted_at=_timestamp(),
        max_logs=1,
    )
    assert rows[0]["node"] == "crsuse2-m2m-295"
    assert rows[0]["status"] == "negative"


def test_later_discovery_does_not_erase_log_recovered_node(tmp_path: Path):
    job = _job(1, node="worker.private.example.com")
    marker_ms = int((NOW - timedelta(minutes=45)).timestamp() * 1000)

    class Client:
        def __init__(self):
            self.log_calls = 0

        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ):
            return [_build(12112, [job])] if pipeline == "amd-ci" else []

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            self.log_calls += 1
            text = (
                "=== Pod: buildkite-amd-abc | Node: crsuse2-m2m-295 | now ===\n"
                f"\x1b_bk;t={marker_ms} getaddrinfo failed"
            )
            return text, len(text.encode())

    client = Client()
    state_path = tmp_path / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    first = collector.collect(
        client=client,
        state_path=state_path,
        output_path=output_path,
        now=NOW,
    )
    second = collector.collect(
        client=client,
        state_path=state_path,
        output_path=output_path,
        now=NOW + timedelta(hours=1),
    )

    assert client.log_calls == 1
    assert first["evidence"]["items"][0]["node"] == "crsuse2-m2m-295"
    assert second["evidence"]["items"][0]["node"] == "crsuse2-m2m-295"
    assert dns.load_state(state_path)["jobs"][0]["node"] == "crsuse2-m2m-295"


def test_window_coverage_uses_episode_time_for_a_long_job():
    # The job finishes in the last hour, but its only DNS episode is two hours old.
    long_job = _positive_record(
        1,
        episode_hours=-2,
        finished_hours=-0.5,
        started_hours=-3,
        targets=("huggingface_hub",),
    )
    output = dns.build_public_output(_state([long_job]))

    one_hour = output["windows"]["1h"]
    assert one_hour["coverage"]["eligible_jobs"] == 1
    assert one_hour["coverage"]["positive_jobs"] == 0
    assert one_hour["coverage"]["negative_jobs"] == 1
    assert one_hour["totals"]["affected_jobs"] == 0
    three_hours = output["windows"]["3h"]
    assert three_hours["coverage"]["positive_jobs"] == 1
    assert three_hours["totals"]["affected_jobs"] == 1


def test_window_rollups_and_evidence_keep_old_hf_separate_from_recent_github():
    old_ms = int((NOW - timedelta(hours=2)).timestamp() * 1000)
    recent_ms = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
    raw_log = "\n".join(
        [
            f"\x1b_bk;t={old_ms} getaddrinfo failed for huggingface.co",
            "x" * 3000,
            f"\x1b_bk;t={recent_ms} getaddrinfo failed for github.com",
        ]
    )
    metadata = _metadata(88, finished_hours=-0.1, started_hours=-3)
    classification = dns.classify_dns_log(
        raw_log,
        job_started_at=metadata["started_at"],
        job_finished_at=metadata["finished_at"],
    )
    assert [metric.target_categories for metric in classification.episode_metrics] == [
        ("huggingface_hub",),
        ("github",),
    ]
    row = dns.scan_record(metadata, classification, attempted_at=_timestamp())
    output = dns.build_public_output(_state([row]))

    one_hour = output["windows"]["1h"]
    assert one_hour["totals"]["affected_jobs"] == 1
    assert one_hour["totals"]["huggingface_affected_jobs"] == 0
    three_hours = output["windows"]["3h"]
    assert three_hours["totals"]["huggingface_affected_jobs"] == 1
    item = output["evidence"]["items"][0]
    assert item["window_ids"] == ["1h", "3h", "12h", "24h", "72h", "168h", "720h"]
    assert item["window_metrics"]["1h"] == {
        "first_at": _timestamp(hours=-0.5),
        "last_at": _timestamp(hours=-0.5),
        "episodes": 1,
        "match_count": 1,
        "signature_ids": ["getaddrinfo_failed"],
        "target_categories": ["github"],
    }
    assert item["window_metrics"]["3h"]["episodes"] == 2
    assert item["window_metrics"]["3h"]["match_count"] == 2
    assert item["window_metrics"]["3h"]["target_categories"] == [
        "huggingface_hub",
        "github",
    ]
    assert {
        key: item[key]
        for key in (
            "first_at",
            "last_at",
            "episodes",
            "match_count",
            "signature_ids",
            "target_categories",
        )
    } == item["window_metrics"]["720h"]


def test_window_start_is_inclusive_and_distinct_job_counts_match_evidence():
    at_start = _positive_record(1, episode_hours=-1, finished_hours=-0.25)
    just_inside = _positive_record(2, episode_hours=-0.5, finished_hours=-0.1)
    output = dns.build_public_output(_state([at_start, just_inside]))

    one_hour = output["windows"]["1h"]
    assert one_hour["totals"]["affected_jobs"] == 2
    assert one_hour["totals"]["episodes"] == 2
    assert one_hour["coverage"]["positive_jobs"] == 2
    assert one_hour["coverage"]["eligible_jobs"] == 2
    assert one_hour["coverage"]["negative_jobs"] == 0
    assert output["evidence"]["evidence_total"] == 2
    assert len({item["job_id"] for item in output["evidence"]["items"]}) == 2


def test_public_windows_reconcile_affected_jobs_by_terminal_outcome():
    rows = [
        _positive_record(1, state="passed"),
        _positive_record(2, state="soft"),
        _positive_record(3, state="hard"),
    ]

    output = dns.build_public_output(_state(rows))
    one_hour = output["windows"]["1h"]
    outcome_counts = {
        "passed_jobs": 1,
        "soft_failed_jobs": 1,
        "hard_failed_jobs": 1,
    }

    assert output["outcome_contract"] == "dns-job-outcomes-v1"
    assert one_hour["totals"]["affected_jobs"] == 3
    assert {key: one_hour["totals"][key] for key in outcome_counts} == outcome_counts
    assert len(one_hour["rows"]) == 1
    node_row = one_hour["rows"][0]
    assert node_row["affected_jobs"] == 3
    assert {key: node_row[key] for key in outcome_counts} == outcome_counts
    assert sum(one_hour["totals"][key] for key in outcome_counts) == 3
    assert sum(node_row[key] for key in outcome_counts) == 3


def test_partial_coverage_reconciles_every_cached_status():
    rows = [
        _positive_record(1),
        _negative_record(2),
        dns.pending_record(_metadata(3)),
        dns.unavailable_record(
            _metadata(4),
            "not_found",
            attempted_at=_timestamp(),
        ),
        dns.oversize_record(
            _metadata(5),
            dns.MAX_LOG_BYTES + 1,
            attempted_at=_timestamp(),
        ),
    ]
    output = dns.build_public_output(_state(rows, discovery_hours=72))
    coverage = output["windows"]["1h"]["coverage"]
    assert coverage == {
        "status": "partial",
        "complete": False,
        "discovery_complete": True,
        "eligible_jobs": 5,
        "scanned_jobs": 2,
        "positive_jobs": 1,
        "negative_jobs": 1,
        "pending_jobs": 1,
        "unavailable_jobs": 1,
        "oversize_jobs": 1,
    }
    assert output["windows"]["720h"]["coverage"]["discovery_complete"] is False


def test_retention_pruning_is_half_open():
    start = NOW - timedelta(hours=dns.RETENTION_HOURS)
    before = dns.pending_record(_metadata(1, finished_hours=-721))
    at_start = dns.pending_record(_metadata(2, finished_hours=-720))
    inside = dns.pending_record(_metadata(3, finished_hours=-1))
    at_end = dns.pending_record(_metadata(4, finished_hours=0))

    retained = dns.prune_state_jobs([before, at_start, inside, at_end], start, NOW)
    assert {row["job_id"] for row in retained} == {_uuid(2), _uuid(3)}


def test_public_writer_preserves_canonical_window_order(tmp_path: Path):
    output = dns.build_public_output(_state([_positive_record(1)]))
    path = tmp_path / "dns_failures.json"
    dns.write_public_output(path, output)
    round_tripped = json.loads(path.read_text())
    assert list(round_tripped["windows"]) == [item["id"] for item in dns.WINDOW_OPTIONS]
    assert path.read_bytes().endswith(b"\n")


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        json_payload: object | None = None,
    ):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self._json_payload = json_payload
        self.closed = False

    def json(self):
        if self._json_payload is None:
            raise requests.exceptions.JSONDecodeError("bad", "", 0)
        return self._json_payload

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._body), max(1, min(chunk_size, 7))):
            yield self._body[offset : offset + max(1, min(chunk_size, 7))]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_build_discovery_is_all_branch_and_includes_retried_jobs():
    session = _FakeSession([_FakeResponse(200, json_payload=[])])
    client = collector.BuildkiteClient("memory-only-token", session=session, sleep=lambda _: None)
    assert client.build_page(
        "amd-ci",
        filters={"finished_from": _timestamp(hours=-24)},
        page=1,
    ) == []
    params = session.calls[0]["params"]
    assert session.calls[0]["allow_redirects"] is False
    assert params["include_retried_jobs"] == "true"
    assert params["exclude_pipeline"] == "true"
    assert "branch" not in params
    assert params["per_page"] == 100
    assert client.request_starts() == {
        "build_page": 1,
        "graphql": 0,
        "job_log": 0,
    }


def test_hard_request_cap_counts_retries_before_the_network_start():
    session = _FakeSession([_FakeResponse(429, headers={"Retry-After": "0"})])
    client = collector.BuildkiteClient(
        "memory-only-token",
        max_request_starts=1,
        session=session,
        sleep=lambda _: None,
    )

    with pytest.raises(collector.RequestBudgetExhausted):
        client.build_page(
            "amd-ci",
            filters={"finished_from": _timestamp(hours=-1)},
            page=1,
        )

    assert len(session.calls) == 1
    assert client.request_starts() == {
        "build_page": 1,
        "graphql": 0,
        "job_log": 0,
    }


def test_direct_client_uses_the_bounded_production_request_default():
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=_FakeSession([]),
        sleep=lambda _: None,
    )

    assert collector.DEFAULT_MAX_REQUESTS == 110
    assert client.max_request_starts == 110


def test_hard_request_cap_is_shared_across_rest_graphql_and_logs():
    session = _FakeSession(
        [
            _FakeResponse(200, json_payload=[]),
            _FakeResponse(200, json_payload={"data": {}}),
            _FakeResponse(
                200,
                body=b"ordinary output",
                headers={"Content-Type": "text/plain"},
            ),
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        max_request_starts=3,
        session=session,
        sleep=lambda _: None,
    )

    assert client.build_page(
        "amd-ci",
        filters={"finished_from": _timestamp(hours=-1)},
        page=1,
    ) == []
    assert client._graphql("query Probe { viewer { id } }", {}, deadline=None) == {}
    assert client.fetch_job_log(_metadata(1))[0] == "ordinary output"
    with pytest.raises(collector.RequestBudgetExhausted):
        client.fetch_job_log(_metadata(2))

    assert len(session.calls) == 3
    assert client.request_starts() == {
        "build_page": 1,
        "graphql": 1,
        "job_log": 1,
    }


def test_hard_request_cap_is_thread_safe_for_concurrent_log_workers():
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                body=b"ordinary output",
                headers={"Content-Type": "text/plain"},
            )
            for _ in range(3)
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        max_request_starts=3,
        session=session,
        sleep=lambda _: None,
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(client.fetch_job_log, _metadata(index))
            for index in range(1, 9)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result()[0])
            except collector.RequestBudgetExhausted:
                outcomes.append("request-budget-exhausted")

    assert outcomes.count("ordinary output") == 3
    assert outcomes.count("request-budget-exhausted") == 5
    assert len(session.calls) == 3
    assert client.request_starts() == {
        "build_page": 0,
        "graphql": 0,
        "job_log": 3,
    }


def test_incremental_graphql_discovers_all_eligible_terminal_outcomes():
    queue = "amd_mi355_1"
    queue_id = "Q2x1c3RlclF1ZXVlLS0x"

    def node(
        index: int,
        state: str,
        *,
        passed: bool,
        soft_failed: bool,
        pipeline: str = "amd-ci",
    ) -> dict:
        return {
            "uuid": _uuid(index),
            "createdAt": _timestamp(hours=-0.75 + index / 100),
            "startedAt": _timestamp(hours=-0.5),
            "finishedAt": _timestamp(hours=-0.1),
            "state": state,
            "passed": passed,
            "softFailed": soft_failed,
            "agent": {
                "metaData": [f"queue={queue}", "k8s:node=crsuse2-m2m-295"]
            },
            "clusterQueue": {"id": queue_id, "key": queue},
            "build": {
                "number": 13000 + index,
                "pipeline": {"slug": pipeline},
            },
        }

    queue_payload = {
        "data": {
            "organization": {
                "cluster": {
                    "queues": {
                        "edges": [
                            {"node": {"id": queue_id, "key": queue}},
                            {"node": {"id": "ignored", "key": "default"}},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    jobs_payload = {
        "data": {
            "organization": {
                "jobs": {
                    "edges": [
                        {"node": node(1, "FINISHED", passed=True, soft_failed=False)},
                        {"node": node(2, "FINISHED", passed=True, soft_failed=True)},
                    ],
                    "pageInfo": {"hasNextPage": True, "endCursor": "jobs-page-2"},
                }
            }
        }
    }
    jobs_page_2_payload = {
        "data": {
            "organization": {
                "jobs": {
                    "edges": [
                        {"node": node(3, "TIMED_OUT", passed=False, soft_failed=False)},
                        {"node": node(4, "BROKEN", passed=False, soft_failed=False)},
                        {"node": node(5, "EXPIRED", passed=False, soft_failed=False)},
                        {
                            "node": node(
                                6,
                                "FINISHED",
                                passed=True,
                                soft_failed=False,
                                pipeline="unrelated-pipeline",
                            )
                        },
                        {
                            "node": node(
                                7,
                                "FINISHED",
                                passed=True,
                                soft_failed=False,
                            )
                            | {"createdAt": _timestamp(hours=-3)}
                        },
                    ],
                    # Indexed connections may advertise another page while
                    # spilling rows just below createdAtFrom. The collector
                    # must stop locally at that ordered boundary.
                    "pageInfo": {
                        "hasNextPage": True,
                        "endCursor": "must-not-be-requested",
                    },
                }
            }
        }
    }
    session = _FakeSession(
        [
            _FakeResponse(200, json_payload=queue_payload),
            _FakeResponse(200, json_payload=jobs_payload),
            _FakeResponse(200, json_payload=jobs_page_2_payload),
            _FakeResponse(200, json_payload=[]),
            _FakeResponse(200, json_payload=[]),
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=lambda _: None,
    )

    rows = client.discover_incremental_job_metadata(
        created_from=_timestamp(hours=-2),
        finished_from=_timestamp(hours=-2),
    )

    assert {row["job_id"]: row["state"] for row in rows} == {
        _uuid(1): "passed",
        _uuid(2): "soft",
        _uuid(3): "hard",
        _uuid(4): "hard",
        _uuid(5): "hard",
    }
    queue_call, jobs_call, jobs_page_2_call, amd_rest_call, ci_rest_call = session.calls
    assert queue_call["method"] == jobs_call["method"] == "POST"
    assert queue_call["url"] == jobs_call["url"] == collector.BUILDKITE_GRAPHQL_API
    assert "$org: ID!" in queue_call["json"]["query"]
    assert "$org: ID!" in jobs_call["json"]["query"]
    assert queue_call["json"]["variables"]["cluster"] == collector.BK_CLUSTER_UUID
    assert jobs_call["json"]["variables"]["queues"] == [queue_id]
    assert jobs_call["json"]["variables"]["from"] == _timestamp(hours=-2)
    assert jobs_call["json"]["variables"]["after"] is None
    assert jobs_page_2_call["json"]["variables"]["after"] == "jobs-page-2"
    assert "createdAtFrom: $from" in jobs_call["json"]["query"]
    assert "clusterQueue: $queues" in jobs_call["json"]["query"]
    assert "FINISHED, TIMED_OUT, BROKEN, EXPIRED" in jobs_call["json"]["query"]
    assert amd_rest_call["params"]["finished_from"] == _timestamp(hours=-2)
    assert ci_rest_call["params"]["finished_from"] == _timestamp(hours=-2)
    assert client.request_starts() == {
        "build_page": 2,
        "graphql": 3,
        "job_log": 0,
    }


def test_discovery_bounds_active_parent_builds_and_unions_finished_cohort():
    old_created_finished_inside = _build(100, [_job(1)])
    old_created_finished_inside["created_at"] = _timestamp(hours=-800)
    old_created_finished_inside["state"] = "blocked"
    current = _build(101, [_job(2)])
    current["state"] = "running"
    finished_current = {**current, "state": "passed"}
    session = _FakeSession(
        [
            _FakeResponse(200, json_payload=[current]),
            _FakeResponse(
                200,
                json_payload=[old_created_finished_inside, finished_current],
            ),
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=lambda _: None,
    )
    builds = client.discover_builds(
        "amd-ci",
        finished_from=_timestamp(hours=-2),
        active_created_from=_timestamp(hours=-24),
        active_created_to=_timestamp(),
    )

    assert {build["number"] for build in builds} == {100, 101}
    assert next(build for build in builds if build["number"] == 101)["state"] == "passed"
    active_params, finished_params = [call["params"] for call in session.calls]
    assert active_params["state[]"] == list(collector.ACTIVE_BUILD_STATES)
    assert "blocked" in active_params["state[]"]
    assert active_params["created_from"] == _timestamp(hours=-24)
    assert active_params["created_to"] == _timestamp()
    assert finished_params["finished_from"] == _timestamp(hours=-2)
    assert "created_from" not in finished_params
    assert all(params["include_retried_jobs"] == "true" for params in (active_params, finished_params))

    rows = collector.discover_job_metadata({"amd-ci": builds, "ci": []})
    assert {row["job_id"] for row in rows} == {_uuid(1), _uuid(2)}


def test_active_discovery_uses_bounded_slices_and_deterministic_dedupe():
    slice_hours = collector.ACTIVE_DISCOVERY_SLICE_HOURS
    assert slice_hours == 7 * 24

    class SlicedClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.lock = threading.Lock()
            self.three_started = threading.Event()
            self.active = 0
            self.peak_active = 0
            self.slice_calls: list[dict] = []

        def _active_slice_builds(
            self,
            pipeline: str,
            *,
            created_from: datetime,
            created_to: datetime,
            deadline: float | None = None,
        ) -> list[dict]:
            with self.lock:
                self.active += 1
                self.peak_active = max(self.peak_active, self.active)
                self.slice_calls.append(
                    {
                        "created_from": collector.iso_timestamp(created_from),
                        "created_to": collector.iso_timestamp(created_to),
                    }
                )
                call_number = len(self.slice_calls)
                if self.active == collector.MAX_CONCURRENT_ACTIVE_SLICES:
                    self.three_started.set()
            assert self.three_started.wait(timeout=1)
            with self.lock:
                self.active -= 1
            return [{"number": call_number}, {"number": 999}]

        def _paginate_builds(
            self,
            pipeline: str,
            *,
            filters: dict,
            deadline: float | None = None,
        ) -> list[dict]:
            assert "finished_from" in filters
            return [{"number": 777}]

    client = SlicedClient()
    builds = client.discover_builds(
        "amd-ci",
        finished_from=_timestamp(hours=-2),
        active_created_from=_timestamp(hours=-(slice_hours * 3 + 1)),
        active_created_to=_timestamp(),
    )

    assert client.peak_active == collector.MAX_CONCURRENT_ACTIVE_SLICES
    assert len(client.slice_calls) == 4
    assert {
        (call["created_from"], call["created_to"])
        for call in client.slice_calls
    } == {
        (_timestamp(hours=-slice_hours), _timestamp()),
        (_timestamp(hours=-(slice_hours * 2)), _timestamp(hours=-slice_hours)),
        (
            _timestamp(hours=-(slice_hours * 3)),
            _timestamp(hours=-(slice_hours * 2)),
        ),
        (
            _timestamp(hours=-(slice_hours * 3 + 1)),
            _timestamp(hours=-(slice_hours * 3)),
        ),
    }
    assert {build["number"] for build in builds} == {1, 2, 3, 4, 777, 999}


def test_full_active_page_is_bisected_into_single_page_time_ranges():
    class AdaptiveClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.calls: list[dict] = []

        def build_page(
            self,
            pipeline: str,
            *,
            filters: dict,
            page: int,
            deadline: float | None = None,
        ) -> list[dict]:
            self.calls.append({**filters, "page": page})
            bounds = (filters["created_from"], filters["created_to"])
            if bounds == (_timestamp(hours=-24), _timestamp()):
                return [{"number": number} for number in range(1, 101)]
            if bounds == (_timestamp(hours=-12), _timestamp()):
                return [{"number": number} for number in range(1, 61)]
            if bounds == (_timestamp(hours=-24), _timestamp(hours=-12)):
                return [{"number": number} for number in range(61, 121)]
            raise AssertionError(f"unexpected range: {bounds}")

    client = AdaptiveClient()
    builds = client._active_slice_builds(
        "ci",
        created_from=NOW - timedelta(hours=24),
        created_to=NOW,
        deadline=123.0,
    )

    assert [build["number"] for build in builds] == list(range(1, 121))
    assert [
        (call["created_from"], call["created_to"])
        for call in client.calls
    ] == [
        (_timestamp(hours=-24), _timestamp()),
        (_timestamp(hours=-12), _timestamp()),
        (_timestamp(hours=-24), _timestamp(hours=-12)),
    ]
    assert all(call["page"] == 1 for call in client.calls)
    assert all(
        call["state[]"] == list(collector.ACTIVE_BUILD_STATES)
        for call in client.calls
    )


def test_full_active_pages_fail_closed_at_subdivision_limit(monkeypatch):
    class AlwaysFullClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.calls = 0

        def build_page(
            self,
            pipeline: str,
            *,
            filters: dict,
            page: int,
            deadline: float | None = None,
        ) -> list[dict]:
            self.calls += 1
            return [{"number": number} for number in range(1, 101)]

    monkeypatch.setattr(collector, "MAX_DISCOVERY_PAGES", 3)
    client = AlwaysFullClient()
    with pytest.raises(collector.CollectionError, match="invalid_response"):
        client._active_slice_builds(
            "ci",
            created_from=NOW - timedelta(hours=24),
            created_to=NOW,
            deadline=None,
        )
    assert client.calls == 3


def test_full_active_probe_rejects_malformed_rows_before_subdivision():
    class MalformedClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.calls = 0

        def build_page(
            self,
            pipeline: str,
            *,
            filters: dict,
            page: int,
            deadline: float | None = None,
        ) -> list[dict]:
            self.calls += 1
            return [{"number": True}, *({"number": number} for number in range(2, 101))]

    client = MalformedClient()
    with pytest.raises(collector.CollectionError, match="invalid_response"):
        client._active_slice_builds(
            "ci",
            created_from=NOW - timedelta(hours=24),
            created_to=NOW,
            deadline=None,
        )
    assert client.calls == 1


def test_full_active_probe_fails_closed_when_timestamp_cannot_split():
    class DenseClient(collector.BuildkiteClient):
        def build_page(
            self,
            pipeline: str,
            *,
            filters: dict,
            page: int,
            deadline: float | None = None,
        ) -> list[dict]:
            return [{"number": number} for number in range(1, 101)]

    client = DenseClient(
        "memory-only-token",
        session=_FakeSession([]),
        sleep=lambda _: None,
    )
    with pytest.raises(collector.CollectionError, match="invalid_response"):
        client._active_slice_builds(
            "ci",
            created_from=NOW,
            created_to=NOW + timedelta(microseconds=1),
            deadline=None,
        )


def test_active_discovery_slice_failure_starts_no_fourth_slice():
    slice_hours = collector.ACTIVE_DISCOVERY_SLICE_HOURS

    class FailingClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.lock = threading.Lock()
            self.initial_window_started = threading.Event()
            self.slice_calls: list[dict] = []

        def _active_slice_builds(
            self,
            pipeline: str,
            *,
            created_from: datetime,
            created_to: datetime,
            deadline: float | None = None,
        ) -> list[dict]:
            with self.lock:
                self.slice_calls.append(
                    {
                        "created_from": collector.iso_timestamp(created_from),
                        "created_to": collector.iso_timestamp(created_to),
                    }
                )
                if len(self.slice_calls) == collector.MAX_CONCURRENT_ACTIVE_SLICES:
                    self.initial_window_started.set()
            assert self.initial_window_started.wait(timeout=1)
            raise collector.CollectionError("network_error")

        def _paginate_builds(
            self,
            pipeline: str,
            *,
            filters: dict,
            deadline: float | None = None,
        ) -> list[dict]:
            raise AssertionError("finished cohort must not run after slice failure")

    client = FailingClient()
    with pytest.raises(collector.CollectionError, match="network_error"):
        client.discover_builds(
            "amd-ci",
            finished_from=_timestamp(hours=-2),
            active_created_from=_timestamp(hours=-(slice_hours * 3 + 1)),
            active_created_to=_timestamp(),
        )

    assert len(client.slice_calls) == collector.MAX_CONCURRENT_ACTIVE_SLICES


def test_rate_limit_honors_numeric_and_http_date_retry_after():
    reference = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    assert collector.retry_after_seconds("7", now=reference) == 7
    assert (
        collector.retry_after_seconds(
            "Mon, 17 Aug 2026 12:00:09 GMT",
            now=reference,
        )
        == 9
    )
    sleeps: list[float] = []
    clock = {"value": 0.0}

    def sleeping(seconds: float) -> None:
        sleeps.append(seconds)
        clock["value"] += seconds

    session = _FakeSession(
        [
            _FakeResponse(429, headers={"Retry-After": "3"}),
            _FakeResponse(200, json_payload=[]),
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeping,
        monotonic=lambda: clock["value"],
    )
    assert client.build_page(
        "ci",
        filters={"finished_from": _timestamp(hours=-1)},
        page=1,
    ) == []
    assert sleeps == [3]


def test_client_proactively_paces_and_adapts_to_shared_quota_headers():
    clock = {"value": 0.0}
    sleeps: list[float] = []

    def sleeping(seconds: float) -> None:
        sleeps.append(seconds)
        clock["value"] += seconds

    session = _FakeSession(
        [
            _FakeResponse(
                200,
                json_payload=[],
                headers={"RateLimit-Remaining": "10", "RateLimit-Reset": "5"},
            ),
            _FakeResponse(200, json_payload=[]),
        ]
    )
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeping,
        monotonic=lambda: clock["value"],
    )
    filters = {"finished_from": _timestamp(hours=-1)}
    client.build_page("ci", filters=filters, page=1)
    client.build_page("ci", filters=filters, page=2)

    # Reset + one-second safety takes precedence over the normal two-second pace.
    assert sleeps == [6]
    assert clock["value"] == 6


def test_proactive_pacing_caps_request_starts_at_thirty_per_minute():
    clock = {"value": 0.0}
    starts: list[float] = []

    class Session(_FakeSession):
        def request(self, method: str, url: str, **kwargs):
            starts.append(clock["value"])
            return super().request(method, url, **kwargs)

    def sleeping(seconds: float) -> None:
        clock["value"] += seconds

    session = Session([_FakeResponse(200, json_payload=[]) for _ in range(31)])
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeping,
        monotonic=lambda: clock["value"],
    )
    for page in range(1, 32):
        client.build_page(
            "ci",
            filters={"finished_from": _timestamp(hours=-1)},
            page=page,
        )
    assert starts[0] == 0
    assert starts[-1] == 60
    assert all(later - earlier >= 2 for earlier, later in zip(starts, starts[1:]))


def test_log_request_does_not_sleep_or_retry_past_monotonic_deadline():
    clock = {"value": 0.0}
    sleeps: list[float] = []
    session = _FakeSession([_FakeResponse(429, headers={"Retry-After": "60"})])
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=session,
        sleep=sleeps.append,
        monotonic=lambda: clock["value"],
    )
    with pytest.raises(collector.BudgetExhausted):
        client.fetch_job_log(_metadata(1), deadline=10)
    assert len(session.calls) == 1
    assert sleeps == []


def test_log_fetch_reads_complete_stream_and_json_envelope():
    first = b"\x1b_bk;t=1786966200000 getaddrinfo failed\n"
    second = b"last line proves this was not a tail"
    plain = _FakeResponse(
        200,
        body=first + second,
        headers={"Content-Type": "text/plain", "Content-Length": str(len(first + second))},
    )
    envelope_body = json.dumps({"content": "no such host at the beginning\nfinal line"}).encode()
    envelope = _FakeResponse(
        200,
        body=envelope_body,
        headers={"Content-Type": "application/json"},
    )
    session = _FakeSession([plain, envelope])
    client = collector.BuildkiteClient("memory-only-token", session=session, sleep=lambda _: None)
    metadata = _metadata(1)

    text, size = client.fetch_job_log(metadata)
    assert text.encode() == first + second
    assert size == len(first + second)
    assert dns.classify_dns_log(
        text,
        job_finished_at=metadata["finished_at"],
        job_started_at=metadata["started_at"],
    ).positive
    text, size = client.fetch_job_log(metadata)
    assert text.endswith("final line")
    assert size == len(text.encode())
    assert all(call["headers"]["Accept"] == "text/plain" for call in session.calls)
    assert client.request_starts() == {
        "build_page": 0,
        "graphql": 0,
        "job_log": 2,
    }
    assert all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in client.telemetry().values()
    )


def test_log_fetch_marks_declared_and_streamed_oversize_without_partial_scan(monkeypatch):
    monkeypatch.setattr(collector, "MAX_LOG_BYTES", 10)
    declared = _FakeResponse(
        200,
        body=b"ignored",
        headers={"Content-Type": "text/plain", "Content-Length": "11"},
    )
    streamed = _FakeResponse(200, body=b"12345678901", headers={"Content-Type": "text/plain"})
    client = collector.BuildkiteClient(
        "memory-only-token",
        session=_FakeSession([declared, streamed]),
        sleep=lambda _: None,
    )
    with pytest.raises(collector.OversizeLog) as declared_error:
        client.fetch_job_log(_metadata(1))
    assert declared_error.value.log_bytes == 11
    with pytest.raises(collector.OversizeLog) as streamed_error:
        client.fetch_job_log(_metadata(2))
    assert streamed_error.value.log_bytes == 11


def test_same_day_incremental_discovery_stays_conservatively_partial(tmp_path: Path):
    prior_end = NOW - timedelta(hours=1)
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    dns.write_state(
        state_path,
        dns.empty_state(prior_end, NOW - timedelta(days=10)),
    )

    class IncrementalClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.incremental_calls: list[tuple[str, str]] = []

        def discover_incremental_job_metadata(
            self,
            *,
            created_from: str,
            finished_from: str,
            deadline: float | None = None,
        ) -> list[dict]:
            self.incremental_calls.append((created_from, finished_from))
            return []

        def discover_builds(self, *args, **kwargs):
            raise AssertionError("same-day runs must use incremental job discovery")

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            raise AssertionError("empty discovery has no logs")

    client = IncrementalClient()
    output = collector.collect(
        client=client,
        state_path=state_path,
        output_path=tmp_path / "dns_failures.json",
        now=NOW,
    )

    overlap_start = dns.iso_timestamp(
        prior_end - timedelta(hours=collector.INCREMENTAL_DISCOVERY_OVERLAP_HOURS)
    )
    assert client.incremental_calls == [(overlap_start, overlap_start)]
    state = dns.load_state(state_path)
    assert state is not None
    assert state["discovery"]["start"] == _timestamp(seconds=-1)
    assert output["coverage"]["discovery_complete"] is False
    assert output["windows"]["1h"]["coverage"]["discovery_complete"] is False


def test_new_utc_day_runs_the_full_active_reconciliation(tmp_path: Path):
    prior_end = NOW - timedelta(hours=13)
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    dns.write_state(
        state_path,
        dns.empty_state(prior_end, NOW - timedelta(days=10)),
    )

    class ReconciliationClient(collector.BuildkiteClient):
        def __init__(self):
            super().__init__(
                "memory-only-token",
                session=_FakeSession([]),
                sleep=lambda _: None,
            )
            self.full_calls: list[str] = []

        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ) -> list[dict]:
            self.full_calls.append(pipeline)
            return []

        def discover_incremental_job_metadata(self, *args, **kwargs):
            raise AssertionError("a new UTC day requires full reconciliation")

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            raise AssertionError("empty discovery has no logs")

    client = ReconciliationClient()
    collector.collect(
        client=client,
        state_path=state_path,
        output_path=tmp_path / "dns_failures.json",
        now=NOW,
    )

    assert client.full_calls == ["amd-ci", "ci"]


def test_minimum_interval_republishes_validated_state_without_buildkite_io(
    tmp_path: Path,
):
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    prior = _state([_negative_record(1)])
    dns.write_state(state_path, prior)
    dns.write_public_output(output_path, dns.build_public_output(prior))
    state_before = state_path.read_bytes()
    output_before = output_path.read_bytes()
    session = _FakeSession([])
    client = collector.BuildkiteClient(
        "memory-only-token",
        max_request_starts=1,
        session=session,
        sleep=lambda _: None,
    )

    output = collector.collect(
        client=client,
        state_path=state_path,
        output_path=output_path,
        minimum_interval_hours=3,
        now=NOW + timedelta(hours=2),
    )

    assert output["generated_at"] == _timestamp()
    assert session.calls == []
    assert client.request_starts() == {
        "build_page": 0,
        "graphql": 0,
        "job_log": 0,
    }
    assert state_path.read_bytes() == state_before
    assert output_path.read_bytes() == output_before


def test_hourly_invocations_gate_request_bearing_scans_to_eight_per_day(
    tmp_path: Path,
):
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    client = _EmptyDiscoveryClient()
    scan_hours = []

    for hour in range(24):
        calls_before = len(client.discovery_calls)
        collector.collect(
            client=client,
            state_path=state_path,
            output_path=output_path,
            minimum_interval_hours=3,
            now=NOW + timedelta(hours=hour),
        )
        if len(client.discovery_calls) > calls_before:
            scan_hours.append(hour)

    assert scan_hours == list(range(0, 24, 3))
    assert len(scan_hours) == 8
    assert len(scan_hours) * 110 == 880


class _EmptyDiscoveryClient:
    def __init__(self):
        self.discovery_calls: list[tuple[str, str, str | None, str | None]] = []

    def discover_builds(
        self,
        pipeline: str,
        *,
        finished_from: str,
        active_created_from: str | None = None,
        active_created_to: str | None = None,
        deadline: float | None = None,
    ):
        self.discovery_calls.append(
            (pipeline, finished_from, active_created_from, active_created_to)
        )
        return []

    def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
        raise AssertionError("empty discovery has no logs")


def test_missing_state_bootstraps_one_exhaustive_day_and_reports_partial_30d(
    tmp_path: Path,
):
    client = _EmptyDiscoveryClient()
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output = collector.collect(
        client=client,
        state_path=state_path,
        output_path=tmp_path / "dns_failures.json",
        discover_days=30,
        now=NOW,
    )

    bootstrap_start = _timestamp(hours=-collector.BOOTSTRAP_DISCOVERY_HOURS)
    assert client.discovery_calls == [
        ("amd-ci", bootstrap_start, _timestamp(hours=-720), _timestamp()),
        ("ci", bootstrap_start, _timestamp(hours=-720), _timestamp()),
    ]
    state = dns.load_state(state_path)
    assert state is not None
    assert state["discovery"]["start"] == bootstrap_start
    assert output["coverage"]["status"] == "partial"
    assert output["coverage"]["discovery_complete"] is False
    assert output["windows"]["720h"]["coverage"]["discovery_complete"] is False


def test_incremental_discovery_overlaps_prior_end_and_carries_contiguous_start(
    tmp_path: Path,
):
    prior_end = NOW - timedelta(hours=1)
    prior_start = NOW - timedelta(days=10)
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    dns.write_state(state_path, dns.empty_state(prior_end, prior_start))
    client = _EmptyDiscoveryClient()

    collector.collect(
        client=client,
        state_path=state_path,
        output_path=tmp_path / "dns_failures.json",
        discover_days=30,
        now=NOW,
    )

    overlap_start = dns.iso_timestamp(
        prior_end
        - timedelta(hours=collector.INCREMENTAL_DISCOVERY_OVERLAP_HOURS)
    )
    assert client.discovery_calls == [
        ("amd-ci", overlap_start, _timestamp(hours=-720), _timestamp()),
        ("ci", overlap_start, _timestamp(hours=-720), _timestamp()),
    ]
    state = dns.load_state(state_path)
    assert state is not None
    assert state["discovery"]["start"] == dns.iso_timestamp(prior_start)
    assert state["discovery"]["end_exclusive"] == dns.iso_timestamp(NOW)


def test_stale_prior_state_resets_to_bounded_bootstrap_without_claiming_history(
    tmp_path: Path,
):
    prior_end = NOW - timedelta(
        hours=collector.MAX_INCREMENTAL_DISCOVERY_GAP_HOURS + 1
    )
    prior_start = NOW - timedelta(days=10)
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    dns.write_state(state_path, dns.empty_state(prior_end, prior_start))
    client = _EmptyDiscoveryClient()

    output = collector.collect(
        client=client,
        state_path=state_path,
        output_path=tmp_path / "dns_failures.json",
        discover_days=30,
        now=NOW,
    )

    bootstrap_start = _timestamp(hours=-collector.BOOTSTRAP_DISCOVERY_HOURS)
    assert {finished_from for _, finished_from, _, _ in client.discovery_calls} == {
        bootstrap_start
    }
    assert {active_from for _, _, active_from, _ in client.discovery_calls} == {
        _timestamp(hours=-720)
    }
    assert {active_to for _, _, _, active_to in client.discovery_calls} == {
        _timestamp()
    }
    state = dns.load_state(state_path)
    assert state is not None
    assert state["discovery"]["start"] == bootstrap_start
    assert output["coverage"]["discovery_complete"] is False


def test_disconnected_prior_interval_does_not_expand_contiguous_coverage():
    newer = dns.empty_state(NOW - timedelta(hours=1), NOW - timedelta(hours=8))
    older = dns.empty_state(NOW - timedelta(hours=12), NOW - timedelta(days=10))

    query_start, coverage_start = collector._discovery_window(
        [older, newer],
        clock=NOW,
        target_start=NOW - timedelta(days=30),
    )

    assert query_start == NOW - timedelta(
        hours=1 + collector.INCREMENTAL_DISCOVERY_OVERLAP_HOURS
    )
    assert coverage_start == NOW - timedelta(hours=8)


def test_carried_discovery_start_is_clamped_to_requested_target():
    prior = dns.empty_state(NOW - timedelta(hours=1), NOW - timedelta(days=40))
    target_start = NOW - timedelta(days=30)

    _, coverage_start = collector._discovery_window(
        [prior],
        clock=NOW,
        target_start=target_start,
    )

    assert coverage_start == target_start


def test_future_prior_state_fails_closed_before_discovery_or_write(tmp_path: Path):
    future_end = NOW + timedelta(hours=1)
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    dns.write_state(
        state_path,
        dns.empty_state(future_end, future_end - timedelta(days=1)),
    )
    output_path.write_text("durable-public-output\n")
    before_state = state_path.read_bytes()
    client = _EmptyDiscoveryClient()

    with pytest.raises(dns.StateValidationError, match="future"):
        collector.collect(
            client=client,
            state_path=state_path,
            output_path=output_path,
            discover_days=30,
            now=NOW,
        )

    assert client.discovery_calls == []
    assert state_path.read_bytes() == before_state
    assert output_path.read_text() == "durable-public-output\n"


def test_time_budget_persists_progress_and_honest_pending_coverage(tmp_path: Path):
    clock = {"value": 0.0}
    jobs = [
        _job(1, finished_hours=-0.1),
        _job(2, finished_hours=-0.2),
        _job(3, finished_hours=-0.3),
    ]

    class Client:
        def __init__(self):
            self.calls: list[str] = []

        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ):
            return [_build(12112, jobs)] if pipeline == "amd-ci" else []

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            self.calls.append(metadata["job_id"])
            clock["value"] = 11.0
            return "ordinary successful output", 26

    client = Client()
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    output = collector.collect(
        client=client,
        state_path=state_path,
        output_path=output_path,
        discover_days=30,
        max_logs=500,
        time_budget_seconds=40,
        now=NOW,
        monotonic=lambda: clock["value"],
    )

    assert client.calls == [_uuid(1)]
    assert state_path.is_file() and output_path.is_file()
    state = dns.load_state(state_path)
    assert state is not None
    assert [row["status"] for row in state["jobs"]].count("negative") == 1
    assert [row["status"] for row in state["jobs"]].count("pending") == 2
    assert output["coverage"]["status"] == "partial"
    assert output["coverage"]["pending_jobs"] == 2


def test_discovery_budget_exhaustion_does_not_replace_durable_state(tmp_path: Path):
    state_path = tmp_path / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    dns.write_state(state_path, _state([_negative_record(1)]))
    output_path.write_text("durable-public-output\n")
    before_state = state_path.read_bytes()

    class Client:
        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ):
            raise collector.BudgetExhausted()

    with pytest.raises(collector.BudgetExhausted):
        collector.collect(
            client=Client(),
            state_path=state_path,
            output_path=output_path,
            time_budget_seconds=40,
            now=NOW,
            monotonic=lambda: 0.0,
        )
    assert state_path.read_bytes() == before_state
    assert output_path.read_text() == "durable-public-output\n"


def test_bootstrap_discovery_budget_exhaustion_writes_no_seed(tmp_path: Path):
    state_path = tmp_path / "dns_health" / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"

    class Client:
        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ):
            raise collector.BudgetExhausted()

    with pytest.raises(collector.BudgetExhausted):
        collector.collect(
            client=Client(),
            state_path=state_path,
            output_path=output_path,
            time_budget_seconds=40,
            now=NOW,
            monotonic=lambda: 0.0,
        )

    assert not state_path.exists()
    assert not output_path.exists()


def test_dry_run_does_not_write_state_or_public_output(tmp_path: Path):
    class Client:
        def discover_builds(
            self,
            pipeline: str,
            *,
            finished_from: str,
            active_created_from: str | None = None,
            active_created_to: str | None = None,
            deadline: float | None = None,
        ):
            return []

        def fetch_job_log(self, metadata: dict, *, deadline: float | None = None):
            raise AssertionError("no jobs")

    state_path = tmp_path / "scan_state.json.gz"
    output_path = tmp_path / "dns_failures.json"
    output = collector.collect(
        client=Client(),
        state_path=state_path,
        output_path=output_path,
        dry_run=True,
        now=NOW,
    )
    assert output["coverage"]["eligible_jobs"] == 0
    assert not state_path.exists()
    assert not output_path.exists()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def test_git_ref_state_requires_established_object_and_rejects_malformed_state(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "DNS test")
    _git(repo, "config", "user.email", "dns-test@example.invalid")
    (repo / "README").write_text("seed\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-qm", "seed")
    with pytest.raises(dns.StateValidationError, match="missing"):
        collector.load_state_from_git_ref("HEAD", repo_root=repo)

    path = repo / collector.STATE_GIT_PATH
    dns.write_state(path, _state([_negative_record(1)]))
    _git(repo, "add", collector.STATE_GIT_PATH)
    _git(repo, "commit", "-qm", "valid state")
    assert collector.load_state_from_git_ref("HEAD", repo_root=repo)["jobs"][0]["status"] == "negative"

    path.write_bytes(b"malformed state")
    _git(repo, "add", collector.STATE_GIT_PATH)
    _git(repo, "commit", "-qm", "malformed state")
    with pytest.raises(dns.StateValidationError):
        collector.load_state_from_git_ref("HEAD", repo_root=repo)


def test_cli_contract_has_no_token_flag_and_exposes_budget(capsys):
    with pytest.raises(SystemExit) as raised:
        collector.main(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "--state" in help_text
    assert "--output" in help_text
    assert "--discover-days" in help_text
    assert "--max-logs" in help_text
    assert "--max-requests" in help_text
    assert "--time-budget-seconds" in help_text
    assert "--minimum-interval-hours" in help_text
    assert "--classification-cache" in help_text
    assert "--merge-state-git-ref" in help_text
    assert "--dry-run" in help_text
    assert "--token" not in help_text
