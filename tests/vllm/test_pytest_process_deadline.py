"""Execute the real workflow shells to verify process deadlines and propagation."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from vllm import buildkite_request_guard as guard

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _steps(workflow):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text())
    job = data["jobs"]["test" if workflow == "ci.yml" else "collect-and-deploy"]
    return job, {step.get("name"): step for step in job["steps"]}


def _running(pid):
    # A killed grandchild can briefly remain a zombie awaiting init's reap.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("workflow", ["ci.yml", "hourly-master.yml"])
@pytest.mark.parametrize("case,expected", [("success", 0), ("failure", 17), ("sleep", 124), ("ignore-term", 137)])
def test_workflow_deadline_propagates_status_and_stops_descendants(tmp_path, workflow, case, expected):
    job, steps = _steps(workflow)
    step = steps["Run tests" if workflow == "ci.yml" else "Run test suite"]
    assert job["timeout-minutes"] == (15 if workflow == "ci.yml" else 75)
    assert step["timeout-minutes"] == 7
    assert "--signal=TERM --kill-after=15s 6m" in step["run"]
    assert "faulthandler_timeout=60" in step["run"]
    # Only duration literals change. The production env isolation, pipeline,
    # PIPESTATUS handling and bounded diagnostic encoding execute unchanged.
    script = step["run"].replace("--kill-after=15s 6m", "--kill-after=0.5s 1s")
    executable = tmp_path / "pytest"
    executable.write_text('''#!/usr/bin/env python
import os,signal,subprocess,sys,time
from pathlib import Path
case=os.environ['PYTEST_PROCESS_CASE']
if os.environ.get('VERIFY_ISOLATION') == '1':
    assert all(key not in os.environ for key in ('BUILDKITE_TOKEN','BUILDKITE_API_TOKEN','BUILDKITE_REQUEST_GUARD_FILE','BUILDKITE_REQUEST_GUARD_ATTEMPT_ID','BUILDKITE_REQUEST_GUARD_ALLOWANCE','GITHUB_REQUEST_STATE_FILE'))
print('mock pytest started',flush=True)
if case in {'success','failure'}:
    print('4 passed' if case == 'success' else 'FAILED mock test',flush=True)
    sys.exit(0 if case == 'success' else 17)
child_code='import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)' if case == 'ignore-term' else 'import time; time.sleep(60)'
child=subprocess.Popen([sys.executable,'-c',child_code])
Path(os.environ['DESCENDANT_PID']).write_text(str(child.pid))
if case == 'ignore-term':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
else:
    def stop(signal_number,frame):
        child.terminate()
        child.wait(timeout=2)
        raise SystemExit(143)
    signal.signal(signal.SIGTERM,stop)
time.sleep(60)
''')
    executable.chmod(0o755)
    output = tmp_path / "github-output"
    pid_path = tmp_path / "child.pid"
    env = {"PATH": os.pathsep.join((str(tmp_path), str(Path(sys.executable).parent), os.environ["PATH"])),
           "PYTHONPATH": str(REPO_ROOT / "scripts"), "LANG": "C.UTF-8",
           "PYTEST_PROCESS_CASE": case, "GITHUB_OUTPUT": str(output), "DESCENDANT_PID": str(pid_path)}
    guard_path = tmp_path / "outer-buildkite-request-guard.json"
    guard_attempt = "pytest-deadline-fixture"
    if workflow == "hourly-master.yml":
        # The publisher's encoder retains its outer guard. Exercise real Python
        # startup with a valid guard that cannot admit a Buildkite request.
        guard.initialize(guard_path, attempt_id=guard_attempt, allowance=0)
        env.update({
            "BUILDKITE_TOKEN": "synthetic-credential-value",
            "BUILDKITE_API_TOKEN": "synthetic-credential-value",
            "BUILDKITE_REQUEST_GUARD_FILE": str(guard_path),
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": guard_attempt,
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "0",
            "GITHUB_REQUEST_STATE_FILE": "synthetic-guard-value",
        })
        env["VERIFY_ISOLATION"] = "1"
    started = time.monotonic()
    try:
        result = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, env=env,
                                capture_output=True, timeout=5)
        assert time.monotonic() - started < 3
        assert b"mock pytest started" in (tmp_path / "test-output.txt").read_bytes()
        if workflow == "ci.yml":
            assert result.returncode == expected
        else:
            assert result.returncode == 0  # Existing workflow defers failure to its validation gate.
            assert guard.read_count(guard_path, attempt_id=guard_attempt, allowance=0) == 0
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            assert int(values["exit_code"]) == expected
            enforce = steps["Enforce publication validation results"]["run"]
            enforce = enforce.replace("${{ steps.live-data-audit.outcome }}", "success")
            enforce = enforce.replace("${{ steps.live-data-audit.outputs.exit_code }}", "0")
            enforce = enforce.replace("${{ steps.run-tests.outputs.exit_code }}", str(expected))
            gate_env = dict(env, HOURLY_DNS_GENERATION_INPUT="test-generation", HOURLY_QUEUE_GENERATION_INPUT="", HOURLY_SKIP_TESTS="false")
            gated = subprocess.run(["bash", "-e", "-c", enforce], cwd=tmp_path, env=gate_env,
                                   capture_output=True, timeout=5)
            assert gated.returncode == (0 if expected == 0 else 1)
        if pid_path.exists():
            pid = int(pid_path.read_text())
            deadline = time.monotonic() + 1
            while _running(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not _running(pid), "timeout left a running child holding the tee pipe"
    finally:
        if pid_path.exists() and _running(pid := int(pid_path.read_text())):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("source", [None, b"last test before deadline\n", b"x" * 1048576 + b"last test before deadline\n"],
                         ids=["missing", "complete", "oversized"])
def test_publisher_diagnostics_always_keep_only_bounded_test_tail(tmp_path, source):
    _, steps = _steps("hourly-master.yml")
    stage = steps["Stage bounded deterministic test diagnostics"]
    upload = steps["Upload bounded deterministic test diagnostics"]
    assert stage["if"] == upload["if"]
    assert "always() && steps.run-tests.outcome != 'skipped'" in stage["if"]
    assert "inputs.dns_generation != ''" in stage["if"]
    assert "inputs.queue_generation != ''" in stage["if"]
    assert "steps.request-attempt.outputs.request_mode == 'reserved'" in stage["if"]
    assert stage["timeout-minutes"] == 1
    assert upload["timeout-minutes"] == 2
    assert upload["continue-on-error"] is True
    assert upload["with"]["path"] == "test-diagnostics/test-output.txt"
    if source is not None:
        (tmp_path / "test-output.txt").write_bytes(source)
    result = subprocess.run(["bash", "-e", "-c", stage["run"]], cwd=tmp_path,
                            capture_output=True, timeout=5, check=True)
    assert not result.stdout and not result.stderr
    tail = (tmp_path / upload["with"]["path"]).read_bytes()
    assert len(tail) <= 1048576
    assert tail == (source[-1048576:] if source is not None else b"Pytest did not produce test-output.txt.\n")
