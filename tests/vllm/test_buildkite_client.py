"""Unit tests for scripts/vllm/ci/buildkite_client.py.

The client wraps the Buildkite REST API with retry, pagination, and
per-build filtering logic. These tests mock ``requests.get`` to verify:

- ``_request`` retries on 429 (Retry-After honoured) and on RETRY_CODES
  (exponential backoff), raises after MAX_RETRIES exhausted
- ``_paginate`` walks the ``Link: rel="next"`` chain until exhausted
- ``fetch_build_jobs`` filters to type=script, terminal state, non-retried
- ``fetch_nightly_builds`` applies the pipeline name regex and cache
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

from vllm.ci import buildkite_client as bk
from vllm.ci import config as cfg
from vllm.pipelines import AMD_NIGHTLY_NAME_PATTERN


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Strip out real time.sleep calls from every retry path."""
    monkeypatch.setattr(bk.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr(cfg, "BK_TOKEN", "fake-token", raising=False)


def _fake_response(status=200, json_body=None, headers=None, links=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.links = links or {}
    r.json.return_value = json_body if json_body is not None else []
    r.content = json.dumps(json_body or []).encode()
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    else:
        r.raise_for_status.return_value = None
    return r


class TestHeaders:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.setattr(cfg, "BK_TOKEN", "", raising=False)
        with pytest.raises(RuntimeError, match="BUILDKITE_TOKEN"):
            bk._headers()

    def test_bearer_token_in_header(self):
        h = bk._headers()
        assert h == {"Authorization": "Bearer fake-token"}


class TestRequest:
    def test_happy_path_returns_response(self, monkeypatch):
        resp = _fake_response(200, json_body=[{"id": 1}])
        monkeypatch.setattr(bk.requests, "get", lambda *a, **k: resp)
        out = bk._request("https://api.buildkite.com/v2/foo")
        assert out is resp

    def test_429_honours_retry_after_header(self, monkeypatch):
        calls = []
        slept = []
        monkeypatch.setattr(bk.time, "sleep", lambda s: slept.append(s))

        def fake_get(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return _fake_response(429, headers={"Retry-After": "7"})
            return _fake_response(200, json_body={"ok": True})

        monkeypatch.setattr(bk.requests, "get", fake_get)
        resp = bk._request("https://api.buildkite.com/v2/foo")
        assert resp.status_code == 200
        assert slept == [7]  # used Retry-After, not default backoff
        assert len(calls) == 2

    def test_429_without_header_uses_backoff(self, monkeypatch):
        calls = []
        slept = []
        monkeypatch.setattr(bk.time, "sleep", lambda s: slept.append(s))

        def fake_get(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return _fake_response(429)
            return _fake_response(200, json_body=[])

        monkeypatch.setattr(bk.requests, "get", fake_get)
        bk._request("https://api.buildkite.com/v2/foo")
        # First retry = RETRY_BACKOFF * attempt(1)
        assert slept == [cfg.RETRY_BACKOFF * 1]

    def test_retry_codes_exponential_backoff(self, monkeypatch):
        slept = []
        monkeypatch.setattr(bk.time, "sleep", lambda s: slept.append(s))
        attempts = {"n": 0}

        def fake_get(*a, **k):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return _fake_response(503)  # in RETRY_CODES
            return _fake_response(200, json_body=[])

        monkeypatch.setattr(bk.requests, "get", fake_get)
        bk._request("https://api.buildkite.com/v2/foo")
        # Attempt 1 fails → wait = BACKOFF*1; attempt 2 fails → wait = BACKOFF*2
        assert slept == [cfg.RETRY_BACKOFF * 1, cfg.RETRY_BACKOFF * 2]
        assert attempts["n"] == 3

    def test_retry_exhausted_raises(self, monkeypatch):
        # 429 on every attempt → last attempt calls raise_for_status which raises
        monkeypatch.setattr(
            bk.requests, "get",
            lambda *a, **k: _fake_response(429, headers={"Retry-After": "1"}),
        )
        with pytest.raises(requests.HTTPError):
            bk._request("https://api.buildkite.com/v2/foo")

    def test_timeout_retries(self, monkeypatch):
        attempts = {"n": 0}

        def fake_get(*a, **k):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise requests.exceptions.Timeout("boom")
            return _fake_response(200, json_body=[])

        monkeypatch.setattr(bk.requests, "get", fake_get)
        bk._request("https://api.buildkite.com/v2/foo")
        assert attempts["n"] == 3

    def test_timeout_exhausted_raises(self, monkeypatch):
        def fake_get(*a, **k):
            raise requests.exceptions.Timeout("boom")

        monkeypatch.setattr(bk.requests, "get", fake_get)
        with pytest.raises(requests.exceptions.Timeout):
            bk._request("https://api.buildkite.com/v2/foo")

    def test_non_retry_4xx_raises_immediately(self, monkeypatch):
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            return _fake_response(404)

        monkeypatch.setattr(bk.requests, "get", fake_get)
        with pytest.raises(requests.HTTPError):
            bk._request("https://api.buildkite.com/v2/foo")
        # No retry for 404 — one call only
        assert len(calls) == 1


class TestPaginate:
    def test_single_page(self, monkeypatch):
        resp = _fake_response(200, json_body=[{"id": 1}, {"id": 2}])
        monkeypatch.setattr(bk.requests, "get", lambda *a, **k: resp)
        out = bk._paginate("https://api.buildkite.com/v2/foo")
        assert out == [{"id": 1}, {"id": 2}]

    def test_walks_link_next_chain(self, monkeypatch):
        page1 = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": "https://api.buildkite.com/v2/foo?page=2"}},
        )
        page2 = _fake_response(
            200,
            json_body=[{"id": 2}],
            links={"next": {"url": "https://api.buildkite.com/v2/foo?page=3"}},
        )
        page3 = _fake_response(200, json_body=[{"id": 3}], links={})
        responses = iter([page1, page2, page3])
        monkeypatch.setattr(bk.requests, "get", lambda *a, **k: next(responses))
        out = bk._paginate("https://api.buildkite.com/v2/foo")
        assert [r["id"] for r in out] == [1, 2, 3]

    def test_empty_response(self, monkeypatch):
        resp = _fake_response(200, json_body=[])
        monkeypatch.setattr(bk.requests, "get", lambda *a, **k: resp)
        assert bk._paginate("https://api.buildkite.com/v2/foo") == []

    def test_params_only_sent_on_first_page(self, monkeypatch):
        """Subsequent pages must use Link URL params, not the caller's params."""
        seen_params = []
        page1 = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": "https://api.buildkite.com/v2/foo?page=2"}},
        )
        page2 = _fake_response(200, json_body=[{"id": 2}], links={})
        responses = iter([page1, page2])

        def fake_get(url, headers=None, params=None, timeout=None):
            seen_params.append(params)
            return next(responses)

        monkeypatch.setattr(bk.requests, "get", fake_get)
        bk._paginate("https://api.buildkite.com/v2/foo", params={"per_page": 100})
        assert seen_params[0] == {"per_page": 100}
        assert seen_params[1] is None  # follow-up pages use the Link URL verbatim

    def test_rejects_repeated_next_url_without_refetching_it(self, monkeypatch):
        start_url = "https://api.buildkite.com/v2/foo"
        response = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": start_url}},
        )
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return response

        monkeypatch.setattr(bk.requests, "get", fake_get)

        with pytest.raises(RuntimeError, match="repeated next URL"):
            bk._paginate(start_url)

        assert calls == [start_url]

    def test_rejects_cross_origin_next_url_before_sending_token(self, monkeypatch):
        start_url = "https://api.buildkite.com/v2/foo"
        response = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": "https://attacker.example/steal"}},
        )
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return response

        monkeypatch.setattr(bk.requests, "get", fake_get)

        with pytest.raises(RuntimeError, match="cross-origin"):
            bk._paginate(start_url)

        assert calls == [start_url]

    def test_rejects_same_origin_different_path_before_sending_token(self, monkeypatch):
        start_url = "https://api.buildkite.com/v2/foo"
        response = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": "https://api.buildkite.com/v2/other?page=2"}},
        )
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return response

        monkeypatch.setattr(bk.requests, "get", fake_get)

        with pytest.raises(RuntimeError, match="different endpoint path"):
            bk._paginate(start_url)

        assert calls == [start_url]

    def test_fails_closed_when_page_cap_still_has_next_link(self, monkeypatch):
        start_url = "https://api.buildkite.com/v2/foo"
        page1 = _fake_response(
            200,
            json_body=[{"id": 1}],
            links={"next": {"url": f"{start_url}?page=2"}},
        )
        page2 = _fake_response(
            200,
            json_body=[{"id": 2}],
            links={"next": {"url": f"{start_url}?page=3"}},
        )
        responses = iter([page1, page2])
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return next(responses)

        monkeypatch.setattr(bk.requests, "get", fake_get)

        with pytest.raises(RuntimeError, match="2-page safety cap"):
            bk._paginate(start_url, max_pages=2)

        assert calls == [start_url, f"{start_url}?page=2"]

    @pytest.mark.parametrize(
        "payload",
        [pytest.param({"id": 1}, id="object"), pytest.param("bad", id="string")],
    )
    def test_fails_closed_on_malformed_non_list_page(self, monkeypatch, payload):
        response = _fake_response(200, json_body=payload)
        monkeypatch.setattr(bk.requests, "get", lambda *args, **kwargs: response)

        with pytest.raises(RuntimeError, match="expected each page to be a JSON list"):
            bk._paginate("https://api.buildkite.com/v2/foo")


