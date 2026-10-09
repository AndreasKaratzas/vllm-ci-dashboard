"""Prepare a code-equivalent fork rewrite without detaching saved dashboard state.

The four public/state refs move together with explicit leases. Collection times,
source refs, generated blobs, budget refs and unmerged branches remain intact.
Preparation only creates Git objects and an external recovery bundle; it never
changes the checkout or a ref. Both saved generations must already contain the
same executable source as main, so this operation cannot disguise a code upgrade.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dashboard_state as state
from . import public_projection as projection


def _git(root: Path, *args: str, payload: bytes | None = None, index: Path | None = None) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(
        ["git", *args], cwd=root, input=payload, capture_output=True, env=env, check=False
    )
    if result.returncode:
        raise state.DashboardStateError(
            f"git {args[0]} failed: {result.stderr.decode(errors='replace')[-2000:]}"
        )
    return result.stdout.decode().strip()


def remote_heads(root: Path, remote: str) -> dict[str, str]:
    state._safe_revision(remote, label="remote")
    return {
        ref: state._full_sha(sha, label="remote head")
        for sha, ref in (
            line.split() for line in _git(root, "ls-remote", "--heads", remote).splitlines()
        )
    }


def _external_backup(root: Path, backup: Path) -> Path:
    backup = backup.resolve()
    directories = (
        Path(_git(root, "rev-parse", "--absolute-git-dir")).resolve(),
        Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(),
    )
    if any(directory == backup or directory in backup.parents for directory in directories):
        raise state.DashboardStateError("recovery bundle must exist outside the Git directory")
    return backup


def _replace_blob(root: Path, commit: str, path: str, payload: bytes, message: str) -> str:
    object_id = _git(root, "hash-object", "-w", "--stdin", payload=payload)
    with tempfile.TemporaryDirectory(prefix="dashboard-rewrite-index-") as directory:
        index = Path(directory) / "index"
        _git(root, "read-tree", commit, index=index)
        _git(root, "update-index", "--cacheinfo", f"100644,{object_id},{path}", index=index)
        tree = _git(root, "write-tree", index=index)
    return _git(root, "commit-tree", tree, payload=message.encode())


def rebind_state(
    root: Path, policy: state.StatePolicy, original: str, flat_main: str
) -> state.ValidatedState:
    saved = state.validate_state_ref(root, original, policy)
    state._validate_code_identity_metadata(
        saved.entries, state._tree_entries_metadata(root, flat_main), policy
    )
    manifest = dict(saved.manifest)
    manifest["code_sha"] = flat_main
    rebound = _replace_blob(
        root,
        original,
        policy.manifest_path,
        state._canonical_manifest_bytes(manifest),
        f"Rebind {saved.generation_id} to equivalent flattened source\n",
    )
    validated = state.validate_state_ref(root, rebound, policy, expected_code_sha=flat_main)
    if validated.manifest["generated_files"] != saved.manifest["generated_files"]:
        raise state.DashboardStateError("history rewrite changed generated data")
    if validated.manifest["source_refs"] != saved.manifest["source_refs"]:
        raise state.DashboardStateError("history rewrite changed source evidence")
    return validated


def prepare_rewrite(
    root: Path, policy: state.StatePolicy, heads: Mapping[str, str], upstream_main: str
) -> dict[str, Any]:
    """Create a fully checked four-ref plan without changing any existing ref."""
    root = root.resolve()
    upstream_main = state._full_sha(upstream_main, label="upstream main")
    refs = [
        "refs/heads/main",
        f"refs/heads/{policy.branch}",
        f"refs/heads/{policy.previous_branch}",
        "refs/heads/gh-pages",
    ]
    if any(ref not in heads for ref in refs):
        raise state.DashboardStateError("main, Pages and both saved generations are required")
    originals = {ref: state._full_sha(heads[ref], label=ref) for ref in refs}
    main = originals[refs[0]]
    _git(root, "merge-base", "--is-ancestor", upstream_main, main)
    tree = _git(root, "rev-parse", f"{main}^{{tree}}")
    flat = _git(
        root,
        "commit-tree",
        tree,
        "-p",
        upstream_main,
        payload=b"Maintain AMD MI GPU CI dashboard\n\nConsolidate fork changes and retain bounded data snapshots.\n",
    )
    current = rebind_state(root, policy, originals[refs[1]], flat)
    previous = rebind_state(root, policy, originals[refs[2]], flat)
    pages = originals[refs[3]]
    old_marker = state.validate_public_marker(
        json.loads(_git(root, "show", f"{pages}:{projection.MARKER_NAME}")),
        expected_state_sha=originals[refs[1]],
    )
    old_state = state.validate_state_ref(root, originals[refs[1]], policy)
    if any(
        old_marker[key] != getattr(old_state, key)
        for key in ("code_sha", "state_tree", "generation_id", "generated_at")
    ):
        raise state.DashboardStateError("Pages does not match the current saved generation")
    with tempfile.TemporaryDirectory(prefix="dashboard-rewrite-proof-") as directory:
        attestation_path = Path(directory) / "attestation.json"
        attestation_path.write_bytes(
            state._cat_blob(root, old_state.entries[projection.ATTESTATION_PATH])
        )
        attestation = projection.verify_git_projection(root, pages, attestation_path)
        marker_path = Path(directory) / projection.MARKER_NAME
        state.write_public_marker(marker_path, current, public_projection=attestation)
        rebound_pages = _replace_blob(
            root,
            pages,
            projection.MARKER_NAME,
            marker_path.read_bytes(),
            f"Bind Pages to equivalent flattened source ({current.generation_id})\n",
        )
        projection.verify_git_projection(
            root, rebound_pages, attestation_path, expected_marker_path=marker_path
        )
    return {
        "schema_version": 1,
        "upstream_main": upstream_main,
        "observed_heads": dict(sorted(heads.items())),
        "updates": {
            ref: {"before": originals[ref], "after": after}
            for ref, after in zip(
                refs, (flat, current.state_sha, previous.state_sha, rebound_pages)
            )
        },
        "generations": [current.generation_id, previous.generation_id],
        "generated_at": [current.generated_at, previous.generated_at],
    }


def verify_rewrite(root: Path, policy: state.StatePolicy, plan: Mapping[str, Any]) -> None:
    updates = plan["updates"]
    refs = {
        "refs/heads/main",
        "refs/heads/gh-pages",
        f"refs/heads/{policy.branch}",
        f"refs/heads/{policy.previous_branch}",
    }
    if plan.get("schema_version") != 1 or set(updates) != refs:
        raise state.DashboardStateError("history rewrite plan has an unexpected shape")
    for ref, update in updates.items():
        state._full_sha(update["before"], label=f"{ref} original")
        state._full_sha(update["after"], label=f"{ref} replacement")
        if plan["observed_heads"].get(ref) != update["before"]:
            raise state.DashboardStateError("rewrite lease differs from captured remote ref")
    before = updates["refs/heads/main"]["before"]
    after = updates["refs/heads/main"]["after"]
    upstream = state._full_sha(plan["upstream_main"], label="upstream main")
    if state._commit_parents(root, after) != (upstream,):
        raise state.DashboardStateError("replacement main must be exactly one commit ahead")
    if _git(root, "rev-parse", f"{before}^{{tree}}") != _git(
        root, "rev-parse", f"{after}^{{tree}}"
    ):
        raise state.DashboardStateError("history rewrite changed the main tree")
    current = None
    for ref in (f"refs/heads/{policy.branch}", f"refs/heads/{policy.previous_branch}"):
        old = state.validate_state_ref(root, updates[ref]["before"], policy)
        new = state.validate_state_ref(root, updates[ref]["after"], policy, expected_code_sha=after)
        expected = dict(old.manifest)
        expected["code_sha"] = after
        if dict(new.manifest) != expected:
            raise state.DashboardStateError(
                "saved generation changed beyond equivalent code binding"
            )
        if ref.endswith("/" + policy.branch):
            current = new
    assert current is not None
    old_pages = state._tree_entries_metadata(root, updates["refs/heads/gh-pages"]["before"])
    new_pages = state._tree_entries_metadata(root, updates["refs/heads/gh-pages"]["after"])
    for entries in (old_pages, new_pages):
        entries.pop(projection.MARKER_NAME, None)
    if old_pages != new_pages:
        raise state.DashboardStateError(
            "history rewrite changed Pages beyond the generation marker"
        )
    with tempfile.TemporaryDirectory(prefix="dashboard-rewrite-verify-") as directory:
        attestation = Path(directory) / "attestation.json"
        attestation.write_bytes(state._cat_blob(root, current.entries[projection.ATTESTATION_PATH]))
        marker_path = Path(directory) / projection.MARKER_NAME
        state.write_public_marker(
            marker_path, current, public_projection=projection.load_attestation(attestation)
        )
        projection.verify_git_projection(
            root,
            updates["refs/heads/gh-pages"]["after"],
            attestation,
            expected_marker_path=marker_path,
        )


def publish_rewrite(
    root: Path,
    policy: state.StatePolicy,
    plan: Mapping[str, Any],
    *,
    remote: str,
    upstream_remote: str,
    backup: Path,
) -> None:
    """Publish only a validated plan whose backup and every remote lease still match."""
    verify_rewrite(root, policy, plan)
    backup = _external_backup(root, backup)
    if not backup.is_file():
        raise state.DashboardStateError("recovery bundle must exist outside the Git directory")
    _git(root, "bundle", "verify", str(backup))
    backed_up = {
        line.split()[0] for line in _git(root, "bundle", "list-heads", str(backup)).splitlines()
    }
    if not set(plan["observed_heads"].values()).issubset(backed_up):
        raise state.DashboardStateError("recovery bundle is missing an original remote branch")
    if remote_heads(root, remote) != plan["observed_heads"]:
        raise state.DashboardStateError("remote branches changed after preparation; prepare again")
    if remote_heads(root, upstream_remote).get("refs/heads/main") != plan["upstream_main"]:
        raise state.DashboardStateError("upstream main changed after preparation; prepare again")
    updates = plan["updates"]
    leases = [f"--force-with-lease={ref}:{update['before']}" for ref, update in updates.items()]
    targets = [f"{update['after']}:{ref}" for ref, update in updates.items()]
    _git(root, "push", "--atomic", *leases, remote, *targets)
    actual = remote_heads(root, remote)
    if any(actual.get(ref) != value["after"] for ref, value in updates.items()):
        raise state.DashboardStateError("published rewrite refs differ from the checked plan")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "publish"))
    parser.add_argument("--root", type=Path, default=state.ROOT)
    parser.add_argument("--config", type=Path, default=state.DEFAULT_CONFIG_PATH)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--upstream", default="upstream")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    policy = state.load_policy(args.config)
    try:
        if args.operation == "prepare":
            _external_backup(root, args.backup)
            if args.backup.exists() or args.plan.exists():
                raise state.DashboardStateError("choose unused external backup and plan paths")
            heads = remote_heads(root, args.remote)
            upstream = remote_heads(root, args.upstream)["refs/heads/main"]
            for ref, sha in heads.items():
                local = _git(
                    root,
                    "rev-parse",
                    "--verify",
                    ref.replace("refs/heads/", f"refs/remotes/{args.remote}/"),
                )
                if local != sha:
                    raise state.DashboardStateError("fetch all remote branches before preparing")
            plan = prepare_rewrite(root, policy, heads, upstream)
            verify_rewrite(root, policy, plan)
            args.backup.parent.mkdir(parents=True, exist_ok=True)
            _git(root, "bundle", "create", str(args.backup.resolve()), "--all")
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
            print(
                f"Prepared four atomic updates; source and generation bytes preserved. Plan: {args.plan}"
            )
        else:
            plan = json.loads(args.plan.read_text())
            publish_rewrite(
                root,
                policy,
                plan,
                remote=args.remote,
                upstream_remote=args.upstream,
                backup=args.backup,
            )
            print(
                "Published main at one commit ahead; both state slots and Pages bindings verified."
            )
    except (
        state.DashboardStateError,
        projection.PublicProjectionError,
        ValueError,
        KeyError,
    ) as exc:
        parser.exit(1, f"History rewrite refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
