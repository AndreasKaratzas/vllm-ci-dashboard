"""Pure incident-transition policy shared by dashboard producers and watchers.

Soft failures are observations, not confirmed incidents, until the same signal
soft-fails in two distinct eligible completed builds.  Hard failures confirm
immediately.  Only an explicit pass resolves confirmed or pending state;
absence and indeterminate observations preserve it.

The public API deliberately returns JSON-safe dictionaries so callers can
persist ``result["state"]`` without coupling their state files to Python class
serialization.
"""

from __future__ import annotations

from typing import Any, Mapping


SOFT_CONFIRMATION_BUILDS = 2
INCIDENT_TRANSITION_POLICY_ID = "confirmed-incidents-v1"
INCIDENT_OUTCOMES = frozenset({"hard", "soft", "passed", "absent", "indeterminate"})
COMPLETED_BUILD_STATES = frozenset({"passed", "failed"})

_HARD_ALIASES = frozenset({"hard", "failed", "failure", "timed_out", "broken", "canceled", "cancelled"})
_SOFT_ALIASES = frozenset({"soft", "soft_fail", "soft_failed"})
_PASS_ALIASES = frozenset({"pass", "passed", "success", "succeeded"})
_ABSENT_ALIASES = frozenset({"absent", "missing", "not_observed", "unobserved"})


def blank_incident_state() -> dict[str, Any]:
    """Return the canonical inactive state."""
    return {
        "status": "clear",
        "severity": None,
        "peak_severity": None,
        "soft_streak": 0,
        "last_eligible_build_id": None,
        "incident_start_build_id": None,
        "confirmed_build_id": None,
    }


def normalize_outcome(value: Any) -> str:
    """Normalize producer-specific result names to the policy vocabulary."""
    outcome = str(value or "").strip().lower()
    if outcome in _HARD_ALIASES:
        return "hard"
    if outcome in _SOFT_ALIASES:
        return "soft"
    if outcome in _PASS_ALIASES:
        return "passed"
    if outcome in _ABSENT_ALIASES:
        return "absent"
    return "indeterminate"


def completed_build_eligibility(build: Mapping[str, Any]) -> tuple[bool, str]:
    """Return whether a Buildkite row may advance incident state and why not."""
    if str(build.get("state") or "").strip().lower() not in COMPLETED_BUILD_STATES:
        return False, "build_state_not_completed"
    if not str(build.get("finished_at") or "").strip():
        return False, "finished_at_missing"
    return True, ""


def _has_build_id(value: Any) -> bool:
    return value not in (None, "") and not isinstance(value, bool)


def _same_build(left: Any, right: Any) -> bool:
    if not _has_build_id(left) or not _has_build_id(right):
        return False
    return str(left) == str(right)


def _legacy_severity(previous: Mapping[str, Any]) -> str:
    for key in ("severity", "peak_severity", "result", "state", "current_state"):
        outcome = normalize_outcome(previous.get(key))
        if outcome in {"hard", "soft"}:
            return outcome
    return "hard"


