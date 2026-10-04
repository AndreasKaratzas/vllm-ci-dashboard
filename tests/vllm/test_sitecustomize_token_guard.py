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
DIRECT_TOKEN_ENTRYPOINTS = (
    ("scripts/collect_ci.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/backfill_agent_nodes.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_agent_health.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_analytics.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_dns_failures.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_hotness.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_perf_eval_artifacts.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_queue_lifecycle.py", "BUILDKITE_API_TOKEN"),
    ("scripts/vllm/collect_queue_snapshot.py", "BUILDKITE_TOKEN"),
    ("scripts/vllm/collect_workload_mapping.py", "BUILDKITE_TOKEN"),
)


def clean_python_environment() -> dict[str, str]:
    removed = {*GUARD_ENV_NAMES, *TOKEN_ENV_NAMES, "PYTHONPATH"}
    return {name: value for name, value in os.environ.items() if name not in removed}


def write_network_sentinel(path: Path) -> None:
    """Provide a requests-shaped module whose transports can never use a socket."""
    path.mkdir()
    (path / "requests.py").write_text(
        """
class Response:
    pass

class RequestException(Exception):
    pass

class _Exceptions:
    Timeout = RequestException
    ConnectionError = RequestException
    ChunkedEncodingError = RequestException
    JSONDecodeError = RequestException
    RequestException = RequestException

exceptions = _Exceptions()

def get(*args, **kwargs):
    raise AssertionError("network transport sentinel reached")

def post(*args, **kwargs):
    raise AssertionError("network transport sentinel reached")

class Session:
    def __init__(self, *args, **kwargs):
        raise AssertionError("network transport sentinel reached")
""".lstrip(),
        encoding="utf-8",
    )


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


@pytest.mark.parametrize(("relative_path", "token_name"), DIRECT_TOKEN_ENTRYPOINTS)
def test_direct_token_entrypoints_exit_78_before_any_transport(
    tmp_path: Path,
    relative_path: str,
    token_name: str,
) -> None:
    sentinel = tmp_path / "network-sentinel"
    write_network_sentinel(sentinel)
    env = clean_python_environment()
    env.update(
        {
            "PYTHONPATH": str(sentinel),
            token_name: "present-but-never-used",
        }
    )

    completed = subprocess.run(
        [sys.executable, str(ROOT / relative_path)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 78, (relative_path, completed.stderr)
    assert "fatal Buildkite request guard activation error" in completed.stderr
    assert "request guard environment is incomplete" in completed.stderr
    assert "network transport sentinel reached" not in completed.stderr


def test_shared_client_import_exits_78_before_dormant_create_build_can_run(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "network-sentinel"
    write_network_sentinel(sentinel)
    env = clean_python_environment()
    env.update(
        {
            "PYTHONPATH": str(sentinel),
            "BUILDKITE_TOKEN": "present-but-never-used",
        }
    )
    code = f"""
import sys
sys.path.insert(0, {str(SCRIPTS)!r})
from vllm.ci import buildkite_client
buildkite_client.create_build('amd-ci', 'HEAD', 'main', 'must not run')
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 78
    assert "request guard environment is incomplete" in completed.stderr
    assert "network transport sentinel reached" not in completed.stderr
    assert "must not run" not in completed.stderr


def test_direct_guard_hook_accepts_a_complete_guard_without_transport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "guard.json"
    guard.initialize(path, attempt_id="direct-100-1", allowance=1)
    env = clean_python_environment()
    env.update(
        {
            "BUILDKITE_TOKEN": "present-but-never-used",
            "BUILDKITE_REQUEST_GUARD_FILE": str(path),
            "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID": "direct-100-1",
            "BUILDKITE_REQUEST_GUARD_ALLOWANCE": "1",
        }
    )
    code = f"""
import sys
sys.path.insert(0, {str(SCRIPTS)!r})
from vllm.buildkite_request_guard import install_from_environment_or_exit
install_from_environment_or_exit()
print('direct-guard-active')
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "direct-guard-active"
    assert guard.read_count(path, attempt_id="direct-100-1", allowance=1) == 0


def test_guard_cli_initialize_and_report_remain_tokenless(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    output = tmp_path / "github-output"
    env = clean_python_environment()
    command = str(SCRIPTS / "vllm/buildkite_request_guard.py")

    initialized = subprocess.run(
        [
            sys.executable,
            command,
            "initialize",
            "--file",
            str(path),
            "--attempt-id",
            "operator-100-1",
            "--allowance",
            "3",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    reported = subprocess.run(
        [
            sys.executable,
            command,
            "report",
            "--file",
            str(path),
            "--attempt-id",
            "operator-100-1",
            "--allowance",
            "3",
            "--github-output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert initialized.returncode == 0, initialized.stderr
    assert "request_start_allowance=3" in initialized.stdout
    assert reported.returncode == 0, reported.stderr
    assert output.read_text(encoding="utf-8") == "actual_request_starts=0\n"


def test_shared_buildkite_modules_remain_importable_without_a_token() -> None:
    env = clean_python_environment()
    code = f"""
import sys
sys.path.insert(0, {str(SCRIPTS)!r})
from vllm.ci import buildkite_client, config, log_parser
assert config.BK_TOKEN == ''
print(buildkite_client.__name__, log_parser.__name__)
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "vllm.ci.buildkite_client vllm.ci.log_parser"


def test_all_token_reading_vllm_sources_have_a_proven_guard_ingress() -> None:
    direct = {
        Path(relative).relative_to("scripts/vllm").as_posix()
        for relative, _ in DIRECT_TOKEN_ENTRYPOINTS
        if relative.startswith("scripts/vllm/")
    }
    shared = {
        "buildkite_request_guard.py",
        "ci/buildkite_client.py",
        "ci/config.py",
        "ci/log_parser.py",
    }
    discovered = set()
    for path in sorted((SCRIPTS / "vllm").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if any(
            marker in source
            for marker in ("BUILDKITE_TOKEN", "BUILDKITE_API_TOKEN", "cfg.BK_TOKEN")
        ):
            discovered.add(path.relative_to(SCRIPTS / "vllm").as_posix())

    assert discovered == direct | shared
    for relative in direct:
        source = (SCRIPTS / "vllm" / relative).read_text(encoding="utf-8")
        assert "install_from_environment_or_exit()" in source
    root_source = (ROOT / "scripts/collect_ci.py").read_text(encoding="utf-8")
    assert "install_from_environment_or_exit()" in root_source
    config_source = (SCRIPTS / "vllm/ci/config.py").read_text(encoding="utf-8")
    assert "install_from_environment_or_exit()" in config_source
    for relative in ("ci/buildkite_client.py", "ci/log_parser.py"):
        source = (SCRIPTS / "vllm" / relative).read_text(encoding="utf-8")
        assert "from . import config as cfg" in source
