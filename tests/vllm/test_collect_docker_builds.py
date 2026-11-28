"""Docker builds collector transport, backfill, and checkpoint tests (no network)."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vllm import collect_docker_builds as collector
from vllm.ci import docker_builds as db


NOW = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def job(index: int, created_at: datetime, **overrides) -> dict:
    value = {
        "uuid": str(uuid.UUID(int=index)),
        "state": "FINISHED",
        "step": {"key": "image-build-amd"},
        "createdAt": iso(created_at),
        "runnableAt": iso(created_at),
        "startedAt": iso(created_at + timedelta(seconds=5)),
        "finishedAt": iso(created_at + timedelta(minutes=14)),
        "retried": False,
        "passed": True,
        "exitStatus": "0",
        "agentQueryRules": ["queue=amd-cpu"],
        "build": {"number": index, "branch": "main", "pullRequest": None},
    }
    value.update(overrides)
    return value


def history(start: int, count: int, spacing: timedelta, now: datetime = NOW) -> list[dict]:
    return [job(start + i, now - spacing * i - timedelta(minutes=1)) for i in range(count)]


class Response:
    def __init__(self, status_code: int = 200, payload: object = None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeBuildkite:
    """Serve ``pipeline.jobs`` newest-first with offset cursors."""

    def __init__(self, jobs: dict[str, list[dict]]):
        self.jobs = jobs
        self.calls: list[dict] = []
        self.rejected_cursors: set[str] = set()

    def __call__(self, url, **kwargs):
        assert url == "https://graphql.buildkite.com/v1"
        assert kwargs["allow_redirects"] is False
        variables = kwargs["json"]["variables"]
        self.calls.append(variables)
        after = variables["after"]
        if after in self.rejected_cursors:
            self.rejected_cursors.discard(after)
            return Response(payload={"errors": [{"message": "Invalid cursor"}], "data": None})
        nodes = self.jobs[variables["slug"].removeprefix("vllm/")]
        offset = int(after or 0)
        end = offset + variables["first"]
        page = nodes[offset:end]
        return Response(
            payload={
                "data": {
                    "pipeline": {
                        "jobs": {
                            "edges": [{"node": node} for node in page],
                            "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end)},
                        }
                    }
                }
            }
        )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def seed(path: Path) -> Path:
    path.write_bytes(db.serialize_public(db.seed_payload(NOW - timedelta(days=1))))
    return path


def run(tmp_path: Path, transport, **overrides) -> int:
    clock = Clock()
    options = {
        "token": "test-token",
        "output": tmp_path / "docker_builds.json",
        "checkpoint": tmp_path / "wip" / "checkpoint.json.gz",
        "baseline_ref": "bootstrap",
        "now": NOW,
        "transport": transport,
        "sleep": clock.sleep,
        "monotonic": clock.monotonic,
        **overrides,
    }
    return collector.collect(**options)


def published_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text())
    assert db.validate_public(payload) == []
    return payload["jobs"]["id"]


def backfill_jobs() -> dict[str, list[dict]]:
    return {
        "ci": history(1, 400, timedelta(hours=2)),
        "amd-ci": history(10_000, 150, timedelta(hours=5)),
    }


def in_window(jobs: dict[str, list[dict]]) -> set[str]:
    cutoff = NOW - timedelta(days=30)
    return {
        node["uuid"]
        for nodes in jobs.values()
        for node in nodes
        if datetime.fromisoformat(node["createdAt"]) >= cutoff
    }


def test_graphql_errors_fail_closed(tmp_path: Path) -> None:
    output = seed(tmp_path / "docker_builds.json")
    before = output.read_bytes()

    def transport(url, **kwargs):
        return Response(payload={"errors": [{"message": "Field 'x' doesn't exist"}]})

    with pytest.raises(collector.GraphQLError, match="doesn't exist"):
        run(tmp_path, transport)
    assert output.read_bytes() == before


def test_graphql_missing_data_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(collector.GraphQLError, match="missing data"):
        run(tmp_path, lambda url, **kwargs: Response(payload={"data": None}))


def test_redirect_is_refused_without_followup_send(tmp_path: Path) -> None:
    calls: list[dict] = []

    def transport(url, **kwargs):
        calls.append(kwargs)
        return Response(302, headers={"Location": "https://graphql.buildkite.com/v2"})

    with pytest.raises(collector.CollectionError, match="redirected"):
        run(tmp_path, transport)
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["timeout"] == (15, 120)


def test_retries_are_counted_starts_and_bounded(tmp_path: Path) -> None:
    calls = 0

    def transport(url, **kwargs):
        nonlocal calls
        calls += 1
        return Response(502)

    with pytest.raises(collector.CollectionError, match="HTTP 502"):
        run(tmp_path, transport)
    assert calls == 3

    calls = 0
    assert run(tmp_path, transport, max_requests=2) == collector.CHECKPOINT_GUARD_EXIT
    assert calls == 2


def test_pacing_and_point_reserve_backoff() -> None:
    clock = Clock()
    remaining = iter(["20000", "5000", "19000"])
    starts: list[float] = []

    def transport(url, **kwargs):
        starts.append(clock.now)
        headers = {
            "RateLimit-Remaining": next(remaining),
            "RateLimit-User-Remaining": "19000",
            "RateLimit-Reset": "120",
        }
        return Response(payload={"data": {}}, headers=headers)

    client = collector.GraphQLClient(
        "test-token",
        max_request_starts=5,
        deadline=1_000,
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    for _ in range(3):
        client.execute("query { viewer { id } }", {})

    assert starts == [0, 22, 143]
    assert clock.sleeps == [22, 121]

    client.deadline = clock.now + 50
    remaining = iter(["10"])
    client.execute("query { viewer { id } }", {})
    with pytest.raises(collector.TimeSpent):
        client.execute("query { viewer { id } }", {})
    assert len(starts) == 4


def client_for(transport, clock: Clock, max_request_starts: int = 5) -> collector.GraphQLClient:
    return collector.GraphQLClient(
        "test-token",
        max_request_starts=max_request_starts,
        deadline=1_000,
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_low_user_remaining_blocks_until_reset() -> None:
    clock = Clock()
    starts: list[float] = []

    def transport(url, **kwargs):
        starts.append(clock.now)
        headers = {
            "RateLimit-Remaining": "20000",
            "RateLimit-User-Remaining": "100",
            "RateLimit-Reset": "60",
        }
        return Response(payload={"data": {}}, headers=headers)

    client = client_for(transport, clock)
    for _ in range(2):
        client.execute("query { viewer { id } }", {})

    assert starts == [0, 61]


def test_429_waits_for_the_reset_instead_of_pacing() -> None:
    clock = Clock()
    responses = iter([
        Response(429, headers={"RateLimit-Reset": "60"}),
        Response(payload={"data": {}}),
    ])
    client = client_for(lambda url, **kwargs: next(responses), clock)

    assert client.execute("query { viewer { id } }", {}) == {}
    assert clock.sleeps == [61]


def test_transport_timeout_retry_is_a_counted_start() -> None:
    clock = Clock()
    calls = 0

    def transport(url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise collector.requests.exceptions.Timeout("read timed out")
        return Response(payload={"data": {}})

    client = client_for(transport, clock)

    assert client.execute("query { viewer { id } }", {}) == {}
    assert calls == 2 and client.request_starts == 2


def test_head_pass_stops_at_floor() -> None:
    fake = FakeBuildkite({"ci": history(1, 350, timedelta(minutes=6))})
    clock = Clock()
    client = collector.GraphQLClient(
        "test-token",
        max_request_starts=10,
        deadline=10_000,
        transport=fake,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    rows, last = collector.fetch_head(client, "ci", NOW - timedelta(hours=10))

    assert [call["after"] for call in fake.calls] == [None, "100"]
    assert len(rows) == 200
    assert last.has_next_page and last.end_cursor == "200"
    assert fake.calls[0] == {
        "slug": "vllm/ci",
        "first": 100,
        "after": None,
        "keys": ["ensure-ci-base-amd", "image-build-amd", "refresh-rocm-base-amd"],
    }


def test_backfill_exits_75_with_checkpoint_and_resumes_from_cursor(tmp_path: Path) -> None:
    fake = FakeBuildkite(backfill_jobs())
    output = seed(tmp_path / "docker_builds.json")
    seed_bytes = output.read_bytes()
    checkpoint = tmp_path / "wip" / "checkpoint.json.gz"

    assert run(tmp_path, fake, max_requests=3) == collector.CHECKPOINT_GUARD_EXIT
    assert checkpoint.is_file()
    assert output.read_bytes() == seed_bytes
    tails, rows, watermark = collector.load_checkpoint(
        checkpoint, baseline_ref="bootstrap", now=NOW
    )
    assert watermark == NOW
    assert tails["ci"].cursor == "100" and not tails["ci"].reached_cutoff
    assert tails["amd-ci"].reached_cutoff
    assert len(rows) == 244

    fake.calls.clear()
    assert run(tmp_path, fake, max_requests=3) == collector.CHECKPOINT_GUARD_EXIT
    assert [(c["slug"], c["after"]) for c in fake.calls] == [
        ("vllm/amd-ci", None),
        ("vllm/ci", None),
        ("vllm/ci", "100"),
    ]

    fake.calls.clear()
    assert run(tmp_path, fake) == 0
    assert [(c["slug"], c["after"]) for c in fake.calls] == [
        ("vllm/amd-ci", None),
        ("vllm/ci", None),
        ("vllm/ci", "200"),
        ("vllm/ci", "300"),
    ]
    assert sorted(published_ids(output)) == sorted(in_window(fake.jobs))


def test_checkpoint_for_another_baseline_is_ignored(tmp_path: Path) -> None:
    fake = FakeBuildkite(backfill_jobs())
    seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, fake, max_requests=3) == collector.CHECKPOINT_GUARD_EXIT
    checkpoint = tmp_path / "wip" / "checkpoint.json.gz"

    assert collector.load_checkpoint(checkpoint, baseline_ref="f" * 40, now=NOW) is None


def test_invalid_cursor_restarts_tail_without_duplicates(tmp_path: Path) -> None:
    fake = FakeBuildkite(backfill_jobs())
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, fake, max_requests=3) == collector.CHECKPOINT_GUARD_EXIT
    fake.rejected_cursors = {"100"}
    fake.calls.clear()

    assert run(tmp_path, fake) == 0

    ci_tail = [c["after"] for c in fake.calls if c["slug"] == "vllm/ci"]
    assert ci_tail == [None, "100", None, "100", "200", "300"]
    ids = published_ids(output)
    assert len(ids) == len(set(ids))
    assert set(ids) == in_window(fake.jobs)


def test_always_rejected_cursor_fails_after_one_restart() -> None:
    fake = FakeBuildkite(backfill_jobs())

    def transport(url, **kwargs):
        if kwargs["json"]["variables"]["after"] is not None:
            fake.calls.append(kwargs["json"]["variables"])
            return Response(payload={"errors": [{"message": "Invalid cursor"}], "data": None})
        return fake(url, **kwargs)

    client = client_for(transport, Clock(), max_request_starts=10)
    state = collector.TailState(cursor="100")

    with pytest.raises(collector.GraphQLError, match="Invalid cursor"):
        collector.fetch_tail(client, "ci", state, NOW - timedelta(days=30), lambda rows: None)
    assert [c["after"] for c in fake.calls] == ["100", None, "100"]


def test_backfill_stops_on_the_first_page_past_the_cutoff(tmp_path: Path) -> None:
    jobs = {
        "ci": history(1, 90 * 12, timedelta(hours=2)),
        "amd-ci": history(10_000, 50, timedelta(hours=5)),
    }
    fake = FakeBuildkite(jobs)
    output = seed(tmp_path / "docker_builds.json")

    assert run(tmp_path, fake, **UNBOUNDED) == 0

    assert [c["after"] for c in fake.calls if c["slug"] == "vllm/ci"] == [None, "100", "200", "300"]
    assert set(published_ids(output)) == in_window(jobs)


def checkpoint_with(tmp_path: Path, tails: dict, rows: list) -> Path:
    path = tmp_path / "checkpoint.json.gz"
    collector.save_checkpoint(path, baseline_ref="bootstrap", watermark=NOW, tails=tails, rows=rows)
    return path


def test_checkpoint_missing_a_pipeline_is_ignored(tmp_path: Path) -> None:
    row = db.normalize_node(job(1, NOW - timedelta(hours=1)), "ci")
    path = checkpoint_with(tmp_path, {"ci": collector.TailState()}, [row])

    assert collector.load_checkpoint(path, baseline_ref="bootstrap", now=NOW) is None


def test_checkpoint_with_an_unknown_step_row_is_ignored(tmp_path: Path) -> None:
    row = db.normalize_node(job(1, NOW - timedelta(hours=1)), "ci")
    tails = {name: collector.TailState() for name in db.PIPELINES}
    path = checkpoint_with(tmp_path, tails, [replace(row, step="build-cpu-image")])

    assert collector.load_checkpoint(path, baseline_ref="bootstrap", now=NOW) is None


def test_non_cursor_graphql_error_in_tail_is_not_retried(tmp_path: Path) -> None:
    fake = FakeBuildkite(backfill_jobs())
    seed(tmp_path / "docker_builds.json")

    def transport(url, **kwargs):
        if kwargs["json"]["variables"]["after"] == "100":
            return Response(payload={"errors": [{"message": "Internal error"}]})
        return fake(url, **kwargs)

    with pytest.raises(collector.GraphQLError, match="Internal error"):
        run(tmp_path, transport)


def test_complete_writes_public_and_exits_0(tmp_path: Path) -> None:
    jobs = backfill_jobs()
    jobs["ci"][0] = job(1, NOW - timedelta(minutes=1), build={
        "number": 7, "branch": "someone:feature", "pullRequest": {"id": "61837"}})
    jobs["ci"][1]["agentQueryRules"] = ["queue=medium_cpu_queue_premerge"]
    fake = FakeBuildkite(jobs)
    output = seed(tmp_path / "docker_builds.json")

    assert run(tmp_path, fake) == 0

    raw = output.read_bytes()
    payload = json.loads(raw)
    assert db.validate_public(payload) == []
    assert raw == db.serialize_public(payload)
    assert payload["generated_at"] == "2026-10-29T23:00:00Z"
    assert payload["coverage"] == {
        "status": "complete",
        "refetch_hours": 10,
        "pipelines": {
            "amd-ci": {"complete_from": "2026-09-29T23:00:00Z"},
            "ci": {"complete_from": "2026-09-29T23:00:00Z"},
        },
    }
    assert set(payload["jobs"]["id"]) == in_window(jobs) - {jobs["ci"][1]["uuid"]}
    assert payload["jobs"]["source"][0] == db.SOURCES.index("pr")
    assert "someone" not in raw.decode() and "61837" not in raw.decode()


def test_incremental_run_reads_only_the_head_after_a_complete_baseline(tmp_path: Path) -> None:
    jobs = backfill_jobs()
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, FakeBuildkite(jobs)) == 0
    later = NOW + timedelta(hours=3)
    jobs["ci"] = history(50_000, 3, timedelta(minutes=30), now=later) + jobs["ci"]
    fake = FakeBuildkite(jobs)

    assert run(tmp_path, fake, now=later, baseline_ref="a" * 40, checkpoint=None) == 0

    assert [c["after"] for c in fake.calls] == [None, None]
    ids = published_ids(output)
    assert {str(uuid.UUID(int=50_000 + i)) for i in range(3)} <= set(ids)
    assert json.loads(output.read_text())["generated_at"] == "2026-10-30T02:00:00Z"


def test_stale_baseline_backfills_the_full_window(tmp_path: Path) -> None:
    jobs = backfill_jobs()
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, FakeBuildkite(jobs)) == 0
    fake = FakeBuildkite(jobs)

    later = NOW + timedelta(hours=49)
    assert run(tmp_path, fake, now=later, baseline_ref="a" * 40, checkpoint=None) == 0

    assert [c["after"] for c in fake.calls if c["slug"] == "vllm/ci"] == [None, "100", "200", "300"]
    assert json.loads(output.read_text())["coverage"]["status"] == "complete"


class KeysetBuildkite:
    """Serve jobs created by ``now`` with Buildkite's (createdAt, uuid) keyset cursors."""

    def __init__(self, jobs: dict[str, list[dict]], now: datetime):
        self.jobs = jobs
        self.now = iso(now)

    def __call__(self, url, **kwargs):
        variables = kwargs["json"]["variables"]
        nodes = sorted(
            (n for n in self.jobs[variables["slug"].removeprefix("vllm/")] if n["createdAt"] <= self.now),
            key=lambda n: (n["createdAt"], n["uuid"]),
            reverse=True,
        )
        if variables["after"]:
            after = tuple(variables["after"].split("|"))
            nodes = [n for n in nodes if (n["createdAt"], n["uuid"]) < after]
        page = nodes[: variables["first"]]
        end = f"{page[-1]['createdAt']}|{page[-1]['uuid']}" if page else None
        return Response(
            payload={
                "data": {
                    "pipeline": {
                        "jobs": {
                            "edges": [{"node": node} for node in page],
                            "pageInfo": {"hasNextPage": len(nodes) > len(page), "endCursor": end},
                        }
                    }
                }
            }
        )


