"""Tests for the PII scrubber in ``scripts/vllm/ci/buildkite_client.py``.

Why this matters: the Buildkite /builds response embeds the author's email,
the creator's email, the PR-author email, and gravatar URLs. The dashboard
caches those JSON blobs to ``data/vllm/ci/.cache/`` for hot-loop performance
on the hourly collector — but that directory previously got committed into
the public repo, leaking ~20 real emails (including third-party contributors)
for anyone to grep. The scrubber runs on every cache write so the on-disk
copy carries only public-safe fields, even if the directory ever becomes
tracked again.

If any of these asserts fail, review whether you broke the scrubber before
merging — a single missed key puts real mailboxes back in git history.
"""

from __future__ import annotations

import copy

import pytest

from vllm.ci.buildkite_client import _scrub_pii


def _sample_build() -> dict:
    # Shape mirrors a real Buildkite /builds response: nested ``creator``,
    # ``author``, ``pull_request.author``, and ``jobs[*].agent.creator``.
    return {
        "id": "abc",
        "number": 42,
        "state": "passed",
        "creator": {
            "name": "Andreas Karatzas",
            "username": "AndreasKaratzas",
            "email": "Andreas.Karatzas@amd.com",
            "avatar_url": "https://avatars.buildkite.com/abc",
            "gravatar_id": "deadbeef",
        },
        "author": {
            "name": "Jane Doe",
            "email": "jane@example.com",
        },
        "pull_request": {
            "id": 99,
            "author": {
                "login": "upstream-dev",
                "email": "upstream@example.net",
            },
        },
        "jobs": [
            {
                "id": "j1",
                "name": "mi250_1: Tests",
                "agent": {
                    "creator": {
                        "email": "ops@example.com",
                        "name": "Ops Bot",
                    },
                },
            },
            {"id": "j2", "name": "upstream", "agent": None},
        ],
    }


class TestScrubPII:
    def test_top_level_creator_email_stripped(self):
        b = _scrub_pii(_sample_build())
        assert "email" not in b["creator"]
        assert "avatar_url" not in b["creator"]
        assert "gravatar_id" not in b["creator"]

    def test_author_email_stripped(self):
        b = _scrub_pii(_sample_build())
        assert "email" not in b["author"]

    def test_nested_pull_request_author_email_stripped(self):
        b = _scrub_pii(_sample_build())
        assert "email" not in b["pull_request"]["author"]

    def test_list_nested_agent_creator_email_stripped(self):
        b = _scrub_pii(_sample_build())
        assert "email" not in b["jobs"][0]["agent"]["creator"]

    def test_preserves_non_pii_fields(self):
        # Stripping too much would break downstream analytics that group by
        # author name / username. Confirm we keep those.
        b = _scrub_pii(_sample_build())
        assert b["creator"]["name"] == "Andreas Karatzas"
        assert b["creator"]["username"] == "AndreasKaratzas"
        assert b["author"]["name"] == "Jane Doe"
        assert b["pull_request"]["id"] == 99
        assert b["jobs"][0]["id"] == "j1"
        assert b["jobs"][0]["agent"]["creator"]["name"] == "Ops Bot"

    def test_idempotent(self):
        # Scrubbing twice must not regress — running the sanitizer over an
        # already-sanitized cache must be a no-op.
        once = _scrub_pii(_sample_build())
        twice = _scrub_pii(copy.deepcopy(once))
        assert once == twice

    def test_handles_none_and_primitives(self):
        # Must not raise on missing or primitive values.
        assert _scrub_pii(None) is None
        assert _scrub_pii("string") == "string"
        assert _scrub_pii(42) == 42

    def test_cache_path_is_gitignored(self):
        # The .cache directory itself must not be tracked — PII-free scrubs
        # are defense in depth, not a license to commit.
        from pathlib import Path
        gi = (Path(__file__).resolve().parent.parent.parent / ".gitignore").read_text()
        assert "data/vllm/ci/.cache/" in gi or "data/vllm/ci/.cache" in gi
