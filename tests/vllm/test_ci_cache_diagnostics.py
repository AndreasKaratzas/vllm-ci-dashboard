"""One-build diagnostics preserve source proof without collection or disclosure."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import yaml

from vllm import export_ci_cache_evidence as evidence
from vllm.ci import analytics_cache as cache

NOW = datetime(2026, 10, 9, 21, tzinfo=timezone.utc)
COMMIT = "ad73a4740dd780c5620099261738a30b979b262c"
KEY = "analytics-builds-v1-Linux-2026-10-09-37986924810-1"
REQUEST = evidence.normalize_request(KEY, "93775", COMMIT)
ROOT = Path(__file__).resolve().parents[2]


def _build(number=93775):
    return {
        "number": number, "branch": "main", "state": "failed", "commit": COMMIT,
        "created_at": "2026-10-09T06:00:03.834Z",
        "started_at": "2026-10-09T06:01:03Z",
        "finished_at": "2026-10-09T08:30:04Z",
        "message": "Full CI run - nightly by private@example.invalid",
        "creator": {"name": "Private author"}, "env": {"TOKEN": "private-secret"},
        "hardware_scope": "amd_mi_gpu", "source_scope_commit": COMMIT,
        "source_definition_tree_sha": "b" * 40,
        "source_scope_index": {
            "version": 1, "commit_sha": COMMIT, "definition_tree_sha": "b" * 40,
            "cpu_routes": [],
        },
        "jobs": [{
            "id": f"job-{number}", "type": "script", "state": "passed",
            "name": "amd_mi355_dpx: Basic Models CPU offload",
            "agent_query_rules": ["queue=amd_mi355_dpx", "token=private-secret"],
            "step": {"id": "step-id", "key": "basic-models-dpx", "label": "private label"},
            "runnable_at": "2026-10-09T06:02:03Z",
            "started_at": "2026-10-09T06:03:03Z",
            "finished_at": "2026-10-09T06:13:03Z",
            "agent": {"hostname": "private-node"}, "command": "private command",
            "env": {"TOKEN": "private-secret"}, "creator": {"name": "Private actor"},
        }],
    }


def _write(tmp_path, builds=None):
    directory = tmp_path / cache.CACHE_DIR_NAME
    cache.write_build_cache(
        directory, "ci", builds=builds if builds is not None else [_build(), _build(93774)],
        watermark=NOW - timedelta(minutes=20), last_full_at=NOW - timedelta(minutes=20),
        updated_at=NOW - timedelta(minutes=20), window_days=8,
        complete_from=NOW - timedelta(days=8), current_only=True,
    )
    return directory


@pytest.fixture(autouse=True)
def no_source_or_collector_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must never make provider requests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


def _export(tmp_path, directory, request=REQUEST, **kwargs):
    return evidence.export_evidence(
        directory, tmp_path / "evidence", request,
        ref_now=kwargs.pop("ref_now", NOW), checkout_root=tmp_path / "checkout", **kwargs,
    )


def test_export_one_exact_dpx_build_preserves_proof_clocks_and_privacy(tmp_path):
    directory = _write(tmp_path)
    before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
    original = json.loads(before[Path("ci.json")])
    original_build = next(build for build in original["builds"] if build["number"] == 93775)
    report = _export(tmp_path, directory)
    exported = json.loads((tmp_path / "evidence/build.json").read_text())
    receipt = json.loads((tmp_path / "evidence/receipt.json").read_text())
    assert exported["number"] == 93775
    assert exported["created_at"] == original_build["created_at"]
    assert exported["source_scope_index"] == _build()["source_scope_index"]
    assert exported["state"] == "failed"
    job = exported["jobs"][0]
    assert job["q"] == "amd_mi355_dpx"
    assert job["step"] == {"id": "step-id", "key": "basic-models-dpx"}
    assert job["started_at"] == "2026-10-09T06:03:03Z"
    assert job["finished_at"] == "2026-10-09T06:13:03Z"
    assert before == {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*.json")}
    combined = (tmp_path / "evidence/build.json").read_bytes() + (tmp_path / "evidence/receipt.json").read_bytes()
    assert all(secret not in combined for secret in (
        b"private-secret", b"private-node", b"Private author", b"private command",
        b"private@example", b"Private actor", b"private label", b"job-93774",
    ))
    assert set(path.name for path in (tmp_path / "evidence").iterdir()) == {"build.json", "receipt.json"}
    assert stat.S_IMODE((tmp_path / "evidence").stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (tmp_path / "evidence").iterdir())
    assert receipt["cache_key"] == KEY
    assert receipt["original_cache_metadata"]["generated_at"] == original["generated_at"]
    assert receipt["original_cache_metadata"]["watermark"] == original["watermark"]
    assert receipt["cache_manifest_proof"]["sha256"] == hashlib.sha256(before[Path("ci.json")]).hexdigest()
    assert receipt["cache_manifest_proof"]["integrity"] == original["integrity"]
    build_raw = (tmp_path / "evidence/build.json").read_bytes()
    assert receipt["selected_build_blob"]["sha256"] == hashlib.sha256(build_raw).hexdigest()
    assert report["bytes"] == len(combined) <= evidence.MAX_EXPORT_BYTES


def test_sharded_cache_uses_real_loader_and_retains_manifest_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_MAX_CACHE_BYTES", 4000)
    directory = _write(tmp_path, [_build(number) for number in range(93774, 93780)])
    manifest = json.loads((directory / "ci.json").read_text())
    assert manifest["cache_kind"] == cache.CACHE_MANIFEST_KIND
    _export(tmp_path, directory)
    receipt = json.loads((tmp_path / "evidence/receipt.json").read_text())
    assert receipt["cache_manifest_proof"]["shards"] == manifest["shards"]
    assert receipt["cache_manifest_proof"]["generation"] == manifest["generation"]


@pytest.mark.parametrize("mutation", ["wrong_commit", "missing_build", "expired", "corrupt"])
def test_invalid_restore_exports_nothing(tmp_path, mutation):
    directory = _write(tmp_path)
    request, now = REQUEST, NOW
    if mutation == "wrong_commit":
        request = evidence.normalize_request(KEY, "93775", "c" * 40)
    elif mutation == "missing_build":
        request = evidence.normalize_request(KEY, "93773", COMMIT)
    elif mutation == "expired":
        now += timedelta(hours=49)
    else:
        path = directory / "ci.json"
        value = json.loads(path.read_text())
        value["builds"][0]["jobs"][0]["step"]["key"] = "altered"
        path.write_text(json.dumps(value))
    with pytest.raises(evidence.EvidenceError):
        _export(tmp_path, directory, request, ref_now=now)
    assert not (tmp_path / "evidence").exists()


def test_legacy_unproved_mi_cache_cannot_be_exported(tmp_path):
    build = _build()
    for field in ("hardware_scope", "source_scope_commit", "source_definition_tree_sha", "source_scope_index"):
        build.pop(field)
    directory = tmp_path / cache.CACHE_DIR_NAME
    cache.write_build_cache(
        directory, "ci", builds=[build], watermark=NOW, last_full_at=NOW,
        updated_at=NOW, window_days=8,
    )
    with pytest.raises(evidence.EvidenceError, match="source authority"):
        _export(tmp_path, directory)
    assert not (tmp_path / "evidence").exists()


def test_saved_index_rejects_cpu_job_even_on_mi_queue(tmp_path):
    build = _build()
    build["source_scope_index"]["cpu_routes"] = [
        {"key": "basic-models-dpx", "label": "Basic Models CPU offload", "agent_pool": "mi355_dpx"},
    ]
    directory = _write(tmp_path, [build])
    with pytest.raises(evidence.EvidenceError, match="CPU or foreign"):
        _export(tmp_path, directory)
    assert not (tmp_path / "evidence").exists()


def test_output_bound_preserves_cache_and_creates_no_artifact(tmp_path, monkeypatch):
    directory = _write(tmp_path)
    original = (directory / "ci.json").read_bytes()
    monkeypatch.setattr(evidence, "MAX_EXPORT_BYTES", 512)
    with pytest.raises(evidence.EvidenceError, match="2 MiB"):
        _export(tmp_path, directory)
    assert not (tmp_path / "evidence").exists()
    assert (directory / "ci.json").read_bytes() == original


def test_export_never_writes_inside_checkout_or_overwrites_evidence(tmp_path):
    directory = _write(tmp_path)
    with pytest.raises(evidence.EvidenceError, match="outside"):
        evidence.export_evidence(directory, tmp_path / "checkout/output", REQUEST, ref_now=NOW,
                                 checkout_root=tmp_path / "checkout")
    output = tmp_path / "evidence"
    output.mkdir()
    sentinel = output / "build.json"
    sentinel.write_text("preserve")
    with pytest.raises(evidence.EvidenceError, match="unused"):
        _export(tmp_path, directory)
    assert sentinel.read_text() == "preserve"


@pytest.mark.parametrize("cache_key,number,commit", [
    (KEY + "\nsecret=unsafe", "93775", COMMIT),
    ("analytics-builds-v1-Linux-2026-02-30-1-1", "93775", COMMIT),
    ("analytics-builds-v1-Linux-2026-10-09-", "93775", COMMIT),
    (KEY, "93775; echo unsafe", COMMIT),
    (KEY, "093775", COMMIT),
    (KEY, "93775", COMMIT[:12]),
    (KEY, "93775", COMMIT + "\nsecret=unsafe"),
])
def test_input_validation_rejects_fallbacks_and_shell_payloads(cache_key, number, commit):
    with pytest.raises(evidence.EvidenceError):
        evidence.normalize_request(cache_key, number, commit)


def test_cli_requires_exact_cache_hit_before_reading(tmp_path, monkeypatch, capsys):
    for key, value in {
        "CACHE_KEY": KEY, "BUILD_NUMBER": "93775", "FULL_COMMIT": COMMIT,
        "CACHE_HIT": "true", "CACHE_MATCHED_KEY": KEY + "-other",
        "CACHE_DIRECTORY": str(tmp_path / "missing"),
        "EVIDENCE_DIRECTORY": str(tmp_path / "evidence"),
    }.items():
        monkeypatch.setenv(key, value)
    assert evidence.main([]) == 1
    assert capsys.readouterr().out == "CI cache evidence rejected; no diagnostic upload is authorized.\n"
    assert not (tmp_path / "evidence").exists()


def test_workflow_is_manual_exact_restore_read_only_and_bounded():
    path = ROOT / ".github/workflows/ci-cache-diagnostics.yml"
    text = path.read_text()
    workflow = yaml.safe_load(text)
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"cache_key", "build_number", "full_commit"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    job = workflow["jobs"]["evidence"]
    assert job["timeout-minutes"] == 15
    assert job["if"] == "github.ref == 'refs/heads/main'"
    steps = job["steps"]
    restore = next(step for step in steps if step.get("id") == "restored")
    assert restore["with"] == {
        "path": "data/vllm/ci/.cache/analytics-builds-v1",
        "key": "${{ steps.request.outputs.cache_key }}", "fail-on-cache-miss": True,
    }
    upload = steps[-1]
    assert upload["with"]["retention-days"] == 1
    assert upload["if"] == "steps.export.outcome == 'success'"
    assert upload["with"]["path"].splitlines() == [
        "${{ runner.temp }}/ci-cache-evidence/build.json",
        "${{ runner.temp }}/ci-cache-evidence/receipt.json",
    ]
    assert not any("cache/save" in step.get("uses", "") for step in steps)
    assert all(len(step["uses"].split("@")[-1]) == 40 for step in steps if "uses" in step)
    assert "secrets." not in text and "BUILDKITE" not in text
    assert all("${{ inputs." not in step.get("run", "") for step in steps)
    assert steps[2]["env"] == {
        "CACHE_KEY": "${{ inputs.cache_key }}", "BUILD_NUMBER": "${{ inputs.build_number }}",
        "FULL_COMMIT": "${{ inputs.full_commit }}",
    }
