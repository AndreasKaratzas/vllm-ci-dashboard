"""Privacy-boundary tests for the private Buildkite nightly-roster cache."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vllm.ci import buildkite_client as bk


def _sample_build() -> dict:
    """A deliberately hostile superset of a Buildkite build response."""
    return {
        "number": 42,
        "created_at": "2026-04-18T00:00:00Z",
        "id": "sensitive-build-id",
        "state": "passed",
        "message": "sensitive build message",
        "branch": "private-branch",
        "commit": "private-commit",
        "env": {"SECRET_TOKEN": "build-env-secret"},
        "meta_data": {"customer": "build-metadata-secret"},
        "command": "export BUILD_COMMAND_SECRET=value",
        "creator": {
            "name": "creator-name-secret",
            "email": "creator@example.invalid",
        },
        "author": {
            "name": "author-name-secret",
            "email": "author@example.invalid",
        },
        "pull_request": {
            "id": 99,
            "author": {"email": "pr-author@example.invalid"},
        },
        "future_unreviewed_field": {"secret": "future-field-secret"},
        "jobs": [
            {
                "type": "script",
                "id": "j1",
                "name": "mi250_1: Tests",
                "state": "passed",
                "soft_failed": True,
                "step_key": "tests-mi250",
                "retried_in_job_id": None,
                "env": {"JOB_SECRET_TOKEN": "job-env-secret"},
                "meta_data": {"tenant": "job-metadata-secret"},
                "command": "export JOB_COMMAND_SECRET=value",
                "agent": {
                    "name": "agent-name-secret",
                    "meta_data": ["queue=private", "host=private-host"],
                },
                "creator": {"email": "job-creator@example.invalid"},
                "step": {"id": "sensitive-step-id"},
                "raw_log_url": "https://example.invalid/private-raw-log",
                "web_url": "https://example.invalid/private-job-page",
                "future_unreviewed_field": "future-job-field-secret",
            },
            {
                "type": "waiter",
                "id": "non-script-sensitive-id",
                "state": "passed",
                "command": "non-script-command-secret",
            },
        ],
    }


class TestNightlyRosterAllowlist:
    def test_projection_has_exact_build_and_job_allowlists(self):
        projected = bk._project_nightly_roster_build(_sample_build())

        assert projected is not None
        assert set(projected) == {"number", "created_at", "jobs"}
        assert projected["number"] == 42
        assert projected["jobs"] == [{
            "type": "script",
            "id": "j1",
            "name": "mi250_1: Tests",
            "state": "passed",
            "soft_failed": True,
            "step_key": "tests-mi250",
        }]
        assert set(projected["jobs"][0]) <= bk._ROSTER_JOB_FIELDS

    def test_persistent_shard_cannot_contain_sensitive_or_future_fields(
        self, tmp_path
    ):
        shard_dir = bk.write_nightly_build_cache(
            "amd",
            [_sample_build()],
            tmp_path,
            now=datetime(2026, 4, 18, 12, tzinfo=timezone.utc),
        )
        shard = shard_dir / "2026-04-18_42.json"
        payload = json.loads(shard.read_text())

        assert set(payload) == {"schema_version", "build"}
        assert payload["schema_version"] == bk.NIGHTLY_ROSTER_CACHE_SCHEMA_VERSION
        assert set(payload["build"]) == {"number", "created_at", "jobs"}
        assert set(payload["build"]["jobs"][0]) == {
            "type", "id", "name", "state", "soft_failed", "step_key"
        }

        serialized = shard.read_text()
        for forbidden in (
            "env",
            "meta_data",
            "creator",
            "author",
            "agent",
            "command",
            "raw_log_url",
            "web_url",
            "pull_request",
            "future_unreviewed_field",
            "example.invalid",
            "secret",
            "private-host",
        ):
            assert forbidden not in serialized

    def test_projection_is_idempotent_and_rejects_non_build_values(self):
        once = bk._project_nightly_roster_build(_sample_build())
        assert once is not None
        assert bk._project_nightly_roster_build(once) == once
        assert bk._project_nightly_roster_build(None) is None
        assert bk._project_nightly_roster_build("string") is None
        assert bk._project_nightly_roster_build(42) is None

    def test_cache_path_is_gitignored(self):
        # The allowlist is defense in depth, not a license to publish caches.
        from pathlib import Path
        gi = (Path(__file__).resolve().parent.parent.parent / ".gitignore").read_text()
        assert "data/vllm/ci/.cache/" in gi or "data/vllm/ci/.cache" in gi
