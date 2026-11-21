"""Collection rejects oversized verbose names before executing or printing them."""

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tests/conftest.py"


def run_fixture(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_bytes(GUARD.read_bytes())
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_fixture.py").write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "test_fixture.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_oversized_auto_ids_fail_before_execution_or_payload_output(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, """
import pytest
from pathlib import Path
@pytest.mark.parametrize('value', ['private-payload-' + 'x' * 5000, 'private-payload-' + 'y' * 5000])
def test_payload(value):
    Path('executed').write_text('unexpected')
""")
    output = result.stdout + result.stderr
    assert result.returncode == 4
    assert "Oversized test IDs: 2 exceed 4096 characters" in output
    assert "Assign short explicit pytest parameter ids" in output
    assert "private-payload-" not in output
    assert max(map(len, output.splitlines())) < 512
    assert not (tmp_path / "executed").exists()


def test_short_explicit_id_keeps_large_payload_and_normal_execution(tmp_path: Path) -> None:
    result = run_fixture(tmp_path, """
import pytest
from pathlib import Path
@pytest.mark.parametrize('value', ['private-payload-' + 'x' * 5000], ids=['oversize-input'])
def test_payload(value):
    assert len(value) > 5000
    Path('executed').write_text('passed')
""")
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "test_payload[oversize-input] PASSED" in output
    assert "1 passed" in output
    assert "private-payload-" not in output
    assert (tmp_path / "executed").read_text() == "passed"


@pytest.mark.parametrize("node_id_length,expected", [(4096, 0), (4097, 4)], ids=["at-limit", "above-limit"])
def test_collection_guard_opens_exactly_at_its_documented_character_limit(
    tmp_path: Path, node_id_length: int, expected: int,
) -> None:
    id_length = node_id_length - len("test_fixture.py::test_payload[]")
    result = run_fixture(tmp_path, f"""
import pytest
from pathlib import Path
@pytest.mark.parametrize('value', [None], ids=['x' * {id_length}])
def test_payload(value):
    Path('executed').write_text('passed')
""")
    assert result.returncode == expected
    assert (tmp_path / "executed").exists() is (expected == 0)
    if expected:
        output = result.stdout + result.stderr
        assert "Oversized test IDs: 1 exceed 4096 characters" in output
        assert max(map(len, output.splitlines())) < 512
