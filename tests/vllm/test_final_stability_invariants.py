"""Cross-workflow invariants that keep long-lived automation deterministic."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_every_python_job_pins_the_exact_project_interpreter() -> None:
    """A clean runner must never supply an implicit, moving Python runtime."""

    command = re.compile(r"(?<![A-Za-z0-9_])(?:python(?:3)?|pytest|pip)(?![A-Za-z0-9_])")
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            run_text = "\n".join(str(step.get("run", "")) for step in steps)
            if command.search(run_text) is None:
                continue
            setup_steps = [
                step
                for step in steps
                if step.get("uses")
                == f"actions/setup-python@{SETUP_PYTHON_SHA}"
            ]
            assert len(setup_steps) == 1, (
                f"{path.name}:{job_name} must have exactly one immutable "
                "actions/setup-python step"
            )
            assert setup_steps[0].get("with", {}).get("python-version") == "3.12.13", (
                f"{path.name}:{job_name} must pin Python 3.12.13"
            )


def test_hourly_pytest_output_is_bounded_and_opaque() -> None:
    workflow = _workflow("hourly-master.yml")
    steps = workflow["jobs"]["collect-and-deploy"]["steps"]
    run_tests = next(step for step in steps if step.get("id") == "run-tests")
    script = run_tests["run"]

    assert "base64.b64encode" in script
    assert "[-4_096:]" in script
    assert "[-16_000:]" in script
    assert "[-60_000:]" in script
    assert "TESTEOF" not in script
    assert "failures<<" not in script
    assert "output<<" not in script

    issue_step = next(
        step for step in steps if step.get("name") == "Create hourly validation incident"
    )
    assert issue_step["env"]["HOURLY_TEST_OUTPUT"] == (
        "${{ steps.run-tests.outputs.output }}"
    )
    issue_script = issue_step["with"]["script"]
    assert "Buffer.from(value, 'base64').toString('utf8')" in issue_script
    assert "${{ steps.run-tests.outputs" not in issue_script
