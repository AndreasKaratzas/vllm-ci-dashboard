from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vllm import buildkite_request_guard as guard


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
GUARD_ENV_NAMES = (
    "BUILDKITE_REQUEST_GUARD_FILE",
    "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
    "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
)
TOKEN_ENV_NAMES = ("BUILDKITE_TOKEN", "BUILDKITE_API_TOKEN")


def clean_python_environment() -> dict[str, str]:
    removed = {*GUARD_ENV_NAMES, *TOKEN_ENV_NAMES, "PYTHONPATH"}
    return {name: value for name, value in os.environ.items() if name not in removed}


@pytest.mark.parametrize("token_name", TOKEN_ENV_NAMES)
def test_sitecustomize_rejects_each_buildkite_token_without_a_guard(
    token_name: str,
) -> None:
    env = clean_python_environment()
    env.update({"PYTHONPATH": str(SCRIPTS), token_name: "present-but-never-used"})

    completed = subprocess.run(
        [sys.executable, "-c", "raise SystemExit('script must not execute')"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 78
    assert "fatal Buildkite request guard activation error" in completed.stderr
    assert "request guard environment is incomplete" in completed.stderr
    assert "script must not execute" not in completed.stderr


@pytest.mark.parametrize("token_name", TOKEN_ENV_NAMES)
def test_sitecustomize_accepts_each_buildkite_token_only_with_a_complete_guard(
    tmp_path: Path,
    token_name: str,
) -> None:
    path = tmp_path / "guard.json"
    guard.initialize(path, attempt_id="workflow-100-1", allowance=2)
    env = clean_python_environment()
    env.update(
        {
            "PYTHONPATH": str(SCRIPTS),
            token_name: "present-but-never-used",
            "BUILDKITE_REQUEST_GUARD_FILE": str(path),
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "workflow-100-1",
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "2",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", "print('guard-active')"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "guard-active"
    assert guard.read_count(path, attempt_id="workflow-100-1", allowance=2) == 0
