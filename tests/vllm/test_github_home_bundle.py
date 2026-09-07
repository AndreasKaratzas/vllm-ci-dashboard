from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from vllm.bounded_json import pretty_json_bytes
from vllm.audit_dashboard_data import DashboardAudit
from vllm.github_home_bundle import (
    HOME_BUNDLE_MAX_BYTES,
    HOME_COMPONENT_MAX_BYTES,
    bounded_collection_payloads,
    bounded_projects_payload,
    publish_collection,
)


def _coverage(query_count: int = 1, *, detail_size: int = 0) -> dict:
    return {
        "complete": True,
        "authoritative_complete": True,
        "population_semantics": "complete",
        "truncated": False,
        "queries": [
            {
                "name": f"query:{index}",
                "scope": "s" * detail_size,
                "complete": True,
                "authoritative": True,
            }
            for index in range(query_count)
        ],
    }


def _payloads(*, rows: int = 3, padding: int = 0) -> dict[str, dict]:
    coverage = _coverage(2)
    issues = []
    prs = []
    items = {}
    for index in range(rows):
        number = 10_000 - index
        issue_number = 20_000 - index
        prs.append(
            {
                "number": number,
                "title": f"PR {number} " + "p" * padding,
                "author": "author",
                "state": "open",
                "updated_at": f"2026-08-{31 - index:02d}T00:00:00Z",
                "html_url": f"https://github.com/vllm-project/vllm/pull/{number}",
                "labels": ["rocm"],
                "is_rocm_pr": True,
                "is_ci_pr": True,
                "ci_issue_numbers": [issue_number],
                "custom_tags": ["CI", "ROCm"],
                "body_head": "b" * 2_000,
            }
        )
        issues.append(
            {
                "number": issue_number,
                "title": f"Issue {issue_number} " + "i" * padding,
                "author": "author",
                "state": "open",
                "updated_at": f"2026-08-{31 - index:02d}T00:00:00Z",
                "html_url": (
                    f"https://github.com/vllm-project/vllm/issues/{issue_number}"
                ),
                "project_url": "https://github.com/orgs/vllm-project/projects/39",
                "repo": "vllm-project/vllm",
                "linked_prs": [
                    {
                        "repo": "vllm-project/vllm",
                        "number": number,
                        "url": (
                            f"https://github.com/vllm-project/vllm/pull/{number}"
                        ),
                    }
                ],
                "body_head": "b" * 8_000,
            }
        )
        items[str(issue_number)] = {
            "issue_number": issue_number,
            "issue_state": "OPEN",
            "repo": "vllm-project/vllm",
            "status": "In Progress",
            "title": "t" * (padding + 200),
            "url": f"https://github.com/vllm-project/vllm/issues/{issue_number}",
        }
    return {
        "prs": {
            "collected_at": "2026-09-01T00:00:00Z",
            "prs": prs,
            "count_semantics": "complete",
            "source_coverage": deepcopy(coverage),
        },
        "issues": {
            "collected_at": "2026-09-01T00:00:00Z",
            "issues": issues,
            "count_semantics": "complete",
            "source_coverage": deepcopy(coverage),
        },
        "project_items": {
            "generated_at": "2026-09-01T00:00:00Z",
            "items_by_number": items,
            "project": "vllm-project/projects/39",
            "project_url": "https://github.com/orgs/vllm-project/projects/39",
            "count_semantics": "complete",
            "source_coverage": deepcopy(coverage),
        },
        "releases": {
            "collected_at": "2026-09-01T00:00:00Z",
            "releases": [
                {
                    "tag_name": "v1.0.0",
                    "name": "Release 1",
                    "published_at": "2026-09-01T00:00:00Z",
                    "html_url": "https://github.com/vllm-project/vllm/releases/v1.0.0",
                }
            ],
        },
    }


def test_component_allocations_compose_exactly_to_home_bundle() -> None:
    assert sum(HOME_COMPONENT_MAX_BYTES.values()) == HOME_BUNDLE_MAX_BYTES
    assert HOME_BUNDLE_MAX_BYTES == 768 * 1024


def test_small_complete_collection_preserves_all_detail_and_exact_counts() -> None:
    source = _payloads()
    bounded = bounded_collection_payloads(source)

    for name, key in (
        ("prs", "prs"),
        ("issues", "issues"),
        ("project_items", "items_by_number"),
        ("releases", "releases"),
    ):
        payload = bounded[name]
        retention = payload["publication_retention"]
        assert retention["rows"] == {
            "source": len(source[name][key]),
            "published": len(source[name][key]),
            "omitted": 0,
            "complete_relative_to_source": True,
        }
        assert retention["complete_relative_to_source"] is True
        assert payload["total_count"] == len(source[name][key])
        assert payload["count_semantics"] == "complete"
        assert len(pretty_json_bytes(payload)) <= HOME_COMPONENT_MAX_BYTES[name]


