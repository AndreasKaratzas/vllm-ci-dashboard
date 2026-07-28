#!/usr/bin/env python3
"""Create one state-owned dashboard-repository issue per regressing CI area.

The latest AMD runtime result is read from the exact 160-row test matrix after
the operations bundle is freshly built. Test targets are attributed back to their commit-pinned upstream
``.buildkite/test_areas/*.yaml`` source, then assigned through the ranked owner
chain. Working-hours/PTO data is private runtime input. Missing availability,
an unavailable chain, or an unassignable selected account escalates to the CI
lead. Issue text intentionally contains no ``@`` mentions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.managed_issue import (  # noqa: E402
    DASHBOARD_REPO,
    GitHubIssueClient,
    normalize_managed_state,
    reconcile_managed_issue,
    validate_target_repo,
)
from vllm.ci.ownership import (  # noqa: E402
    build_ownership_status,
    evaluate_availability,
    isoformat_z,
    load_ownership_config,
    matrix_runtime_targets,
    parse_timestamp,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "vllm_ci_ownership.json"
DATA = ROOT / "data" / "vllm" / "ci"
MANIFEST = DATA / "operations_v2_manifest.json"
PARITY = DATA / "config_parity.json"
OWNERSHIP_PARITY = DATA / "ownership_config_parity.json"
MATRIX = DATA / "amd_test_matrix.json"
STATE = DATA / "open_ci_area_regression_issues.json"
STATUS = DATA / "ci_ownership.json"
MAX_SOURCE_AGE = timedelta(hours=3)
MAX_NIGHTLY_AGE = timedelta(hours=36)
FUTURE_SKEW = timedelta(minutes=15)
MAX_ISSUE_ROWS = 50
OWNERSHIP_MARKER_PREFIX = "vllm-ci-dashboard:managed-alert:ci-area-regression"
DASHBOARD_URL = (
    "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
    "?ops_health_view=ownership#ci-health"
)
UPSTREAM_PARITY_EXAMPLE = "https://github.com/vllm-project/vllm/pull/49340"
COMMIT_IN_YAML_URL_RE = re.compile(
    r"raw\.githubusercontent\.com/vllm-project/vllm/"
    r"(?P<commit>[0-9a-f]{40})/\.buildkite/test-amd\.yaml",
    re.IGNORECASE,
)


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_availability() -> Any:
    raw = os.getenv("CI_OWNER_AVAILABILITY_JSON", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.error("CI_OWNER_AVAILABILITY_JSON is not valid JSON; failing closed")
        return None


def _source_generated_at() -> datetime | None:
    return parse_timestamp(_load_json(MANIFEST).get("generated_at"))


def _source_is_fresh(now: datetime) -> bool:
    generated = _source_generated_at()
    if generated is None:
        return False
    age = now - generated
    return -FUTURE_SKEW <= age <= MAX_SOURCE_AGE


def _timestamp_is_fresh(
    value: Any,
    now: datetime,
    *,
    max_age: timedelta = MAX_SOURCE_AGE,
) -> bool:
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return False
    age = now - timestamp
    return -FUTURE_SKEW <= age <= max_age


def _matrix_commit(matrix: dict) -> str:
    yaml_url = str((matrix.get("source") or {}).get("yaml_url") or "")
    match = COMMIT_IN_YAML_URL_RE.search(yaml_url)
    return match.group("commit").lower() if match else ""


def _source_validation_error(
    now: datetime,
    matrix: dict,
    parity: dict,
    ownership_parity: dict,
) -> str:
    if not _source_is_fresh(now):
        return "operations_source_stale"
    if not _timestamp_is_fresh(matrix.get("generated_at"), now):
        return "amd_test_matrix_stale"
    if not _timestamp_is_fresh(parity.get("generated_at"), now):
        return "current_config_parity_stale"
    if not _timestamp_is_fresh(ownership_parity.get("generated_at"), now):
        return "ownership_config_parity_stale"
    matrix_source = matrix.get("source") or {}
    if not _timestamp_is_fresh(
        matrix_source.get("latest_build_created_at"),
        now,
        max_age=MAX_NIGHTLY_AGE,
    ):
        return "amd_nightly_signal_stale"
    rows = matrix.get("rows")
    definition_rows = (matrix.get("summary") or {}).get("definition_rows")
    if (
        not isinstance(rows, list)
        or not rows
        or not isinstance(definition_rows, int)
        or isinstance(definition_rows, bool)
        or definition_rows != len(rows)
    ):
        return "amd_test_matrix_incomplete"
    matrix_commit = _matrix_commit(matrix)
    ownership_commit = str(
        (ownership_parity.get("source") or {}).get("commit_sha") or ""
    ).lower()
    if not matrix_commit or ownership_commit != matrix_commit:
        return "ownership_parity_commit_mismatch"
    return ""


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "areas": {},
        "last_run": "",
    }


def _read_state() -> dict:
    raw = _load_json(STATE)
    state = _default_state()
    if raw.get("schema_version") != 1:
        return state
    areas = raw.get("areas")
    if isinstance(areas, dict):
        state["areas"] = {
            str(area): normalize_managed_state(value)
            for area, value in areas.items()
            if isinstance(value, dict)
        }
    state["last_run"] = str(raw.get("last_run") or "")
    return state


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _md(value: Any) -> str:
    return str(value or "-").replace("|", "\\|").replace("\n", " ")


def _owner_rank(area: dict) -> str:
    selected_login = str((area.get("selected_owner") or {}).get("github_login") or "")
    for row in area.get("owners") or []:
        if str(row.get("github_login") or "").casefold() == selected_login.casefold():
            return str(row.get("rank") or "-")
    return "CI lead"


def _issue_title(area: dict) -> str:
    counts = area.get("counts") or {}
    return (
        f"AMD CI regression [{area['source_file']}]: "
        f"{int(counts.get('incidents') or 0)} failing target groups"
    )


def _issue_body(area: dict, run_url: str) -> str:
    counts = area.get("counts") or {}
    selected = area.get("selected_owner") or {}
    actual = area.get("actual_assignee") or selected
    lines = [
        f"## AMD CI regression — `{area['source_file']}`",
        "",
        (
            f"**{int(counts.get('incidents') or 0)} target groups are regressing: "
            f"{int(counts.get('hard') or 0)} hard, "
            f"{int(counts.get('soft') or 0)} soft.**"
        ),
        "",
        f"Selected owner: **{_md(selected.get('display_name'))}** (rank {_owner_rank(area)}).",
        f"GitHub assignee: **{_md(actual.get('display_name'))}**.",
        f"Assignment decision: `{_md(area.get('assignment_reason'))}`.",
        "",
        "### Escalation chain",
        "",
        "| rank | engineer |",
        "|---:|---|",
    ]
    for owner in area.get("owners") or []:
        lines.append(
            f"| {int(owner.get('rank') or 0)} | {_md(owner.get('display_name'))} |"
        )
    lines.extend(
        [
            "",
            "### Current regressions",
            "",
            "| target group | latest result | build | observed |",
            "|---|---|---|---|",
        ]
    )
    regressions = area.get("regressions") or []
    for row in regressions[:MAX_ISSUE_ROWS]:
        label = _md(row.get("label"))
        url = str(row.get("url") or "")
        label_md = f"[{label}]({url})" if url else label
        build = int(row.get("build_number") or 0)
        lines.append(
            f"| {label_md} | {_md(row.get('result'))} | "
            f"{f'#{build}' if build else '-'} | {_md(row.get('observed_at'))} |"
        )
    if len(regressions) > MAX_ISSUE_ROWS:
        lines.extend(
            [
                "",
                f"{len(regressions) - MAX_ISSUE_ROWS} additional regressions remain in managed state.",
            ]
        )
    gaps = area.get("upstream_parity_gaps") or []
    lines.extend(
        [
            "",
            "### Area ownership expectations",
            "",
            "- Fix the active regression and preserve exact evidence for the resolution.",
            "- Simplify and improve test quality, including retiring obsolete model coverage.",
            "- Reduce test-group time to completion through measurement-backed refactoring.",
            "- Restore parity with upstream definitions when the AMD cadence diverges.",
            "",
            (
                f"Current upstream-only definitions in this area: **{len(gaps)}**. "
                f"[Parity drift example]({UPSTREAM_PARITY_EXAMPLE})."
            ),
            f"[Open the CI ownership dashboard]({DASHBOARD_URL}).",
            "",
            (
                f"*Managed from {run_url}. This watcher updates or closes only the tracked "
                "issue for this test area. Issue bodies intentionally contain no user mentions.*"
            ),
        ]
    )
    body = "\n".join(lines) + "\n"
    if "@" in body:
        raise ValueError("Managed CI ownership issue bodies must not contain @ mentions")
    return body


def _fingerprint(area: dict) -> str:
    compact = {
        "area": area.get("area"),
        "selected_owner": (area.get("selected_owner") or {}).get("github_login"),
        "actual_assignee": (area.get("actual_assignee") or {}).get("github_login"),
        "assignment_reason": area.get("assignment_reason"),
        "regressions": [
            {
                "id": row.get("id"),
                "result": row.get("result"),
                "build_number": row.get("build_number"),
            }
            for row in area.get("regressions") or []
        ],
        "upstream_parity_gaps": [
            row.get("label") for row in area.get("upstream_parity_gaps") or []
        ],
    }
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _owner_by_login(config: dict, login: str) -> dict:
    for owner in config.get("owners") or []:
        if owner["github_login"].casefold() == login.casefold():
            return dict(owner)
    return dict(config["ci_lead"])


def _actual_assignee(
    area: dict,
    config: dict,
    client: GitHubIssueClient,
) -> tuple[dict, str]:
    selected = area.get("selected_owner") or config["ci_lead"]
    selected_login = str(selected.get("github_login") or "")
    lead_login = config["ci_lead"]["github_login"]
    if client.is_assignable(selected_login):
        reason = (
            "ranked_owner_selected_and_assignable"
            if not area.get("escalated_to_ci_lead")
            else "ranked_chain_unavailable_ci_lead"
        )
        return _owner_by_login(config, selected_login), reason
    if not client.is_assignable(lead_login):
        log.error("Neither selected owner nor CI lead is assignable; leaving issue unassigned")
        return {}, "no_assignable_owner"
    return _owner_by_login(config, lead_login), "selected_owner_not_assignable_ci_lead"


def _mark_unavailable_status(now: datetime, reason: str) -> None:
    previous = _load_json(STATUS)
    payload = {
        **previous,
        "schema_version": 1,
        "generated_at": isoformat_z(now),
        "available": False,
        "unavailable_reason": reason,
    }
    _write_json(STATUS, payload)


def _attach_issue(area: dict, managed: dict, repo: str) -> None:
    issue_number = int(((managed.get("issue") or {}).get("number") or 0))
    if issue_number:
        area["issue"] = {
            "number": issue_number,
            "url": f"https://github.com/{repo}/issues/{issue_number}",
            "suppressed": False,
        }
    elif managed.get("suppressed"):
        area["issue"] = {
            "number": None,
            "url": "",
            "suppressed": True,
        }


def _checkpoint_state(areas: dict[str, dict], observed_at: str) -> None:
    _write_json(
        STATE,
        {
            "schema_version": 1,
            "areas": areas,
            "last_run": observed_at,
        },
    )


def _can_mutate_area(active: bool, actual_assignee: dict) -> bool:
    return not active or bool(actual_assignee)


def run() -> int:
    repo = os.getenv("GITHUB_REPOSITORY") or DASHBOARD_REPO
    validate_target_repo(repo)
    now = datetime.now(timezone.utc)
    matrix = _load_json(MATRIX)
    parity = _load_json(PARITY)
    ownership_parity = _load_json(OWNERSHIP_PARITY)
    source_error = _source_validation_error(
        now,
        matrix,
        parity,
        ownership_parity,
    )
    if source_error:
        log.error("%s; refusing issue mutations", source_error)
        _mark_unavailable_status(now, source_error)
        return 0
    matrix_targets = matrix_runtime_targets(matrix)
    if not matrix_targets:
        log.error("AMD test matrix definitions are unavailable; refusing issue mutations")
        _mark_unavailable_status(now, "amd_test_matrix_unavailable")
        return 0
    gating = {"active_target_groups": matrix_targets}

    try:
        config = load_ownership_config(CONFIG)
    except ValueError as error:
        log.error("%s; refusing issue mutations", error)
        _mark_unavailable_status(now, "ownership_config_invalid")
        return 0
    availability, availability_source = evaluate_availability(
        _read_availability(),
        config["owners"],
        now=now,
    )
    observed_at = isoformat_z(now)
    status = build_ownership_status(
        gating,
        parity,
        config,
        availability,
        availability_source,
        generated_at=observed_at,
        attribution_parity=ownership_parity,
    )

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        log.warning("GITHUB_TOKEN not set; writing dry-run ownership status without issue mutations")
        status["available"] = True
        status["issue_mutations"] = "disabled_missing_token"
        _write_json(STATUS, status)
        return 0

    client = GitHubIssueClient(token, repo)
    state = _read_state()
    prior_areas = state.get("areas") or {}
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = (
        f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else f"https://github.com/{repo}"
    )
    next_areas: dict[str, dict] = {}
    checkpoint_areas = dict(prior_areas)
    for area in status["areas"]:
        actual, assignment_reason = _actual_assignee(area, config, client)
        area["actual_assignee"] = actual or None
        area["assignment_reason"] = assignment_reason
        area_key = area["area"]
        active = bool((area.get("counts") or {}).get("incidents"))
        if not _can_mutate_area(active, actual):
            log.error(
                "No assignee could be verified for %s; refusing to open or mutate its issue",
                area["source_file"],
            )
            preserved = normalize_managed_state(prior_areas.get(area_key) or {})
            checkpoint_areas[area_key] = preserved
            next_areas[area_key] = preserved
            area["issue_mutations"] = "skipped_no_verified_assignee"
            _attach_issue(area, preserved, repo)
            _checkpoint_state(checkpoint_areas, observed_at)
            continue
        assignees = [actual["github_login"]] if actual else None
        marker = f"<!-- {OWNERSHIP_MARKER_PREFIX}:{area_key}:v1 -->"
        reconciled = reconcile_managed_issue(
            prior_areas.get(area_key) or {},
            active=active,
            fingerprint=_fingerprint(area),
            title=_issue_title(area),
            body=_issue_body(area, run_url),
            ownership_marker=marker,
            recovery_body=(
                f"All latest AMD runtime regressions for `{area['source_file']}` have "
                "recovered. Closing this tracked test-area issue.\n\n"
                f"*{run_url}*"
            ),
            observed_at=observed_at,
            label_specs=[
                ("automated", "6f42c1", "Managed by dashboard automation"),
                ("amd-ci-regression", "d73a49", "Latest AMD CI target regression"),
                ("workstream:dev", "1d76db", "AMD CI test-area development"),
                (
                    f"test-area:{area_key}",
                    "bfdadc",
                    f"Owned by the {area['source_file']} rotation",
                ),
            ],
            client=client,
            assignees=assignees,
        )
        next_areas[area_key] = reconciled
        checkpoint_areas[area_key] = reconciled
        _attach_issue(area, reconciled, repo)
        _checkpoint_state(checkpoint_areas, observed_at)

    status["issue_mutations"] = "enabled"
    _checkpoint_state(next_areas, observed_at)
    _write_json(STATUS, status)
    log.info(
        "CI area watcher reconciled %d areas, %d active issues, %d incidents",
        len(status["areas"]),
        sum(bool(row.get("issue")) and not row["issue"].get("suppressed") for row in status["areas"]),
        status["summary"]["incidents"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
