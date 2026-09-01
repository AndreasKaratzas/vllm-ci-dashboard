"""Bounded private Operations snapshot and publication-boundary contracts."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from vllm import build_operations_snapshot as operations
from vllm.audit_dashboard_data import DashboardAudit


ROOT = Path(__file__).resolve().parents[2]
BUILD_SITE_PATH = ROOT / "scripts" / "build_site.py"


def _load_build_site():
    spec = importlib.util.spec_from_file_location(
        "compressed_operations_build_site",
        BUILD_SITE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD_SITE = _load_build_site()
PAYLOAD = {
    "schema_version": 2,
    "generated_at": "2026-09-01T00:00:00Z",
    "nightly": {"pipelines": []},
}


def test_gzip_snapshot_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    first = tmp_path / "first" / operations.DEFAULT_OUTPUT_NAME
    second = tmp_path / "second" / operations.DEFAULT_OUTPUT_NAME

    operations.write_snapshot_bundle(first, PAYLOAD, log=False)
    operations.write_snapshot_bundle(second, PAYLOAD, log=False)

    assert operations.DEFAULT_OUTPUT_NAME == "operations_v2.json.gz"
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes()[4:8] == b"\x00\x00\x00\x00"
    assert operations.load_snapshot_payload(first) == PAYLOAD


def test_successful_writer_removes_the_stale_alternate(tmp_path: Path) -> None:
    compressed = tmp_path / "operations_v2.json.gz"
    raw = tmp_path / "operations_v2.json"
    raw.write_text("stale")

    operations.write_snapshot_bundle(compressed, PAYLOAD, log=False)

    assert compressed.is_file()
    assert not raw.exists()

    compressed.write_bytes(b"stale")
    operations.write_snapshot_bundle(raw, PAYLOAD, log=False)
    assert raw.is_file()
    assert json.loads(raw.read_text()) == PAYLOAD
    assert not compressed.exists()


def test_raw_write_ceiling_fails_before_mutating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "operations_v2.json"
    output.write_text("existing-generation")
    monkeypatch.setattr(operations, "OPERATIONS_RAW_WRITE_MAX_BYTES", 8)

    with pytest.raises(RuntimeError, match="Raw Operations snapshot.*write limit"):
        operations.write_snapshot_bundle(output, PAYLOAD, log=False)

    assert output.read_text() == "existing-generation"
    assert not (tmp_path / operations.OPERATIONS_MANIFEST_NAME).exists()


def test_gzip_write_and_decompression_ceilings_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "operations_v2.json.gz"
    monkeypatch.setattr(operations, "OPERATIONS_GZIP_MAX_BYTES", 1)
    with pytest.raises(RuntimeError, match="Compressed Operations snapshot"):
        operations.write_snapshot_bundle(output, PAYLOAD, log=False)
    assert not output.exists()

    expanded = tmp_path / "expanded.json.gz"
    expanded.write_bytes(gzip.compress(b'{"value":"' + b"x" * 64 + b'"}', mtime=0))
    monkeypatch.setattr(operations, "OPERATIONS_GZIP_MAX_BYTES", 1024)
    monkeypatch.setattr(operations, "OPERATIONS_DECOMPRESSED_MAX_BYTES", 16)
    with pytest.raises(RuntimeError, match="expands to more than"):
        operations.load_snapshot_payload(expanded)


def test_small_raw_snapshot_remains_supported(tmp_path: Path) -> None:
    output = tmp_path / "operations_v2.json"
    operations.write_snapshot_bundle(output, PAYLOAD, log=False)

    assert operations.load_snapshot_payload(output) == PAYLOAD


def test_site_uses_one_declared_gzip_input_without_publishing_it(
    tmp_path: Path,
) -> None:
    source_data = tmp_path / "data"
    site_data = tmp_path / "site-data"
    source = source_data / "vllm/ci/operations_v2.json.gz"
    operations.write_snapshot_bundle(source, PAYLOAD, log=False)
    manifest = {"build_inputs": ["vllm/ci/operations_v2.json.gz"]}

    BUILD_SITE.materialize_operations_bundle(source_data, site_data, manifest)

    public_root = site_data / "vllm/ci"
    assert not (public_root / "operations_v2.json.gz").exists()
    assert not (public_root / "operations_v2.json").exists()
    public_manifest = json.loads(
        (public_root / operations.OPERATIONS_MANIFEST_NAME).read_text()
    )
    assert public_manifest["monolith"] is None
    assert (public_root / "operations_v2/nightly.json").is_file()


@pytest.mark.parametrize(
    "declared",
    [
        [],
        ["vllm/ci/operations_v2.json", "vllm/ci/operations_v2.json.gz"],
    ],
)
def test_site_rejects_ambiguous_or_missing_operations_declaration(
    tmp_path: Path,
    declared: list[str],
) -> None:
    with pytest.raises(RuntimeError, match="exactly one private Operations source"):
        BUILD_SITE.materialize_operations_bundle(
            tmp_path / "data",
            tmp_path / "site-data",
            {"build_inputs": declared},
        )


def test_audit_transparently_reads_the_gzip_production_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "data/vllm/ci/operations_v2.json.gz"
    operations.write_snapshot_bundle(source, PAYLOAD, log=False)
    audit = DashboardAudit(tmp_path)

    assert audit.load_json("data/vllm/ci/operations_v2.json", {}) == PAYLOAD
    assert not audit.report.errors


def test_production_limits_are_below_repository_boundaries() -> None:
    assert operations.OPERATIONS_GZIP_MAX_BYTES == 64 * 1024 * 1024
    assert operations.OPERATIONS_RAW_WRITE_MAX_BYTES == 85 * 1024 * 1024
    assert operations.OPERATIONS_DECOMPRESSED_MAX_BYTES == 256 * 1024 * 1024
    assert operations.OPERATIONS_GZIP_MAX_BYTES < 90_000_000
