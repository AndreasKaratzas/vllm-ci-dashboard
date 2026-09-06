#!/usr/bin/env python3
"""Plan a deduplicated recovery run for a stale canonical publication."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Support direct execution as ``python scripts/vllm/plan_publication_watchdog.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm.plan_dns_publication_reconcile import (  # noqa: E402
    FUTURE_SKEW,
    STATUS_MAX_BYTES,
    _has_valid_contract,
    _load_bounded_json,
    _parse_timestamp,
)


DEFAULT_MAX_PUBLICATION_AGE_MINUTES = 95.0
DEFAULT_RETRY_COOLDOWN_MINUTES = 15.0
DEFAULT_ACTIVE_RUN_MAX_AGE_MINUTES = 75.0
AUTOMATED_COLLECTION_CADENCE_MINUTES = 120.0
WATCHDOG_OBSERVATION_INTERVAL_MINUTES = 15.0
NORMAL_COLLECTION_RUNTIME_MINUTES = 25.0
COLLECTION_TIMEOUT_MINUTES = 60.0
SITE_HEALTH_FRESHNESS_LIMIT_MINUTES = 180.0
WORKFLOW_RUNS_MAX_BYTES = 2 * 1024 * 1024
UNAVAILABLE_GENERATION = "unavailable"
RECOVERY_TARGETS = frozenset({"collector", "deploy-pages", "dns-health"})
RECOVERY_KEY_RE = re.compile(r"(?:^| )\[recovery:([0-9a-f]{64})\](?:$| )")
COMPLETED_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)
FAILED_ATTEMPT_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "stale",
        "startup_failure",
        "timed_out",
    }
)
FORCED_RECOVERY_REASONS = frozenset(
    {
        "buildkite-collection-due",
        "dns-only-degraded",
        "site-health-failed",
        "state-slot-repair",
        "state-pages-mismatch",
        "state-uninitialized",
        "status-invalid",
        "status-unavailable",
    }
)


def state_pages_mismatch_target(
    generated_at: object,
    *,
    now: datetime,
    max_state_age_minutes: float = DEFAULT_MAX_PUBLICATION_AGE_MINUTES,
) -> str:
    """Choose deploy-only whenever authoritative state needs reprojection.

    Availability recovery precedes freshness recovery: a missing or corrupt
    Pages projection must first be repaired from the validated state without a
    Buildkite collection.  The deploy completion triggers this watchdog again,
    at which point the normal status-age planner can request a collector if the
    now-readable publication is stale.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    _positive_finite(max_state_age_minutes, label="max state age")
    now = now.astimezone(timezone.utc)
    parsed = _parse_timestamp(generated_at)
    if parsed is None or parsed > now + FUTURE_SKEW:
        raise ValueError("state generated_at is invalid")
    return "deploy-pages"


QUEUED_RUN_STATUSES = frozenset({"queued", "pending", "requested", "waiting"})
RUN_STATUSES = QUEUED_RUN_STATUSES | {"in_progress", "completed"}


@dataclass(frozen=True)
class PublicationObservation:
    """Validated state observed on the canonical Pages branch."""

    recovery_reason: str | None
    generation: str
    generated_at: datetime | None


@dataclass(frozen=True)
class RecoveryDecision:
    """A bounded decision that is safe to write to ``GITHUB_OUTPUT``."""

    required: bool
    reason: str
    observed_generation: str
    recovery_key: str = ""


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    head_branch: str
    status: str
    created_at: datetime
    run_started_at: datetime | None
    updated_at: datetime
    conclusion: str | None
    recovery_key: str | None


def recovery_key(*, target: str, reason: str, observed_generation: str) -> str:
    """Return the incident-scoped key embedded in target workflow run names."""
    if target not in RECOVERY_TARGETS:
        raise ValueError("recovery target is invalid")
    if not isinstance(reason, str) or not reason or len(reason) > 100:
        raise ValueError("recovery reason is invalid")
    if not isinstance(observed_generation, str) or not observed_generation:
        raise ValueError("observed generation is invalid")
    material = f"dashboard-recovery-v1\0{target}\0{reason}\0{observed_generation}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _display_title_recovery_key(display_title: object) -> str | None:
    if not isinstance(display_title, str) or not 1 <= len(display_title) <= 256:
        raise ValueError("workflow run state contains an invalid display title")
    matches = RECOVERY_KEY_RE.findall(display_title)
    if len(matches) > 1:
        raise ValueError("workflow run state contains multiple recovery keys")
    return matches[0] if matches else None


