"""Unit tests for proposed AMD gating PR collection."""

from __future__ import annotations

import requests

from vllm import collect_gating_proposals as cgp


BASE_YAML = """
group: Samplers
steps:
- label: Samplers Test
  key: samplers-test
  device: h200_35gb
  commands:
  - pytest -v -s samplers
- label: Existing AMD Mirror
  key: existing-amd-mirror
  commands:
  - pytest -v -s existing
  mirror:
    amd:
      device: mi325_1
      depends_on:
      - image-build-amd
"""


HEAD_YAML = """
group: Samplers
steps:
- label: Samplers Test
  key: samplers-test
  device: h200_35gb
  commands:
  - pytest -v -s samplers
  mirror:
    amd:
      device: mi300_1
      timeout_in_minutes: 60
      depends_on:
      - image-build-amd
      source_file_dependencies:
      - vllm/v1/sample/
      - tests/samplers
- label: Existing AMD Mirror
  key: existing-amd-mirror
  commands:
  - pytest -v -s existing
  mirror:
    amd:
      device: mi250_1
      depends_on:
      - image-build-amd
"""


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping

    def _resolve(self, key):
        value = self.mapping[key]
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, url, *, params=None):
        key = (url, tuple(sorted((params or {}).items())))
        if key in self.mapping:
            return self._resolve(key)
        return self._resolve(url)

    def get_text(self, url):
        return self._resolve(url)


def pr_search_item(number: int, *, author: str = "alice", title: str | None = None) -> dict:
    return {
        "number": number,
        "title": title or f"PR {number}",
        "html_url": f"https://github.com/vllm-project/vllm/pull/{number}",
        "state": "open",
        "created_at": f"2026-06-{number % 28 + 1:02d}T00:00:00Z",
        "updated_at": f"2026-06-{number % 28 + 1:02d}T12:00:00Z",
        "user": {"login": author},
        "pull_request": {
            "url": f"https://api.github.com/repos/vllm-project/vllm/pulls/{number}",
            "html_url": f"https://github.com/vllm-project/vllm/pull/{number}",
        },
    }


def test_new_mirrors_detects_new_amd_mirror_not_existing_edits():
    rows = cgp.new_mirrors(BASE_YAML, HEAD_YAML, ".buildkite/test_areas/samplers.yaml")
    assert [row.label for row in rows] == ["Samplers Test"]
    row = rows[0]
    assert row.device == "mi300_1"
    assert row.timeout_in_minutes == 60
    assert row.source_file_dependencies == ("vllm/v1/sample/", "tests/samplers")
    assert row.yaml_file == ".buildkite/test_areas/samplers.yaml"


def test_search_open_pr_numbers_queries_each_tracked_author():
    mapping = {}
    for author, number in [("alice", 11), ("bob", 12)]:
        mapping[(
            "https://api.github.com/search/issues",
            tuple(sorted({
                "q": f"repo:vllm-project/vllm is:pr is:open author:{author}",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
                "page": 1,
            }.items())),
        )] = {"items": [pr_search_item(number, author=author)]}
    assert cgp.search_open_pr_numbers(FakeClient(mapping), "vllm-project/vllm", ["alice", "bob"]) == [12, 11]


def test_search_open_pr_numbers_reads_additional_pages():
    first_page = [pr_search_item(number) for number in range(1, 101)]
    mapping = {
        (
            "https://api.github.com/search/issues",
            tuple(sorted({
                "q": "repo:vllm-project/vllm is:pr is:open author:alice",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
                "page": 1,
            }.items())),
        ): {"items": first_page},
        (
            "https://api.github.com/search/issues",
            tuple(sorted({
                "q": "repo:vllm-project/vllm is:pr is:open author:alice",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
                "page": 2,
            }.items())),
        ): {"items": [pr_search_item(101)]},
    }
    numbers = cgp.search_open_pr_numbers(FakeClient(mapping), "vllm-project/vllm", ["alice"])
    assert 101 in numbers
    assert len(numbers) == 101


