"""Unit contracts for commit-pinned CI definition matching."""

from __future__ import annotations

import io
import tarfile

from vllm import config_parity
from vllm.config_parity import ConfigStep, _match_config_steps


def _step(label: str, identity: str, commands: list[str], source: str) -> ConfigStep:
    return ConfigStep(
        label=label,
        normalized_label=label.lower(),
        identity_key=identity,
        source_file=source,
        group="test",
        commands=commands,
    )


def _snapshot_archive() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        files = {
            "vllm-deadbeef/.buildkite/test-amd.yaml": b"steps:\n  - label: AMD test\n    commands: [pytest amd]\n",
            "vllm-deadbeef/.buildkite/test_areas/basic.yaml": b"group: basic\nsteps:\n  - label: Upstream test\n    commands: [pytest upstream]\n",
            "vllm-deadbeef/README.md": b"not part of the parity snapshot\n",
        }
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def test_source_snapshot_resolves_main_once_and_pins_archive_to_commit(monkeypatch):
    calls = []
    archive = _snapshot_archive()

    class Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/commits/main"):
            return Response({"sha": "deadbeef"})
        assert url.endswith("/tarball/deadbeef")
        return Response(content=archive)

    monkeypatch.setattr(config_parity, "_SOURCE_SNAPSHOT", None)
    monkeypatch.setattr(config_parity.requests, "get", fake_get)

    first = config_parity._load_source_snapshot()
    second = config_parity._load_source_snapshot()

    assert first is second
    assert first.commit_sha == "deadbeef"
    assert sorted(first.files) == [
        ".buildkite/test-amd.yaml",
        ".buildkite/test_areas/basic.yaml",
    ]
    assert calls == [
        "https://api.github.com/repos/vllm-project/vllm/commits/main",
        "https://api.github.com/repos/vllm-project/vllm/tarball/deadbeef",
    ]


def test_exact_commands_and_platform_neutral_titles_form_a_twin():
    amd = _step(
        "Docker Build Metadata (ROCm)",
        "docker build metadata (rocm)",
        ["python tools/check.py"],
        ".buildkite/test-amd.yaml",
    )
    upstream = _step(
        "Docker Build Metadata",
        "docker build metadata",
        ["python tools/check.py"],
        ".buildkite/test_areas/basic.yaml",
    )

    matches, amd_only, upstream_only = _match_config_steps([amd], [upstream], [])

    assert not amd_only
    assert not upstream_only
    assert len(matches) == 1
    assert matches[0].match_method == "command_twin"
    assert matches[0].command_similarity == 1.0


def test_command_twin_requires_nonempty_commands():
    amd = _step("Same title", "amd-key", [], ".buildkite/test-amd.yaml")
    upstream = _step("Same title", "up-key", [], ".buildkite/test_areas/basic.yaml")

    matches, amd_only, upstream_only = _match_config_steps([amd], [upstream], [])

    assert not matches
    assert amd_only == [amd]
    assert upstream_only == [upstream]


def test_ambiguous_exact_command_candidates_remain_unmatched():
    amd = _step("Model correctness", "amd-key", ["pytest tests/models"], ".buildkite/test-amd.yaml")
    upstream_a = _step("Model correctness CUDA", "up-a", ["pytest tests/models"], ".buildkite/test_areas/a.yaml")
    upstream_b = _step("Model correctness H100", "up-b", ["pytest tests/models"], ".buildkite/test_areas/b.yaml")

    matches, amd_only, upstream_only = _match_config_steps(
        [amd], [upstream_a, upstream_b], [],
    )

    assert not matches
    assert amd_only == [amd]
    assert {step.identity_key for step in upstream_only} == {"up-a", "up-b"}