class TestFetchBuildJobs:
    def test_filters_type_script_only(self):
        build = {"jobs": [
            {"type": "script", "state": "passed"},
            {"type": "wait", "state": "passed"},
            {"type": "trigger", "state": "passed"},
        ]}
        out = bk.fetch_build_jobs(build)
        assert len(out) == 1
        assert out[0]["type"] == "script"

    def test_filters_terminal_states_only(self):
        build = {"jobs": [
            {"type": "script", "state": "running"},
            {"type": "script", "state": "scheduled"},
            {"type": "script", "state": "passed"},
            {"type": "script", "state": "failed"},
        ]}
        out = bk.fetch_build_jobs(build)
        states = {j["state"] for j in out}
        assert states == {"passed", "failed"}

    def test_excludes_retried_jobs(self):
        build = {"jobs": [
            {"type": "script", "state": "failed", "retried_in_job_id": "new-123"},
            {"type": "script", "state": "passed"},
        ]}
        out = bk.fetch_build_jobs(build)
        assert len(out) == 1
        assert out[0].get("retried_in_job_id") is None

    def test_empty_jobs_returns_empty(self):
        assert bk.fetch_build_jobs({"jobs": []}) == []

    def test_missing_jobs_key_returns_empty(self):
        assert bk.fetch_build_jobs({}) == []