def _positive_finite(value: float, *, label: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return value


def _canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def observe_publication(
    payload: object,
    *,
    now: datetime,
    max_publication_age_minutes: float = DEFAULT_MAX_PUBLICATION_AGE_MINUTES,
) -> PublicationObservation:
    """Classify one public status payload without trusting its raw strings."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    _positive_finite(max_publication_age_minutes, label="max publication age")
    now = now.astimezone(timezone.utc)
    if not _has_valid_contract(payload, now=now):
        return PublicationObservation("status-invalid", UNAVAILABLE_GENERATION, None)

    assert isinstance(payload, dict)
    generated_at = _parse_timestamp(payload.get("generated_at"))
    assert generated_at is not None
    generation = _canonical_timestamp(generated_at)
    if now - generated_at >= timedelta(minutes=max_publication_age_minutes):
        reason = "publication-blocked" if payload.get("mode") == "blocked" else "publication-stale"
        return PublicationObservation(reason, generation, generated_at)
    return PublicationObservation(None, generation, generated_at)


def load_publication_observation(
    path: Path,
    *,
    now: datetime,
    max_publication_age_minutes: float = DEFAULT_MAX_PUBLICATION_AGE_MINUTES,
) -> PublicationObservation:
    loaded, payload = _load_bounded_json(path, max_bytes=STATUS_MAX_BYTES)
    if not loaded:
        return PublicationObservation("status-unavailable", UNAVAILABLE_GENERATION, None)
    return observe_publication(
        payload,
        now=now,
        max_publication_age_minutes=max_publication_age_minutes,
    )


def is_dns_only_degraded(payload: object, *, now: datetime) -> bool:
    """Return true only for the strict public DNS-only degraded contract."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    return bool(
        _has_valid_contract(payload, now=now)
        and isinstance(payload, dict)
        and payload.get("status") == "degraded"
        and payload.get("publication_blocked") is False
        and payload.get("affected_surfaces") == ["DNS health"]
        and payload.get("affected_surface_count") == 1
    )


def parse_workflow_runs(payload: object, *, now: datetime) -> tuple[WorkflowRun, ...]:
    """Validate the exact Actions response used for duplicate suppression."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ValueError("workflow run state is not a JSON object with workflow_runs")

    runs = []
    for row in payload["workflow_runs"]:
        if not isinstance(row, dict):
            raise ValueError("workflow run state contains a non-object row")
        run_id = row.get("id")
        head_branch = row.get("head_branch")
        status = row.get("status")
        created_at = _parse_timestamp(row.get("created_at"))
        run_started_value = row.get("run_started_at")
        run_started_at = (
            _parse_timestamp(run_started_value) if run_started_value is not None else None
        )
        updated_at = _parse_timestamp(row.get("updated_at"))
        conclusion = row.get("conclusion")
        display_title = row.get("display_title")
        parsed_recovery_key = _display_title_recovery_key(display_title)
        if (
            type(run_id) is not int
            or run_id <= 0
            or head_branch != "main"
            or not isinstance(status, str)
            or status not in RUN_STATUSES
            or created_at is None
            or created_at > now + FUTURE_SKEW
            or updated_at is None
            or updated_at > now + FUTURE_SKEW
            or (run_started_value is not None and run_started_at is None)
            or (run_started_at is not None and run_started_at > now + FUTURE_SKEW)
            or (
                status == "completed"
                and (not isinstance(conclusion, str) or conclusion not in COMPLETED_CONCLUSIONS)
            )
            or (status != "completed" and conclusion is not None)
        ):
            raise ValueError("workflow run state contains an invalid row")
        runs.append(
            WorkflowRun(
                run_id=run_id,
                head_branch=head_branch,
                status=status,
                created_at=created_at,
                run_started_at=run_started_at,
                updated_at=updated_at,
                conclusion=conclusion,
                recovery_key=parsed_recovery_key,
            )
        )
    return tuple(runs)


def load_workflow_runs(path: Path, *, now: datetime) -> tuple[WorkflowRun, ...]:
    loaded, payload = _load_bounded_json(path, max_bytes=WORKFLOW_RUNS_MAX_BYTES)
    if not loaded:
        raise ValueError("workflow run state is unavailable or invalid")
    return parse_workflow_runs(payload, now=now)


def watchdog_decision(
    observation: PublicationObservation,
    workflow_runs: tuple[WorkflowRun, ...],
    *,
    now: datetime,
    recovery_target: str = "collector",
    retry_cooldown_minutes: float = DEFAULT_RETRY_COOLDOWN_MINUTES,
    active_run_max_age_minutes: float = DEFAULT_ACTIVE_RUN_MAX_AGE_MINUTES,
) -> RecoveryDecision:
    """Decide whether the watchdog should dispatch the canonical collector."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    _positive_finite(retry_cooldown_minutes, label="retry cooldown")
    _positive_finite(active_run_max_age_minutes, label="active run max age")
    now = now.astimezone(timezone.utc)
    if observation.recovery_reason is None:
        return RecoveryDecision(False, "canonical-current", observation.generation)

    incident_key = recovery_key(
        target=recovery_target,
        reason=observation.recovery_reason,
        observed_generation=observation.generation,
    )

    active_cutoff = now - timedelta(minutes=active_run_max_age_minutes)
    for run in workflow_runs:
        # Bound queued and running suppression so an orphaned API state cannot
        # disable recovery forever. The 75-minute default is the collector's
        # 60-minute timeout plus scheduling and status-propagation grace.
        if run.status in QUEUED_RUN_STATUSES:
            if run.created_at >= active_cutoff:
                return RecoveryDecision(
                    False, "collection-active", observation.generation, incident_key
                )
        if run.status == "in_progress":
            active_since = run.run_started_at or run.created_at
            if active_since >= active_cutoff:
                return RecoveryDecision(
                    False, "collection-active", observation.generation, incident_key
                )

    cooldown_cutoff = now - timedelta(minutes=retry_cooldown_minutes)
    cooldown_conclusions = (
        COMPLETED_CONCLUSIONS if recovery_target == "dns-health" else FAILED_ATTEMPT_CONCLUSIONS
    )
    if any(
        run.status == "completed"
        and run.updated_at >= cooldown_cutoff
        and run.recovery_key == incident_key
        and run.conclusion in cooldown_conclusions
        for run in workflow_runs
    ):
        return RecoveryDecision(
            False, "recent-recovery-attempt", observation.generation, incident_key
        )
    return RecoveryDecision(True, observation.recovery_reason, observation.generation, incident_key)


