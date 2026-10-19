#!/usr/bin/env python3
"""Plan a deduplicated recovery run for a stale canonical publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

# Support direct execution as ``python scripts/vllm/plan_publication_watchdog.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm.plan_dns_publication_reconcile import (  # noqa: E402
    FUTURE_SKEW,
    STATUS_MAX_BYTES,
    _has_valid_contract,
    _load_bounded_json,
    _parse_timestamp,
    _reject_duplicate_keys,
)


DEFAULT_MAX_PUBLICATION_AGE_MINUTES = 75.0
DEFAULT_RETRY_COOLDOWN_MINUTES = 15.0
DEFAULT_ACTIVE_RUN_MAX_AGE_MINUTES = 75.0
WORKFLOW_RUNS_MAX_BYTES = 2 * 1024 * 1024
UNAVAILABLE_GENERATION = "unavailable"
RECOVERY_TARGETS = frozenset({"collector", "deploy-pages", "dns-health"})
# Run titles tag their mode: ``[full:<key>]``, ``[queue:<key>]`` and
# ``[dns:<generation>]`` for Data Collection, ``[recovery:<key>]`` for keyed
# Pages and DNS monitor runs. Untagged runs are routine.
RUN_TAG_RE = re.compile(r"(?<!\S)\[(full|queue|dns|recovery):([^\s\[\]]*)\](?!\S)")
RECOVERY_KEY_RE = re.compile(r"[0-9a-f]{64}")
RECONCILIATION_MODES = frozenset({"queue", "dns"})
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
FRESHNESS_TARGETS = Path(__file__).resolve().parents[2] / "config/data_freshness_targets.json"
PAGES_DATA_ROOT = "data/vllm"
OPERATIONS_MANIFEST = "ci/operations_v2_manifest.json"
PAGES_JSON_MAX_BYTES = 16 * 1024 * 1024
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
        "collection-retry-due",
        "dns-only-degraded",
        "site-health-failed",
        "site-health-stale",
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
    head_sha: str | None = None
    mode: str = "routine"


@dataclass(frozen=True)
class CoreTargets:
    """Core collector sources and their collection-age target."""

    sources: tuple[str, ...]
    target_minutes: float
    own_files: Mapping[str, str]


@dataclass(frozen=True)
class CoreDataAge:
    """The oldest core source; ``collected_at`` is None when its clock is unreadable."""

    source: str
    collected_at: datetime | None
    stale: bool


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


def _display_title_tag(display_title: object) -> tuple[str, str | None]:
    """Return the run mode and its recovery key from a bounded run title."""
    if not isinstance(display_title, str) or not 1 <= len(display_title) <= 256:
        raise ValueError("workflow run state contains an invalid display title")
    matches = RUN_TAG_RE.findall(display_title)
    if len(matches) > 1:
        raise ValueError("workflow run state contains multiple run tags")
    if not matches:
        return "routine", None
    mode, value = matches[0]
    keyed = mode != "dns" and RECOVERY_KEY_RE.fullmatch(value) is not None
    return mode, value if keyed else None


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
        head_sha = row.get("head_sha")
        status = row.get("status")
        created_at = _parse_timestamp(row.get("created_at"))
        run_started_value = row.get("run_started_at")
        run_started_at = (
            _parse_timestamp(run_started_value) if run_started_value is not None else None
        )
        updated_at = _parse_timestamp(row.get("updated_at"))
        conclusion = row.get("conclusion")
        mode, parsed_recovery_key = _display_title_tag(row.get("display_title"))
        if (
            type(run_id) is not int
            or run_id <= 0
            or head_branch != "main"
            or (
                head_sha is not None
                and (not isinstance(head_sha, str) or COMMIT_SHA_RE.fullmatch(head_sha) is None)
            )
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
                head_sha=head_sha,
                mode=mode,
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
    current_code_sha: str | None = None,
) -> RecoveryDecision:
    """Decide whether the watchdog should dispatch the canonical collector."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    _positive_finite(retry_cooldown_minutes, label="retry cooldown")
    _positive_finite(active_run_max_age_minutes, label="active run max age")
    if recovery_target == "dns-health":
        if not isinstance(current_code_sha, str) or COMMIT_SHA_RE.fullmatch(current_code_sha) is None:
            raise ValueError("DNS recovery requires an exact current code SHA")
        if any(run.head_sha is None for run in workflow_runs):
            raise ValueError("DNS workflow run state is missing an exact head SHA")
    elif current_code_sha is not None:
        raise ValueError("current code SHA is supported only for DNS recovery")
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
        # Queue and DNS reconciliations republish without collecting, so they
        # never stand in for the collection or recovery being planned.
        if run.mode in RECONCILIATION_MODES:
            continue
        # Bound queued and running suppression so an orphaned API state cannot
        # disable recovery forever. The 75-minute default matches the full
        # collector job's timeout; older orphaned states cannot block a retry.
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
    # Normal DNS failures/cancellations consume the same quota as keyed
    # recovery runs. Cool down that code even when publication generations
    # change; a newly checked-out fix can recover without waiting on old code.
    if any(
        run.status == "completed"
        and run.updated_at >= cooldown_cutoff
        and (
            run.recovery_key == incident_key
            or (
                recovery_target == "dns-health"
                and run.recovery_key is None
                and run.conclusion in FAILED_ATTEMPT_CONCLUSIONS
            )
        )
        and (recovery_target != "dns-health" or run.head_sha == current_code_sha)
        and run.conclusion in cooldown_conclusions
        for run in workflow_runs
    ):
        return RecoveryDecision(
            False, "recent-recovery-attempt", observation.generation, incident_key
        )
    return RecoveryDecision(True, observation.recovery_reason, observation.generation, incident_key)