def test_search_open_pr_numbers_skips_issue_rows_without_pull_request_metadata():
    mapping = {
        (
            "https://api.github.com/search/issues",
            tuple(sorted({
                "q": "repo:vllm-project/vllm is:pr is:open author:alice",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
                "page": 1,
            }.items())),
        ): {"items": [
            {"number": 10, "title": "plain issue"},
            pr_search_item(11),
        ]},
    }
    assert cgp.search_open_pr_numbers(FakeClient(mapping), "vllm-project/vllm", ["alice"]) == [11]


def test_collect_pr_emits_added_mirror_rows_from_changed_test_area_yaml():
    repo = "vllm-project/vllm"
    base_sha = "base123"
    head_sha = "head456"
    mapping = {
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": {
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "html_url": "https://github.com/vllm-project/vllm/pull/44969",
            "state": "open",
            "user": {"login": "AndreasKaratzas"},
            "head": {"ref": "akaratza_stage_d_gating", "sha": head_sha},
            "base": {"ref": "main", "sha": base_sha},
            "updated_at": "2026-06-15T19:57:59Z",
            "created_at": "2026-06-09T06:56:07Z",
        },
        (
            "https://api.github.com/repos/vllm-project/vllm/pulls/44969/files",
            (("page", 1), ("per_page", 100)),
        ): [
            {"filename": ".buildkite/test_areas/samplers.yaml"},
            {"filename": "vllm/v1/sample/rejection_sampler.py"},
        ],
        cgp.raw_url(repo, base_sha, ".buildkite/test_areas/samplers.yaml"): BASE_YAML,
        cgp.raw_url(repo, head_sha, ".buildkite/test_areas/samplers.yaml"): HEAD_YAML,
    }
    pr = cgp.collect_pr(FakeClient(mapping), repo, 44969)
    assert pr is not None
    assert pr["number"] == 44969
    assert pr["author"] == "AndreasKaratzas"
    assert pr["new_mirror_count"] == 1
    assert pr["new_mirrors"][0]["label"] == "Samplers Test"
    assert pr["new_mirrors"][0]["device"] == "mi300_1"


def test_collect_pr_reads_changed_files_after_first_page():
    repo = "vllm-project/vllm"
    base_sha = "base123"
    head_sha = "head456"
    mapping = {
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": {
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "html_url": "https://github.com/vllm-project/vllm/pull/44969",
            "state": "open",
            "user": {"login": "AndreasKaratzas"},
            "head": {"ref": "akaratza_stage_d_gating", "sha": head_sha},
            "base": {"ref": "main", "sha": base_sha},
            "updated_at": "2026-06-15T19:57:59Z",
            "created_at": "2026-06-09T06:56:07Z",
        },
        (
            "https://api.github.com/repos/vllm-project/vllm/pulls/44969/files",
            (("page", 1), ("per_page", 100)),
        ): [{"filename": f"docs/page-{idx}.md"} for idx in range(100)],
        (
            "https://api.github.com/repos/vllm-project/vllm/pulls/44969/files",
            (("page", 2), ("per_page", 100)),
        ): [{"filename": ".buildkite/test_areas/samplers.yaml"}],
        cgp.raw_url(repo, base_sha, ".buildkite/test_areas/samplers.yaml"): BASE_YAML,
        cgp.raw_url(repo, head_sha, ".buildkite/test_areas/samplers.yaml"): HEAD_YAML,
    }

    pr = cgp.collect_pr(FakeClient(mapping), repo, 44969)

    assert pr is not None
    assert pr["new_mirror_count"] == 1
    assert pr["new_mirrors"][0]["label"] == "Samplers Test"


def test_collect_pr_ignores_closed_prs():
    mapping = {
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": {
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "html_url": "https://github.com/vllm-project/vllm/pull/44969",
            "state": "closed",
            "user": {"login": "AndreasKaratzas"},
        },
    }
    assert cgp.collect_pr(FakeClient(mapping), "vllm-project/vllm", 44969) is None