UNBOUNDED = {"max_requests": 1000, "time_budget_seconds": 1e9}


def dense_jobs() -> dict[str, list[dict]]:
    start = NOW - timedelta(days=31)
    return {
        "ci": [job(i, start + timedelta(minutes=5 * i)) for i in range((31 * 24 + 60) * 12)],
        "amd-ci": [job(100_000 + i, start + timedelta(hours=6 * i)) for i in range(200)],
    }


def assert_contiguous(output: Path, jobs: dict[str, list[dict]], now: datetime) -> None:
    payload = json.loads(output.read_text())
    assert payload["coverage"]["status"] == "complete"
    cutoff, until = iso(now - timedelta(days=30)), iso(now)
    expected = {n["uuid"] for nodes in jobs.values() for n in nodes if cutoff <= n["createdAt"] <= until}
    assert set(payload["jobs"]["id"]) == expected


def test_resumed_backfill_rereads_everything_since_the_last_head_pass(tmp_path: Path) -> None:
    jobs = dense_jobs()
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, KeysetBuildkite(jobs, NOW), max_requests=4) == collector.CHECKPOINT_GUARD_EXIT

    later = NOW + timedelta(hours=30)
    assert run(tmp_path, KeysetBuildkite(jobs, later), now=later, **UNBOUNDED) == 0

    assert_contiguous(output, jobs, later)