def load_core_targets(path: Path = FRESHNESS_TARGETS) -> CoreTargets:
    """Read the core source list shared with Site Health and the web UI."""
    loaded, payload = _load_bounded_json(path, max_bytes=STATUS_MAX_BYTES)
    try:
        if not loaded:
            raise TypeError
        core = payload["core"]
        sources = tuple(core["sources"])
        target_minutes = _positive_finite(float(core["target_minutes"]), label="core target")
        own_files = {
            key: payload["sources"][key]["path"]
            for key in sources
            if "path" in payload["sources"][key]
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("data freshness targets are invalid") from exc
    if not sources or not all(isinstance(key, str) for key in sources) or not all(
        isinstance(own, str) for own in own_files.values()
    ):
        raise ValueError("data freshness targets are invalid")
    return CoreTargets(sources, target_minutes, own_files)


def core_data_age(
    read_json: Callable[[str], object],
    targets: CoreTargets,
    *,
    now: datetime,
) -> CoreDataAge:
    """Return the oldest core collection clock on the published site.

    Sources with their own published file use its ``generated_at``; the rest
    use the operations manifest ``shell.sources`` timestamps. A missing or
    unreadable clock counts as stale: the collector admission still bounds
    every Buildkite request.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    manifest = read_json(OPERATIONS_MANIFEST)
    shell = manifest.get("shell") if isinstance(manifest, dict) else None
    published = shell.get("sources") if isinstance(shell, dict) else None
    oldest: tuple[str, datetime] | None = None
    for key in targets.sources:
        if key in targets.own_files:
            payload = read_json(targets.own_files[key])
            raw = payload.get("generated_at") if isinstance(payload, dict) else None
        else:
            entry = published.get(key) if isinstance(published, dict) else None
            raw = entry.get("timestamp") if isinstance(entry, dict) else None
        collected_at = _parse_timestamp(raw)
        if collected_at is None or collected_at > now + FUTURE_SKEW:
            return CoreDataAge(key, None, True)
        if oldest is None or collected_at < oldest[1]:
            oldest = (key, collected_at)
    assert oldest is not None
    source, collected_at = oldest
    stale = now - collected_at >= timedelta(minutes=targets.target_minutes)
    return CoreDataAge(source, collected_at, stale)


def pages_json_reader(ref: str) -> Callable[[str], object]:
    """Read bounded JSON below ``data/vllm`` from a proven Pages commit."""

    def read(relative: str) -> object:
        spec = f"{ref}:{PAGES_DATA_ROOT}/{relative}"
        size = subprocess.run(["git", "cat-file", "-s", spec], capture_output=True, text=True)
        if size.returncode != 0 or not size.stdout.strip().isdigit():
            return None
        if int(size.stdout) > PAGES_JSON_MAX_BYTES:
            return None
        blob = subprocess.run(["git", "cat-file", "blob", spec], capture_output=True)
        if blob.returncode != 0:
            return None
        try:
            return json.loads(blob.stdout.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeError, ValueError, RecursionError):
            return None

    return read


def admitted_observation(
    observation: PublicationObservation, core_age: CoreDataAge
) -> PublicationObservation:
    """Promote a current publication once the collector admission is open.

    Queue and DNS reconciliations keep the publication clock fresh, so a
    current publication says nothing about the core collection; the open
    durable admission is the authority that a collection is due.
    """
    if observation.recovery_reason is not None:
        return observation
    reason = "core-data-stale" if core_age.stale else "buildkite-collection-due"
    return PublicationObservation(reason, observation.generation, observation.generated_at)

def _append_github_output(path: Path, decision: RecoveryDecision) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"required={'true' if decision.required else 'false'}\n")
        handle.write(f"reason={decision.reason}\n")
        handle.write(f"observed_generation={decision.observed_generation}\n")
        handle.write(f"recovery_key={decision.recovery_key}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--workflow-runs", type=Path, required=True)
    parser.add_argument("--recovery-target", choices=sorted(RECOVERY_TARGETS), required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--collector-admission", type=Path)
    parser.add_argument(
        "--pages-ref",
        help="Proven Pages commit whose core source clocks label an admitted collection",
    )
    parser.add_argument(
        "--current-code-sha",
        help="Exact trusted checkout HEAD for DNS same-code retry suppression",
    )
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

    if (args.recovery_target == "dns-health") != (args.current_code_sha is not None):
        parser.error("--current-code-sha is required exactly with DNS workflow recovery")
    if args.collector_admission and (args.recovery_target != "collector" or args.force_recovery_reason == "state-uninitialized"):
        parser.error("--collector-admission requires a non-bootstrap collector route")
    if (args.collector_admission is None) != (args.pages_ref is None):
        parser.error("--pages-ref is required exactly with --collector-admission")

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
        admission = None
        if args.collector_admission:
            from vllm.observe_watchdog_collection_admission import load_admission

            admission = load_admission(args.collector_admission, now=now)
            if admission.required:
                core_age = core_data_age(
                    pages_json_reader(args.pages_ref), load_core_targets(), now=now,
                )
                print(
                    f"Oldest core source: {core_age.source} collected at "
                    f"{_canonical_timestamp(core_age.collected_at) if core_age.collected_at else 'unknown'}"
                    f" ({'stale' if core_age.stale else 'within target'})"
                )
                observation = admitted_observation(observation, core_age)
        runs = load_workflow_runs(args.workflow_runs, now=now)
        decision = watchdog_decision(
            observation,
            runs,
            now=now,
            recovery_target=args.recovery_target,
            retry_cooldown_minutes=args.retry_cooldown_minutes,
            active_run_max_age_minutes=args.active_run_max_age_minutes,
            current_code_sha=args.current_code_sha,
        )
        if admission is not None and not admission.required:
            decision = RecoveryDecision(
                False, admission.reason, decision.observed_generation, decision.recovery_key,
            )
    except (OSError, ValueError) as exc:
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
