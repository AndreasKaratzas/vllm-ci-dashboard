"""Bounded, truthful publication for the GitHub Home data bundle.

The five Home files have one aggregate allocation in the immutable dashboard
state tree.  Each producer writes against a component allocation whose exact
sum is that aggregate.  Variable GitHub/configuration detail is compacted
before whole newest-first rows are omitted, while exact source/published row
counts remain available in every payload.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vllm.bounded_json import atomic_write_bytes, pretty_json_bytes
from vllm.dashboard_storage_budget import group_max_bytes, writer_max_bytes


HOME_BUNDLE_MAX_BYTES = group_max_bytes("github_home")
HOME_COMPONENT_MAX_BYTES = {
    "projects": writer_max_bytes("github_home_projects"),
    "prs": writer_max_bytes("github_home_prs"),
    "issues": writer_max_bytes("github_home_issues"),
    "project_items": writer_max_bytes("github_home_project_items"),
    "releases": writer_max_bytes("github_home_releases"),
}
if sum(HOME_COMPONENT_MAX_BYTES.values()) != HOME_BUNDLE_MAX_BYTES:
    raise RuntimeError(
        "GitHub Home component allocations must exactly fill the bundle allocation"
    )

_VLLM_REPOSITORY = "vllm-project/vllm"


@dataclass(frozen=True)
class _BoundedRows:
    payload: dict[str, Any]
    published_count: int
    compact_details: bool
    keep_query_details: bool


def _account(source: int, published: int) -> dict[str, Any]:
    omitted = source - published
    return {
        "source": source,
        "published": published,
        "omitted": omitted,
        "complete_relative_to_source": omitted == 0,
    }


def _coverage_projection(
    payload: dict[str, Any], *, keep_query_details: bool
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    raw = payload.get("source_coverage")
    if not isinstance(raw, dict):
        return None, _account(0, 0)
    coverage = deepcopy(raw)
    queries = coverage.get("queries")
    source_queries = len(queries) if isinstance(queries, list) else 0
    published_queries = source_queries if keep_query_details else 0
    if isinstance(queries, list) and not keep_query_details:
        coverage["queries"] = []
    coverage["query_count"] = source_queries
    coverage["query_retention"] = _account(source_queries, published_queries)
    return coverage, _account(source_queries, published_queries)


def _same_repo_reference(ref: object) -> bool:
    if not isinstance(ref, dict):
        return False
    repo = str(ref.get("repo") or _VLLM_REPOSITORY).casefold()
    return repo == _VLLM_REPOSITORY.casefold()


def _issue_relation_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        for ref in (row.get("linked_prs") or [])
        if _same_repo_reference(ref) and isinstance(ref.get("number"), int)
    )


def _pr_relation_count(rows: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in rows
        for number in (row.get("ci_issue_numbers") or [])
        if isinstance(number, int)
    )


def _source_aggregates(
    rows: list[dict[str, Any]], *, relation_kind: str | None
) -> dict[str, int]:
    aggregates = {"total": len(rows)}
    if relation_kind == "issues":
        aggregates.update(
            {
                "open": sum(
                    1 for row in rows if str(row.get("state") or "").lower() == "open"
                ),
                "linked_pr_refs": _issue_relation_count(rows),
            }
        )
    elif relation_kind == "prs":
        aggregates.update(
            {
                "open": sum(
                    1 for row in rows if str(row.get("state") or "").lower() == "open"
                ),
                "ci": sum(1 for row in rows if row.get("is_ci_pr") is True),
                "rocm": sum(1 for row in rows if row.get("is_rocm_pr") is True),
            }
        )
    return aggregates


def _project_rows(
    source_rows: list[dict[str, Any]],
    *,
    published_count: int,
    compact_details: bool,
    optional_fields: tuple[str, ...],
    allowed_related: set[int] | None,
    relation_kind: str | None,
) -> list[dict[str, Any]]:
    rows = deepcopy(source_rows[:published_count])
    if compact_details:
        for row in rows:
            for field in optional_fields:
                row.pop(field, None)
    if relation_kind == "issues" and allowed_related is not None:
        for row in rows:
            refs = row.get("linked_prs")
            if not isinstance(refs, list):
                continue
            row["linked_prs"] = [
                ref
                for ref in refs
                if not _same_repo_reference(ref)
                or (
                    isinstance(ref, dict)
                    and isinstance(ref.get("number"), int)
                    and ref["number"] in allowed_related
                )
            ]
    elif relation_kind == "prs" and allowed_related is not None:
        for row in rows:
            numbers = row.get("ci_issue_numbers")
            if isinstance(numbers, list):
                row["ci_issue_numbers"] = [
                    number
                    for number in numbers
                    if isinstance(number, int) and number in allowed_related
                ]
    return rows


def _row_candidate(
    payload: dict[str, Any],
    *,
    key: str,
    source_rows: list[dict[str, Any]],
    published_count: int,
    compact_details: bool,
    keep_query_details: bool,
    optional_fields: tuple[str, ...],
    max_bytes: int,
    policy: str = "aggregate_counts_first_newest_whole_rows_v1",
    relation_kind: str | None = None,
    allowed_related: set[int] | None = None,
) -> dict[str, Any]:
    rows = _project_rows(
        source_rows,
        published_count=published_count,
        compact_details=compact_details,
        optional_fields=optional_fields,
        allowed_related=allowed_related,
        relation_kind=relation_kind,
    )
    result = deepcopy(payload)
    result[key] = rows

    coverage, query_account = _coverage_projection(
        payload, keep_query_details=keep_query_details
    )
    if coverage is not None:
        result["source_coverage"] = coverage

    detail_source = sum(
        1 for row in source_rows for field in optional_fields if field in row
    )
    detail_published = sum(
        1 for row in rows for field in optional_fields if field in row
    )
    if relation_kind == "issues":
        relation_source = _issue_relation_count(source_rows)
        relation_published = _issue_relation_count(rows)
    elif relation_kind == "prs":
        relation_source = _pr_relation_count(source_rows)
        relation_published = _pr_relation_count(rows)
    else:
        relation_source = 0
        relation_published = 0

    retention = {
        "policy": policy,
        "max_bytes": max_bytes,
        "aggregate_counts_complete": True,
        "source_aggregates": _source_aggregates(
            source_rows, relation_kind=relation_kind
        ),
        "rows": _account(len(source_rows), len(rows)),
        "detail_fields": _account(detail_source, detail_published),
        "relationship_refs": _account(relation_source, relation_published),
        "source_queries": query_account,
    }
    retention["complete_relative_to_source"] = all(
        account["omitted"] == 0
        for account in (
            retention["rows"],
            retention["detail_fields"],
            retention["relationship_refs"],
            retention["source_queries"],
        )
    )
    result["total_count"] = len(source_rows)
    result["publication_retention"] = retention
    source_semantics = str(payload.get("count_semantics") or "complete")
    result["count_semantics"] = (
        "complete"
        if source_semantics == "complete"
        and retention["complete_relative_to_source"]
        else "lower_bound"
    )
    return result


def _bound_rows(
    payload: dict[str, Any],
    *,
    key: str,
    optional_fields: tuple[str, ...],
    max_bytes: int,
    policy: str = "aggregate_counts_first_newest_whole_rows_v1",
    relation_kind: str | None = None,
    allowed_related: set[int] | None = None,
) -> _BoundedRows:
    raw_rows = payload.get(key)
    if not isinstance(raw_rows, list):
        raise ValueError(f"GitHub Home payload {key!r} must be a list")
    source_rows = [deepcopy(row) for row in raw_rows if isinstance(row, dict)]
    if len(source_rows) != len(raw_rows):
        raise ValueError(f"GitHub Home payload {key!r} contains a non-object row")

    def candidate(
        published_count: int,
        *,
        compact_details: bool,
        keep_query_details: bool,
    ) -> dict[str, Any]:
        return _row_candidate(
            payload,
            key=key,
            source_rows=source_rows,
            published_count=published_count,
            compact_details=compact_details,
            keep_query_details=keep_query_details,
            optional_fields=optional_fields,
            max_bytes=max_bytes,
            policy=policy,
            relation_kind=relation_kind,
            allowed_related=allowed_related,
        )

    for compact_details, keep_query_details in (
        (False, True),
        (True, True),
        (True, False),
    ):
        complete = candidate(
            len(source_rows),
            compact_details=compact_details,
            keep_query_details=keep_query_details,
        )
        if len(pretty_json_bytes(complete)) <= max_bytes:
            return _BoundedRows(
                complete,
                len(source_rows),
                compact_details,
                keep_query_details,
            )

    low = 0
    high = len(source_rows)
    best: _BoundedRows | None = None
    while low <= high:
        middle = (low + high) // 2
        current = candidate(
            middle,
            compact_details=True,
            keep_query_details=False,
        )
        if len(pretty_json_bytes(current)) <= max_bytes:
            best = _BoundedRows(current, middle, True, False)
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        irreducible = candidate(
            0,
            compact_details=True,
            keep_query_details=False,
        )
        raise RuntimeError(
            f"GitHub Home {key} fixed metadata exceeds its byte budget; preserving "
            "the last-known-good file: "
            f"{len(pretty_json_bytes(irreducible))} > {max_bytes} bytes"
        )
    return best


def _mapping_payload(
    payload: dict[str, Any],
    *,
    key: str,
    ordered_keys: list[str],
    published_count: int,
    compact_details: bool,
    keep_query_details: bool,
    optional_fields: tuple[str, ...],
    max_bytes: int,
    policy: str,
) -> dict[str, Any]:
    raw_mapping = payload.get(key)
    if not isinstance(raw_mapping, dict):
        raise ValueError(f"GitHub Home payload {key!r} must be an object")
    source_rows = [
        deepcopy(raw_mapping[name])
        for name in ordered_keys
        if isinstance(raw_mapping.get(name), dict)
    ]
    if len(source_rows) != len(raw_mapping):
        raise ValueError(f"GitHub Home payload {key!r} contains a non-object row")
    sequence_payload = dict(payload)
    sequence_payload[key] = source_rows
    bounded = _row_candidate(
        sequence_payload,
        key=key,
        source_rows=source_rows,
        published_count=published_count,
        compact_details=compact_details,
        keep_query_details=keep_query_details,
        optional_fields=optional_fields,
        max_bytes=max_bytes,
        policy=policy,
    )
    bounded[key] = {
        name: row
        for name, row in zip(
            ordered_keys[:published_count], bounded[key], strict=True
        )
    }
    return bounded


def _bound_mapping(
    payload: dict[str, Any],
    *,
    key: str,
    order: Callable[[str], tuple[Any, ...]],
    optional_fields: tuple[str, ...],
    max_bytes: int,
    policy: str,
) -> dict[str, Any]:
    raw_mapping = payload.get(key)
    if not isinstance(raw_mapping, dict):
        raise ValueError(f"GitHub Home payload {key!r} must be an object")
    ordered_keys = sorted(raw_mapping, key=order)

    def candidate(
        published_count: int,
        *,
        compact_details: bool,
        keep_query_details: bool,
    ) -> dict[str, Any]:
        return _mapping_payload(
            payload,
            key=key,
            ordered_keys=ordered_keys,
            published_count=published_count,
            compact_details=compact_details,
            keep_query_details=keep_query_details,
            optional_fields=optional_fields,
            max_bytes=max_bytes,
            policy=policy,
        )

    for compact_details, keep_query_details in (
        (False, True),
        (True, True),
        (True, False),
    ):
        complete = candidate(
            len(ordered_keys),
            compact_details=compact_details,
            keep_query_details=keep_query_details,
        )
        if len(pretty_json_bytes(complete)) <= max_bytes:
            return complete

    low = 0
    high = len(ordered_keys)
    best: dict[str, Any] | None = None
    while low <= high:
        middle = (low + high) // 2
        current = candidate(
            middle,
            compact_details=True,
            keep_query_details=False,
        )
        if len(pretty_json_bytes(current)) <= max_bytes:
            best = current
            low = middle + 1
        else:
            high = middle - 1
    if best is None:
        irreducible = candidate(
            0,
            compact_details=True,
            keep_query_details=False,
        )
        raise RuntimeError(
            f"GitHub Home {key} fixed metadata exceeds its byte budget; preserving "
            "the last-known-good file: "
            f"{len(pretty_json_bytes(irreducible))} > {max_bytes} bytes"
        )
    return best


def bounded_projects_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound ``data/site/projects.json`` while retaining vLLM first."""
    return _bound_mapping(
        payload,
        key="projects",
        order=lambda name: (name != "vllm", name.casefold(), name),
        optional_fields=("fork", "depends_on", "build_workflows"),
        max_bytes=HOME_COMPONENT_MAX_BYTES["projects"],
        policy="vllm_first_deterministic_project_rows_v1",
    )


