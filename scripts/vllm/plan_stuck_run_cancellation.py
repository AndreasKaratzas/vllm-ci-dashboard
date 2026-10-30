#!/usr/bin/env python3
"""Select workflow runs stuck queued or in progress past their timeout bound.

GitHub can leave a run ``queued`` or ``in_progress`` long after every job ended
(e.g. Data Collection 39462175700 from 2026-10-20). Such a run never ends on
its own. A run is stuck once it has been active for longer than the sum of its
workflow's job timeouts plus a margin for queueing; the publication watchdog
force-cancels the selected runs.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Support direct execution as ``python scripts/vllm/plan_stuck_run_cancellation.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vllm.plan_dns_publication_reconcile import (  # noqa: E402
    FUTURE_SKEW,
    _load_bounded_json,
    _parse_timestamp,
)


POLICY_PATH = Path(__file__).resolve().parents[2] / "config/run_janitor.json"
POLICY_MAX_BYTES = 64 * 1024
RUNS_MAX_BYTES = 4 * 1024 * 1024
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


@dataclass(frozen=True)
class JanitorPolicy:
    margin_minutes: int
    default_timeout_minutes: int
    max_cancellations: int
    workflow_timeout_minutes: dict[str, int]

    def limit(self, path: str) -> timedelta:
        timeout = self.workflow_timeout_minutes.get(path, self.default_timeout_minutes)
        return timedelta(minutes=timeout + self.margin_minutes)


@dataclass(frozen=True)
class StuckRun:
    run_id: int
    name: str
    path: str
    status: str
    active_since: datetime
    limit: timedelta


def _positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("run janitor policy is invalid")
    return value


def load_policy(path: Path = POLICY_PATH) -> JanitorPolicy:
    loaded, payload = _load_bounded_json(path, max_bytes=POLICY_MAX_BYTES)
    if not loaded or not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("run janitor policy is invalid")
    timeouts = payload.get("workflow_timeout_minutes")
    if not isinstance(timeouts, dict) or not all(isinstance(key, str) for key in timeouts):
        raise ValueError("run janitor policy is invalid")
    return JanitorPolicy(
        margin_minutes=_positive_int(payload.get("margin_minutes")),
        default_timeout_minutes=_positive_int(payload.get("default_timeout_minutes")),
        max_cancellations=_positive_int(payload.get("max_cancellations")),
        workflow_timeout_minutes={key: _positive_int(value) for key, value in timeouts.items()},
    )


def select_stuck_runs(
    payloads: list[object],
    policy: JanitorPolicy,
    *,
    now: datetime,
    exclude_run_id: int | None = None,
) -> list[StuckRun]:
    """Return the oldest stuck runs first, at most ``max_cancellations`` of them.

    ``payloads`` are the per-status run listings. Any malformed listing or row
    rejects them all, so nothing is cancelled on evidence the planner cannot read.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    rows = []
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise ValueError("active run listing is not a JSON object with workflow_runs")
        rows.extend(payload["workflow_runs"])
    stuck: dict[int, StuckRun] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("active run listing contains a non-object row")
        run_id = row.get("id")
        name = row.get("name")
        path = row.get("path")
        status = row.get("status")
        created_at = _parse_timestamp(row.get("created_at"))
        started_value = row.get("run_started_at")
        started_at = _parse_timestamp(started_value) if started_value is not None else None
        if (
            type(run_id) is not int
            or run_id <= 0
            or not isinstance(name, str)
            or not isinstance(path, str)
            or status not in ACTIVE_STATUSES
            or created_at is None
            or created_at > now + FUTURE_SKEW
            or (started_value is not None and started_at is None)
            or (started_at is not None and started_at > now + FUTURE_SKEW)
        ):
            raise ValueError("active run listing contains an invalid row")
        if run_id == exclude_run_id:
            continue
        # A re-run attempt restarts the clock at its own start time.
        active_since = max(created_at, started_at) if started_at else created_at
        limit = policy.limit(path)
        if now - active_since > limit:
            stuck[run_id] = StuckRun(run_id, name, path, status, active_since, limit)
    ordered = sorted(stuck.values(), key=lambda run: (run.active_since, run.run_id))
    return ordered[: policy.max_cancellations]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=Path, action="append", required=True, help="One run listing per status"
    )
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--exclude-run-id", type=int)
    parser.add_argument("--output", type=Path, required=True, help="Run ids to cancel, one per line")
    parser.add_argument("--now", help="timezone-aware ISO-8601 test override")
    args = parser.parse_args(argv)

    now = _parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be a timezone-aware ISO-8601 timestamp")
    try:
        policy = load_policy(args.policy)
        payloads = []
        for path in args.runs:
            loaded, payload = _load_bounded_json(path, max_bytes=RUNS_MAX_BYTES)
            if not loaded:
                raise ValueError("active run listing is unavailable or invalid")
            payloads.append(payload)
        runs = select_stuck_runs(payloads, policy, now=now, exclude_run_id=args.exclude_run_id)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for run in runs:
        age_hours = (now - run.active_since).total_seconds() / 3600
        print(
            f"Stuck {run.status} run {run.run_id} ({run.name}, {run.path}): "
            f"active {age_hours:.1f} h, bound {run.limit.total_seconds() / 3600:.1f} h"
        )
    print(f"{len(runs)} stuck run(s) selected for force-cancel")
    args.output.write_text("".join(f"{run.run_id}\n" for run in runs), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