class TestFetchNightlyBuilds:
    @pytest.fixture
    def fake_cfg(self, monkeypatch):
        monkeypatch.setattr(cfg, "BK_ORG", "vllm", raising=False)
        monkeypatch.setattr(cfg, "PIPELINES", {
            "amd": {
                "slug": "amd-ci",
                "branch": "main",
                "name_pattern": r"nightly",
            },
        }, raising=False)

    def test_filters_by_name_pattern(self, monkeypatch, fake_cfg):
        builds = [
            {"number": 1, "message": "Nightly build 2026-04-18", "state": "passed", "created_at": "2026-04-18T00:00:00Z"},
            {"number": 2, "message": "random commit", "state": "passed", "created_at": "2026-04-18T01:00:00Z"},
            {"number": 3, "message": "Nightly smoke", "state": "running", "created_at": "2026-04-18T02:00:00Z"},
        ]
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: builds)
        out = bk.fetch_nightly_builds("amd", days=8)
        nums = [b["number"] for b in out]
        assert 1 in nums and 3 in nums and 2 not in nums

    def test_discovery_excludes_embedded_jobs_and_pipeline(self, monkeypatch, fake_cfg):
        observed = {}

        def paginate(url, params=None):
            observed["url"] = url
            observed["params"] = dict(params or {})
            return []

        monkeypatch.setattr(bk, "_paginate", paginate)

        assert bk.fetch_nightly_builds("amd", days=8) == []
        assert observed["params"]["exclude_jobs"] == "true"
        assert observed["params"]["exclude_pipeline"] == "true"
        assert "include_retried_jobs" not in observed["params"]

    def test_amd_pattern_excludes_therock_nightly(self, monkeypatch):
        monkeypatch.setattr(cfg, "BK_ORG", "vllm", raising=False)
        monkeypatch.setattr(cfg, "PIPELINES", {
            "amd": {
                "slug": "amd-ci",
                "branch": "main",
                "name_pattern": AMD_NIGHTLY_NAME_PATTERN,
            },
        }, raising=False)
        builds = [
            {
                "number": 9537,
                "message": "AMD Full CI Run - nightly",
                "state": "passed",
                "created_at": "2026-06-15T09:00:00Z",
            },
            {
                "number": 9542,
                "message": "AMD Full CI Run - TheRock nightly (2026-06-15, base 9872921c5)",
                "state": "running",
                "created_at": "2026-06-15T12:00:00Z",
            },
        ]
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: builds)

        out = bk.fetch_nightly_builds("amd", days=1)

        assert [b["number"] for b in out] == [9537]

    def test_sorts_newest_first(self, monkeypatch, fake_cfg):
        builds = [
            {"number": 1, "message": "nightly", "state": "passed", "created_at": "2026-04-17T00:00:00Z"},
            {"number": 2, "message": "nightly", "state": "passed", "created_at": "2026-04-18T00:00:00Z"},
        ]
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: builds)
        out = bk.fetch_nightly_builds("amd")
        assert out[0]["number"] == 2
        assert out[1]["number"] == 1

    def test_terminal_cache_keeps_jobs_but_live_metadata_wins(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        cached_build = {
            "number": 42, "message": "nightly cached", "state": "running",
            "created_at": "2026-04-18T00:00:00Z",
            "jobs": [{"type": "script", "name": "late job", "state": "running"}],
        }
        now = datetime(2026, 4, 18, 12, tzinfo=timezone.utc)
        bk.write_nightly_build_cache("amd", [cached_build], tmp_path, now=now)

        api_build = {
            "number": 42, "message": "nightly api", "state": "passed",
            "created_at": "2026-04-18T00:00:00Z", "finished_at": "2026-04-18T08:00:00Z",
        }
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [api_build])
        out = bk.fetch_nightly_builds("amd", cache_dir=tmp_path, now=now)
        assert len(out) == 1
        assert out[0]["state"] == "passed"
        assert out[0]["message"] == "nightly api"
        assert out[0]["finished_at"] == "2026-04-18T08:00:00Z"
        assert out[0]["jobs"] == cached_build["jobs"]

    def test_cache_miss_for_non_terminal(self, monkeypatch, fake_cfg, tmp_path):
        """Builds that are still running must NOT be served from cache."""
        cache_file = tmp_path / "builds_amd.json"
        cache_file.write_text(json.dumps([{
            "number": 42, "message": "nightly", "state": "passed", "cached": True,
        }]))
        api_build = {
            "number": 42, "message": "nightly", "state": "running",
            "created_at": "2026-04-18T00:00:00Z", "cached": False,
        }
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [api_build])
        out = bk.fetch_nightly_builds("amd", cache_dir=tmp_path)
        assert out[0]["cached"] is False  # live API value wins

    def test_cache_written_on_exit(self, monkeypatch, fake_cfg, tmp_path):
        build = {"number": 1, "message": "nightly", "state": "passed", "created_at": "2026-04-18T00:00:00Z"}
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [build])
        bk.fetch_nightly_builds(
            "amd",
            cache_dir=tmp_path,
            now=datetime(2026, 4, 18, 12, tzinfo=timezone.utc),
        )
        cache_file = tmp_path / "nightly-rosters-v2" / "amd" / "2026-04-18_1.json"
        assert cache_file.exists()
        payload = json.loads(cache_file.read_text())
        assert payload == {
            "schema_version": 2,
            "build": {
                "number": 1,
                "created_at": "2026-04-18T00:00:00Z",
                "jobs": [],
            },
        }
        assert not (tmp_path / "builds_amd.json").exists()

    def test_cache_write_admits_build_created_while_list_request_was_in_flight(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        started_at = datetime(2026, 9, 2, 0, 0, 0, tzinfo=timezone.utc)
        completed_at = datetime(2026, 9, 2, 0, 0, 1, tzinfo=timezone.utc)
        clocks = iter((started_at, completed_at))

        class SequencedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz is not None
                value = next(clocks)
                return cls.fromtimestamp(value.timestamp(), tz=tz)

        build = {
            "number": 1,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-09-02T00:00:00.500000Z",
            "jobs": [],
        }
        monkeypatch.setattr(bk, "datetime", SequencedDateTime)
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [build])

        assert bk.fetch_nightly_builds("amd", cache_dir=tmp_path) == [build]

        assert (
            tmp_path
            / bk.NIGHTLY_ROSTER_CACHE_DIR
            / "amd"
            / "2026-09-02_1.json"
        ).is_file()

    def test_sharded_terminal_roster_is_restored_without_detail_fetch(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        cached = {
            "number": 42,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-04-18T00:00:00Z",
            "jobs": [{
                "type": "script",
                "id": "job-42",
                "name": "cached job",
                "state": "failed",
                "soft_failed": True,
                "step_key": "cached-step",
            }],
        }
        now = datetime(2026, 4, 18, 12, tzinfo=timezone.utc)
        bk.write_nightly_build_cache("amd", [cached], tmp_path, now=now)
        summary = {key: value for key, value in cached.items() if key != "jobs"}
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [summary])

        [restored] = bk.fetch_nightly_builds(
            "amd", cache_dir=tmp_path, now=now
        )

        assert restored["jobs"] == cached["jobs"]

    def test_sharded_cache_prunes_builds_older_than_retention(self, tmp_path):
        old = {
            "number": 1,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-03-01T00:00:00Z",
            "jobs": [],
        }
        current = {
            "number": 2,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-04-18T00:00:00Z",
            "jobs": [],
        }

        shard_dir = bk.write_nightly_build_cache(
            "amd",
            [old, current],
            tmp_path,
            now=datetime(2026, 4, 18, 12, tzinfo=timezone.utc),
        )

        assert [path.name for path in shard_dir.glob("*.json")] == [
            "2026-04-18_2.json"
        ]
        assert all(
            path.stat().st_size <= bk.NIGHTLY_ROSTER_MAX_SHARD_BYTES
            for path in shard_dir.glob("*.json")
        )

    def test_restored_boundary_expiry_is_pruned_without_disabling_cache(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        before_midnight = datetime(
            2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc
        )
        after_midnight = datetime(
            2026, 9, 2, 0, 0, 1, tzinfo=timezone.utc
        )
        expired = {
            "number": 1,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-08-17T06:00:00Z",
            "jobs": [{
                "type": "script",
                "id": "expired-job",
                "name": "expired roster",
                "state": "passed",
            }],
        }
        retained = {
            "number": 2,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-08-18T06:00:00Z",
            "jobs": [{
                "type": "script",
                "id": "retained-job",
                "name": "retained roster",
                "state": "passed",
            }],
        }
        bk.write_nightly_build_cache(
            "amd",
            [expired, retained],
            tmp_path,
            now=before_midnight,
        )
        summary = {key: value for key, value in retained.items() if key != "jobs"}
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [summary])
        cache_errors = []

        [restored] = bk.fetch_nightly_builds(
            "amd",
            cache_dir=tmp_path,
            cache_errors=cache_errors,
            now=after_midnight,
        )

        shard_dir = tmp_path / bk.NIGHTLY_ROSTER_CACHE_DIR / "amd"
        assert cache_errors == []
        assert restored["jobs"] == retained["jobs"]
        assert not (shard_dir / "2026-08-17_1.json").exists()
        assert (shard_dir / "2026-08-18_2.json").is_file()
        assert bk.validate_nightly_roster_cache(
            tmp_path,
            now=after_midnight,
        )["shards"] == 1

    def test_expiry_prune_leaves_malformed_old_shard_for_strict_rejection(
        self, tmp_path
    ):
        now = datetime(2026, 9, 2, 0, 0, 1, tzinfo=timezone.utc)
        shard = (
            tmp_path
            / bk.NIGHTLY_ROSTER_CACHE_DIR
            / "amd"
            / "2026-08-17_1.json"
        )
        shard.parent.mkdir(parents=True)
        shard.write_text("not-json\n")

        assert bk.prune_expired_nightly_roster_cache(tmp_path, now=now) == 0
        assert shard.is_file()
        with pytest.raises(bk.NightlyRosterCacheError, match="invalid shard"):
            bk.validate_nightly_roster_cache(tmp_path, now=now)

    def test_roster_repair_removes_unexpected_and_unapproved_restored_state(
        self, tmp_path
    ):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        root = tmp_path / bk.NIGHTLY_ROSTER_CACHE_DIR
        shard_dir = root / "amd"
        shard_dir.mkdir(parents=True)
        unexpected = shard_dir / "unvalidated.bin"
        unexpected.write_bytes(b"private restored bytes")
        poisoned = shard_dir / "2026-08-20_999.json"
        poisoned.write_text('{"unapproved_secret":"must-not-survive"}\n')
        nested = root / "unexpected-pipeline" / "nested"
        nested.mkdir(parents=True)
        (nested / "large.bin").write_bytes(b"x" * 1024)

        bk.write_nightly_build_cache(
            "amd",
            [{
                "number": 1,
                "created_at": now.isoformat(),
                "jobs": [],
            }],
            tmp_path,
            now=now,
        )

        assert not unexpected.exists()
        assert not poisoned.exists()
        assert not (root / "unexpected-pipeline").exists()
        assert {path.name for path in shard_dir.iterdir()} == {
            "2026-08-20_1.json"
        }
        assert bk.validate_nightly_roster_cache(tmp_path, now=now) == {
            "shards": 1,
            "bytes": (shard_dir / "2026-08-20_1.json").stat().st_size,
        }

    def test_roster_aggregate_cap_is_global_across_both_pipelines(
        self, monkeypatch, tmp_path
    ):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        amd = {
            "number": 1,
            "created_at": (now - timedelta(days=1)).isoformat(),
            "jobs": [{"type": "script", "state": "passed", "name": "a" * 500}],
        }
        upstream = {
            "number": 2,
            "created_at": now.isoformat(),
            "jobs": [{"type": "script", "state": "passed", "name": "b" * 500}],
        }
        bk.write_nightly_build_cache("amd", [amd], tmp_path, now=now)
        bk.write_nightly_build_cache("upstream", [upstream], tmp_path, now=now)
        root = tmp_path / bk.NIGHTLY_ROSTER_CACHE_DIR
        sizes = [path.stat().st_size for path in root.glob("*/*.json")]
        assert len(sizes) == 2
        monkeypatch.setattr(bk, "NIGHTLY_ROSTER_MAX_TOTAL_BYTES", max(sizes))

        bk.write_nightly_build_cache("upstream", [upstream], tmp_path, now=now)

        remaining = list(root.glob("*/*.json"))
        assert sum(path.stat().st_size for path in remaining) <= max(sizes)
        assert [path.name for path in remaining] == ["2026-08-20_2.json"]
        stats = bk.validate_nightly_roster_cache(tmp_path, now=now)
        assert stats["bytes"] <= bk.NIGHTLY_ROSTER_MAX_TOTAL_BYTES

    def test_roster_retention_keeps_exactly_sixteen_calendar_days(self, tmp_path):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        builds = [
            {
                "number": index + 1,
                "created_at": (now - timedelta(days=index)).isoformat(),
                "jobs": [],
            }
            for index in range(17)
        ]

        shard_dir = bk.write_nightly_build_cache("amd", builds, tmp_path, now=now)

        assert len(list(shard_dir.glob("*.json"))) == 16
        assert not (shard_dir / "2026-08-04_17.json").exists()
        assert (shard_dir / "2026-08-05_16.json").exists()

    def test_writer_skips_future_api_timestamp_without_evicting_valid_state(
        self, tmp_path
    ):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        current = now - timedelta(hours=1)
        future = now + timedelta(hours=1)
        bk.write_nightly_build_cache(
            "amd",
            [{"number": 1, "created_at": current.isoformat(), "jobs": []}],
            tmp_path,
            now=now,
        )
        bk.write_nightly_build_cache(
            "amd",
            [{"number": 2, "created_at": future.isoformat(), "jobs": []}],
            tmp_path,
            now=now,
        )

        shards = list(
            (tmp_path / bk.NIGHTLY_ROSTER_CACHE_DIR / "amd").glob("*.json")
        )
        assert [path.name for path in shards] == ["2026-08-20_1.json"]
        assert bk.validate_nightly_roster_cache(tmp_path, now=now)["shards"] == 1

    def test_strict_validator_rejects_restored_same_day_future_timestamp(
        self, tmp_path
    ):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        future = now + timedelta(hours=1)
        bk.write_nightly_build_cache(
            "amd",
            [{"number": 1, "created_at": future.isoformat(), "jobs": []}],
            tmp_path,
            now=future,
        )

        with pytest.raises(bk.NightlyRosterCacheError, match="invalid shard"):
            bk.validate_nightly_roster_cache(tmp_path, now=now)

    def test_strict_validator_rejects_broken_roster_root_symlink(self, tmp_path):
        root = tmp_path / bk.NIGHTLY_ROSTER_CACHE_DIR
        root.symlink_to(tmp_path / "missing-roster-root", target_is_directory=True)

        with pytest.raises(bk.NightlyRosterCacheError, match="not a directory"):
            bk.validate_nightly_roster_cache(
                tmp_path,
                now=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
            )

    def test_legacy_caches_are_ignored_and_safely_removed_on_write(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        legacy_monolith = tmp_path / "builds_amd.json"
        legacy_monolith.write_text(json.dumps([{
            "number": 42,
            "created_at": "2026-04-18T00:00:00Z",
            "jobs": [{"type": "script", "name": "legacy", "state": "passed"}],
        }]))
        legacy_shard = (
            tmp_path / "nightly-rosters-v1" / "amd" / "2026-04-18_42.json"
        )
        legacy_shard.parent.mkdir(parents=True)
        legacy_shard.write_text(legacy_monolith.read_text())
        unexpected = legacy_shard.parent / "keep-me.txt"
        unexpected.write_text("not a recognized roster shard")

        api_build = {
            "number": 42,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-04-18T00:00:00Z",
        }
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [api_build])

        [restored] = bk.fetch_nightly_builds("amd", cache_dir=tmp_path)

        assert "jobs" not in restored
        assert not legacy_monolith.exists()
        assert not legacy_shard.exists()
        assert unexpected.exists()

    def test_v2_shard_with_unapproved_field_is_rejected(
        self, monkeypatch, fake_cfg, tmp_path
    ):
        build = {
            "number": 42,
            "created_at": "2026-04-18T00:00:00Z",
            "jobs": [{
                "type": "script",
                "id": "job-1",
                "name": "cached job",
                "state": "passed",
            }],
        }
        now = datetime(2026, 4, 18, 12, tzinfo=timezone.utc)
        shard_dir = bk.write_nightly_build_cache("amd", [build], tmp_path, now=now)
        shard = shard_dir / "2026-04-18_42.json"
        payload = json.loads(shard.read_text())
        payload["build"]["jobs"][0]["command"] = "export SECRET=leak"
        shard.write_text(json.dumps(payload))
        summary = {
            "number": 42,
            "message": "nightly",
            "state": "passed",
            "created_at": "2026-04-18T00:00:00Z",
        }
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [summary])

        [restored] = bk.fetch_nightly_builds(
            "amd", cache_dir=tmp_path, now=now
        )

        assert "jobs" not in restored

    def test_corrupt_cache_is_ignored(self, monkeypatch, fake_cfg, tmp_path):
        cache_file = tmp_path / "builds_amd.json"
        cache_file.write_text("not json")
        build = {"number": 1, "message": "nightly", "state": "passed", "created_at": "2026-04-18T00:00:00Z"}
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [build])
        out = bk.fetch_nightly_builds("amd", cache_dir=tmp_path)
        assert len(out) == 1


class TestFetchBuildDetail:
    def test_returns_json_with_full_retry_roster(self, monkeypatch):
        monkeypatch.setattr(cfg, "BK_ORG", "vllm", raising=False)
        monkeypatch.setattr(cfg, "PIPELINES", {"amd": {"slug": "amd-ci"}}, raising=False)
        expected = {"number": 99, "jobs": []}
        resp = _fake_response(200, json_body=expected)
        observed = {}

        def request(url, params=None):
            observed["url"] = url
            observed["params"] = dict(params or {})
            return resp

        monkeypatch.setattr(bk, "_request", request)
        out = bk.fetch_build_detail("amd", 99)
        assert out == expected
        assert observed["params"] == {
            "include_retried_jobs": "true",
            "exclude_pipeline": "true",
        }
