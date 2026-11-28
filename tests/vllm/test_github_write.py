from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm import dns_request_budget, github_write, queue_request_budget
from vllm import request_bearing_attempt_budget


# Stderr GitHub returned during the 2026-11-04 incident (request ids elided).
INTERNAL_SERVER_ERROR = (
    "remote: Internal Server Error        \n"
    " ! [remote rejected] {sha} -> {branch} (Internal Server Error)\n"
    "error: failed to push some refs to 'https://github.com/o/r'\n"
)
COMMIT_REFS_FAILURE = (
    "remote: fatal error in commit_refs        \n"
    " ! [remote rejected] HEAD -> queue-data (failure)\n"
    "error: failed to push some refs to 'https://github.com/o/r'\n"
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, check=True, capture_output=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(github_write, "PUSH_RETRY_DELAYS", (0, 0, 0))
    checkout = tmp_path / "repo"
    checkout.mkdir()
    git(checkout, "init", "-q")
    git(checkout, "config", "user.name", "Push Test")
    git(checkout, "config", "user.email", "push-test@example.invalid")
    git(checkout, "config", "commit.gpgsign", "false")
    git(tmp_path, "init", "-q", "--bare", str(tmp_path / "remote.git"))
    git(checkout, "remote", "add", "origin", str(tmp_path / "remote.git"))
    return checkout


def commit(root: Path, message: str) -> str:
    return git(root, "commit-tree", git(root, "mktree"), "-m", message)


def remote(root: Path, ref: str) -> str | None:
    return github_write.remote_refs(root, "origin", [ref])[ref]


def inject_push_errors(
    monkeypatch: pytest.MonkeyPatch, stderr: list[str], *, landed: bool = False
) -> list[tuple[str, ...]]:
    """Answer the next pushes with canned stderr, optionally after applying them."""
    real_git = github_write._git
    pushes: list[tuple[str, ...]] = []

    def fake_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args[0] != "push":
            return real_git(root, *args)
        pushes.append(args)
        if not stderr:
            return real_git(root, *args)
        if landed:
            assert real_git(root, *args).returncode == 0
        return subprocess.CompletedProcess(args, 1, b"", stderr.pop(0).encode())

    monkeypatch.setattr(github_write, "_git", fake_git)
    return pushes


@pytest.mark.parametrize(
    ("detail", "transient"),
    [
        (INTERNAL_SERVER_ERROR.format(sha="a" * 40, branch="x"), True),
        (COMMIT_REFS_FAILURE, True),
        ("error: RPC failed; HTTP 502 curl 22 The requested URL returned error: 502\n"
         "fatal: the remote end hung up unexpectedly\nEverything up-to-date", True),
        ("fatal: unable to access 'https://h/r/': Could not resolve host: h", True),
        ("gh: Server Error (HTTP 502)", True),
        ("unexpected end of JSON input", True),
        (" ! [rejected]        abc -> x (stale info)", False),
        ("error: atomic push failed for ref refs/heads/y. status: 7\n"
         "fatal: the remote end hung up unexpectedly\n"
         " ! [rejected]        abc -> x (atomic push failed)\n"
         " ! [rejected]        abc -> y (stale info)", False),
        (f" ! [remote rejected] {'9a' * 20} -> dashboard-state (refusing to allow a GitHub "
         "App to create or update workflow `.github/workflows/x.yml` without `workflows` "
         f"permission)\n ! [remote rejected] {'74' * 20} -> dashboard-state-previous "
         "(atomic transaction failed)", False),
        ("fatal: unable to access 'https://h/r/': The requested URL returned error: 403", False),
        ("error: RPC failed; HTTP 403 curl 22 The requested URL returned error: 403\n"
         "send-pack: unexpected disconnect while reading sideband packet\n"
         "fatal: the remote end hung up unexpectedly", False),
        ("remote: error: GH013: Repository rule violations found for refs/heads/main.\n"
         "fatal: the remote end hung up unexpectedly", False),
        (" ! [remote rejected] a -> x (Internal Server Error)\n"
         " ! [remote rejected] b -> y (pre-receive hook declined)", False),
        (" ! [remote rejected] a -> x (failure)\n ! [rejected] b -> y (stale info)", False),
        ("gh: Validation Failed (HTTP 422)", False),
    ],
)
def test_only_server_and_transport_failures_are_transient(detail: str, transient: bool) -> None:
    assert github_write.is_transient(detail) is transient


def test_transient_rejection_is_retried_under_the_same_lease(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, new = commit(repo, "base"), commit(repo, "new")
    git(repo, "push", "-q", "origin", f"{base}:refs/heads/x")
    pushes = inject_push_errors(
        monkeypatch, [INTERNAL_SERVER_ERROR.format(sha=new, branch="x"), COMMIT_REFS_FAILURE]
    )
    github_write.push_refs(repo, "origin", {"refs/heads/x": new}, leases={"refs/heads/x": base})
    assert len(pushes) == 3
    assert remote(repo, "refs/heads/x") == new


def test_push_that_landed_despite_a_5xx_is_success_without_another_push(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = commit(repo, "new")
    pushes = inject_push_errors(
        monkeypatch, [INTERNAL_SERVER_ERROR.format(sha=new, branch="x")], landed=True
    )
    github_write.push_refs(repo, "origin", {"refs/heads/x": new}, leases={"refs/heads/x": None})
    assert len(pushes) == 1
    assert remote(repo, "refs/heads/x") == new


def test_exhausted_retry_budget_raises_the_original_error(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = commit(repo, "new")
    pushes = inject_push_errors(
        monkeypatch, [INTERNAL_SERVER_ERROR.format(sha=new, branch="x")] * 4
    )
    with pytest.raises(github_write.GitPushError, match="Internal Server Error"):
        github_write.push_refs(repo, "origin", {"refs/heads/x": new}, leases={"refs/heads/x": None})
    assert len(pushes) == 4
    assert remote(repo, "refs/heads/x") is None


@pytest.mark.parametrize(
    "push",
    [
        queue_request_budget._push_budget,
        dns_request_budget._push_budget,
        request_bearing_attempt_budget._push,
    ],
)
def test_budget_ledger_push_survives_a_commit_refs_failure(
    repo: Path, failing_git_pushes, push
) -> None:
    base, new = commit(repo, "base"), commit(repo, "new")
    git(repo, "push", "-q", "origin", f"{base}:refs/heads/ledger")
    pushes = failing_git_pushes(1)
    push(repo, "origin", new, base, SimpleNamespace(branch="ledger"))
    assert pushes() == 2
    assert remote(repo, "refs/heads/ledger") == new


def test_lease_conflict_fails_without_retry(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base, other, new = commit(repo, "base"), commit(repo, "other"), commit(repo, "new")
    git(repo, "push", "-q", "origin", f"{other}:refs/heads/x")
    pushes = inject_push_errors(monkeypatch, [])
    with pytest.raises(github_write.GitPushError, match="(?s)force-with-lease.*stale info"):
        github_write.push_refs(
            repo, "origin", {"refs/heads/x": new}, leases={"refs/heads/x": base}
        )
    assert len(pushes) == 1
    assert remote(repo, "refs/heads/x") == other


def test_atomic_transient_failure_with_mixed_remote_state_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, new = commit(repo, "base"), commit(repo, "new")
    git(repo, "push", "-q", "origin", f"{base}:refs/heads/x", f"{new}:refs/heads/y")
    pushes = inject_push_errors(
        monkeypatch, [INTERNAL_SERVER_ERROR.format(sha=new, branch="x")]
    )
    with pytest.raises(github_write.GitPushError, match="Internal Server Error"):
        github_write.push_refs(
            repo,
            "origin",
            {"refs/heads/x": new, "refs/heads/y": new},
            leases={"refs/heads/x": base, "refs/heads/y": base},
            atomic=True,
        )
    assert len(pushes) == 1
    assert remote(repo, "refs/heads/x") == base


def test_cli_plain_push_that_landed_reports_success(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo, "update-ref", "HEAD", commit(repo, "heartbeat"))
    pushes = inject_push_errors(monkeypatch, [COMMIT_REFS_FAILURE], landed=True)
    argv = ["push", "--root", str(repo), "--ref", "refs/heads/main"]
    assert github_write.main(argv) == 0
    assert len(pushes) == 1
    assert remote(repo, "refs/heads/main") == git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("stderr", "status", "calls"),
    [("gh: Server Error (HTTP 502)", 0, 2), ("gh: Validation Failed (HTTP 422)", 1, 1)],
)
def test_gh_api_replays_the_dispatch_payload_only_after_transient_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str, status: int, calls: int
) -> None:
    fake = tmp_path / "gh"
    fake.write_text(
        "#!/bin/sh\n"
        f"n=$(ls '{tmp_path}' | grep -c '^call-')\n"
        f"cat > '{tmp_path}/call-'$n\n"
        f"[ $n -eq 0 ] && {{ printf '%s' '{stderr}' >&2; exit 1; }}\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(github_write, "GH_API_RETRY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"ref":"main"}')))
    argv = ["gh-api", "--", "--method", "POST", "repos/o/r/dispatches", "--input", "-"]
    assert github_write.main(argv) == status
    payloads = [path.read_bytes() for path in sorted(tmp_path.glob("call-*"))]
    assert payloads == [b'{"ref":"main"}'] * calls
