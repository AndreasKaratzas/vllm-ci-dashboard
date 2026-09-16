from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import build_site
from vllm import public_projection as projection


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_git_projection_commands_disable_lazy_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(tmp_path, "init")
    real_run = subprocess.run
    observed: list[str | None] = []

    def recording_run(*args, **kwargs):
        observed.append((kwargs.get("env") or {}).get("GIT_NO_LAZY_FETCH"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(projection.subprocess, "run", recording_run)
    projection._git(tmp_path, "rev-parse", "--git-dir")
    assert observed == ["1"]


def make_site(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    site = tmp_path / "_site"
    (site / "assets").mkdir(parents=True)
    (site / "data/vllm").mkdir(parents=True)
    (site / "index.html").write_text("<h1>dashboard</h1>\n")
    (site / "assets/app.js").write_text("console.log('ok');\n")
    (site / "data/vllm/current.json").write_text('{"healthy":true}\n')
    os.chmod(site / "assets/app.js", 0o755)

    manifest = site / projection.MANIFEST_NAME
    attestation = tmp_path / "public_projection_attestation.json"
    projection.create_manifest(site, manifest)
    bound = projection.write_attestation(manifest, attestation)
    marker = site / projection.MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generation_id": "test-1",
                "public_projection": bound,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return site, manifest, attestation, bound


def commit_site(site: Path) -> str:
    git(site, "init")
    git(site, "config", "user.name", "Projection Test")
    git(site, "config", "user.email", "projection@example.com")
    git(site, "config", "commit.gpgsign", "false")
    git(site, "add", "-A")
    git(site, "commit", "-m", "publish exact projection")
    return git(site, "rev-parse", "HEAD")


def rewrite_as_safe_historical_projection(
    site: Path, manifest: Path, attestation: Path
) -> dict[str, object]:
    payload = json.loads(manifest.read_text())
    payload["limits"]["max_tree_bytes"] = projection.MAX_TREE_BYTES + 1
    raw = projection._canonical_json(payload)
    manifest.write_bytes(raw)
    bound = projection._attestation_for_manifest(payload, raw)
    attestation.write_bytes(projection._canonical_json(bound))
    marker = json.loads((site / projection.MARKER_NAME).read_text())
    marker["public_projection"] = bound
    (site / projection.MARKER_NAME).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n"
    )
    return bound


def test_local_and_git_tree_round_trip_excludes_only_preview_subtree(tmp_path: Path) -> None:
    site = tmp_path / "_site"
    (site / "pr-preview/pr-17").mkdir(parents=True)
    (site / "pr-preview/pr-17/index.html").write_text("preview\n")
    (site / "data").mkdir()
    (site / "data/current.json").write_text('{"ok":true}\n')
    (site / "index.html").write_text("dashboard\n")
    manifest = site / projection.MANIFEST_NAME
    attestation = tmp_path / "attestation.json"
    created = projection.create_manifest(site, manifest)
    bound = projection.write_attestation(manifest, attestation)
    assert set(created["files"]) == {"data/current.json", "index.html"}
    assert created["excluded_prefixes"] == ["pr-preview/"]
    assert created["limits"] == {
        "max_blob_bytes": 85 * 1024 * 1024,
        "max_tree_bytes": 256 * 1024 * 1024,
        "max_files": 10_000,
    }

    marker = site / projection.MARKER_NAME
    marker.write_text(
        json.dumps({"schema_version": 2, "public_projection": bound}, sort_keys=True)
        + "\n"
    )
    assert projection.verify_local_projection(site, manifest, attestation, marker) == bound

    commit_site(site)
    assert (
        projection.verify_git_projection(
            site,
            "HEAD",
            attestation,
            expected_marker_path=marker,
        )
        == bound
    )


def test_local_verification_detects_changed_missing_and_extra_files(tmp_path: Path) -> None:
    site, manifest, attestation, _bound = make_site(tmp_path)
    marker = site / projection.MARKER_NAME

    (site / "index.html").write_text("changed\n")
    with pytest.raises(projection.PublicProjectionError, match="changed"):
        projection.verify_local_projection(site, manifest, attestation, marker)

    (site / "index.html").write_text("<h1>dashboard</h1>\n")
    (site / "assets/app.js").unlink()
    with pytest.raises(projection.PublicProjectionError, match="missing"):
        projection.verify_local_projection(site, manifest, attestation, marker)

    (site / "assets/app.js").write_text("console.log('ok');\n")
    os.chmod(site / "assets/app.js", 0o755)
    (site / "undeclared.txt").write_text("extra\n")
    with pytest.raises(projection.PublicProjectionError, match="undeclared"):
        projection.verify_local_projection(site, manifest, attestation, marker)


def test_local_generation_rejects_symlink_special_file_and_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "_site"
    site.mkdir()
    (site / "real.txt").write_text("real\n")
    (site / "link.txt").symlink_to("real.txt")
    with pytest.raises(projection.PublicProjectionError, match="symlink"):
        projection.create_manifest(site, site / projection.MANIFEST_NAME)

    (site / "link.txt").unlink()
    fifo = site / "events"
    os.mkfifo(fifo)
    with pytest.raises(projection.PublicProjectionError, match="not a regular file"):
        projection.create_manifest(site, site / projection.MANIFEST_NAME)
    fifo.unlink()

    monkeypatch.setattr(projection, "MAX_BLOB_BYTES", 3)
    with pytest.raises(projection.PublicProjectionError, match="public blob"):
        projection.create_manifest(site, site / projection.MANIFEST_NAME)


def test_projection_bounds_reserve_manifest_and_marker_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "_site"
    site.mkdir()
    (site / "index.html").write_text("ok\n")
    monkeypatch.setattr(projection, "MAX_FILES", 2)

    with pytest.raises(projection.PublicProjectionError, match="plus metadata"):
        projection.create_manifest(site, site / projection.MANIFEST_NAME)


def test_manifest_and_attestation_are_strict_and_canonical(tmp_path: Path) -> None:
    site, manifest, attestation, bound = make_site(tmp_path)
    raw = manifest.read_text()
    duplicate = raw.replace(
        '"schema_version":1',
        '"schema_version":1,"schema_version":1',
        1,
    )
    manifest.write_text(duplicate)
    with pytest.raises(projection.PublicProjectionError, match="duplicate JSON key"):
        projection.load_manifest(manifest)

    projection.create_manifest(site, manifest)
    payload = json.loads(manifest.read_text())
    first = next(iter(payload["files"].values()))
    payload["files"]["../escape"] = first
    payload["file_count"] += 1
    payload["total_bytes"] += first["bytes"]
    manifest.write_bytes(projection._canonical_json(payload))
    with pytest.raises(projection.PublicProjectionError, match="canonical POSIX path"):
        projection.load_manifest(manifest)
    with pytest.raises(projection.PublicProjectionError, match="valid UTF-8"):
        projection._safe_relative_path("bad-\udcff", label="test path")

    bad = dict(bound)
    bad["manifest_sha256"] = "0" * 64
    attestation.write_bytes(projection._canonical_json(bad))
    projection.create_manifest(site, manifest)
    with pytest.raises(projection.PublicProjectionError, match="disagrees"):
        projection.verify_local_projection(
            site,
            manifest,
            attestation,
            site / projection.MARKER_NAME,
        )


def test_safe_historical_limits_are_opt_in_read_only_and_current_creation_resumes(
    tmp_path: Path,
) -> None:
    site, manifest, attestation, _bound = make_site(tmp_path)
    historical_bound = rewrite_as_safe_historical_projection(site, manifest, attestation)
    marker = site / projection.MARKER_NAME

    with pytest.raises(projection.PublicProjectionError, match="limits disagree"):
        projection.verify_local_projection(site, manifest, attestation, marker)
    assert (
        projection.verify_local_projection(
            site,
            manifest,
            attestation,
            marker,
            allow_safe_legacy_limits=True,
        )
        == historical_bound
    )

    commit_site(site)
    with pytest.raises(projection.PublicProjectionError, match="limits disagree"):
        projection.verify_git_projection(site, "HEAD", attestation)
    assert (
        projection.verify_git_projection(
            site,
            "HEAD",
            attestation,
            allow_safe_legacy_limits=True,
        )
        == historical_bound
    )

    # The compatibility flag never affects writers. The next generation is
    # exact current policy and validates without the historical opt-in.
    projection.create_manifest(site, manifest)
    current_bound = projection.write_attestation(manifest, attestation)
    marker_payload = json.loads(marker.read_text())
    marker_payload["public_projection"] = current_bound
    marker.write_text(json.dumps(marker_payload, indent=2, sort_keys=True) + "\n")
    current, _raw = projection.load_manifest(manifest)
    assert current["limits"] == {
        "max_blob_bytes": projection.MAX_BLOB_BYTES,
        "max_tree_bytes": projection.MAX_TREE_BYTES,
        "max_files": projection.MAX_FILES,
    }
    assert projection.verify_local_projection(site, manifest, attestation, marker) == current_bound


@pytest.mark.parametrize(
    "limits",
    [
        {
            "max_blob_bytes": projection.MAX_BLOB_BYTES + 1,
            "max_tree_bytes": projection.MAX_TREE_BYTES + 1,
            "max_files": projection.MAX_FILES,
        },
        {
            "max_blob_bytes": projection.MAX_BLOB_BYTES,
            "max_tree_bytes": projection.MAX_TREE_BYTES - 1,
            "max_files": projection.MAX_FILES,
        },
        {
            "max_blob_bytes": projection.MAX_BLOB_BYTES,
            "max_tree_bytes": projection.MAX_TREE_BYTES + 1,
            "max_files": projection.MAX_FILES + 1,
        },
        {
            "max_blob_bytes": projection.MAX_BLOB_BYTES,
            "max_tree_bytes": True,
            "max_files": projection.MAX_FILES,
        },
    ],
)
def test_safe_historical_limit_compatibility_rejects_weaker_or_malformed_policy(
    tmp_path: Path, limits: dict[str, object]
) -> None:
    _site, manifest, _attestation, _bound = make_site(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["limits"] = limits
    manifest.write_bytes(projection._canonical_json(payload))

    with pytest.raises(projection.PublicProjectionError, match="historical policy"):
        projection.load_manifest(manifest, allow_safe_legacy_limits=True)


def test_safe_historical_limit_compatibility_still_enforces_current_actual_bounds(
    tmp_path: Path,
) -> None:
    _site, manifest, _attestation, _bound = make_site(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["limits"]["max_tree_bytes"] = projection.MAX_TREE_BYTES + 1

    declared_too_small = json.loads(json.dumps(payload))
    declared_too_small["limits"]["max_blob_bytes"] = 1
    manifest.write_bytes(projection._canonical_json(declared_too_small))
    with pytest.raises(projection.PublicProjectionError, match="declared blob limit"):
        projection.load_manifest(manifest, allow_safe_legacy_limits=True)

    oversized = json.loads(json.dumps(payload))
    template = next(iter(oversized["files"].values()))
    oversized["files"] = {
        f"large-{index}.bin": {**template, "bytes": size}
        for index, size in enumerate(
            (
                projection.MAX_BLOB_BYTES,
                projection.MAX_BLOB_BYTES,
                projection.MAX_BLOB_BYTES,
                2 * 1024 * 1024,
            )
        )
    }
    oversized["file_count"] = len(oversized["files"])
    oversized["total_bytes"] = sum(
        descriptor["bytes"] for descriptor in oversized["files"].values()
    )
    manifest.write_bytes(projection._canonical_json(oversized))
    with pytest.raises(projection.PublicProjectionError, match="tree byte limit"):
        projection.load_manifest(manifest, allow_safe_legacy_limits=True)


def test_git_verification_detects_blob_mode_file_set_and_gitlink_changes(
    tmp_path: Path,
) -> None:
    site, _manifest, attestation, _bound = make_site(tmp_path)
    marker = site / projection.MARKER_NAME
    commit_site(site)

    (site / "index.html").write_text("corrupt\n")
    git(site, "add", "index.html")
    git(site, "commit", "-m", "corrupt blob")
    with pytest.raises(projection.PublicProjectionError, match="public blobs differ"):
        projection.verify_git_projection(site, "HEAD", attestation)

    git(site, "reset", "--hard", "HEAD~1")
    (site / "extra.txt").write_text("extra\n")
    git(site, "add", "extra.txt")
    git(site, "commit", "-m", "undeclared file")
    with pytest.raises(projection.PublicProjectionError, match="file set differs"):
        projection.verify_git_projection(site, "HEAD", attestation)

    git(site, "reset", "--hard", "HEAD~1")
    git(site, "update-index", "--add", "--cacheinfo", f"160000,{git(site, 'rev-parse', 'HEAD')},vendor")
    git(site, "commit", "-m", "unsafe gitlink")
    with pytest.raises(projection.PublicProjectionError, match="unsafe"):
        projection.verify_git_projection(site, "HEAD", attestation)

    # The expected marker is byte-exact, not merely a matching generation.
    git(site, "reset", "--hard", "HEAD~1")
    other_marker = tmp_path / "other-marker.json"
    shutil.copyfile(marker, other_marker)
    other_marker.write_text(other_marker.read_text() + " ")
    with pytest.raises(projection.PublicProjectionError, match="expected state marker"):
        projection.verify_git_projection(
            site,
            "HEAD",
            attestation,
            expected_marker_path=other_marker,
        )


def test_git_verifier_reads_only_manifest_and_marker_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site, _manifest, attestation, _bound = make_site(tmp_path)
    commit_site(site)
    original = projection._git_blob
    requested: list[str] = []

    def recording_blob(
        root: Path,
        object_id: str,
        *,
        limit: int,
        label: str,
    ) -> bytes:
        requested.append(label)
        return original(root, object_id, limit=limit, label=label)

    monkeypatch.setattr(projection, "_git_blob", recording_blob)
    projection.verify_git_projection(site, "HEAD", attestation)
    assert requested == ["deployed projection manifest", "deployed publication marker"]


def test_cache_bust_can_be_recreated_from_the_stable_state_generation(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    source = '<script src="assets/app.js?v=17"></script>\n'
    first.write_text(source)
    second.write_text(source)
    generation = "hourly-123456-2"

    build_site.cache_bust_index(first, generation)
    build_site.cache_bust_index(second, generation)

    assert first.read_bytes() == second.read_bytes()
    assert f"?v={generation}" in first.read_text()