def test_checkpoint_without_a_recent_watermark_restarts_the_backfill(tmp_path: Path) -> None:
    jobs = dense_jobs()
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, KeysetBuildkite(jobs, NOW), **UNBOUNDED) == 0
    hour = NOW + timedelta(hours=1)
    assert (
        run(tmp_path, KeysetBuildkite(jobs, hour), now=hour, baseline_ref="a" * 40, max_requests=1)
        == collector.CHECKPOINT_GUARD_EXIT
    )

    later = NOW + timedelta(hours=60)
    fake = KeysetBuildkite(jobs, later)
    assert run(tmp_path, fake, now=later, baseline_ref="a" * 40, **UNBOUNDED) == 0

    assert_contiguous(output, jobs, later)


def test_incremental_run_rereads_the_gap_since_the_last_publication(tmp_path: Path) -> None:
    jobs = dense_jobs()
    output = seed(tmp_path / "docker_builds.json")
    assert run(tmp_path, KeysetBuildkite(jobs, NOW), checkpoint=None, **UNBOUNDED) == 0

    later = NOW + timedelta(hours=40)
    fake = KeysetBuildkite(jobs, later)
    assert run(tmp_path, fake, now=later, baseline_ref="a" * 40, checkpoint=None, **UNBOUNDED) == 0

    assert_contiguous(output, jobs, later)


