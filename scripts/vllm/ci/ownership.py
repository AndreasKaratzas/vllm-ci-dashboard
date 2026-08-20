"""Ranked CI ownership, working-hours routing, and test-area attribution.

The ownership configuration contains names, GitHub logins, ranked test-area
chains, and shared regional working-hours profiles. Missing or invalid schedules
fail closed to the configured CI lead.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vllm.collect_gating_target_candidates import hardware_fold_key
from vllm.ci.incident_transitions import advance_incident


INCIDENT_STATES = {"failed", "hard", "soft", "soft_fail", "soft_failed"}
PASS_STATES = {"passed", "pass"}
MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
SHARD_TEMPLATE_SUFFIX_RE = re.compile(r"\s*%N\s*$", re.IGNORECASE)
GITHUB_LOGIN_RE = re.compile(
    r"(?=.{1,39}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\Z"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_area(value: Any) -> str:
    area = Path(str(value or "").strip()).name.lower()
    if area.endswith((".yaml", ".yml")):
        area = area.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "_", area).strip("_")


def normalize_label(value: Any) -> str:
    label = AMD_PREFIX_RE.sub("", str(value or "").strip())
    return MULTISPACE_RE.sub(" ", label).casefold()


def label_keys(value: Any) -> set[str]:
    label = SHARD_TEMPLATE_SUFFIX_RE.sub("", str(value or "").strip())
    return {
        key
        for key in (
            normalize_label(label),
            normalize_label(hardware_fold_key(label)),
        )
        if key
    }


def _normalize_working_hours_profile(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Working-hours profile {name!r} must be an object")
    zone_name = str(raw.get("timezone") or "").strip()
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"Working-hours profile {name!r} has invalid timezone {zone_name!r}"
        ) from None
    weekdays = raw.get("weekdays")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(
            not isinstance(day, int)
            or isinstance(day, bool)
            or day not in range(7)
            for day in weekdays
        )
        or len(set(weekdays)) != len(weekdays)
    ):
        raise ValueError(
            f"Working-hours profile {name!r} requires unique weekdays from 0 to 6"
        )
    start = str(raw.get("start") or "")
    end = str(raw.get("end") or "")
    if _parse_clock(start) is None or _parse_clock(end) is None or start == end:
        raise ValueError(
            f"Working-hours profile {name!r} requires distinct HH:MM start/end"
        )
    return {
        "timezone": zone_name,
        "working_hours": {
            "weekdays": list(weekdays),
            "start": start,
            "end": end,
        },
    }


def load_ownership_config(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load ownership config {path}: {error}") from error
    return validate_ownership_config(payload)


def validate_ownership_config(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Ownership config must be a schema_version=1 object")

    lead = payload.get("ci_lead")
    if not isinstance(lead, dict):
        raise ValueError("Ownership config requires ci_lead")
    lead_login = str(lead.get("github_login") or "").strip()
    lead_name = str(lead.get("display_name") or "").strip()
    if not lead_login or not lead_name:
        raise ValueError("ci_lead requires display_name and github_login")
    if not GITHUB_LOGIN_RE.fullmatch(lead_login):
        raise ValueError(f"ci_lead has invalid GitHub login {lead_login!r}")

    policy = payload.get("policy")
    if not isinstance(policy, dict) or policy.get("mentions") is not True:
        raise ValueError("Ownership policy must enable GitHub mentions")

    raw_profiles = payload.get("working_hours_profiles") or {}
    if not isinstance(raw_profiles, dict):
        raise ValueError("working_hours_profiles must be an object")
    working_hours_profiles: dict[str, dict[str, Any]] = {}
    for raw_name, raw_profile in raw_profiles.items():
        name = str(raw_name or "").strip().upper()
        if not name or name in working_hours_profiles:
            raise ValueError(f"Duplicate or invalid working-hours profile {raw_name!r}")
        working_hours_profiles[name] = _normalize_working_hours_profile(
            name,
            raw_profile,
        )

    raw_owners = payload.get("owners")
    if not isinstance(raw_owners, list) or not raw_owners:
        raise ValueError("Ownership config requires a non-empty owners list")
    owners: list[dict[str, str]] = []
    owners_by_login: dict[str, dict[str, str]] = {}
    for row in raw_owners:
        if not isinstance(row, dict):
            raise ValueError("Every owner must be an object")
        login = str(row.get("github_login") or "").strip()
        name = str(row.get("display_name") or "").strip()
        if not login or not name:
            raise ValueError("Every owner requires display_name and github_login")
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise ValueError(f"Owner has invalid GitHub login {login!r}")
        folded = login.casefold()
        if folded in owners_by_login:
            raise ValueError(f"Duplicate owner login: {login}")
        profile = str(row.get("working_hours_profile") or "").strip().upper()
        if working_hours_profiles and not profile:
            raise ValueError(f"Owner {login!r} requires working_hours_profile")
        if profile and profile not in working_hours_profiles:
            raise ValueError(
                f"Owner {login!r} references unknown working-hours profile {profile!r}"
            )
        normalized = {
            "github_login": login,
            "display_name": name,
            **({"working_hours_profile": profile} if profile else {}),
        }
        owners.append(normalized)
        owners_by_login[folded] = normalized
    if lead_login.casefold() not in owners_by_login:
        raise ValueError("ci_lead must also be present in owners")

    raw_areas = payload.get("areas")
    if not isinstance(raw_areas, dict) or not raw_areas:
        raise ValueError("Ownership config requires a non-empty areas object")
    areas: dict[str, list[dict[str, Any]]] = {}
    for raw_area, raw_chain in raw_areas.items():
        area = normalize_area(raw_area)
        if not area or area in areas:
            raise ValueError(f"Duplicate or invalid area: {raw_area}")
        if not isinstance(raw_chain, list):
            raise ValueError(f"{raw_area} owner chain must be a list")
        chain: list[dict[str, Any]] = []
        for raw_owner in raw_chain:
            if not isinstance(raw_owner, dict):
                raise ValueError(f"{raw_area} owner entries must be objects")
            rank = raw_owner.get("rank")
            login = str(raw_owner.get("github_login") or "").strip()
            if rank not in {1, 2, 3}:
                raise ValueError(f"{raw_area} has invalid rank {rank!r}")
            owner = owners_by_login.get(login.casefold())
            if owner is None:
                raise ValueError(f"{raw_area} references unknown owner {login!r}")
            chain.append(
                {
                    "rank": int(rank),
                    "github_login": owner["github_login"],
                    "display_name": owner["display_name"],
                }
            )
        chain.sort(key=lambda row: row["rank"])
        ranks = [row["rank"] for row in chain]
        if not 1 <= len(chain) <= 3:
            raise ValueError(f"{raw_area} must define between one and three owners")
        if len(set(ranks)) != len(ranks):
            raise ValueError(f"{raw_area} must use distinct ranks")
        if len({row["github_login"].casefold() for row in chain}) != len(chain):
            raise ValueError(f"{raw_area} must have distinct owners")
        areas[area] = chain

    area_aliases: dict[str, str] = {}
    for raw_alias, raw_target in (payload.get("area_aliases") or {}).items():
        alias = normalize_area(raw_alias)
        target = normalize_area(raw_target)
        if not alias or target not in areas:
            raise ValueError(f"Invalid area alias {raw_alias!r} -> {raw_target!r}")
        area_aliases[alias] = target

    target_area_overrides: dict[str, str] = {}
    for raw_label, raw_target in (payload.get("target_area_overrides") or {}).items():
        target = normalize_area(raw_target)
        if target not in areas:
            raise ValueError(f"Invalid target-area override {raw_label!r} -> {raw_target!r}")
        for key in label_keys(raw_label):
            prior = target_area_overrides.get(key)
            if prior and prior != target:
                raise ValueError(f"Conflicting target-area override for {raw_label!r}")
            target_area_overrides[key] = target

    raw_project = payload.get("project")
    if not isinstance(raw_project, dict):
        raise ValueError("Ownership config requires one linked GitHub project")
    project: dict[str, Any] = {
        "id": str(raw_project.get("id") or "").strip(),
        "number": raw_project.get("number"),
        "title": str(raw_project.get("title") or "").strip(),
        "url": str(raw_project.get("url") or "").strip(),
        "repository": str(raw_project.get("repository") or "").strip(),
    }
    if (
        not project["id"]
        or not isinstance(project["number"], int)
        or isinstance(project["number"], bool)
        or project["number"] <= 0
        or not project["title"]
        or not project["url"].startswith("https://github.com/")
        or project["repository"].casefold() != "andreaskaratzas/vllm-ci-dashboard"
    ):
        raise ValueError("Ownership project must be linked to the dashboard repository")

    return {
        **payload,
        "ci_lead": owners_by_login[lead_login.casefold()],
        "owners": owners,
        "areas": dict(sorted(areas.items())),
        "area_aliases": dict(sorted(area_aliases.items())),
        "target_area_overrides": dict(sorted(target_area_overrides.items())),
        "working_hours_profiles": dict(sorted(working_hours_profiles.items())),
        "project": project,
    }


def _parse_clock(value: Any) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _within_working_hours(now: datetime, record: dict) -> bool | None:
    zone_name = str(record.get("timezone") or "").strip()
    schedule = record.get("working_hours")
    if not zone_name or not isinstance(schedule, dict):
        return None
    try:
        local = now.astimezone(ZoneInfo(zone_name))
    except ZoneInfoNotFoundError:
        return None
    weekdays = schedule.get("weekdays")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(not isinstance(day, int) or isinstance(day, bool) or day not in range(7) for day in weekdays)
    ):
        return None
    start = _parse_clock(schedule.get("start"))
    end = _parse_clock(schedule.get("end"))
    if start is None or end is None or start == end:
        return None
    clock = (local.hour, local.minute)
    allowed_days = set(weekdays)
    if start < end:
        return local.weekday() in allowed_days and start <= clock < end
    if clock >= start:
        return local.weekday() in allowed_days
    if clock < end:
        return (local.weekday() - 1) % 7 in allowed_days
    return False


def evaluate_availability(
    owners: list[dict],
    *,
    working_hours_profiles: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    observed = (now or utc_now()).astimezone(timezone.utc)
    profiles = working_hours_profiles or {}
    unknown = {
        owner["github_login"].casefold(): {
            "status": "unknown",
            "reason": "working_hours_unconfigured",
        }
        for owner in owners
    }
    if not profiles:
        return unknown, {
            "configured": False,
            "fresh": False,
            "reason": "working_hours_unconfigured",
            "generated_at": "",
        }
    source = {
        "configured": True,
        "fresh": True,
        "reason": "working_hours_profiles",
        "generated_at": "",
    }

    evaluated: dict[str, dict[str, str]] = {}
    for owner in owners:
        login = owner["github_login"]
        profile_name = str(owner.get("working_hours_profile") or "").upper()
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            evaluated[login.casefold()] = {
                "status": "unknown",
                "reason": "working_hours_profile_missing",
            }
            continue
        in_hours = _within_working_hours(observed, profile)
        if in_hours is None:
            evaluated[login.casefold()] = {
                "status": "unknown",
                "reason": "schedule_invalid",
            }
        elif not in_hours:
            evaluated[login.casefold()] = {
                "status": "unavailable",
                "reason": "outside_working_hours",
            }
        else:
            evaluated[login.casefold()] = {
                "status": "available",
                "reason": "within_working_hours",
            }

    if any(record["status"] == "unknown" for record in evaluated.values()):
        source.update({
            "fresh": False,
            "reason": "working_hours_profiles_invalid",
        })

    return evaluated, source


def select_owner(
    chain: list[dict],
    availability: dict[str, dict[str, str]],
    ci_lead: dict,
) -> dict[str, Any]:
    evaluated_chain: list[dict[str, Any]] = []
    for owner in sorted(chain, key=lambda row: row["rank"]):
        status = availability.get(
            owner["github_login"].casefold(),
            {"status": "unknown", "reason": "working_hours_profile_missing"},
        )
        row = {**owner, "availability": status["status"]}
        evaluated_chain.append(row)
    selected = next(
        (owner for owner in evaluated_chain if owner["availability"] == "available"),
        None,
    )
    if selected is not None:
        return {
            "owner": {
                key: selected[key]
                for key in ("rank", "github_login", "display_name")
            },
            "reason": f"rank_{selected['rank']}_selected",
            "escalated_to_ci_lead": False,
            "chain": [
                {
                    key: owner[key]
                    for key in ("rank", "github_login", "display_name")
                }
                for owner in evaluated_chain
            ],
        }
    return {
        "owner": {
            **ci_lead,
            "rank": None,
        },
        "reason": "no_ranked_owner_selected",
        "escalated_to_ci_lead": True,
        "chain": [
            {
                key: owner[key]
                for key in ("rank", "github_login", "display_name")
            }
            for owner in evaluated_chain
        ],
    }


def source_area(value: Any) -> str:
    source = str(value or "").strip()
    if not source:
        return ""
    return normalize_area(Path(source).name)


def parity_area_index(
    parity: dict,
    known_areas: set[str],
    area_aliases: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    aliases = area_aliases or {}

    def add(label: Any, source: Any) -> None:
        area = aliases.get(source_area(source), source_area(source))
        if area not in known_areas:
            return
        for key in label_keys(label):
            index.setdefault(key, set()).add(area)

    for row in parity.get("matches") or []:
        if not isinstance(row, dict):
            continue
        source = row.get("nvidia_source") or row.get("source_file")
        add(row.get("amd_label"), source)
        add(row.get("nvidia_label"), source)
    for row in parity.get("nvidia_only") or []:
        if isinstance(row, dict):
            add(row.get("label"), row.get("source"))
    for row in parity.get("mirrors") or []:
        if isinstance(row, dict):
            add(row.get("nvidia_label"), row.get("source_file"))
    return index


def infer_target_area(
    target: dict,
    parity_index: dict[str, set[str]],
    known_areas: set[str],
    target_area_overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    labels = [target.get("label")]
    resolution = target.get("runtime_resolution")
    if isinstance(resolution, dict):
        labels.extend(resolution.get("amd_definition_labels") or [])
    candidates: set[str] = set()
    for label in labels:
        for key in label_keys(label):
            candidates.update(parity_index.get(key, set()))
    if len(candidates) == 1:
        return next(iter(candidates)), "definition_parity"
    if len(candidates) > 1:
        return "", "ambiguous_definition_area"
    overrides = target_area_overrides or {}
    override_candidates = {
        overrides[key]
        for label in labels
        for key in label_keys(label)
        if key in overrides
    }
    if len(override_candidates) == 1:
        return next(iter(override_candidates)), "reviewed_area_override"
    if len(override_candidates) > 1:
        return "", "ambiguous_area_override"
    return "", "area_unmapped"


def target_result(target: dict) -> str:
    latest = target.get("latest_amd_result")
    state = str((latest or {}).get("state") or "unknown").lower()
    if state in INCIDENT_STATES:
        return "soft" if state.startswith("soft") else "hard"
    if state in PASS_STATES:
        return "passed"
    return "unobserved"


def matrix_runtime_targets(matrix: dict) -> list[dict]:
    """Project every exact matrix definition into the ownership target shape."""
    generated_at = str(matrix.get("generated_at") or "")
    build_observed_at = str(
        (matrix.get("source") or {}).get("latest_build_created_at")
        or generated_at
    )
    latest_build = (matrix.get("summary") or {}).get("latest_build_number")
    definitions = [row for row in matrix.get("rows") or [] if isinstance(row, dict)]
    title_counts = Counter(str(row.get("title") or "unknown") for row in definitions)
    projected: list[dict] = []
    for row in definitions:
        evidence: list[dict] = []
        labels: list[str] = []
        states: list[str] = []
        for architecture, cell in (row.get("cells") or {}).items():
            if not isinstance(cell, dict) or not cell.get("exists"):
                continue
            state = str(cell.get("latest_state") or "unknown").lower()
            states.append(state)
            label = str(cell.get("primary_label") or "")
            if label:
                labels.append(label)
            url = str(cell.get("latest_url") or "")
            if url:
                evidence.append(
                    {
                        "architecture": str(architecture),
                        "state": state,
                        "url": url,
                    }
                )
        if any(state in {"failed", "hard"} for state in states):
            state = "hard"
        elif any(state in {"soft", "soft_fail", "soft_failed"} for state in states):
            state = "soft"
        elif any(state in PASS_STATES for state in states):
            state = "passed"
        else:
            state = "unknown"
        evidence.sort(
            key=lambda item: (
                {"failed": 0, "hard": 0, "soft": 1, "soft_fail": 1, "passed": 2}.get(
                    item["state"],
                    3,
                ),
                item["architecture"],
            )
        )
        raw_title = str(row.get("title") or "unknown")
        signature = str(row.get("signature") or "")
        display_title = (
            f"{raw_title} [{signature}]"
            if title_counts[raw_title] > 1 and signature
            else raw_title
        )
        projected.append(
            {
                "id": row.get("id"),
                "label": display_title,
                "area": str(row.get("area") or ""),
                "latest_amd_result": {
                    "state": state,
                    "build_number": latest_build,
                    # Evidence identity must remain stable when the same completed
                    # build is collected again.  ``generated_at`` is collector
                    # freshness, not the time of the observed build.
                    "observed_at": build_observed_at,
                    "evidence": evidence,
                },
                "runtime_resolution": {
                    "status": "matched" if evidence else "not_observed",
                    "amd_definition_labels": sorted(
                        set(labels),
                        key=str.casefold,
                    ),
                },
            }
        )
    return projected


def upstream_parity_gaps(
    parity: dict,
    known_areas: set[str],
    area_aliases: dict[str, str] | None = None,
) -> dict[str, list[dict]]:
    gaps: dict[str, list[dict]] = {area: [] for area in known_areas}
    aliases = area_aliases or {}
    for row in parity.get("nvidia_only") or []:
        if not isinstance(row, dict):
            continue
        source = source_area(row.get("source"))
        area = aliases.get(source, source)
        if area in gaps:
            gaps[area].append(
                {
                    "label": str(row.get("label") or "unknown"),
                    "url": str(row.get("source_url") or ""),
                }
            )
    for rows in gaps.values():
        rows.sort(key=lambda row: row["label"].casefold())
    return gaps


def build_ownership_status(
    gating: dict,
    parity: dict,
    config: dict,
    availability: dict[str, dict[str, str]],
    availability_source: dict,
    *,
    generated_at: str,
    attribution_parity: dict | None = None,
) -> dict:
    known_areas = set(config["areas"])
    parity_index = parity_area_index(
        attribution_parity or parity,
        known_areas,
        config.get("area_aliases") or {},
    )
    parity_gaps = upstream_parity_gaps(
        parity,
        known_areas,
        config.get("area_aliases") or {},
    )
    grouped: dict[str, list[dict]] = {area: [] for area in known_areas}
    unmapped: list[dict] = []
    targets = gating.get("active_target_groups") or []
    for target in targets:
        if not isinstance(target, dict):
            continue
        area, method = infer_target_area(
            target,
            parity_index,
            known_areas,
            config.get("target_area_overrides") or {},
        )
        row = {
            "id": target.get("id"),
            "label": str(target.get("label") or "unknown"),
            "result": target_result(target),
            "build_number": (target.get("latest_amd_result") or {}).get("build_number"),
            "observed_at": str((target.get("latest_amd_result") or {}).get("observed_at") or ""),
            "url": str(
                next(
                    (
                        evidence.get("url")
                        for evidence in (target.get("latest_amd_result") or {}).get("evidence") or []
                        if isinstance(evidence, dict) and evidence.get("url")
                    ),
                    "",
                )
            ),
            "area_method": method,
        }
        if area:
            grouped[area].append(row)
        else:
            unmapped.append(row)

    area_rows: list[dict] = []
    for area in sorted(known_areas):
        chain = config["areas"][area]
        selection = select_owner(chain, availability, config["ci_lead"])
        rows = sorted(
            grouped[area],
            key=lambda row: (
                {"hard": 0, "soft": 1, "unobserved": 2, "passed": 3}.get(row["result"], 4),
                row["label"].casefold(),
            ),
        )
        counts = {
            status: sum(row["result"] == status for row in rows)
            for status in ("hard", "soft", "unobserved", "passed")
        }
        area_rows.append(
            {
                "area": area,
                "source_file": f"{area}.yaml",
                "owners": selection["chain"],
                "selected_owner": selection["owner"],
                "selection_reason": selection["reason"],
                "escalated_to_ci_lead": selection["escalated_to_ci_lead"],
                "counts": {
                    "targets": len(rows),
                    "incidents": counts["hard"] + counts["soft"],
                    **counts,
                    "upstream_parity_gaps": len(parity_gaps[area]),
                },
                "regressions": [
                    row for row in rows if row["result"] in {"hard", "soft"}
                ],
                "targets": rows,
                "upstream_parity_gaps": parity_gaps[area],
                "issue": None,
                "actual_assignee": None,
                "assignment_reason": "not_reconciled",
            }
        )

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "available": True,
        "policy": {
            "issue_grain": "one state-owned issue per test area",
            "assignment": "first available owner by ascending rank; otherwise CI lead",
            "availability": "regional working hours only; missing or invalid schedules fail closed",
            "mentions": "selected owner and verified assignee tagged once; remaining ranked owners CCed once",
            "repository": "AndreasKaratzas/vllm-ci-dashboard",
            "workstream": "dev",
        },
        "availability": availability_source,
        "sources": {
            "runtime_commit": str(
                (((attribution_parity or parity).get("source") or {}).get("commit_sha"))
                or ""
            ),
            "current_parity_commit": str(
                ((parity.get("source") or {}).get("commit_sha")) or ""
            ),
        },
        "ci_lead": config["ci_lead"],
        "project": config["project"],
        "summary": {
            "areas": len(area_rows),
            "areas_with_incidents": sum(row["counts"]["incidents"] > 0 for row in area_rows),
            "incidents": sum(row["counts"]["incidents"] for row in area_rows),
            "hard": sum(row["counts"]["hard"] for row in area_rows),
            "soft": sum(row["counts"]["soft"] for row in area_rows),
            "unobserved": sum(row["counts"]["unobserved"] for row in area_rows),
            "upstream_parity_gaps": sum(
                row["counts"]["upstream_parity_gaps"] for row in area_rows
            ),
            "unmapped_targets": len(unmapped),
        },
        "areas": area_rows,
        "unmapped_targets": sorted(unmapped, key=lambda row: row["label"].casefold()),
    }


def _target_signal_key(target: dict) -> str:
    target_id = str(target.get("id") or "").strip()
    if target_id:
        return target_id
    return f"label:{normalize_label(target.get('label'))}"


def _target_failure_evidence(target: dict) -> dict:
    return {
        key: target.get(key)
        for key in ("build_number", "observed_at", "url")
        if target.get(key) not in (None, "")
    }


def _numeric_build_id(value: Any) -> int | None:
    """Return a comparable Buildkite build number when one is available."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None