def test_summary_groups_proposals_by_author_and_device():
    payload = [{
        "author": "AndreasKaratzas",
        "new_mirror_count": 2,
        "new_mirrors": [
            {"device": "mi300_1"},
            {"device": "mi325_1"},
        ],
    }]
    summary = cgp.summarize(payload, scanned_pr_count=3, authors=["AndreasKaratzas", "micah-wil"])
    assert summary == {
        "tracked_author_count": 2,
        "scanned_pr_count": 3,
        "proposal_pr_count": 1,
        "proposed_group_count": 2,
        "by_device": {"mi300_1": 1, "mi325_1": 1},
        "by_author": {"AndreasKaratzas": 2},
    }


def test_default_tracked_authors_include_amd_gating_owners():
    assert {
        "AndreasKaratzas",
        "micah-wil",
        "divakar-amd",
        "mawong-amd",
        "Fangzhou-Ai",
        "aarushjain29",
        "stefankoncarevic",
        "okorzh-amd",
    } <= set(cgp.TRACKED_AUTHORS)
    assert {"charlifu", "djramic", "music-dino", "peizhang56"}.isdisjoint(
        cgp.TRACKED_AUTHORS
    )


def _search_key(author: str, page: int = 1):
    return (
        "https://api.github.com/search/issues",
        tuple(sorted({
            "q": f"repo:vllm-project/vllm is:pr is:open author:{author}",
            "sort": "updated",
            "order": "desc",
            "per_page": 100,
            "page": page,
        }.items())),
    )


def previous_payload_for_partial_scan():
    return {
        "generated_at": "2026-06-18T12:00:00Z",
        "pull_requests": [{
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "url": "https://github.com/vllm-project/vllm/pull/44969",
            "author": "AndreasKaratzas",
            "head_ref": "akaratza_stage_d_gating",
            "updated_at": "2026-06-15T19:57:59Z",
            "new_mirror_count": 1,
            "new_mirrors": [{"label": "Samplers Test", "device": "mi300_1"}],
        }],
    }


def test_collect_gating_proposals_retains_previous_pr_when_pr_fetch_fails():
    mapping = {
        _search_key("AndreasKaratzas"): {"items": [pr_search_item(44969, author="AndreasKaratzas")]},
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": requests.HTTPError("temporary upstream failure"),
    }

    payload = cgp.collect_gating_proposals(
        "vllm-project/vllm",
        ["AndreasKaratzas"],
        client=FakeClient(mapping),
        previous=previous_payload_for_partial_scan(),
        since_date="",
    )

    assert payload["collection"]["complete"] is False
    assert payload["collection"]["retained_pr_numbers"] == [44969]
    assert payload["summary"]["proposal_pr_count"] == 1
    assert payload["summary"]["proposed_group_count"] == 1
    assert payload["pull_requests"][0]["retained_from_previous_scan"] is True
    assert payload["pull_requests"][0]["last_seen_at"] == "2026-06-18T12:00:00Z"


def test_collect_gating_proposals_retains_previous_author_rows_when_search_fails():
    mapping = {
        _search_key("AndreasKaratzas"): requests.HTTPError("search unavailable"),
        _search_key("micah-wil"): {"items": []},
    }

    payload = cgp.collect_gating_proposals(
        "vllm-project/vllm",
        ["AndreasKaratzas", "micah-wil"],
        client=FakeClient(mapping),
        previous=previous_payload_for_partial_scan(),
        since_date="",
    )

    assert payload["collection"]["complete"] is False
    assert payload["collection"]["errors"][0]["scope"] == "search"
    assert payload["collection"]["retained_pr_count"] == 1
    assert payload["pull_requests"][0]["number"] == 44969


