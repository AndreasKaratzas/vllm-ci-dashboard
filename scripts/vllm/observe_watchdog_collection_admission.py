#!/usr/bin/env python3
# cspell:ignore killpg
"""Observe collector admission and wait for a gate that opens within the run budget.

The watchdog spends the rest of its bounded run waiting for a near gate, so a
tick that lands shortly before the next collection is due still dispatches it.
This hint suppresses no-op recovery dispatches. It never reserves an attempt,
changes a quota ledger or grants token access; the collector still reserves
under its normal concurrency boundary immediately before Buildkite access.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vllm import request_bearing_attempt_budget as budget  # noqa: E402

MAX_WAIT_SECONDS = 30 * 60
POLL_SECONDS = 30
PROOF_MAX_BYTES = 4096
PROOF_MAX_AGE_SECONDS = 360
OBSERVE_TIMEOUT_SECONDS = 60
PROCESS_CLEANUP_SECONDS = 5
GATED_MODES = frozenset({"success_gated", "retry_gated", "cap_gated"})
GATED_REASONS = frozenset({
    "collection-success-gated", "collection-retry-gated", "collection-cap-gated",
    "collection-admission-shifted", "collection-admission-deadline",
})


@dataclass(frozen=True)
class AdmissionDecision:
    schema_version: int
    required: bool
    reason: str
    request_mode: str
    available_at: str
    budget_sha: str
    observed_at: str
    waited_seconds: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("collector admission clock must be UTC")
    return value.astimezone(timezone.utc)


def _gate_horizon(policy: budget.AttemptPolicy, mode: str) -> timedelta:
    """Latest availability each gate can report; failed retries back off up to the success interval."""
    if mode == "cap_gated":
        return timedelta(hours=policy.window_hours)
    return timedelta(minutes=policy.success_interval_minutes)


def _validated_observation(
    value: Mapping[str, object], *, observed_at: datetime, policy: budget.AttemptPolicy,
) -> tuple[bool, str, datetime | None, str]:
    if not isinstance(value, Mapping) or policy.producer != "data_collection" or value.get("observation_valid") != "true":
        raise ValueError("collector admission observation is not verified")
    mode = value.get("request_mode")
    required = value.get("required")
    sha = value.get("budget_sha")
    if (
        not isinstance(mode, str) or mode not in GATED_MODES | {"reserved"}
        or not isinstance(required, str) or required not in {"true", "false"}
        or (required == "true") != (mode == "reserved")
        or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None
    ):
        raise ValueError("collector admission decision is inconsistent")
    if mode == "reserved":
        if value.get("available_at") != "":
            raise ValueError("open collector admission has a conflicting availability clock")
        return True, mode, None, sha
    available = budget._timestamp(value.get("available_at"), label="collector available_at")
    if not observed_at < available <= observed_at + _gate_horizon(policy, mode):
        raise ValueError("collector availability is inconsistent with its verified gate")
    return False, str(mode), available, sha


def _run_bounded_observation(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Kill and reap the complete read-only Git process group on timeout."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=PROCESS_CLEANUP_SECONDS)
        raise ValueError("durable collector observation exceeded its deadline") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _observe_subprocess(
    root: Path, policy: budget.AttemptPolicy, *, now: datetime, remote: str, timeout: float,
) -> dict[str, object]:
    """Bound the entire read, including its Git and immutable-evidence proofs."""
    config = root / "config/data_collection_attempt_budget.json"
    if budget.load_policy(config) != policy:
        raise ValueError("collector observation policy changed")
    try:
        result = _run_bounded_observation([
            sys.executable, str(root / "scripts/vllm/request_bearing_attempt_budget.py"),
            "--root", str(root), "--config", str(config), "observe", "--now", budget._iso(now),
            "--remote", remote,
        ], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("durable collector observation exceeded its deadline") from exc
    if result.returncode != 0 or not result.stdout or len(result.stdout) > PROOF_MAX_BYTES:
        raise ValueError("durable collector observation failed or exceeded its bound")
    values: dict[str, object] = {}
    for line in result.stdout.decode("utf-8", errors="strict").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("durable collector observation output is ambiguous")
        values[key] = value
    return values


def observe_admission(
    root: Path, policy: budget.AttemptPolicy, *, remote: str = "origin",
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    observe: Callable[..., dict[str, object]] | None = None,
    max_wait_seconds: int,
) -> AdmissionDecision:
    if type(max_wait_seconds) is not int or not 0 <= max_wait_seconds <= MAX_WAIT_SECONDS:
        raise ValueError("collector admission wait must be bounded by thirty minutes")
    started_clock = _utc(clock())
    started_mono = monotonic()
    if not math.isfinite(started_mono):
        raise ValueError("collector admission elapsed clock must be finite")
    deadline_clock = started_clock + timedelta(seconds=max_wait_seconds)
    deadline_mono = started_mono + max_wait_seconds
    # The final read must finish 30 seconds after the wait deadline; a
    # zero-wait request still gets one bounded 60-second read.
    observation_deadline = deadline_mono + (30 if max_wait_seconds else OBSERVE_TIMEOUT_SECONDS)
    previous_clock = started_clock
    previous_mono = started_mono
    waiting = False
    while True:
        observed_at = _utc(clock())
        before = monotonic()
        if observed_at < previous_clock or not math.isfinite(before) or before < previous_mono:
            raise ValueError("collector admission clock moved backwards")
        remaining = observation_deadline - before - PROCESS_CLEANUP_SECONDS
        if remaining <= 0:
            raise ValueError("durable collector observation exceeded its deadline")
        value = (
            observe(root, policy, now=observed_at, remote=remote) if observe is not None
            else _observe_subprocess(root, policy, now=observed_at, remote=remote,
                                     timeout=min(OBSERVE_TIMEOUT_SECONDS, remaining))
        )
        required, mode, available, sha = _validated_observation(
            value, observed_at=observed_at, policy=policy,
        )
        current = _utc(clock())
        elapsed = monotonic()
        if current < observed_at or not math.isfinite(elapsed) or elapsed < before:
            raise ValueError("collector admission clock moved backwards")
        if elapsed > observation_deadline:
            raise ValueError("durable collector observation exceeded its deadline")
        reason = "collection-admission-open" if required else "collection-" + mode.replace("_", "-")
        if required:
            break
        assert available is not None
        # Never extend the original deadline after a concurrent reservation.
        if available > deadline_clock:
            if waiting:
                reason = "collection-admission-shifted"
            break
        if elapsed >= deadline_mono or current >= deadline_clock:
            reason = "collection-admission-deadline"
            break
        waiting = True
        duration = min(POLL_SECONDS, max(0, (available - current).total_seconds()), deadline_mono - elapsed)
        if duration > 0:
            sleep(duration)
        previous_clock, previous_mono = current, elapsed
    return AdmissionDecision(
        1, required, reason, mode, budget._iso(available) if available is not None else "",
        sha, budget._iso(observed_at), min(MAX_WAIT_SECONDS, max(0, math.ceil(elapsed - started_mono))),
    )


def _load_admission(path: Path, *, now: datetime) -> AdmissionDecision:
    """Validate the short-lived hint consumed by the dispatch planner."""
    raw = path.read_bytes()
    if not raw or len(raw) > PROOF_MAX_BYTES:
        raise ValueError("collector admission proof exceeds its bound")
    value = budget._decode_json(raw, label="collector admission proof")
    if not isinstance(value, dict) or set(value) != set(AdmissionDecision.__dataclass_fields__):
        raise ValueError("collector admission proof shape is invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or type(value["required"]) is not bool:
        raise ValueError("collector admission proof version or decision is invalid")
    if type(value["waited_seconds"]) is not int or not 0 <= value["waited_seconds"] <= MAX_WAIT_SECONDS:
        raise ValueError("collector admission wait exceeds its bound")
    if not isinstance(value["budget_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", value["budget_sha"]) is None:
        raise ValueError("collector admission proof has no immutable budget identity")
    observed = budget._timestamp(value["observed_at"], label="collector observed_at")
    now = _utc(now)
    if not observed <= now <= observed + timedelta(seconds=PROOF_MAX_AGE_SECONDS):
        raise ValueError("collector admission proof is future or expired")
    if value["required"]:
        if value["request_mode"] != "reserved" or value["available_at"] != "" or value["reason"] != "collection-admission-open":
            raise ValueError("collector admission open proof is inconsistent")
    else:
        available = budget._timestamp(value["available_at"], label="collector available_at")
        mode = value["request_mode"]
        reason = value["reason"]
        policy = budget.load_policy(budget.ROOT / "config/data_collection_attempt_budget.json")
        if policy.producer != "data_collection":
            raise ValueError("collector admission proof policy is invalid")
        if (
            not isinstance(mode, str) or mode not in GATED_MODES
            or not isinstance(reason, str) or reason not in GATED_REASONS
            or reason not in {"collection-" + mode.replace("_", "-"),
                              "collection-admission-shifted", "collection-admission-deadline"}
            or not observed < available <= observed + _gate_horizon(policy, mode)
        ):
            raise ValueError("collector admission gated proof is inconsistent")
    return AdmissionDecision(**value)


def load_admission(path: Path, *, now: datetime) -> AdmissionDecision:
    try:
        return _load_admission(path, now=now)
    except budget.AttemptBudgetError as exc:
        raise ValueError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=budget.ROOT)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--max-wait-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        decision = observe_admission(args.root.resolve(), budget.load_policy(args.config.resolve()),
                                     max_wait_seconds=args.max_wait_seconds)
        args.output.write_text(json.dumps(asdict(decision), sort_keys=True) + "\n", encoding="utf-8")
        budget._append_outputs(args.github_output, {
            "required": str(decision.required).lower(), "reason": decision.reason,
            "request_mode": decision.request_mode, "available_at": decision.available_at,
            "budget_sha": decision.budget_sha, "waited_seconds": decision.waited_seconds,
        })
        print(f"Collector admission: {decision.reason}; next availability {decision.available_at or 'open'}; waited {decision.waited_seconds}s")
    except (budget.AttemptBudgetError, OSError, ValueError) as exc:
        print(f"collector admission failed closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