def generation_preflight_decision(
    observation: PublicationObservation,
    *,
    expected_generation: str,
    now: datetime,
) -> RecoveryDecision:
    """Recheck a queued wake-up after it acquires the publication lock."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if expected_generation == UNAVAILABLE_GENERATION:
        if observation.generated_at is not None and observation.recovery_reason is None:
            return RecoveryDecision(False, "publication-recovered", observation.generation)
        return RecoveryDecision(
            True,
            observation.recovery_reason or "status-unavailable",
            observation.generation,
        )
    expected = _parse_timestamp(expected_generation)
    if expected is None or expected > now + FUTURE_SKEW:
        return RecoveryDecision(True, "expected-generation-invalid", observation.generation)
    if (
        observation.generated_at is not None
        and observation.generated_at > expected
        and observation.recovery_reason is None
    ):
        return RecoveryDecision(False, "generation-advanced", observation.generation)
    if observation.recovery_reason is not None:
        return RecoveryDecision(True, observation.recovery_reason, observation.generation)
    return RecoveryDecision(True, "generation-pending", observation.generation)


def cadence_preflight_decision(
    observation: PublicationObservation,
) -> RecoveryDecision:
    """Coalesce a scheduled run queued behind a just-finished recovery."""
    if observation.recovery_reason is None:
        return RecoveryDecision(False, "canonical-recent", observation.generation)
    return RecoveryDecision(True, observation.recovery_reason, observation.generation)


def _append_github_output(path: Path, decision: RecoveryDecision) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"required={'true' if decision.required else 'false'}\n")
        handle.write(f"reason={decision.reason}\n")
        handle.write(f"observed_generation={decision.observed_generation}\n")
        handle.write(f"recovery_key={decision.recovery_key}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--workflow-runs", type=Path)
    mode.add_argument("--expected-generation")
    mode.add_argument("--cadence-preflight", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--recovery-target", choices=sorted(RECOVERY_TARGETS))
    parser.add_argument(
        "--max-age-minutes",
        type=float,
        default=DEFAULT_MAX_PUBLICATION_AGE_MINUTES,
    )
    parser.add_argument(
        "--retry-cooldown-minutes",
        type=float,
        default=DEFAULT_RETRY_COOLDOWN_MINUTES,
    )
    parser.add_argument(
        "--active-run-max-age-minutes",
        type=float,
        default=DEFAULT_ACTIVE_RUN_MAX_AGE_MINUTES,
    )
    parser.add_argument("--now", help="timezone-aware ISO-8601 test override")
    parser.add_argument(
        "--force-recovery-reason",
        choices=sorted(FORCED_RECOVERY_REASONS),
        help="Force a trusted workflow-classified route through duplicate suppression",
    )
    args = parser.parse_args(argv)

    if args.force_recovery_reason and args.workflow_runs is None:
        parser.error("--force-recovery-reason requires --workflow-runs")
    if (args.workflow_runs is None) != (args.recovery_target is None):
        parser.error("--recovery-target is required exactly with --workflow-runs")

    now = _parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be a timezone-aware ISO-8601 timestamp")
    try:
        observation = load_publication_observation(
            args.status,
            now=now,
            max_publication_age_minutes=args.max_age_minutes,
        )
        if args.force_recovery_reason:
            observation = PublicationObservation(
                args.force_recovery_reason,
                observation.generation,
                observation.generated_at,
            )
        if args.workflow_runs is not None:
            assert args.recovery_target is not None
            runs = load_workflow_runs(args.workflow_runs, now=now)
            decision = watchdog_decision(
                observation,
                runs,
                now=now,
                recovery_target=args.recovery_target,
                retry_cooldown_minutes=args.retry_cooldown_minutes,
                active_run_max_age_minutes=args.active_run_max_age_minutes,
            )
        elif args.expected_generation is not None:
            decision = generation_preflight_decision(
                observation,
                expected_generation=args.expected_generation,
                now=now,
            )
        else:
            decision = cadence_preflight_decision(observation)
    except ValueError as exc:
        parser.error(str(exc))

    print(
        "Canonical publication recovery "
        f"{'required' if decision.required else 'not required'}: {decision.reason} "
        f"(observed generation {decision.observed_generation})"
    )
    if args.github_output:
        _append_github_output(args.github_output, decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
