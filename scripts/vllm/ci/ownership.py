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


INCIDENT_STATES = {"failed", "hard", "soft", "soft_fail", "soft_failed"}
PASS_STATES = {"passed", "pass"}
MULTISPACE_RE = re.compile(r"\s+")
AMD_PREFIX_RE = re.compile(r"^AMD:\s*", re.IGNORECASE)
SHARD_TEMPLATE_SUFFIX_RE = re.compile(r"\s*%N\s*$", re.IGNORECASE)


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
        if ranks != [1, 2, 3]:
            raise ValueError(f"{raw_area} must define exactly ranks 1, 2, and 3")
        if len({row["github_login"].casefold() for row in chain}) != 3:
            raise ValueError(f"{raw_area} must have three distinct owners")
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
                    "observed_at": generated_at,
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
            "mentions": "issue bodies never use @ mentions",
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
