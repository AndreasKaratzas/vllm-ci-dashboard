#!/usr/bin/env python3
"""Create one state-owned dashboard-repository issue per confirmed CI area.

The latest AMD runtime result is read from the exact 160-row test matrix after
the operations bundle is freshly built. Test targets are attributed back to their commit-pinned upstream
``.buildkite/test_areas/*.yaml`` source, then assigned through the ranked owner
chain. Working hours come only from the committed regional profiles. A missing
schedule, an out-of-hours chain, or an unassignable selected account escalates
to the CI lead. Each managed regression issue tags the selected owner and the
verified assignee, then CCs every remaining ranked area owner exactly once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
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
    apply_incident_hysteresis,
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
ISSUE_BODY_SCHEMA_VERSION = 3
SIGNAL_FINGERPRINT_VERSION = 2
AREA_RETIREMENT_STREAK_REQUIRED = 3
GITHUB_LOGIN_RE = re.compile(
    r"(?=.{1,39}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\Z"
)
GITHUB_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9-])@"
    r"(?P<login>(?=.{1,39}(?:[^A-Za-z0-9-]|$))[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
    r"(?![A-Za-z0-9-])"
)
DASHBOARD_URL = (
    "https://andreaskaratzas.github.io/vllm-ci-dashboard/"
    "?ops_health_view=targets&ops_health_result=non_passing#ci-health"
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


def _complete_current_area_keys(status: dict, config: dict) -> set[str] | None:
    """Return current area keys only when the derived evidence is exhaustive.

    Retirement is destructive state cleanup, so a partial status projection
    must be treated differently from a valid configuration that intentionally
    removed an area. The status builder is expected to emit every configured
    area, including areas with zero targets.
    """
    if status.get("available") is not True:
        return None
    rows = status.get("areas")
    configured = config.get("areas")
    if not isinstance(rows, list) or not isinstance(configured, dict):
        return None
    keys: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        area_key = str(row.get("area") or "")
        if not area_key:
            return None
        keys.append(area_key)
    current = set(keys)
    expected = {str(area_key) for area_key in configured}
    if len(current) != len(keys) or current != expected:
        return None
    return current


def _default_state() -> dict:
    return {
        "schema_version": 1,
        "areas": {},
        "last_run": "",
    }


def _normalize_area_state(value: Any) -> dict:
    """Normalize managed-issue fields without discarding watcher extensions."""
    raw = value if isinstance(value, dict) else {}
    state = normalize_managed_state(raw)
    signals = raw.get("signals")
    state["signals"] = {
        str(signal_id): dict(signal)
        for signal_id, signal in (signals or {}).items()
        if isinstance(signal, dict)
    } if isinstance(signals, dict) else {}
    for key in (
        "body_schema_version",
        "signal_fingerprint_version",
        "incident_state_version",
    ):
        raw_value = raw.get(key)
        if isinstance(raw_value, int) and not isinstance(raw_value, bool):
            state[key] = raw_value
    raw_retirement_streak = raw.get("retirement_streak")
    state.pop("retirement_streak", None)
    if (
        isinstance(raw_retirement_streak, int)
        and not isinstance(raw_retirement_streak, bool)
        and raw_retirement_streak > 0
    ):
        state["retirement_streak"] = min(
            raw_retirement_streak,
            AREA_RETIREMENT_STREAK_REQUIRED,
        )
    return state


def _read_state() -> dict:
    raw = _load_json(STATE)
    state = _default_state()
    if raw.get("schema_version") != 1:
        return state
    areas = raw.get("areas")
    if isinstance(areas, dict):
        state["areas"] = {
            str(area): _normalize_area_state(value)
            for area, value in areas.items()
            if isinstance(value, dict)
        }
    state["last_run"] = str(raw.get("last_run") or "")
    return state


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _md(value: Any) -> str:
    return (
        str(value or "-")
        .replace("@", "&#64;")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def _github_login(value: Any) -> str:
    login = str(value or "").strip()
    if login and not GITHUB_LOGIN_RE.fullmatch(login):
        raise ValueError(f"Invalid GitHub login in ownership issue: {login!r}")
    return login


def _notification_lines(area: dict) -> tuple[list[str], list[str]]:
    """Render role-aware, case-insensitively deduplicated GitHub mentions."""
    selected = _github_login(
        (area.get("selected_owner") or {}).get("github_login")
    )
    actual = _github_login(
        (area.get("actual_assignee") or {}).get("github_login")
    )
    if not selected:
        raise ValueError("Managed CI ownership issue requires a selected owner")

    lines: list[str] = []
    mentioned: list[str] = []
    seen: set[str] = set()

    def mention(login: str) -> str:
        folded = login.casefold()
        if folded in seen:
            raise ValueError(f"Duplicate ownership mention requested for {login!r}")
        seen.add(folded)
        mentioned.append(login)
        return f"@{login}"

    if actual and actual.casefold() == selected.casefold():
        lines.append(
            f"- Selected owner and GitHub assignee: {mention(selected)}"
        )
    else:
        lines.append(f"- Selected owner: {mention(selected)}")
        if actual:
            lines.append(f"- GitHub assignee: {mention(actual)}")

    cc: list[str] = []
    for owner in sorted(
        (row for row in (area.get("owners") or []) if isinstance(row, dict)),
        key=lambda row: int(row.get("rank") or 0),
    ):
        login = _github_login(owner.get("github_login"))
        if not login or login.casefold() in seen:
            continue
        cc.append(mention(login))
    if cc:
        lines.append(f"- CC (remaining ranked area owners): {' '.join(cc)}")
    return lines, mentioned


def _owner_rank(area: dict) -> str:
    selected_login = str((area.get("selected_owner") or {}).get("github_login") or "")
    for row in area.get("owners") or []:
        if str(row.get("github_login") or "").casefold() == selected_login.casefold():
            return str(row.get("rank") or "-")
    return "CI lead"


def _issue_title(area: dict) -> str:
    counts = area.get("counts") or {}
    return (
        f"AMD CI confirmed incident [{area['source_file']}]: "
        f"{int(counts.get('incidents') or 0)} affected target groups"
    )


def _displayed_failure_evidence(row: dict) -> dict:
    """Return the exact failure evidence rendered for one incident row."""
    retained = row.get("last_failure_evidence") or {}
    if not isinstance(retained, dict):
        retained = {}
    held = row.get("incident_observation_eligible") is False or str(
        row.get("raw_result") or row.get("result") or ""
    ).lower() in {"unobserved", "unknown", "indeterminate"}
    primary, fallback = (retained, row) if held else (row, retained)
    evidence = {}
    for key in ("build_number", "observed_at", "url"):
        value = primary.get(key)
        if value in (None, ""):
            value = fallback.get(key)
        if value not in (None, ""):
            evidence[key] = value
    return evidence


def _issue_body(area: dict, run_url: str) -> str:
    counts = area.get("counts") or {}
    confirmed_hard = int(counts.get("confirmed_hard", counts.get("hard", 0)) or 0)
    confirmed_soft = int(counts.get("confirmed_soft", counts.get("soft", 0)) or 0)
    selected = area.get("selected_owner") or {}
    actual = area.get("actual_assignee") or {}
    notification_lines, expected_mentions = _notification_lines(area)
    lines = [
        f"## AMD CI confirmed incident — `{_md(area['source_file'])}`",
        "",
        (
            f"**{int(counts.get('incidents') or 0)} target groups have confirmed incidents: "
            f"{confirmed_hard} hard, "
            f"{confirmed_soft} soft.**"
        ),
        "",
        f"Selected owner: **{_md(selected.get('display_name'))}** (rank {_owner_rank(area)}).",
        f"GitHub assignee: **{_md(actual.get('display_name'))}**.",
        f"Assignment decision: `{_md(area.get('assignment_reason'))}`.",
        "",
        "### Notifications",
        "",
        *notification_lines,
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
            "### Confirmed incidents",
            "",
            "| target group | confirmed severity | latest observation | build | observed |",
            "|---|---|---|---|---|",
        ]
    )
    regressions = area.get("regressions") or []
    for row in regressions[:MAX_ISSUE_ROWS]:
        evidence = _displayed_failure_evidence(row)
        label = _md(row.get("label"))
        url = str(evidence.get("url") or "")
        label_md = f"[{label}]({url})" if url else label
        try:
            build = int(evidence.get("build_number") or 0)
        except (TypeError, ValueError, OverflowError):
            build = 0
        observed_at = evidence.get("observed_at")
        observation = str(row.get("raw_result") or row.get("result") or "")
        if row.get("incident_observation_eligible") is False:
            observation = f"{observation} (ignored older build)"
        lines.append(
            f"| {label_md} | {_md(row.get('incident_severity'))} | "
            f"{_md(observation)} | "
            f"{f'#{build}' if build else '-'} | {_md(observed_at)} |"
        )
    if len(regressions) > MAX_ISSUE_ROWS:
        lines.extend(
            [
                "",
                f"{len(regressions) - MAX_ISSUE_ROWS} additional confirmed incidents remain in managed state.",
            ]
        )
    pending = area.get("pending_soft_observations") or []
    if pending:
        lines.extend(
            [
                "",
                "### Pending soft observations",
                "",
                (
                    f"{len(pending)} soft target observations are visible but have not "
                    "recurred on two distinct completed builds, so they do not open or "
                    "escalate this incident."
                ),
            ]
        )
    gaps = area.get("upstream_parity_gaps") or []
    lines.extend(
        [
            "",
            "### Area ownership expectations",
            "",
            "- Fix the confirmed incident and preserve exact evidence for the resolution.",
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
                "issue for this test area. The selected owner, verified assignee, and "
                "remaining ranked owners are notified once in this body.*"
            ),
        ]
    )
    body = "\n".join(lines) + "\n"
    observed_mentions = [
        match.group("login") for match in GITHUB_MENTION_RE.finditer(body)
    ]
    if observed_mentions != expected_mentions:
        raise ValueError(
            "Managed CI ownership issue mentions must contain only the deduplicated "
            "selected owner, assignee, and ranked CC list"
        )
    return body


def _legacy_fingerprint_payload(area: dict) -> dict:
    return {
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


def _hash_fingerprint_payload(compact: dict) -> str:
    encoded = json.dumps(compact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _legacy_fingerprint(area: dict) -> str:
    """Return the pre-notification-policy signal hash for state migration only."""
    return _hash_fingerprint_payload(_legacy_fingerprint_payload(area))


def _fingerprint_payload(area: dict) -> dict:
    """Return stable incident identity, excluding routing and build evidence."""
    regressions = [
        {
            "id": str(row.get("id") or ""),
            "generation": str(row.get("incident_start_build_id") or ""),
            "peak_severity": str(
                row.get("incident_peak_severity")
                or row.get("incident_severity")
                or row.get("result")
                or ""
            ),
        }
        for row in area.get("regressions") or []
    ]
    regressions.sort(key=lambda row: (row["id"], row["generation"]))
    return {
        "area": str(area.get("area") or ""),
        "regressions": regressions,
    }


def _fingerprint(area: dict) -> str:
    return _hash_fingerprint_payload(_fingerprint_payload(area))


def _content_fingerprint(area: dict) -> str:
    """Hash mutable issue presentation without changing suppression identity."""
    compact = _legacy_fingerprint_payload(area)
    compact["source_file"] = area.get("source_file")
    compact["selected_owner"] = area.get("selected_owner")
    compact["actual_assignee"] = area.get("actual_assignee")
    compact["owners"] = [
        {
            "rank": row.get("rank"),
            "github_login": row.get("github_login"),
            "display_name": row.get("display_name"),
        }
        for row in area.get("owners") or []
    ]
    compact["regressions"] = [
        {
            "id": row.get("id"),
            "label": row.get("label"),
            "result": row.get("result"),
            "raw_result": row.get("raw_result"),
            "incident_observation_eligible": row.get(
                "incident_observation_eligible"
            ),
            "incident_severity": row.get("incident_severity"),
            "incident_peak_severity": row.get("incident_peak_severity"),
            "displayed_failure_evidence": _displayed_failure_evidence(row),
        }
        for row in area.get("regressions") or []
    ]
    compact["pending_soft_observations"] = [
        {
            "id": row.get("id"),
            "label": row.get("label"),
            "raw_result": row.get("raw_result"),
            "incident_observation_eligible": row.get(
                "incident_observation_eligible"
            ),
            "displayed_failure_evidence": _displayed_failure_evidence(row),
            "soft_streak": row.get("soft_streak"),
        }
        for row in area.get("pending_soft_observations") or []
    ]
    return _hash_fingerprint_payload(compact)


def _migrate_body_schema_state(
    state: dict,
    *,
    fingerprint: str,
    legacy_fingerprint: str,
) -> dict:
    """Migrate stable signal identity and force one open-body refresh."""
    normalized = _normalize_area_state(state)
    try:
        signal_fingerprint_version = int(
            normalized.get("signal_fingerprint_version") or 1
        )
    except (TypeError, ValueError):
        signal_fingerprint_version = 1
    if signal_fingerprint_version < SIGNAL_FINGERPRINT_VERSION:
        # Previous area fingerprints included the latest build and routing
        # decision, so they cannot be compared to the stable signal hash. Keep
        # manual closes suppressed through this one-way migration and bind
        # open issues to the current stable incident generation.
        if normalized["suppressed"]:
            normalized["suppressed_fingerprint"] = fingerprint
            normalized["last_fingerprint"] = fingerprint
        elif normalized.get("issue"):
            normalized["last_fingerprint"] = fingerprint
            normalized["last_content_fingerprint"] = ""
        normalized["signal_fingerprint_version"] = SIGNAL_FINGERPRINT_VERSION

    try:
        body_schema = int(normalized.get("body_schema_version") or 1)
    except (TypeError, ValueError):
        body_schema = 1
    if body_schema >= ISSUE_BODY_SCHEMA_VERSION:
        return normalized

    if normalized["suppressed"]:
        # Schema-v1 suppression predates the ordered notification chain. This
        # branch remains for state files that have not yet crossed the stable
        # fingerprint migration above.
        if (
            signal_fingerprint_version >= SIGNAL_FINGERPRINT_VERSION
            or normalized["suppressed_fingerprint"] == legacy_fingerprint
        ):
            normalized["suppressed_fingerprint"] = fingerprint
            if normalized["last_fingerprint"] == legacy_fingerprint:
                normalized["last_fingerprint"] = fingerprint
    elif normalized.get("issue"):
        # Body schema is intentionally separate from signal identity.
        normalized["last_content_fingerprint"] = ""
    normalized["body_schema_version"] = ISSUE_BODY_SCHEMA_VERSION
    return normalized


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


def _state_with_signals(managed: dict, signals: dict[str, dict]) -> dict:
    merged = _normalize_area_state(managed)
    merged["signals"] = signals
    merged["incident_state_version"] = 1
    return merged


def _current_area_state(managed: dict, signals: dict[str, dict]) -> dict:
    """Return current-area state with any prior retirement evidence cleared."""
    merged = _state_with_signals(managed, signals)
    merged.pop("retirement_streak", None)
    return merged


def _preserved_missing_area_states(
    prior_areas: dict[str, dict],
    current_area_keys: set[str],
    next_signals: dict[str, dict[str, dict]],
    *,
    complete_evidence: bool = False,
) -> dict[str, dict]:
    preserved: dict[str, dict] = {}
    for area_key, prior_area in prior_areas.items():
        normalized_key = str(area_key)
        if not isinstance(prior_area, dict) or normalized_key in current_area_keys:
            continue
        area_state = _state_with_signals(
            prior_area,
            next_signals.get(
                normalized_key,
                (prior_area.get("signals") or {}),
            ),
        )
        if complete_evidence:
            area_state["retirement_streak"] = min(
                int(area_state.get("retirement_streak") or 0) + 1,
                AREA_RETIREMENT_STREAK_REQUIRED,
            )
        preserved[normalized_key] = area_state
    return preserved


def _retire_area_issue(
    area_key: str,
    area_state: dict,
    client: GitHubIssueClient,
    run_url: str,
) -> bool:
    """Close every exact-marker-owned issue before pruning retired area state."""
    marker = f"<!-- {OWNERSHIP_MARKER_PREFIX}:{area_key}:v1 -->"
    tracked_number = int(((area_state.get("issue") or {}).get("number") or 0))
    find_open_issues = getattr(client, "find_open_issues", None)
    if not callable(find_open_issues):
        log.warning("Cannot verify retired area %s without marker-owned issue lookup", area_key)
        return False
    try:
        raw_open_numbers = find_open_issues(marker)
    except Exception as error:
        log.warning("Retired area %s issue lookup failed: %s", area_key, error)
        return False
    if not isinstance(raw_open_numbers, (list, tuple, set)):
        log.warning("Retired area %s issue lookup returned invalid data", area_key)
        return False
    open_numbers: set[int] = set()
    for number in raw_open_numbers:
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            log.warning("Retired area %s issue lookup returned an invalid number", area_key)
            return False
        open_numbers.add(number)
    if tracked_number:
        open_numbers.add(tracked_number)

    for number in sorted(open_numbers):
        try:
            remote_state = client.issue_state(number, marker)
        except Exception as error:
            log.warning("Retired area %s issue #%d verification failed: %s", area_key, number, error)
            return False
        if remote_state == "foreign":
            if number == tracked_number:
                log.error(
                    "Tracked issue #%d for retired area %s lacks its exact marker; preserving state",
                    number,
                    area_key,
                )
                return False
            continue
        if remote_state == "closed":
            continue
        if remote_state != "open":
            log.warning(
                "Retired area %s issue #%d state is unverified; preserving state",
                area_key,
                number,
            )
            return False
        comment_issue = getattr(client, "comment_issue", None)
        if callable(comment_issue):
            try:
                comment_issue(
                    number,
                    f"This CI test area was absent from {AREA_RETIREMENT_STREAK_REQUIRED} "
                    "consecutive complete current ownership projections. Closing its "
                    f"retired-area issue.\n\n*{run_url}*",
                )
            except Exception as error:
                log.warning("Comment on retiring area issue #%d failed: %s", number, error)
        try:
            closed = client.close_issue(number)
        except Exception as error:
            log.warning("Close retiring area issue #%d failed: %s", number, error)
            return False
        if not closed:
            log.warning("Close retiring area issue #%d failed; preserving state", number)
            return False
        log.info("Closed issue #%d for retired CI area %s", number, area_key)
    return True


def _prune_retired_area_states(
    area_states: dict[str, dict],
    client: GitHubIssueClient,
    run_url: str,
) -> set[str]:
    retired: set[str] = set()
    for area_key in sorted(list(area_states)):
        area_state = area_states[area_key]
        streak = int(area_state.get("retirement_streak") or 0)
        if streak < AREA_RETIREMENT_STREAK_REQUIRED:
            continue
        if not _retire_area_issue(area_key, area_state, client, run_url):
            continue
        area_states.pop(area_key, None)
        retired.add(area_key)
        log.info(
            "Pruned retired CI area %s after %d complete observations",
            area_key,
            streak,
        )
    return retired


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
        config["owners"],
        working_hours_profiles=config.get("working_hours_profiles"),
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
    current_area_keys = _complete_current_area_keys(status, config)
    if current_area_keys is None:
        log.error("CI area evidence is incomplete; refusing issue mutations or retirement progress")
        _mark_unavailable_status(now, "ci_area_evidence_incomplete")
        return 0
    state = _read_state()
    prior_areas = state.get("areas") or {}
    next_signals = apply_incident_hysteresis(status, prior_areas)

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        log.warning("GITHUB_TOKEN not set; writing dry-run ownership status without issue mutations")
        status["available"] = True
        status["issue_mutations"] = "disabled_missing_token"
        dry_run_areas = _preserved_missing_area_states(
            prior_areas,
            current_area_keys,
            next_signals,
            complete_evidence=True,
        )
        dry_run_areas.update(
            {
                area_key: _current_area_state(
                    prior_areas.get(area_key) or {},
                    signals,
                )
                for area_key, signals in next_signals.items()
            }
        )
        _checkpoint_state(dry_run_areas, observed_at)
        _write_json(STATUS, status)
        return 0

    client = GitHubIssueClient(token, repo)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = (
        f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else f"https://github.com/{repo}"
    )
    next_areas = _preserved_missing_area_states(
        prior_areas,
        current_area_keys,
        next_signals,
        complete_evidence=True,
    )
    checkpoint_areas = {**prior_areas, **next_areas}
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
            preserved = _current_area_state(
                prior_areas.get(area_key) or {},
                next_signals.get(area_key) or {},
            )
            checkpoint_areas[area_key] = preserved
            next_areas[area_key] = preserved
            area["issue_mutations"] = "skipped_no_verified_assignee"
            _attach_issue(area, preserved, repo)
            _checkpoint_state(checkpoint_areas, observed_at)
            continue
        assignees = [actual["github_login"]] if actual else None
        marker = f"<!-- {OWNERSHIP_MARKER_PREFIX}:{area_key}:v1 -->"
        fingerprint = _fingerprint(area)
        prior_area = _migrate_body_schema_state(
            prior_areas.get(area_key) or {},
            fingerprint=fingerprint,
            legacy_fingerprint=_legacy_fingerprint(area),
        )
        prior_area = _current_area_state(
            prior_area,
            next_signals.get(area_key) or {},
        )
        reconciled = reconcile_managed_issue(
            prior_area,
            active=active,
            fingerprint=fingerprint,
            content_fingerprint=_content_fingerprint(area),
            title=_issue_title(area),
            body=_issue_body(area, run_url),
            ownership_marker=marker,
            recovery_body=(
                f"All confirmed AMD runtime incidents for `{area['source_file']}` have "
                "recovered. Closing this tracked test-area issue.\n\n"
                f"*{run_url}*"
            ),
            observed_at=observed_at,
            label_specs=[
                ("automated", "6f42c1", "Managed by dashboard automation"),
                ("amd-ci-regression", "d73a49", "Confirmed AMD CI target incident"),
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
        reconciled["body_schema_version"] = ISSUE_BODY_SCHEMA_VERSION
        reconciled["signals"] = next_signals.get(area_key) or {}
        reconciled["incident_state_version"] = 1
        next_areas[area_key] = reconciled
        checkpoint_areas[area_key] = reconciled
        _attach_issue(area, reconciled, repo)
        _checkpoint_state(checkpoint_areas, observed_at)

    retired_areas = _prune_retired_area_states(next_areas, client, run_url)
    for area_key in retired_areas:
        checkpoint_areas.pop(area_key, None)

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