def test_growth_compacts_deterministically_and_reconciles_relationships() -> None:
    source = _payloads(rows=180, padding=4_000)
    source["prs"]["source_coverage"] = _coverage(180, detail_size=2_000)
    source["issues"]["source_coverage"] = _coverage(180, detail_size=2_000)
    source["project_items"]["source_coverage"] = _coverage(
        180, detail_size=2_000
    )

    first = bounded_collection_payloads(source)
    second = bounded_collection_payloads(deepcopy(source))

    assert first == second
    assert sum(len(pretty_json_bytes(payload)) for payload in first.values()) <= sum(
        HOME_COMPONENT_MAX_BYTES[name] for name in first
    )
    assert any(
        payload["publication_retention"]["complete_relative_to_source"] is False
        for payload in first.values()
    )
    for name, payload in first.items():
        retention = payload["publication_retention"]
        rows = retention["rows"]
        assert rows["source"] == rows["published"] + rows["omitted"]
        assert retention["aggregate_counts_complete"] is True
        assert payload["total_count"] == rows["source"]
        assert len(pretty_json_bytes(payload)) <= HOME_COMPONENT_MAX_BYTES[name]
        if retention["complete_relative_to_source"] is False:
            assert payload["count_semantics"] == "lower_bound"

    assert first["prs"]["publication_retention"]["source_aggregates"] == {
        "total": 180,
        "open": 180,
        "ci": 180,
        "rocm": 180,
    }
    assert first["issues"]["publication_retention"]["source_aggregates"] == {
        "total": 180,
        "open": 180,
        "linked_pr_refs": 180,
    }

    retained_prs = {row["number"] for row in first["prs"]["prs"]}
    retained_issues = {row["number"] for row in first["issues"]["issues"]}
    assert all(
        ref["number"] in retained_prs
        for issue in first["issues"]["issues"]
        for ref in issue.get("linked_prs") or []
        if (ref.get("repo") or "vllm-project/vllm") == "vllm-project/vllm"
    )
    assert all(
        issue_number in retained_issues
        for pr in first["prs"]["prs"]
        for issue_number in pr.get("ci_issue_numbers") or []
    )


def test_projects_growth_is_bounded_with_vllm_first_and_exact_accounting() -> None:
    source = {
        "projects": {
            "vllm": {"repo": "vllm-project/vllm", "role": "upstream_watch"},
            **{
                f"project-{index}": {
                    "repo": f"example/{index}-" + "x" * 2_000,
                    "role": "active_dev",
                    "build_workflows": ["y" * 4_000],
                }
                for index in range(100)
            },
        }
    }

    bounded = bounded_projects_payload(source)
    rows = bounded["publication_retention"]["rows"]

    assert len(pretty_json_bytes(bounded)) <= HOME_COMPONENT_MAX_BYTES["projects"]
    assert "vllm" in bounded["projects"]
    assert rows["source"] == 101
    assert rows["source"] == rows["published"] + rows["omitted"]
    assert rows["omitted"] > 0
    assert bounded["count_semantics"] == "lower_bound"


def test_irreducible_overflow_preserves_every_last_known_good_file(
    tmp_path: Path,
) -> None:
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("prs", "issues", "project_items", "releases")
    }
    for name, path in paths.items():
        path.write_bytes(f"prior-{name}\n".encode())
    before = {name: path.read_bytes() for name, path in paths.items()}
    payloads = _payloads()
    payloads["releases"]["irreducible"] = "x" * (
        HOME_COMPONENT_MAX_BYTES["releases"] + 1
    )

    with pytest.raises(RuntimeError, match="preserving the last-known-good"):
        publish_collection(paths, payloads)

    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_dashboard_audit_rejects_aggregate_home_overflow(tmp_path: Path) -> None:
    oversized = tmp_path / "data/site/projects.json"
    oversized.parent.mkdir(parents=True)
    oversized.write_bytes(b"x" * (HOME_BUNDLE_MAX_BYTES + 1))

    audit = DashboardAudit(root=tmp_path)
    audit.audit_github_home_bundle()

    assert [finding.code for finding in audit.report.findings] == [
        "github-home-bundle-budget"
    ]


def test_dashboard_audit_accepts_truthfully_omitted_relationship_detail(
    tmp_path: Path,
) -> None:
    bounded = bounded_collection_payloads(_payloads(rows=180, padding=4_000))
    target = tmp_path / "data/vllm"
    target.mkdir(parents=True)
    (target / "prs.json").write_bytes(pretty_json_bytes(bounded["prs"]))
    (target / "issues.json").write_bytes(pretty_json_bytes(bounded["issues"]))

    audit = DashboardAudit(root=tmp_path)
    audit.audit_home_pr_issue_data()

    assert {
        finding.code
        for finding in audit.report.findings
        if finding.code
        in {
            "linked-ci-pr-missing",
            "ci-pr-issue-missing",
            "ci-pr-tag",
            "home-retention-semantics",
        }
    } == set()
