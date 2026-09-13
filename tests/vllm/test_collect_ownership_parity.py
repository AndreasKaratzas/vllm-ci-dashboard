"""Tests for the build-pinned ownership parity collector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm import collect_ownership_parity


COMMIT_SHA = "7f599d78546819948c32f2b23d913507bbb38875"


def _write_matrix(input_dir: Path, yaml_url: str | None = None) -> None:
    source = {}
    if yaml_url is not None:
        source["yaml_url"] = yaml_url
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "amd_test_matrix.json").write_text(
        json.dumps({"source": source}) + "\n"
    )


def _pinned_yaml_url(commit_sha: str = COMMIT_SHA) -> str:
    return (
        "https://raw.githubusercontent.com/vllm-project/vllm/"
        f"{commit_sha}/.buildkite/test-amd.yaml"
    )


def test_collects_parity_from_the_matrix_commit(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_matrix(input_dir, _pinned_yaml_url())

    sentinel_snapshot = object()
    original_branch = collect_ownership_parity.config_parity.VLLM_BRANCH
    original_snapshot = collect_ownership_parity.config_parity._SOURCE_SNAPSHOT
    monkeypatch.setattr(
        collect_ownership_parity,
        "load_pinned_snapshot",
        lambda commit: sentinel_snapshot,
    )
    observed = {}

    def fake_build_config_parity():
        observed["branch"] = collect_ownership_parity.config_parity.VLLM_BRANCH
        observed["snapshot"] = collect_ownership_parity.config_parity._SOURCE_SNAPSHOT
        return {
            "generated_at": "2026-07-28T00:00:00Z",
            "source": {"commit_sha": COMMIT_SHA},
            "summary": {"matched": 123},
        }

    monkeypatch.setattr(
        collect_ownership_parity.config_parity,
        "build_config_parity",
        fake_build_config_parity,
    )

    output_path = collect_ownership_parity.collect_ownership_parity(
        input_dir,
        output_dir,
    )

    assert observed == {"branch": COMMIT_SHA, "snapshot": sentinel_snapshot}
    assert output_path == output_dir / "ownership_config_parity.json"
    assert json.loads(output_path.read_text())["source"]["commit_sha"] == COMMIT_SHA
    assert collect_ownership_parity.config_parity.VLLM_BRANCH == original_branch
    assert collect_ownership_parity.config_parity._SOURCE_SNAPSHOT is original_snapshot


def test_ownership_parity_overflow_preserves_lkg(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_matrix(input_dir, _pinned_yaml_url())
    output_dir.mkdir()
    output_path = output_dir / "ownership_config_parity.json"
    output_path.write_text('{"generation":"last-known-good"}\n')
    monkeypatch.setattr(
        collect_ownership_parity,
        "load_pinned_snapshot",
        lambda commit: object(),
    )
    monkeypatch.setattr(
        collect_ownership_parity.config_parity,
        "build_config_parity",
        lambda: {
            "source": {"commit_sha": COMMIT_SHA},
            "padding": "x" * 1_000,
        },
    )
    monkeypatch.setattr(
        collect_ownership_parity,
        "OWNERSHIP_CONFIG_PARITY_MAX_BYTES",
        128,
    )

    with pytest.raises(RuntimeError, match="exceeds its byte budget"):
        collect_ownership_parity.collect_ownership_parity(input_dir, output_dir)

    assert json.loads(output_path.read_text()) == {
        "generation": "last-known-good"
    }


def _oversized_parity_report() -> dict:
    report = {
        "generated_at": "2026-09-01T00:00:00Z",
        "source": {"commit_sha": COMMIT_SHA, "repository": "vllm-project/vllm"},
        "summary": {"total_amd_steps": 180, "uncovered": 20},
    }
    for collection, count in {
        "matches": 18,
        "inline_mirror_variants": 12,
        "additional_variants": 8,
        "amd_only": 16,
        "nvidia_only": 14,
        "mirrors": 40,
    }.items():
        report[collection] = [
            {
                "identity_key": f"{collection}-{index:03d}",
                "color": "red" if collection == "amd_only" else "green",
                "command_similarity": 0.1 if collection == "amd_only" else 1.0,
                "commands": ["pytest " + ("x" * 500)],
            }
            for index in range(count)
        ]
    return report


def test_config_parity_compaction_drops_duplicate_targets_before_core_rows():
    report = _oversized_parity_report()

    bounded = collect_ownership_parity.bounded_config_parity_payload(
        report,
        max_bytes=50_000,
    )
    encoded = (json.dumps(bounded, indent=2) + "\n").encode()
    retention = bounded["publication_retention"]

    assert len(encoded) <= 50_000
    assert retention["complete_relative_to_source"] is False
    assert retention["aggregate_summary_complete"] is True
    assert retention["collections"]["mirrors"]["omitted"] > 0
    for name in (
        "matches",
        "inline_mirror_variants",
        "additional_variants",
        "amd_only",
        "nvidia_only",
    ):
        assert retention["collections"][name]["omitted"] == 0
    assert bounded["summary"] == report["summary"]


def test_config_parity_compaction_retains_actionable_rows_first_and_is_stable():
    report = _oversized_parity_report()
    reversed_report = dict(report)
    for name in collect_ownership_parity.CONFIG_PARITY_ROW_COLLECTIONS:
        reversed_report[name] = list(reversed(report[name]))

    bounded = collect_ownership_parity.bounded_config_parity_payload(
        report,
        max_bytes=14_000,
    )
    reversed_bounded = collect_ownership_parity.bounded_config_parity_payload(
        reversed_report,
        max_bytes=14_000,
    )

    assert bounded == reversed_bounded
    assert len((json.dumps(bounded, indent=2) + "\n").encode()) <= 14_000
    retention = bounded["publication_retention"]["collections"]
    assert retention["mirrors"]["published"] == 0
    assert retention["amd_only"]["published"] > 0
    assert retention["matches"]["published"] == 0
    for name in collect_ownership_parity.CONFIG_PARITY_ROW_COLLECTIONS:
        counts = retention[name]
        assert counts["source"] == counts["published"] + counts["omitted"]


def test_each_parity_writer_half_cap_composes_within_pair_budget():
    report = _oversized_parity_report()
    half_cap = 20_000

    primary = collect_ownership_parity.bounded_config_parity_payload(
        report,
        max_bytes=half_cap,
    )
    ownership = collect_ownership_parity.bounded_config_parity_payload(
        report,
        max_bytes=half_cap,
    )
    sizes = [
        len((json.dumps(payload, indent=2) + "\n").encode())
        for payload in (primary, ownership)
    ]

    assert all(size <= half_cap for size in sizes)
    assert sum(sizes) <= half_cap * 2


@pytest.mark.parametrize(
    "yaml_url",
    [
        None,
        "main",
        "https://example.invalid/vllm/main/.buildkite/test-amd.yaml",
        (
            "https://example.invalid/vllm-project/vllm/"
            f"{COMMIT_SHA}/.buildkite/test-amd.yaml"
        ),
        f"https://example.invalid/{COMMIT_SHA[:-1]}/test-amd.yaml",
        f"https://example.invalid/{COMMIT_SHA}0/test-amd.yaml",
        f"https://example.invalid/{COMMIT_SHA}/{COMMIT_SHA}/test-amd.yaml",
    ],
)
def test_main_fails_for_missing_or_malformed_matrix_commit(
    tmp_path,
    capsys,
    yaml_url,
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_matrix(input_dir, yaml_url)

    result = collect_ownership_parity.main(
        ["--input-dir", str(input_dir), "--output", str(output_dir)]
    )

    assert result == 1
    assert "failed" in capsys.readouterr().err.lower()
    assert not (output_dir / "ownership_config_parity.json").exists()


def test_main_fails_when_collector_reports_a_different_commit(
    tmp_path,
    monkeypatch,
    capsys,
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_matrix(input_dir, _pinned_yaml_url())
    other_sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(
        collect_ownership_parity.config_parity,
        "build_config_parity",
        lambda: {"source": {"commit_sha": other_sha}},
    )
    monkeypatch.setattr(
        collect_ownership_parity,
        "load_pinned_snapshot",
        lambda commit: object(),
    )

    result = collect_ownership_parity.main(
        ["--input-dir", str(input_dir), "--output", str(output_dir)]
    )

    assert result == 1
    assert "source commit mismatch" in capsys.readouterr().err
    assert not (output_dir / "ownership_config_parity.json").exists()


def test_raw_public_yaml_fetch_does_not_receive_github_token(monkeypatch):
    captured = {}

    class Response:
        text = "group: kernels\nsteps: []\n"

        @staticmethod
        def raise_for_status():
            return None

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return Response()

    monkeypatch.setattr(collect_ownership_parity.requests, "get", fake_get)

    path, payload = collect_ownership_parity._fetch_yaml(
        ".buildkite/test_areas/kernels.yaml",
        COMMIT_SHA,
    )

    assert path == ".buildkite/test_areas/kernels.yaml"
    assert payload["group"] == "kernels"
    assert "Authorization" not in captured["headers"]
    assert f"/{COMMIT_SHA}/" in captured["url"]
