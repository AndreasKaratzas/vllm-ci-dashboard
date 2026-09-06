"""Regression coverage for the pre-push publication test-gap guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vllm.publication_retest_required import publication_retest_decision


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def _publication_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "publication-test@example.com")
    _git(repo, "config", "user.name", "Publication Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _write(repo / "app.py", "VERSION = 1\n")
    _write(repo / "data/generated.json", '{"generation": 1}\n')
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial code and data")
    baseline = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "publication")
    _write(repo / "data/generated.json", '{"generation": 2}\n')
    _git(repo, "add", "data/generated.json")
    _git(repo, "commit", "-m", "auto: update data")
    tested_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    return repo, baseline, tested_tree


def test_unchanged_publication_parent_and_tree_do_not_repeat_tests(
    tmp_path: Path,
) -> None:
    repo, baseline, tested_tree = _publication_repo(tmp_path)

    decision = publication_retest_decision(
        repo,
        baseline_parent=baseline,
        tested_tree=tested_tree,
    )

    assert decision.required is False
    assert decision.reasons == ()
    assert decision.current_parent == baseline
    assert decision.candidate_tree == tested_tree


def test_staged_tree_change_requires_exact_candidate_retest(tmp_path: Path) -> None:
    repo, baseline, tested_tree = _publication_repo(tmp_path)
    _write(repo / "data/generated.json", '{"generation": 3}\n')
    _git(repo, "add", "data/generated.json")

    decision = publication_retest_decision(
        repo,
        baseline_parent=baseline,
        tested_tree=tested_tree,
    )

    assert decision.required is True
    assert decision.reasons == ("publication-tree-changed",)
    assert decision.current_parent == baseline
    assert decision.candidate_tree != tested_tree


def test_human_code_commit_entering_rebase_gap_requires_retest(
    tmp_path: Path,
) -> None:
    repo, baseline, tested_tree = _publication_repo(tmp_path)
    _git(repo, "checkout", "main")
    _write(repo / "app.py", "VERSION = 2\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "human: change application code")
    human_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "publication")
    _git(repo, "rebase", "main")

    decision = publication_retest_decision(
        repo,
        baseline_parent=baseline,
        tested_tree=tested_tree,
    )

    assert decision.required is True
    assert decision.reasons == (
        "publication-parent-changed",
        "publication-tree-changed",
    )
    assert decision.current_parent == human_commit
    assert decision.candidate_tree != tested_tree