def _canonical_state(previous: Mapping[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return blank_incident_state()

    status = str(previous.get("status") or "")
    if status not in {"clear", "pending_soft", "confirmed"}:
        # Existing watcher ``active`` rows predate this policy and represent
        # confirmed incidents.  Treating them as such is a fail-safe migration:
        # they still require an explicit pass to resolve.
        status = "confirmed"

    severity = previous.get("severity")
    if severity not in {"hard", "soft"}:
        severity = _legacy_severity(previous) if status != "clear" else None
    peak = previous.get("peak_severity")
    if peak not in {"hard", "soft"}:
        peak = severity
    if severity == "hard":
        peak = "hard"

    try:
        soft_streak = max(0, int(previous.get("soft_streak") or 0))
    except (TypeError, ValueError, OverflowError):
        soft_streak = 0
    if status == "pending_soft" and "soft_streak" not in previous:
        soft_streak = 1
    if status == "clear":
        return blank_incident_state()

    start = previous.get("incident_start_build_id")
    if start in (None, ""):
        start = previous.get("bad_build_number")
    if start in (None, ""):
        start = previous.get("build_number")
    confirmed = previous.get("confirmed_build_id")
    if confirmed in (None, "") and status == "confirmed":
        confirmed = previous.get("build_number") or start

    return {
        "status": status,
        "severity": severity,
        "peak_severity": peak,
        "soft_streak": soft_streak,
        "last_eligible_build_id": previous.get("last_eligible_build_id"),
        "incident_start_build_id": start,
        "confirmed_build_id": confirmed,
    }


def advance_incident(
    previous: Mapping[str, Any] | None,
    outcome: Any,
    build_id: Any,
    *,
    soft_threshold: int = SOFT_CONFIRMATION_BUILDS,
) -> dict[str, Any]:
    """Advance one signal and return its state plus presentation classification.

    ``classification`` is one of the existing transition buckets plus
    ``pending_soft`` and ``none``.  ``change`` is more precise metadata for
    stateful consumers; callers that only render compatibility buckets can
    ignore it.
    """
    if isinstance(soft_threshold, bool) or not isinstance(soft_threshold, int) or soft_threshold < 2:
        raise ValueError("soft_threshold must be an integer of at least 2")

    state = _canonical_state(previous)
    normalized = normalize_outcome(outcome)
    status = state["status"]

    if normalized in {"absent", "indeterminate"}:
        if status == "confirmed":
            classification = "not_observed" if normalized == "absent" else "indeterminate"
        elif status == "pending_soft":
            classification = "pending_soft"
        else:
            classification = "none"
        return {
            "state": state,
            "classification": classification,
            "change": "held" if status != "clear" else "none",
            "outcome": normalized,
        }

    if normalized == "passed":
        if status == "confirmed":
            classification = "fixed"
            change = "resolved"
        elif status == "pending_soft":
            classification = "none"
            change = "pending_cleared"
        else:
            classification = "none"
            change = "none"
        return {
            "state": blank_incident_state(),
            "classification": classification,
            "change": change,
            "outcome": normalized,
        }

    if normalized == "hard":
        if status == "confirmed":
            change = "escalated" if state["severity"] == "soft" else "none"
            state.update({
                "severity": "hard",
                "peak_severity": "hard",
                "soft_streak": 0,
                "last_eligible_build_id": build_id,
            })
            classification = "recurring"
        else:
            start = state["incident_start_build_id"] or build_id
            state.update({
                "status": "confirmed",
                "severity": "hard",
                "peak_severity": "hard",
                "soft_streak": 0,
                "last_eligible_build_id": build_id,
                "incident_start_build_id": start,
                "confirmed_build_id": build_id,
            })
            classification = "new"
            change = "confirmed"
        return {
            "state": state,
            "classification": classification,
            "change": change,
            "outcome": normalized,
        }

    # The remaining eligible outcome is soft.
    if status == "confirmed":
        change = "deescalated" if state["severity"] == "hard" else "none"
        state.update({
            "severity": "soft",
            "last_eligible_build_id": build_id,
        })
        return {
            "state": state,
            "classification": "recurring",
            "change": change,
            "outcome": normalized,
        }

    if status == "pending_soft":
        distinct = _has_build_id(build_id) and not _same_build(
            state["last_eligible_build_id"], build_id
        )
        streak = state["soft_streak"] + int(distinct)
        state.update({
            "soft_streak": streak,
            "last_eligible_build_id": build_id if distinct else state["last_eligible_build_id"],
            "incident_start_build_id": state["incident_start_build_id"] or (
                build_id if distinct else None
            ),
        })
        if streak >= soft_threshold:
            state.update({
                "status": "confirmed",
                "severity": "soft",
                "peak_severity": state["peak_severity"] or "soft",
                "confirmed_build_id": build_id,
            })
            return {
                "state": state,
                "classification": "new",
                "change": "confirmed",
                "outcome": normalized,
            }
        return {
            "state": state,
            "classification": "pending_soft",
            "change": "pending_advanced" if distinct else "held",
            "outcome": normalized,
        }

    state.update({
        "status": "pending_soft",
        "severity": "soft",
        "peak_severity": "soft",
        "soft_streak": int(_has_build_id(build_id)),
        "last_eligible_build_id": build_id if _has_build_id(build_id) else None,
        "incident_start_build_id": build_id if _has_build_id(build_id) else None,
        "confirmed_build_id": None,
    })
    return {
        "state": state,
        "classification": "pending_soft",
        "change": "pending_started",
        "outcome": normalized,
    }


__all__ = [
    "COMPLETED_BUILD_STATES",
    "INCIDENT_OUTCOMES",
    "INCIDENT_TRANSITION_POLICY_ID",
    "SOFT_CONFIRMATION_BUILDS",
    "advance_incident",
    "blank_incident_state",
    "completed_build_eligibility",
    "normalize_outcome",
]