def bounded_project_items_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound the Project #39 fallback snapshot independently."""
    return _bound_mapping(
        payload,
        key="items_by_number",
        order=lambda number: (
            -int(number) if str(number).isdigit() else 0,
            str(number),
        ),
        optional_fields=("title",),
        max_bytes=HOME_COMPONENT_MAX_BYTES["project_items"],
        policy="descending_issue_number_whole_rows_v1",
    )


def bounded_collection_payloads(
    payloads: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Bound all four GitHub collector outputs with reconciled relationships."""
    if set(payloads) != {"prs", "issues", "project_items", "releases"}:
        raise ValueError("GitHub Home collection payload set is incomplete")

    issues = _bound_rows(
        payloads["issues"],
        key="issues",
        optional_fields=("body_head",),
        max_bytes=HOME_COMPONENT_MAX_BYTES["issues"],
        relation_kind="issues",
    )
    prs = _bound_rows(
        payloads["prs"],
        key="prs",
        optional_fields=("body_head", "other_tags"),
        max_bytes=HOME_COMPONENT_MAX_BYTES["prs"],
        relation_kind="prs",
    )
    project_items = bounded_project_items_payload(payloads["project_items"])
    releases = _bound_rows(
        payloads["releases"],
        key="releases",
        optional_fields=("name",),
        max_bytes=HOME_COMPONENT_MAX_BYTES["releases"],
    )

    retained_issues = {
        row.get("number")
        for row in issues.payload["issues"]
        if isinstance(row.get("number"), int)
    }
    retained_prs = {
        row.get("number")
        for row in prs.payload["prs"]
        if isinstance(row.get("number"), int)
    }

    # Rebuild with the already-selected row counts so no retained relationship
    # points at detail omitted from the companion file.  This can only remove
    # relationship bytes; nevertheless, keep a hard assertion at the boundary.
    issues_payload = _row_candidate(
        payloads["issues"],
        key="issues",
        source_rows=payloads["issues"]["issues"],
        published_count=issues.published_count,
        compact_details=issues.compact_details,
        keep_query_details=issues.keep_query_details,
        optional_fields=("body_head",),
        max_bytes=HOME_COMPONENT_MAX_BYTES["issues"],
        relation_kind="issues",
        allowed_related=retained_prs,
    )
    prs_payload = _row_candidate(
        payloads["prs"],
        key="prs",
        source_rows=payloads["prs"]["prs"],
        published_count=prs.published_count,
        compact_details=prs.compact_details,
        keep_query_details=prs.keep_query_details,
        optional_fields=("body_head", "other_tags"),
        max_bytes=HOME_COMPONENT_MAX_BYTES["prs"],
        relation_kind="prs",
        allowed_related=retained_issues,
    )
    bounded = {
        "prs": prs_payload,
        "issues": issues_payload,
        "project_items": project_items,
        "releases": releases.payload,
    }
    for name, payload in bounded.items():
        size = len(pretty_json_bytes(payload))
        max_bytes = HOME_COMPONENT_MAX_BYTES[name]
        if size > max_bytes:
            raise RuntimeError(
                f"GitHub Home {name} exceeds its component budget; preserving "
                f"the last-known-good file: {size} > {max_bytes} bytes"
            )
    return bounded


def publish_projects(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Atomically publish the bounded project selector payload."""
    bounded = bounded_projects_payload(payload)
    atomic_write_bytes(path, pretty_json_bytes(bounded))
    return bounded


def publish_collection(
    paths: dict[str, Path], payloads: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Stage all collection bytes in memory, then atomically replace each LKG."""
    if set(paths) != {"prs", "issues", "project_items", "releases"}:
        raise ValueError("GitHub Home collection path set is incomplete")
    bounded = bounded_collection_payloads(payloads)
    encoded = {name: pretty_json_bytes(payload) for name, payload in bounded.items()}
    # Write relationship-independent files first and the audited issue/PR pair
    # last. Workflow publication is still gated by the cross-surface audit.
    for name in ("releases", "project_items", "issues", "prs"):
        atomic_write_bytes(paths[name], encoded[name])
    return bounded
