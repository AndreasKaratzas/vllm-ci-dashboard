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
