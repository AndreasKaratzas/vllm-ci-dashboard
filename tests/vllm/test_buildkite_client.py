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

    def test_cache_hit_for_terminal_builds(self, monkeypatch, fake_cfg, tmp_path):
        """Terminal builds should be served from the cache instead of being re-fetched."""
        cache_file = tmp_path / "builds_amd.json"
        cached_build = {
            "number": 42, "message": "nightly cached", "state": "passed",
            "created_at": "2026-04-18T00:00:00Z", "cached": True,
        }
        cache_file.write_text(json.dumps([cached_build]))

        api_build = {
            "number": 42, "message": "nightly api", "state": "passed",
            "created_at": "2026-04-18T00:00:00Z", "cached": False,
        }
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [api_build])
        out = bk.fetch_nightly_builds("amd", cache_dir=tmp_path)
        assert len(out) == 1
        assert out[0]["cached"] is True  # served from disk, not API

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
        bk.fetch_nightly_builds("amd", cache_dir=tmp_path)
        cache_file = tmp_path / "builds_amd.json"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text())[0]["number"] == 1

    def test_corrupt_cache_is_ignored(self, monkeypatch, fake_cfg, tmp_path):
        cache_file = tmp_path / "builds_amd.json"
        cache_file.write_text("not json")
        build = {"number": 1, "message": "nightly", "state": "passed", "created_at": "2026-04-18T00:00:00Z"}
        monkeypatch.setattr(bk, "_paginate", lambda url, params=None: [build])
        out = bk.fetch_nightly_builds("amd", cache_dir=tmp_path)
        assert len(out) == 1


class TestFetchBuildDetail:
    def test_returns_json(self, monkeypatch):
        monkeypatch.setattr(cfg, "BK_ORG", "vllm", raising=False)
        monkeypatch.setattr(cfg, "PIPELINES", {"amd": {"slug": "amd-ci"}}, raising=False)
        expected = {"number": 99, "jobs": []}
        resp = _fake_response(200, json_body=expected)
        monkeypatch.setattr(bk, "_request", lambda url: resp)
        out = bk.fetch_build_detail("amd", 99)
        assert out == expected
