from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.vllm.test_dashboard_state import git, init_repo, make_state
from vllm import dashboard_state as state
from vllm import flatten_dashboard_history as history
from vllm import public_projection as projection


@pytest.fixture
def rewrite_repository(tmp_path: Path):
    root = tmp_path / "checkout"
    root.mkdir()
    upstream_sha = init_repo(root)
    git(root, "checkout", "-B", "main")
    for version in (2, 3):
        (root / "scripts/app.py").write_text(f"print({version})\n")
        git(root, "add", "scripts/app.py")
        git(root, "commit", "-m", f"code change {version}")
    main_sha = git(root, "rev-parse", "HEAD")
    policy = state.StatePolicy(
        branch="dashboard-state",
        previous_branch="dashboard-state-previous",
        manifest_path="data/vllm/ci/dashboard_state.json",
        generated_roots=("data", "dashboards", "README.md"),
        max_blob_bytes=1024 * 1024,
        max_tree_bytes=8 * 1024 * 1024,
        max_files=100,
    )
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>AMD MI GPU tests</p>\n")
    projection.create_manifest(site, site / projection.MANIFEST_NAME)
    attestation_path = tmp_path / "attestation.json"
    attestation = projection.write_attestation(site / projection.MANIFEST_NAME, attestation_path)
    previous = make_state(
        root, policy, main_sha, generation="hourly-1-1", value=2, projection_attestation=attestation
    )
    current = make_state(
        root, policy, main_sha, generation="hourly-2-1", value=3, projection_attestation=attestation
    )
    state.write_public_marker(site / projection.MARKER_NAME, current, public_projection=attestation)
    index = tmp_path / "pages-index"
    history._git(root, "read-tree", "--empty", index=index)
    for path in site.iterdir():
        oid = history._git(root, "hash-object", "-w", "--stdin", payload=path.read_bytes())
        history._git(
            root, "update-index", "--add", "--cacheinfo", f"100644,{oid},{path.name}", index=index
        )
    pages_tree = history._git(root, "write-tree", index=index)
    pages = git(root, "commit-tree", pages_tree, input_text="Pages\n")
    ledger = git(root, "commit-tree", f"{main_sha}^{{tree}}", input_text="budget\n")
    original_heads = {
        "refs/heads/main": main_sha,
        "refs/heads/dashboard-state": current.state_sha,
        "refs/heads/dashboard-state-previous": previous.state_sha,
        "refs/heads/gh-pages": pages,
        "refs/heads/data-collection-attempt-budget": ledger,
        "refs/heads/user-unmerged-work": main_sha,
    }
    origin = tmp_path / "origin.git"
    upstream = tmp_path / "upstream.git"
    for remote in (origin, upstream):
        remote.mkdir()
        git(remote, "init", "--bare")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "remote", "add", "upstream", str(upstream))
    git(root, "push", "upstream", f"{upstream_sha}:refs/heads/main")
    for ref, sha in original_heads.items():
        git(root, "update-ref", ref, sha)
    git(root, "push", "origin", *[f"{sha}:{ref}" for ref, sha in original_heads.items()])
    backup = tmp_path / "recovery.bundle"
    git(root, "bundle", "create", str(backup), "--all")
    return root, policy, original_heads, upstream_sha, backup


def test_atomic_rewrite_preserves_code_generations_ledgers_and_user_branches(rewrite_repository):
    root, policy, heads, upstream, backup = rewrite_repository
    checkout_before = git(root, "status", "--porcelain")
    index_before = (root / ".git/index").read_bytes()
    plan = history.prepare_rewrite(root, policy, heads, upstream)
    history.verify_rewrite(root, policy, plan)
    assert git(root, "status", "--porcelain") == checkout_before
    assert (root / ".git/index").read_bytes() == index_before
    assert history.remote_heads(root, "origin") == heads
    history.publish_rewrite(
        root, policy, plan, remote="origin", upstream_remote="upstream", backup=backup
    )
    actual = history.remote_heads(root, "origin")
    main = actual["refs/heads/main"]
    assert git(root, "rev-list", "--count", f"{upstream}..{main}") == "1"
    assert git(root, "diff", "--name-only", heads["refs/heads/main"], main) == ""
    for ref in ("refs/heads/dashboard-state", "refs/heads/dashboard-state-previous"):
        old = state.validate_state_ref(root, heads[ref], policy)
        new = state.validate_state_ref(root, actual[ref], policy, expected_code_sha=main)
        assert new.generated_at == old.generated_at
        assert new.generation_id == old.generation_id
        assert new.manifest["source_refs"] == old.manifest["source_refs"]
        assert new.manifest["generated_files"] == old.manifest["generated_files"]
        assert (
            git(root, "diff", "--name-only", old.state_sha, new.state_sha) == policy.manifest_path
        )
    for ref in ("refs/heads/data-collection-attempt-budget", "refs/heads/user-unmerged-work"):
        assert actual[ref] == heads[ref]
    assert (
        git(
            root, "diff", "--name-only", heads["refs/heads/gh-pages"], actual["refs/heads/gh-pages"]
        )
        == projection.MARKER_NAME
    )


def test_rewrite_refuses_changed_source_in_previous_slot(rewrite_repository):
    root, policy, heads, upstream, _backup = rewrite_repository
    old_code = git(root, "rev-parse", "main~1")
    git(root, "checkout", old_code, "--", "scripts/app.py")
    incompatible = make_state(root, policy, old_code, generation="hourly-old-1", value=1)
    heads = dict(heads)
    heads["refs/heads/dashboard-state-previous"] = incompatible.state_sha
    with pytest.raises(state.DashboardStateError, match="source tree differs"):
        history.prepare_rewrite(root, policy, heads, upstream)


def test_remote_budget_race_aborts_every_update(rewrite_repository):
    root, policy, heads, upstream, backup = rewrite_repository
    plan = history.prepare_rewrite(root, policy, heads, upstream)
    changed = git(root, "commit-tree", "main^{tree}", input_text="new budget reservation\n")
    git(root, "push", "origin", f"{changed}:refs/heads/data-collection-attempt-budget", "--force")
    with pytest.raises(state.DashboardStateError, match="remote branches changed"):
        history.publish_rewrite(
            root, policy, plan, remote="origin", upstream_remote="upstream", backup=backup
        )
    actual = history.remote_heads(root, "origin")
    for ref in plan["updates"]:
        assert actual[ref] == heads[ref]


def test_rewrite_requires_external_backup_of_all_remote_branches(rewrite_repository):
    root, policy, heads, upstream, _backup = rewrite_repository
    plan = history.prepare_rewrite(root, policy, heads, upstream)
    partial_backup = root / ".git/recovery.bundle"
    git(root, "bundle", "create", str(partial_backup), "main")
    with pytest.raises(state.DashboardStateError, match="outside the Git directory"):
        history.publish_rewrite(
            root, policy, plan, remote="origin", upstream_remote="upstream", backup=partial_backup
        )
    partial_backup = root.parent / "partial.bundle"
    git(root, "bundle", "create", str(partial_backup), "main")
    with pytest.raises(state.DashboardStateError, match="missing an original remote branch"):
        history.publish_rewrite(
            root, policy, plan, remote="origin", upstream_remote="upstream", backup=partial_backup
        )
    assert history.remote_heads(root, "origin") == heads