def test_time_budget_exits_76(tmp_path: Path) -> None:
    fake = FakeBuildkite(backfill_jobs())
    seed(tmp_path / "docker_builds.json")

    assert run(tmp_path, fake, time_budget_seconds=30) == collector.CHECKPOINT_WALL_CLOCK_EXIT
    assert len(fake.calls) == 2
    tails, rows, _ = collector.load_checkpoint(
        tmp_path / "wip" / "checkpoint.json.gz", baseline_ref="bootstrap", now=NOW
    )
    assert len(rows) == 200 and tails["ci"].cursor == "100"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_restore_from_git_ref_validates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    published = repo / db.PUBLIC_PATH
    published.parent.mkdir(parents=True)
    _git(repo, "init", "-q")
    rows = [db.normalize_node(job(1, NOW - timedelta(hours=1)), "ci")]
    payload = db.encode_public(
        rows,
        generated_at=NOW,
        window_days=30,
        coverage=db.complete_coverage(NOW - timedelta(days=30)),
    )
    published.write_bytes(db.serialize_public(payload))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "valid")
    valid = _git(repo, "rev-parse", "HEAD")
    payload["jobs"]["id"][0] = "https://buildkite.com/vllm/ci/builds/1"
    published.write_bytes(db.serialize_public(payload))
    _git(repo, "commit", "-qam", "invalid")
    output = tmp_path / "restored.json"

    with pytest.raises(collector.CollectionError, match="invalid"):
        collector.restore_from_git_ref("HEAD", output, repo_root=repo)
    assert not output.exists()
    with pytest.raises(collector.CollectionError, match="does not exist"):
        collector.restore_from_git_ref("HEAD~5", output, repo_root=repo)

    assert collector.restore_from_git_ref(valid, output, repo_root=repo) == rows
    assert output.read_bytes() == _git(repo, "show", f"{valid}:{db.PUBLIC_PATH}").encode() + b"\n"


def test_cli_validate_only_and_clear_checkpoint(tmp_path: Path) -> None:
    output = seed(tmp_path / "docker_builds.json")
    assert collector.main(["--validate-only", "--output", str(output)]) == 0
    output.write_text(output.read_text().replace('"n":0', '"n":1'))
    assert collector.main(["--validate-only", "--output", str(output)]) == 1

    checkpoint = tmp_path / "checkpoint.json.gz"
    checkpoint.write_bytes(b"wip")
    assert collector.main(["--clear-checkpoint", "--checkpoint", str(checkpoint)]) == 0
    assert not checkpoint.exists()
