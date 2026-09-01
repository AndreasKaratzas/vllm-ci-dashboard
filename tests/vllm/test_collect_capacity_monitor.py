"""Tests for the queue Capacity Monitor static data collector."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _snapshot_archive() -> bytes:
    stream = io.BytesIO()
    yaml_bytes = b"""group: Models
steps:
- label: Pinned mirror
  key: pinned-mirror
  mirror:
    amd:
      device: mi300_1
"""
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(
            "vllm-pinned/.buildkite/test_areas/models.yaml"
        )
        info.size = len(yaml_bytes)
        archive.addfile(info, io.BytesIO(yaml_bytes))
    return stream.getvalue()


def test_parse_amd_mirror_groups_counts_only_mirror_amd(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    groups = ccm.parse_amd_mirror_groups(repo)

    assert [group["key"] for group in groups] == ["base-mirrored", "override-mirrored"]
    assert groups[0]["queue"] == "amd_mi325_1"
    assert groups[0]["in_capacity_scope"] is True
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
    assert payload["schema_version"] == 2
    assert payload["summary"]["total_capacity"] == 934
    assert payload["summary"]["total_gpu_capacity"] == 1218
    assert payload["summary"]["total_eight_gpu_node_equivalents"] == 152.25
    assert payload["summary"]["future_eligible_capacity"] == 734
    assert payload["summary"]["future_eligible_gpu_capacity"] == 998
    assert payload["summary"]["future_eligible_eight_gpu_node_equivalents"] == 124.75
    projection = payload["projection"]
    assert projection["target_groups"] == 4
    assert projection["theoretical_groups"] == 4
    assert projection["scale"] == 2.0
    assert projection["projected_total_jobs"] == 10.0
    assert projection["projected_total_gpus"] == 10.0
    assert projection["future_capacity"]["concurrent_jobs"] == 734
    assert projection["queues_requiring_migration"] == ["amd_mi325_1"]
    assert projection["projected_gap_jobs"] == 4.0
    queues = {row["id"]: row for row in projection["queues"]}
    assert queues["amd_mi250_8"]["max_agents"] == 4
    assert queues["amd_mi250_8"]["max_concurrent_jobs"] == 4
    assert queues["amd_mi250_1"]["projected_jobs"] == 6.0
    assert queues["amd_mi325_1"]["projected_jobs"] == 4.0
    assert queues["amd_mi250_1"]["projected_capacity_ratio"] == round(6 / 78, 4)
    assert queues["amd_mi325_1"]["capacity_eligible"] is False
    assert queues["amd_mi325_1"]["future_max_concurrent_jobs"] == 0
    assert queues["amd_mi325_1"]["projected_future_capacity_ratio"] is None
    assert queues["amd_mi325_1"]["requires_migration"] is True


def test_capacity_payload_defaults_to_target_group_goal(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)

    payload = ccm.build_capacity_payload(repo)

    assert payload["assumptions"]["default_theoretical_groups"] == 160
    assert payload["assumptions"]["configured_target_groups"] == 160
    assert payload["projection"]["target_groups"] == 160
    assert payload["projection"]["theoretical_groups"] == 160
    assert payload["projection"]["declared_current_mirror_groups"] == 54
    assert payload["projection"]["declared_existing_groups"] == 147
    assert payload["projection"]["declared_new_groups"] == 10
    assert payload["projection"]["declared_total_groups"] == 157
    assert payload["projection"]["planning_headroom_groups"] == 3


def test_capacity_config_has_exact_standard_queue_scope_and_quotas() -> None:
    config = ccm.load_capacity_config()

    assert config["schema_version"] == 1
    assert config["projection"] == {
        "target_groups": 160,
        "declared_current_mirror_groups": 54,
        "declared_existing_groups": 147,
        "declared_new_groups": 10,
        "note": (
            "147 existing + 10 new = 157 declared groups; "
            "target 160 includes 3 groups of planning headroom."
        ),
    }
    assert config["workload_pipelines"] == {
        "omni": ["vllm-omni-amd-ci"],
        "main": ["ci", "amd-ci", "amd-distributed-inference-ci"],
    }
    assert config["scope"]["excluded_queue_classes"] == ["perf_eval"]
    assert config["scope"]["non_gating_queues"] == [
        {
            "id": "amd-cpu",
            "label": "amd_cpu",
            "family": "MI250-CPU",
            "purpose": "docker_builds_only",
            "max_concurrent_jobs": 15,
            "node_equivalents": 1.875,
        },
        {
            "id": "amd_mi300_perf_eval",
            "family": "MI300",
            "purpose": "perf_eval",
            "max_concurrent_jobs": 1,
            "node_equivalents": 1.0,
        },
        {
            "id": "amd_mi355_perf_eval",
            "family": "MI355",
            "provider": "TW",
            "purpose": "perf_eval",
            "max_concurrent_jobs": 6,
            "node_equivalents": 6.0,
        },
    ]

    queues = {row["id"]: row for row in config["queues"]}
    assert len(queues) == 16
    assert not any("perf_eval" in queue_id for queue_id in queues)
    assert "amd-cpu" not in queues
    assert [queues[f"amd_mi250_{size}"]["max_concurrent_jobs"] for size in (1, 2, 4, 8)] == [
        78,
        24,
        16,
        4,
    ]
    assert [queues[f"amd_mi300_{size}"]["max_concurrent_jobs"] for size in (1, 2, 4, 8)] == [
        296,
        18,
        19,
        2,
    ]
    assert [queues[f"amd_mi325_{size}"]["max_concurrent_jobs"] for size in (1, 2, 4, 8)] == [
        188,
        8,
        4,
        0,
    ]
    assert [queues[f"amd_mi355_{size}"]["max_concurrent_jobs"] for size in (1, 2, 4, 8)] == [
        240,
        20,
        16,
        1,
    ]
    assert [queues[f"amd_mi355_{size}"]["provider"] for size in (1, 2, 4, 8)] == [
        "Crusoe",
        "Crusoe",
        "TW",
        "Crusoe",
    ]
    assert all(row["monitored"] for row in queues.values())
    assert all(
        row["capacity_eligible"] and row["lifecycle"] == "active"
        for row in queues.values()
        if row["family"] in {"MI250", "MI300", "MI355"}
    )
    assert all(
        not row["capacity_eligible"] and row["lifecycle"] == "retiring"
        for row in queues.values()
        if row["family"] == "MI325"
    )


def test_family_capacity_rollups_weight_each_queue_by_gpus_per_job(tmp_path: Path) -> None:
    payload = ccm.build_capacity_payload(_fake_repo(tmp_path), theoretical_groups=4)
    families = {row["family"]: row for row in payload["families"]}

    assert families["MI250"]["max_concurrent_jobs"] == 122
    assert families["MI250"]["gpu_capacity"] == 222
    assert families["MI250"]["eight_gpu_node_equivalents"] == 27.75
    assert families["MI300"]["max_concurrent_jobs"] == 335
    assert families["MI300"]["gpu_capacity"] == 424
    assert families["MI300"]["eight_gpu_node_equivalents"] == 53.0
    assert families["MI325"]["max_concurrent_jobs"] == 200
    assert families["MI325"]["gpu_capacity"] == 220
    assert families["MI325"]["eight_gpu_node_equivalents"] == 27.5
    assert families["MI325"]["future_gpu_capacity"] == 0
    assert families["MI355"]["max_concurrent_jobs"] == 277
    assert families["MI355"]["gpu_capacity"] == 352
    assert families["MI355"]["eight_gpu_node_equivalents"] == 44.0


def test_160_group_sensitivity_flags_mi300_8_bottleneck() -> None:
    groups = []
    queue_layout = [
        ("amd_mi250_1", [1] * 6),
        ("amd_mi300_1", [2] * 12 + [1] * 25),
        ("amd_mi300_2", [1] * 5),
        ("amd_mi300_4", [1] * 4),
        ("amd_mi300_8", [1]),
    ]
    for queue, parallelisms in queue_layout:
        for parallelism in parallelisms:
            groups.append(
                {
                    "queue": queue,
                    "parallelism": parallelism,
                    "in_capacity_scope": True,
                    "dependency_file_count": 0,
                    "dependency_lines": 0,
                    "_dependency_files": [],
                }
            )

    assert len(groups) == 53
    rollups = ccm._queue_rollups(groups)
    projection = ccm._projection(ccm._summary(groups, rollups), rollups, 160)
    queues = {row["id"]: row for row in projection["queues"]}

    assert projection["scale"] == round(160 / 53, 4)
    assert projection["projected_total_jobs"] == 196.2
    assert projection["bottleneck_queue"] == "amd_mi300_8"
    assert projection["queues_over_capacity"] == ["amd_mi300_8"]
    assert queues["amd_mi300_8"]["projected_jobs"] == 3.0
    assert queues["amd_mi300_8"]["projected_future_capacity_ratio"] == round(
        (160 / 53) / 2,
        4,
    )
    assert queues["amd_mi300_8"]["projected_gap_jobs"] == 1.0


def test_workflow_config_sha_is_resolved_once_and_archive_is_commit_pinned(
    monkeypatch,
) -> None:
    requested_sha = "a" * 40
    archive = _snapshot_archive()
    calls: list[str] = []

    class Response:
        def __init__(self, payload=None, content=b"") -> None:
            self._payload = payload
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        calls.append(url)
        assert url == ccm._archive_url(ccm.GITHUB_REPO, requested_sha)
        return Response(content=archive)

    monkeypatch.setenv("VLLM_CONFIG_SHA", requested_sha)
    monkeypatch.setattr(ccm.requests, "get", fake_get)

    with ccm.repo_root_context(
        None,
        github_repo=ccm.GITHUB_REPO,
        ref=ccm.GITHUB_REF,
    ) as (repo_root, source_kind, commit_sha):
        payload = ccm.build_capacity_payload(
            repo_root,
            source_kind=source_kind,
            github_repo=ccm.GITHUB_REPO,
            ref=ccm.GITHUB_REF,
            requested_ref=ccm._requested_config_ref(),
            commit_sha=commit_sha,
        )

    assert calls == [
        ccm._archive_url(ccm.GITHUB_REPO, requested_sha),
    ]
    assert payload["source"]["branch"] == "main"
    assert payload["source"]["ref"] == "main"
    assert payload["source"]["requested_ref"] == requested_sha
    assert payload["source"]["commit_sha"] == requested_sha
    assert payload["source"]["commit_url"].endswith(f"/commit/{requested_sha}")
    assert [row["label"] for row in payload["groups"]] == ["Pinned mirror"]


def test_capacity_commit_resolution_requires_full_sha(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"sha": "deadbeef"}

    monkeypatch.setattr(ccm.requests, "get", lambda *args, **kwargs: Response())

    try:
        ccm._resolve_commit_sha(ccm.GITHUB_REPO, "main")
    except ValueError as exc:
        assert "full 40-hex SHA" in str(exc)
    else:
        raise AssertionError("short resolved SHA should be rejected")


def test_capacity_branch_ref_resolves_to_full_sha(monkeypatch) -> None:
    resolved_sha = "b" * 40
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"sha": resolved_sha}

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(ccm.requests, "get", fake_get)

    assert ccm._resolve_commit_sha(ccm.GITHUB_REPO, "main") == resolved_sha
    assert calls == [
        f"https://api.github.com/repos/{ccm.GITHUB_REPO}/commits/main"
    ]


def test_capacity_writer_preserves_lkg_on_byte_overflow(tmp_path, monkeypatch) -> None:
    repo = _fake_repo(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    published = output / "capacity_monitor.json"
    published.write_text("existing-capacity")
    monkeypatch.setattr(
        ccm,
        "parse_args",
        lambda: SimpleNamespace(
            output=str(output),
            repo_root=str(repo),
            github_repo=ccm.GITHUB_REPO,
            ref=ccm.GITHUB_REF,
            theoretical_groups=None,
        ),
    )
    monkeypatch.setattr(ccm, "CAPACITY_MONITOR_MAX_BYTES", 1)

    with pytest.raises(RuntimeError, match="capacity monitor fixed aggregates exceed"):
        ccm.main()

    assert published.read_text() == "existing-capacity"
    assert list(output.glob(".capacity_monitor.json.*.tmp")) == []


def test_capacity_payload_compacts_detail_then_group_rows_with_exact_summary(
    tmp_path,
) -> None:
    payload = ccm.build_capacity_payload(_fake_repo(tmp_path))
    template = payload["groups"][0]
    payload["groups"] = [
        {
            **template,
            "key": f"group-{index:04d}",
            "yaml_index": index,
            "source_file_dependencies": ["x" * 2_000],
        }
        for index in range(500)
    ]
    expected_summary = payload["summary"]

    bounded = ccm.bounded_capacity_payload(payload, max_bytes=50_000)

    assert len(ccm.pretty_json_bytes(bounded)) <= 50_000
    assert bounded["summary"] == expected_summary
    retention = bounded["publication_retention"]
    assert retention["aggregate_summaries_complete"] is True
    assert retention["complete_relative_to_source"] is False
    assert retention["group_index"]["omitted"] > 0


def test_local_vllm_checkout_has_capacity_scoped_amd_mirrors() -> None:
    repo = Path("/app/vllm")
    if not (repo / ".buildkite" / "test_areas").exists():
        return

    groups = ccm.parse_amd_mirror_groups(repo)

    assert len(groups) >= 24
    assert all(group["in_capacity_scope"] for group in groups)
