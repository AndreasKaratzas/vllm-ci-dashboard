"""Tests for the queue Capacity Monitor static data collector."""

from __future__ import annotations

from pathlib import Path

from vllm import collect_capacity_monitor as ccm


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "vllm"
    _write(repo / "vllm" / "core.py", "a\nb\nc\n")
    _write(repo / "vllm" / "rocm.py", "r1\nr2\n")
    _write(repo / "tests" / "models" / "test_a.py", "t1\nt2\n")
    _write(repo / "tests" / "models" / "test_b.py", "u1\n")
    _write(repo / "tests" / "unused.py", "unused\n")
    _write(
        repo / ".buildkite" / "test_areas" / "models.yaml",
        """
group: Models
steps:
- label: Base mirrored
  key: base-mirrored
  device: h200_18gb
  parallelism: 2
  source_file_dependencies:
  - vllm/core.py
  - tests/models/test_a.py
  mirror:
    amd:
      device: mi325_1
- label: Override mirrored
  key: override-mirrored
  device: h200_35gb
  source_file_dependencies:
  - tests/unused.py
  mirror:
    amd:
      device: mi250_1
      parallelism: 3
      source_file_dependencies:
      - vllm/rocm.py
      - tests/models/
- label: Torch nightly only
  key: torch-nightly-only
  source_file_dependencies:
  - vllm/core.py
  mirror:
    torch_nightly: {}
- label: Not mirrored
  key: not-mirrored
  source_file_dependencies:
  - vllm/core.py
""",
    )
    return repo


def test_parse_amd_mirror_groups_counts_only_mirror_amd(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    groups = ccm.parse_amd_mirror_groups(repo)

    assert [group["key"] for group in groups] == ["base-mirrored", "override-mirrored"]
    assert groups[0]["queue"] == "amd_mi325_1"
    assert groups[0]["dependency_file_count"] == 2
    assert groups[0]["dependency_lines"] == 5
    assert groups[0]["parallelism"] == 2
    assert groups[1]["queue"] == "amd_mi250_1"
    assert groups[1]["source_file_dependencies"] == ["vllm/rocm.py", "tests/models/"]
    assert groups[1]["dependency_file_count"] == 3
    assert groups[1]["dependency_lines"] == 5
    assert groups[1]["parallelism"] == 3


def test_capacity_payload_projects_theoretical_group_count(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    payload = ccm.build_capacity_payload(repo, theoretical_groups=4)

    assert payload["summary"]["gated_group_count"] == 2
    assert payload["summary"]["capacity_scoped_group_count"] == 2
    assert payload["summary"]["gated_job_count"] == 5
    assert payload["summary"]["total_capacity"] == 711
    projection = payload["projection"]
    assert projection["theoretical_groups"] == 4
    assert projection["scale"] == 2.0
    assert projection["projected_total_jobs"] == 10.0
    queues = {row["id"]: row for row in projection["queues"]}
    assert queues["amd_mi250_1"]["projected_jobs"] == 6.0
    assert queues["amd_mi325_1"]["projected_jobs"] == 4.0
    assert queues["amd_mi250_1"]["projected_capacity_ratio"] == round(6 / 78, 4)


def test_capacity_payload_defaults_to_target_group_goal(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    payload = ccm.build_capacity_payload(repo)

    assert payload["assumptions"]["default_theoretical_groups"] == 125
    assert payload["projection"]["theoretical_groups"] == 125


def test_local_vllm_checkout_has_capacity_scoped_amd_mirrors() -> None:
    repo = Path("/app/vllm")
    if not (repo / ".buildkite" / "test_areas").exists():
        return

    groups = ccm.parse_amd_mirror_groups(repo)

    assert len(groups) >= 24
    assert all(group["in_capacity_scope"] for group in groups)