def _signal_build_watermark(signal: dict | None) -> int | None:
    if not isinstance(signal, dict):
        return None
    for key in ("build_watermark", "last_eligible_build_id"):
        watermark = _numeric_build_id(signal.get(key))
        if watermark is not None:
            return watermark
    evidence = signal.get("evidence")
    if isinstance(evidence, dict):
        return _numeric_build_id(evidence.get("build_number"))
    return None


def _target_identity(target: dict, signal_key: str) -> dict:
    identity = {
        "id": target.get("id"),
        "label": str(target.get("label") or signal_key),
    }
    if identity["id"] in (None, ""):
        identity["id"] = signal_key
    area_method = target.get("area_method")
    if area_method not in (None, ""):
        identity["area_method"] = area_method
    return identity


def _retained_target(
    signal_key: str,
    signal: dict,
    *,
    build_number: Any,
    observed_at: str,
) -> dict:
    identity = signal.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    return {
        "id": identity.get("id") or signal_key,
        "label": str(identity.get("label") or signal_key),
        "result": "unobserved",
        "build_number": build_number,
        "observed_at": observed_at,
        "url": "",
        "area_method": identity.get("area_method") or "retained_incident_identity",
        "target_disappeared": True,
    }


def apply_incident_hysteresis(
    status: dict,
    prior_areas: dict[str, dict],
    *,
    soft_threshold: int = 2,
) -> dict[str, dict[str, dict]]:
    """Annotate raw ownership results with confirmed incident state.

    Hard observations confirm immediately. Soft observations remain visible as
    pending until they recur on ``soft_threshold`` distinct completed builds.
    Re-reading the same build cannot advance the streak, and an unobserved
    target holds (rather than resolving) its prior incident state.

    The returned mapping is deliberately separate from the public status. It
    is persisted alongside each area's managed-issue state and supplied on the
    next watcher run.
    """
    next_signals: dict[str, dict[str, dict]] = {}
    summary_raw = Counter()
    summary_confirmed = Counter()
    areas_with_pending = 0

    for area in status.get("areas") or []:
        if not isinstance(area, dict):
            continue
        area_key = str(area.get("area") or "")
        prior_area = prior_areas.get(area_key) or {}
        raw_prior_signals = prior_area.get("signals") or {}
        prior_signals = {
            str(signal_key): dict(signal)
            for signal_key, signal in raw_prior_signals.items()
            if isinstance(signal, dict)
        } if isinstance(raw_prior_signals, dict) else {}
        grandfather_soft_incidents = not prior_signals and bool(
            prior_area.get("issue") or prior_area.get("suppressed")
        )

        current_targets = [
            dict(target)
            for target in area.get("targets") or []
            if isinstance(target, dict)
        ]
        area_observations = [
            (
                build_id,
                target.get("build_number"),
                str(target.get("observed_at") or ""),
            )
            for target in current_targets
            if (build_id := _numeric_build_id(target.get("build_number")))
            is not None
        ]
        if area_observations:
            _, area_build_number, area_observed_at = max(
                area_observations,
                key=lambda row: row[0],
            )
        else:
            area_build_number = None
            area_observed_at = next(
                (
                    str(target.get("observed_at") or "")
                    for target in current_targets
                    if target.get("observed_at")
                ),
                "",
            )

        area_signals: dict[str, dict] = {}
        confirmed: list[dict] = []
        pending_soft: list[dict] = []
        raw_counts = Counter()
        annotated_targets: list[dict] = []
        seen_signal_keys: set[str] = set()

        def record_target(raw_target: dict) -> None:
            target = dict(raw_target)
            raw_result = str(target.get("result") or "unobserved").lower()
            raw_counts[raw_result] += 1
            summary_raw[raw_result] += 1
            outcome = {
                "hard": "hard",
                "soft": "soft",
                "passed": "passed",
                "unobserved": "absent",
            }.get(raw_result, "indeterminate")
            signal_key = _target_signal_key(target)
            seen_signal_keys.add(signal_key)
            previous_signal = area_signals.get(signal_key) or prior_signals.get(
                signal_key
            )
            previous_evidence = (
                previous_signal.get("evidence")
                if isinstance(previous_signal, dict)
                and isinstance(previous_signal.get("evidence"), dict)
                else {}
            )
            if (
                previous_signal is None
                and grandfather_soft_incidents
                and outcome == "soft"
            ):
                # Area issues created before per-target transition state already
                # represent confirmed incidents. Seed their current soft members
                # as confirmed so rollout cannot close a still-active issue after
                # relabeling a single current soft observation as pending.
                legacy_generation = f"legacy:{area_key}:{signal_key}"
                seeded = advance_incident(
                    None,
                    "soft",
                    f"{legacy_generation}:1",
                    soft_threshold=soft_threshold,
                )
                previous_signal = advance_incident(
                    seeded["state"],
                    "soft",
                    f"{legacy_generation}:2",
                    soft_threshold=soft_threshold,
                )["state"]

            previous_watermark = _signal_build_watermark(previous_signal)
            current_watermark = _numeric_build_id(target.get("build_number"))
            non_monotonic = outcome != "absent" and (
                previous_watermark is not None
                and (
                    current_watermark is None
                    or current_watermark <= previous_watermark
                )
            )
            if (
                current_watermark is None
                and previous_watermark is None
                and isinstance(previous_signal, dict)
                and previous_signal.get("status") in {"pending_soft", "confirmed"}
                and outcome != "absent"
            ):
                # An unnumbered observation cannot prove that it is newer than an
                # active incident.  Hold until a completed build ID is available.
                non_monotonic = True
            effective_outcome = "absent" if non_monotonic else outcome
            transition = advance_incident(
                previous_signal,
                effective_outcome,
                target.get("build_number") if not non_monotonic else None,
                soft_threshold=soft_threshold,
            )
            signal = transition["state"]
            evidence = (
                _target_failure_evidence(target)
                if not non_monotonic and outcome in {"hard", "soft"}
                else previous_evidence
            )
            persisted_signal = dict(signal)
            accepted_watermark = previous_watermark
            if current_watermark is not None and (
                accepted_watermark is None
                or current_watermark > accepted_watermark
            ):
                accepted_watermark = current_watermark
            if accepted_watermark is not None:
                persisted_signal["build_watermark"] = accepted_watermark
            previous_identity = (
                previous_signal.get("identity")
                if isinstance(previous_signal, dict)
                and isinstance(previous_signal.get("identity"), dict)
                else {}
            )
            identity = dict(previous_identity)
            if not target.get("target_disappeared") or not identity:
                identity.update(_target_identity(target, signal_key))
            persisted_signal["identity"] = identity
            if signal["status"] != "clear" and evidence:
                persisted_signal["evidence"] = dict(evidence)
                target["last_failure_evidence"] = dict(evidence)
            area_signals[signal_key] = persisted_signal
            target.update(
                {
                    "raw_result": raw_result,
                    "incident_status": signal["status"],
                    "incident_severity": signal.get("severity"),
                    "incident_peak_severity": signal.get("peak_severity"),
                    "incident_start_build_id": signal.get(
                        "incident_start_build_id"
                    ),
                    "incident_classification": transition["classification"],
                    "incident_change": transition["change"],
                    "incident_observation_eligible": not non_monotonic,
                    "soft_streak": signal.get("soft_streak", 0),
                    "soft_threshold": soft_threshold,
                }
            )
            if non_monotonic:
                target["incident_observation_reason"] = (
                    "ignored_non_monotonic_build"
                )
            annotated_targets.append(target)
            if signal["status"] == "confirmed":
                severity = str(signal.get("severity") or "soft")
                summary_confirmed[severity] += 1
                confirmed.append(target)
            elif signal["status"] == "pending_soft":
                pending_soft.append(target)

        for target in current_targets:
            record_target(target)

        # A missing row is absence, not recovery.  Keep active identity and exact
        # failure evidence visible until an explicit newer pass resolves it.
        for signal_key, previous_signal in prior_signals.items():
            if signal_key in seen_signal_keys:
                continue
            if previous_signal.get("status") in {"pending_soft", "confirmed"}:
                record_target(
                    _retained_target(
                        signal_key,
                        previous_signal,
                        build_number=area_build_number,
                        observed_at=area_observed_at,
                    )
                )
            else:
                area_signals[signal_key] = dict(previous_signal)

        confirmed.sort(
            key=lambda row: (
                0 if row.get("incident_severity") == "hard" else 1,
                str(row.get("label") or "").casefold(),
            )
        )
        pending_soft.sort(key=lambda row: str(row.get("label") or "").casefold())
        confirmed_counts = Counter(
            str(row.get("incident_severity") or "soft") for row in confirmed
        )
        counts = area.setdefault("counts", {})
        counts.update(
            {
                "targets": len(annotated_targets),
                "incidents": len(confirmed),
                "confirmed_hard": confirmed_counts["hard"],
                "confirmed_soft": confirmed_counts["soft"],
                "pending_soft": len(pending_soft),
                "raw_incidents": raw_counts["hard"] + raw_counts["soft"],
                "hard": raw_counts["hard"],
                "soft": raw_counts["soft"],
                "passed": raw_counts["passed"],
                "unobserved": raw_counts["unobserved"],
                "raw_results": {
                    result: raw_counts[result]
                    for result in ("hard", "soft", "unobserved", "passed")
                },
            }
        )
        area["targets"] = annotated_targets
        area["regressions"] = confirmed
        area["pending_soft_observations"] = pending_soft
        next_signals[area_key] = area_signals
        if pending_soft:
            areas_with_pending += 1

    # Configuration or attribution changes must not silently orphan managed
    # issue state for an area omitted from the current public projection.
    for raw_area_key, prior_area in prior_areas.items():
        area_key = str(raw_area_key)
        if area_key in next_signals or not isinstance(prior_area, dict):
            continue
        raw_signals = prior_area.get("signals") or {}
        next_signals[area_key] = {
            str(signal_key): dict(signal)
            for signal_key, signal in raw_signals.items()
            if isinstance(signal, dict)
        } if isinstance(raw_signals, dict) else {}

    summary = status.setdefault("summary", {})
    summary.update(
        {
            "areas_with_incidents": sum(
                bool((area.get("counts") or {}).get("incidents"))
                for area in status.get("areas") or []
                if isinstance(area, dict)
            ),
            "areas_with_pending_soft": areas_with_pending,
            "incidents": summary_confirmed["hard"] + summary_confirmed["soft"],
            "confirmed_hard": summary_confirmed["hard"],
            "confirmed_soft": summary_confirmed["soft"],
            "pending_soft": sum(
                len(area.get("pending_soft_observations") or [])
                for area in status.get("areas") or []
                if isinstance(area, dict)
            ),
            "raw_incidents": summary_raw["hard"] + summary_raw["soft"],
            "hard": summary_raw["hard"],
            "soft": summary_raw["soft"],
            "passed": summary_raw["passed"],
            "unobserved": summary_raw["unobserved"],
            "raw_results": {
                result: summary_raw[result]
                for result in ("hard", "soft", "unobserved", "passed")
            },
        }
    )
    status.setdefault("policy", {})["incident_confirmation"] = (
        f"hard observations confirm immediately; soft observations require "
        f"{soft_threshold} distinct completed builds; absent observations hold state; "
        "non-monotonic build observations cannot change incident state"
    )
    return next_signals