def test_collect_gating_proposals_drops_retired_authors_from_cached_rows():
    previous = previous_payload_for_partial_scan()
    previous["pull_requests"][0]["author"] = "charlifu"
    previous["collection"] = {
        "candidate_cache": {
            "pull_requests": [
                {
                    "number": 44969,
                    "author": "charlifu",
                    "has_new_mirrors": True,
                }
            ]
        }
    }
    mapping = {
        _search_key("AndreasKaratzas"): requests.HTTPError("search unavailable"),
    }

    payload = cgp.collect_gating_proposals(
        "vllm-project/vllm",
        ["AndreasKaratzas"],
        client=FakeClient(mapping),
        previous=previous,
        since_date="",
    )

    assert payload["collection"]["complete"] is False
    assert payload["collection"]["candidate_cache"]["pr_count"] == 0
    assert payload["collection"]["retained_pr_count"] == 0
    assert payload["pull_requests"] == []


def test_collect_gating_proposals_follows_previous_proposals_but_drops_them_on_clean_rescan():
    repo = "vllm-project/vllm"
    mapping = {
        _search_key("AndreasKaratzas"): {"items": []},
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": {
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "html_url": "https://github.com/vllm-project/vllm/pull/44969",
            "state": "open",
            "user": {"login": "AndreasKaratzas"},
            "head": {"ref": "akaratza_stage_d_gating", "sha": "head456"},
            "base": {"ref": "main", "sha": "base123"},
            "updated_at": "2026-06-15T19:57:59Z",
            "created_at": "2026-06-09T06:56:07Z",
        },
        (
            f"https://api.github.com/repos/{repo}/pulls/44969/files",
            (("page", 1), ("per_page", 100)),
        ): [],
    }

    payload = cgp.collect_gating_proposals(
        repo,
        ["AndreasKaratzas"],
        client=FakeClient(mapping),
        previous=previous_payload_for_partial_scan(),
        since_date="",
    )

    assert payload["collection"]["complete"] is True
    assert payload["collection"]["retained_pr_count"] == 0
    assert payload["collection"]["candidate_cache"]["pr_count"] == 1
    assert payload["collection"]["candidate_cache"]["pull_requests"][0]["has_new_mirrors"] is False
    assert payload["pull_requests"] == []


def test_collect_gating_proposals_writes_candidate_cache_for_active_proposals():
    repo = "vllm-project/vllm"
    mapping = {
        _search_key("AndreasKaratzas"): {"items": [pr_search_item(44969, author="AndreasKaratzas")]},
        "https://api.github.com/repos/vllm-project/vllm/pulls/44969": {
            "number": 44969,
            "title": "[ROCm][CI] Gating more ROCm tests",
            "html_url": "https://github.com/vllm-project/vllm/pull/44969",
            "state": "open",
            "user": {"login": "AndreasKaratzas"},
            "head": {"ref": "akaratza_stage_d_gating", "sha": "head456"},
            "base": {"ref": "main", "sha": "base123"},
            "updated_at": "2026-06-15T19:57:59Z",
            "created_at": "2026-06-09T06:56:07Z",
        },
        (
            f"https://api.github.com/repos/{repo}/pulls/44969/files",
            (("page", 1), ("per_page", 100)),
        ): [{"filename": ".buildkite/test_areas/samplers.yaml"}],
        cgp.raw_url(repo, "base123", ".buildkite/test_areas/samplers.yaml"): BASE_YAML,
        cgp.raw_url(repo, "head456", ".buildkite/test_areas/samplers.yaml"): HEAD_YAML,
    }

    payload = cgp.collect_gating_proposals(
        repo,
        ["AndreasKaratzas"],
        client=FakeClient(mapping),
        since_date="",
    )

    cache = payload["collection"]["candidate_cache"]
    assert cache["proposal_pr_numbers"] == [44969]
    assert cache["pull_requests"][0]["has_new_mirrors"] is True
    assert cache["pull_requests"][0]["new_mirror_count"] == 1
    assert cache["pull_requests"][0]["new_mirror_labels"] == ["Samplers Test"]
