"""Webhook ingestion for the ``vllm/perf-eval`` nightly pipeline.

The webhook endpoint consumes payloads that the perf-eval pipeline emits and
persists them to the bounded rolling event store
(``data/vllm/perf_eval/events.jsonl``). ``collect_perf_eval.py`` later folds
that store into the aggregated ``perf_eval.json`` the frontend reads. The
scheduled artifact collector writes through the same retention implementation.

Three payload shapes are accepted, all delivered as HTTP POSTs:

1. **Buildkite native build events** (``build.running`` / ``build.finished``
   for the ``perf-eval`` pipeline). These carry build identity — number,
   commit, branch, message, ``web_url`` and the ``VLLM_COMMIT`` / ``VLLM_IMAGE``
   build env — so a result can always be traced back to the exact vLLM
   commit/image that produced it.
2. **Perf result pushes** — the JSON shape ``lib/ingest_perf.py`` POSTs for
   every ``vllm bench serve`` config (per-GPU throughput, TTFT, TPOT, ...).
3. **Accuracy result pushes** — the ``{"kind": "results", ...}`` shape
   ``lib/ingest.py`` POSTs for every lm-eval / bfcl task.

Only **AMD** workloads (MI2xx/MI3xx GPUs, or ROCm images) are kept; NVIDIA
workloads (H200/B200) are dropped at normalization time. Nightly identity is
preserved on every event so the collector can keep the nightly-only history
the executive view needs while still ignoring ad-hoc opt-in runs.

The normalization helpers in this module are deliberately pure (no I/O, no
globals) so they can be unit-tested without a live Buildkite or HTTP server.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

from ..dashboard_storage_budget import writer_max_bytes

log = logging.getLogger(__name__)

PERF_EVAL_PIPELINE_SLUG = "perf-eval"

# AMD GPU device tags (``parse_workload.py`` emits ``gpu.lower()`` as the
# device, e.g. ``mi355x`` / ``mi300x``). NVIDIA tags (``h200`` / ``b200`` /
# ``a100``) deliberately do not match.
_AMD_DEVICE_RE = re.compile(r"^mi\d+", re.IGNORECASE)
# Workload stems encode the hardware suffix (``minimax_m2_5_mi355x``).
_AMD_WORKLOAD_RE = re.compile(r"(?:^|[_-])mi\d+[a-z]?(?:$|[_-])", re.IGNORECASE)

# Perf metrics we surface, with executive-facing display metadata. ``direction``
# drives the red/green + arrow logic in the frontend: ``higher`` means a larger
# value is better (throughput), ``lower`` means smaller is better (latency).
# Embedding this in the data payload keeps the frontend data-driven so new
# metrics render with the right "higher/lower is better" hint automatically.
METRIC_META: dict[str, dict[str, str]] = {
    "tput_per_gpu": {"label": "Total throughput / GPU", "unit": "tok/s", "direction": "higher"},
    "output_tput_per_gpu": {"label": "Output throughput / GPU", "unit": "tok/s", "direction": "higher"},
    "input_tput_per_gpu": {"label": "Input throughput / GPU", "unit": "tok/s", "direction": "higher"},
    "mean_ttft": {"label": "Mean TTFT", "unit": "s", "direction": "lower"},
    "median_ttft": {"label": "Median TTFT", "unit": "s", "direction": "lower"},
    "p99_ttft": {"label": "P99 TTFT", "unit": "s", "direction": "lower"},
    "mean_tpot": {"label": "Mean TPOT", "unit": "s", "direction": "lower"},
    "median_tpot": {"label": "Median TPOT", "unit": "s", "direction": "lower"},
    "p99_tpot": {"label": "P99 TPOT", "unit": "s", "direction": "lower"},
    "mean_itl": {"label": "Mean ITL", "unit": "s", "direction": "lower"},
    "mean_e2el": {"label": "Mean end-to-end latency", "unit": "s", "direction": "lower"},
    "mean_intvty": {"label": "Mean interactivity", "unit": "tok/s", "direction": "higher"},
}

# Accuracy is always "higher is better" and lives on a 0..1 scale.
ACCURACY_DIRECTION = "higher"


def utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_from_image(image: str) -> str:
    """Extract a vLLM commit SHA embedded in an image tag, if present.

    Mirrors the regex in perf-eval's ``parse_workload.py`` so the dashboard
    derives the same commit the pipeline tagged the run with.
    """
    if not image:
        return ""
    _, sep, tag = image.rpartition(":")
    if not sep:
        return ""
    tag = tag.split("@", 1)[0]
    m = (
        re.match(r"nightly-([0-9a-f]{7,40})(?:[-_.].*)?$", tag, re.IGNORECASE)
        or re.search(r"(?:^|[-_.])([0-9a-f]{12,40})(?:$|[-_.])", tag, re.IGNORECASE)
    )
    return m.group(1) if m else ""


def is_amd_device(device: Optional[str]) -> bool:
    return bool(device) and bool(_AMD_DEVICE_RE.match(str(device).strip()))


def is_amd_workload(
    workload: Optional[str] = None,
    image: Optional[str] = None,
    device: Optional[str] = None,
) -> bool:
    """True if any signal marks this run as AMD/ROCm.

    AMD if the device is an MIxxx tag, the workload stem carries an MIxxx
    suffix, or the image is a ROCm image. Everything else (H200/B200/A100)
    is treated as NVIDIA and excluded.
    """
    if is_amd_device(device):
        return True
    if workload and _AMD_WORKLOAD_RE.search(str(workload)):
        return True
    if image and "rocm" in str(image).lower():
        return True
    return False


def is_nightly(payload: dict) -> bool:
    """Whether a payload is part of the scheduled nightly run.

    The pipeline marks nightly rows with a literal ``nightly: true`` (set by
    ``NIGHTLY=1`` in ingest scripts) or, for Buildkite native events, a build
    message / env that names the nightly schedule.
    """
    if payload.get("nightly") is True:
        return True
    md = payload.get("metadata")
    if isinstance(md, dict) and md.get("nightly") is True:
        return True
    env = payload.get("env")
    if isinstance(env, dict) and str(env.get("NIGHTLY", "")).strip() in {"1", "true", "yes"}:
        return True
    message = str(payload.get("message", "") or "")
    return bool(re.search(r"\bnightly\b", message, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Payload classification
# ---------------------------------------------------------------------------

def classify_payload(headers: dict, body: dict) -> str:
    """Return ``buildkite`` | ``perf`` | ``eval`` | ``unknown`` for a payload.

    Buildkite native events are identified by the ``X-Buildkite-Event``
    header. Result pushes are distinguished by their shape: accuracy pushes
    carry ``kind`` (``results`` / ``samples``); perf pushes carry the
    per-GPU throughput columns ``ingest_perf.py`` emits.
    """
    if headers:
        # HTTP headers are case-insensitive; normalize for lookup.
        lower = {str(k).lower(): v for k, v in headers.items()}
        if lower.get("x-buildkite-event"):
            return "buildkite"
    if not isinstance(body, dict):
        return "unknown"
    if body.get("kind") in {"results", "samples"}:
        return "eval"
    if "tput_per_gpu" in body or "device" in body and "model" in body:
        return "perf"
    if "build" in body and "pipeline" in body:
        return "buildkite"
    return "unknown"


# ---------------------------------------------------------------------------
# Normalizers — raw push payload -> canonical event dict
# ---------------------------------------------------------------------------

def _build_identity(payload: dict) -> dict:
    """Pull a compact build-identity block out of any payload shape."""
    image = (payload.get("image") or "").strip()
    commit = (payload.get("vllm_commit") or "").strip() or commit_from_image(image)
    return {
        "build_number": _to_int(payload.get("buildkite_build_number")),
        "build_url": payload.get("buildkite_build_url") or "",
        "build_commit": payload.get("buildkite_commit") or "",
        "branch": payload.get("buildkite_branch") or "",
        "image": image,
        "vllm_commit": commit,
    }


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def perf_metrics(payload: dict) -> dict[str, float]:
    """Extract the known perf metrics present in an ``ingest_perf`` payload."""
    out: dict[str, float] = {}
    for key in METRIC_META:
        f = _to_float(payload.get(key))
        if f is not None:
            out[key] = f
    return out


def normalize_perf_payload(payload: dict) -> Optional[dict]:
    """Canonicalize a ``vllm bench serve`` perf push. Returns None if NVIDIA."""
    if not isinstance(payload, dict):
        return None
    device = (payload.get("device") or "").strip()
    image = (payload.get("image") or "").strip()
    if not is_amd_workload(image=image, device=device):
        return None
    metrics = perf_metrics(payload)
    if not metrics:
        return None
    identity = _build_identity(payload)
    return {
        "event": "perf_result",
        "received_at": utcnow_iso(),
        "nightly": is_nightly(payload),
        "model": (payload.get("model") or "").strip(),
        "device": device,
        "precision": (payload.get("precision") or "").strip(),
        "tp": _to_int(payload.get("tp")),
        "isl": _to_int(payload.get("isl")),
        "osl": _to_int(payload.get("osl")),
        "conc": _to_int(payload.get("conc")),
        "date": payload.get("date") or "",
        **identity,
        "metrics": metrics,
    }


def model_from_eval(payload: dict) -> str:
    """Best-effort model id from an lm-eval ``results`` payload."""
    data = payload.get("data") or {}
    cfg = data.get("config") or {}
    for candidate in (cfg.get("model_name"), cfg.get("model"), data.get("model_name")):
        if candidate:
            return str(candidate)
    # model_args sometimes carries pretrained=<model>
    args = cfg.get("model_args")
    if isinstance(args, str):
        m = re.search(r"pretrained=([^,]+)", args)
        if m:
            return m.group(1)
    return ""


def accuracy_rows(payload: dict) -> list[dict]:
    """Flatten lm-eval ``results`` into ``{task, metric, value}`` rows.

    lm-eval reports each task as a dict of ``metric,filter`` keys. We keep the
    numeric, non-stderr metrics and tag the first as ``primary`` so the
    frontend can headline one score per task without hard-coding metric names.
    """
    data = payload.get("data") or {}
    results = data.get("results") or {}
    rows: list[dict] = []
    for task_name, metrics in results.items():
        if not isinstance(metrics, dict):
            continue
        primary_assigned = False
        for raw_key, raw_val in metrics.items():
            key = str(raw_key)
            if key in {"alias"} or "stderr" in key:
                continue
            value = _to_float(raw_val)
            if value is None:
                continue
            rows.append({
                "task": str(task_name),
                "metric": key,
                "value": value,
                "primary": not primary_assigned,
            })
            primary_assigned = True
    return rows


def normalize_eval_payload(payload: dict) -> Optional[dict]:
    """Canonicalize an lm-eval ``results`` push. Returns None if NVIDIA or empty."""
    if not isinstance(payload, dict) or payload.get("kind") != "results":
        return None
    workload = (payload.get("workload") or "").strip()
    image = (payload.get("image") or "").strip()
    device = (payload.get("device") or "").strip()
    if not is_amd_workload(workload=workload, image=image, device=device):
        return None
    rows = accuracy_rows(payload)
    if not rows:
        return None
    identity = _build_identity(payload)
    model = model_from_eval(payload) or workload
    return {
        "event": "accuracy_result",
        "received_at": utcnow_iso(),
        "nightly": is_nightly(payload),
        "model": model,
        "workload": workload,
        "task": (payload.get("task") or "").strip(),
        "device": device,
        **identity,
        "results": rows,
    }


def normalize_build_event(event_type: str, body: dict) -> Optional[dict]:
    """Canonicalize a Buildkite ``build.*`` event for the perf-eval pipeline."""
    pipeline = body.get("pipeline") or {}
    if (pipeline.get("slug") or "") != PERF_EVAL_PIPELINE_SLUG:
        return None
    build = body.get("build") or {}
    env = build.get("env") or {}
    image = (env.get("VLLM_IMAGE") or "").strip()
    commit = (env.get("VLLM_COMMIT") or "").strip() or commit_from_image(image)
    return {
        "event": "build",
        "received_at": utcnow_iso(),
        "event_type": event_type,
        "nightly": is_nightly({**build, "env": env}),
        "build_number": _to_int(build.get("number")),
        "build_url": build.get("web_url") or "",
        "build_commit": build.get("commit") or "",
        "branch": build.get("branch") or "",
        "state": build.get("state") or "",
        "message": build.get("message") or "",
        "created_at": build.get("created_at") or "",
        "finished_at": build.get("finished_at") or "",
        "image": image,
        "vllm_commit": commit,
    }


def normalize(headers: dict, body: dict) -> Optional[dict]:
    """Dispatch a raw payload to the right normalizer. Returns None to drop."""
    kind = classify_payload(headers, body)
    if kind == "buildkite":
        event_type = ""
        if headers:
            lower = {str(k).lower(): v for k, v in headers.items()}
            event_type = lower.get("x-buildkite-event", "")
        return normalize_build_event(event_type, body)
    if kind == "perf":
        return normalize_perf_payload(body)
    if kind == "eval":
        return normalize_eval_payload(body)
    return None


# ---------------------------------------------------------------------------
# Durable event store
# ---------------------------------------------------------------------------

# The event log and derived payload receive separate 4 MiB allocations inside
# the shared 8 MiB perf-eval state group. Both writers enforce this exact
# pre-replacement guard and remain far below the dashboard's 90 MB sync limit.
PERF_EVAL_MAX_BYTES = min(
    writer_max_bytes("perf_eval_events"),
    writer_max_bytes("perf_eval_summary"),
)

# Six months normally remain available for trend inspection. If an unusually
# wide workload set reaches the byte budget first, deterministic policies below
# shorten history while retaining at least two complete nightlies for deltas.
PERF_EVAL_HISTORY_DAYS = 180
PERF_EVAL_MIN_NIGHTLIES = 30

# Artifact collection supports at most a 30-day Buildkite lookback. Retain
# exact artifact identities longer than that window so pruning result history
# cannot make an artifact eligible for a repeated download.
PERF_EVAL_MAX_ARTIFACT_LOOKBACK_DAYS = 30
PERF_EVAL_ARTIFACT_IDENTITY_DAYS = 45

ARTIFACT_MARKER_EVENT = "buildkite_artifact_ingested"
ARTIFACT_INDEX_EVENT = "buildkite_artifact_identity_index"
ARTIFACT_INDEX_SCHEMA_VERSION = 1

_STORE_POLICIES = (
    (PERF_EVAL_HISTORY_DAYS, PERF_EVAL_MIN_NIGHTLIES, 30),
    (120, 20, 30),
    (90, 14, 30),
    (60, 10, 30),
    (45, 7, 30),
    (30, 4, 30),
    (14, 2, 14),
)


def enforced_byte_budget(requested: int) -> int:
    """Return a positive testable budget that can never raise the hard cap."""
    if requested <= 0:
        raise ValueError("perf-eval byte budget must be positive")
    return min(requested, PERF_EVAL_MAX_BYTES)


def _event_datetime(event: dict) -> Optional[datetime.datetime]:
    for raw in (
        event.get("date"),
        event.get("finished_at"),
        event.get("created_at"),
        event.get("received_at"),
        event.get("generated_at"),
    ):
        if not raw:
            continue
        text = str(raw).strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = (
                    datetime.datetime.fromisoformat(text)
                    if fmt is None
                    else datetime.datetime.strptime(text, fmt)
                )
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
    return None


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nightly_identity(event: dict) -> str:
    commit = str(event.get("vllm_commit") or event.get("build_commit") or "").strip()
    if commit:
        return f"commit:{commit}"
    if event.get("build_number") is not None:
        return f"build:{event['build_number']}"
    observed_at = _event_datetime(event)
    return f"date:{_iso(observed_at) if observed_at else event.get('received_at', '')}"


def artifact_key(record: dict) -> Optional[tuple]:
    """Return the exact stable identity for an artifact-bearing record."""
    artifact_id = str(record.get("buildkite_artifact_id") or "").strip()
    if artifact_id:
        return "id", artifact_id

    build_number = record.get("build_number")
    job_id = str(record.get("buildkite_artifact_job_id") or "").strip()
    path = str(record.get("buildkite_artifact_path") or "").strip().lstrip("./")
    sha1 = str(record.get("buildkite_artifact_sha1") or "").strip().lower()
    if build_number is not None and job_id and path and sha1:
        return "metadata", build_number, job_id, path, sha1
    return None


def _indexed_artifact_rows(event: dict) -> list[tuple[tuple, Optional[datetime.datetime]]]:
    if event.get("event") != ARTIFACT_INDEX_EVENT:
        key = artifact_key(event)
        return [(key, _event_datetime(event))] if key is not None else []

    rows: list[tuple[tuple, Optional[datetime.datetime]]] = []
    if event.get("schema_version") != ARTIFACT_INDEX_SCHEMA_VERSION:
        return rows
    for encoded in event.get("identities") or []:
        if not isinstance(encoded, list) or not encoded:
            continue
        if encoded[0] == "id" and len(encoded) == 3 and str(encoded[1]).strip():
            key = ("id", str(encoded[1]).strip())
            stamp = _event_datetime({"received_at": encoded[2]})
        elif encoded[0] == "metadata" and len(encoded) == 6:
            key = (
                "metadata",
                encoded[1],
                str(encoded[2]).strip(),
                str(encoded[3]).strip().lstrip("./"),
                str(encoded[4]).strip().lower(),
            )
            stamp = _event_datetime({"received_at": encoded[5]})
            if not all(key[2:]):
                continue
        else:
            continue
        rows.append((key, stamp))
    return rows


def artifact_keys_from_event(event: dict) -> tuple[tuple, ...]:
    """Expose direct and compact-index identities to pre-download dedup."""
    return tuple(key for key, _ in _indexed_artifact_rows(event))


def _encode_artifact_identity(key: tuple, observed_at: datetime.datetime) -> list:
    if key[0] == "id":
        return ["id", key[1], _iso(observed_at)]
    return ["metadata", *key[1:], _iso(observed_at)]


def _result_identity(event: dict) -> tuple:
    base = (
        event.get("event"),
        _nightly_identity(event),
        str(event.get("model") or "").strip(),
        str(event.get("device") or "").strip(),
    )
    if event.get("event") == "perf_result":
        return base + (
            event.get("tp"),
            event.get("precision"),
            event.get("isl"),
            event.get("osl"),
            event.get("conc"),
        )
    return base + (str(event.get("workload") or "").strip(),)


def _merge_result_events(previous: dict, current: dict) -> dict:
    """Make repeated nightly pushes one complete canonical observation."""
    merged = dict(previous)
    merged.update(current)
    if current.get("event") == "perf_result":
        merged["metrics"] = {
            **(previous.get("metrics") or {}),
            **(current.get("metrics") or {}),
        }
    else:
        rows = {}
        for event in (previous, current):
            for row in event.get("results") or []:
                if not isinstance(row, dict):
                    continue
                rows[(str(row.get("task") or ""), str(row.get("metric") or ""))] = row
        merged["results"] = [rows[key] for key in sorted(rows)]
    return merged


def _encoded_events(events: list[dict]) -> bytes:
    return "".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        for event in events
    ).encode("utf-8")


def _compact_events_once(
    events: list[dict],
    now: datetime.datetime,
    *,
    history_days: int,
    min_nightlies: int,
    auxiliary_days: int,
) -> list[dict]:
    normalized: list[tuple[int, dict, datetime.datetime]] = []
    artifact_identities: dict[tuple, datetime.datetime] = {}
    for position, raw in enumerate(events):
        if not isinstance(raw, dict):
            continue
        event = dict(raw)
        observed_at = _event_datetime(event)
        if observed_at is None:
            observed_at = now
            event.setdefault("received_at", _iso(now))
        normalized.append((position, event, observed_at))
        for key, identity_at in _indexed_artifact_rows(event):
            stamp = identity_at or observed_at
            oldest = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            if stamp > artifact_identities.get(key, oldest):
                artifact_identities[key] = stamp

    nightly_latest: dict[str, datetime.datetime] = {}
    for _, event, observed_at in normalized:
        if event.get("event") in {"perf_result", "accuracy_result"} and event.get("nightly") is True:
            identity = _nightly_identity(event)
            nightly_latest[identity] = max(nightly_latest.get(identity, observed_at), observed_at)
    protected_nightlies = {
        identity
        for identity, _ in sorted(
            nightly_latest.items(),
            key=lambda item: (item[1], item[0]),
        )[-min_nightlies:]
    }

    history_cutoff = now - datetime.timedelta(days=history_days)
    auxiliary_cutoff = now - datetime.timedelta(days=auxiliary_days)
    deduped_results: dict[tuple, tuple[int, dict, datetime.datetime]] = {}
    retained_other: list[tuple[int, dict, datetime.datetime]] = []
    for position, event, observed_at in normalized:
        kind = event.get("event")
        if kind in {ARTIFACT_MARKER_EVENT, ARTIFACT_INDEX_EVENT}:
            continue
        if kind in {"perf_result", "accuracy_result"}:
            keep = (
                event.get("nightly") is True
                and (
                    observed_at >= history_cutoff
                    or _nightly_identity(event) in protected_nightlies
                )
            ) or (event.get("nightly") is not True and observed_at >= auxiliary_cutoff)
            if not keep:
                continue
            key = _result_identity(event)
            previous = deduped_results.get(key)
            if previous is None:
                deduped_results[key] = (position, event, observed_at)
            else:
                deduped_results[key] = (
                    previous[0],
                    _merge_result_events(previous[1], event),
                    max(previous[2], observed_at),
                )
            continue
        if observed_at >= auxiliary_cutoff:
            retained_other.append((position, event, observed_at))

    retained = [*retained_other, *deduped_results.values()]
    # Preserve first-observed order. The aggregator already sorts every metric
    # series by timestamp, while a few legacy config labels use the first row's
    # metadata; chronological reordering here would change otherwise identical
    # dashboard output merely because the store was compacted.
    retained.sort(key=lambda row: (row[0], json.dumps(row[1], sort_keys=True)))
    compacted = [event for _, event, _ in retained]

    direct_keys = {
        key
        for event in compacted
        for key in artifact_keys_from_event(event)
    }
    identity_cutoff = now - datetime.timedelta(days=PERF_EVAL_ARTIFACT_IDENTITY_DAYS)
    index_rows = [
        _encode_artifact_identity(key, observed_at)
        for key, observed_at in artifact_identities.items()
        if key not in direct_keys and observed_at >= identity_cutoff
    ]
    index_rows.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    if index_rows:
        compacted.append(
            {
                "event": ARTIFACT_INDEX_EVENT,
                "schema_version": ARTIFACT_INDEX_SCHEMA_VERSION,
                "generated_at": _iso(now),
                "retention_days": PERF_EVAL_ARTIFACT_IDENTITY_DAYS,
                "identities": index_rows,
            }
        )
    return compacted


def compact_events(
    events: list[dict],
    *,
    now: Optional[datetime.datetime] = None,
    max_bytes: int = PERF_EVAL_MAX_BYTES,
) -> list[dict]:
    """Apply deterministic retention until exact JSONL bytes fit the budget."""
    max_bytes = enforced_byte_budget(max_bytes)
    current_time = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc
    )
    smallest: list[dict] = []
    for history_days, min_nightlies, auxiliary_days in _STORE_POLICIES:
        candidate = _compact_events_once(
            events,
            current_time,
            history_days=history_days,
            min_nightlies=min_nightlies,
            auxiliary_days=auxiliary_days,
        )
        smallest = candidate
        if len(_encoded_events(candidate)) <= max_bytes:
            return candidate
    required = len(_encoded_events(smallest))
    raise RuntimeError(
        "perf-eval events cannot fit the byte budget while preserving two "
        f"nightlies and the artifact dedup horizon: {required} > {max_bytes} bytes"
    )


def _atomic_write_bytes(path: Path, payload: bytes, *, max_bytes: int) -> None:
    """Check size in memory, then atomically replace the destination."""
    max_bytes = enforced_byte_budget(max_bytes)
    if len(payload) > max_bytes:
        raise RuntimeError(
            f"perf-eval payload exceeds its byte budget: {len(payload)} > {max_bytes} bytes"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def write_events_atomic(
    store_path: Path,
    events: list[dict],
    *,
    now: Optional[datetime.datetime] = None,
    max_bytes: int = PERF_EVAL_MAX_BYTES,
) -> int:
    compacted = compact_events(events, now=now, max_bytes=max_bytes)
    _atomic_write_bytes(store_path, _encoded_events(compacted), max_bytes=max_bytes)
    return len(compacted)


def append_events(
    store_path: Path,
    events: list[dict],
    *,
    now: Optional[datetime.datetime] = None,
    max_bytes: int = PERF_EVAL_MAX_BYTES,
) -> int:
    return write_events_atomic(
        store_path,
        [*read_events_strict(store_path), *events],
        now=now,
        max_bytes=max_bytes,
    )


def append_event(store_path: Path, event: dict) -> None:
    """Atomically append and compact one canonical event."""
    append_events(store_path, [event])


def read_events(store_path: Path) -> list[dict]:
    """Read every well-formed event from the durable store, in order."""
    if not store_path.exists():
        return []
    out: list[dict] = []
    for line in store_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.warning("skipping malformed event line")
            continue
        if not isinstance(event, dict):
            log.warning("skipping non-object event line")
            continue
        out.append(event)
    return out


def read_events_strict(store_path: Path) -> list[dict]:
    """Read a JSONL store without silently discarding any non-empty line."""
    if not store_path.exists():
        return []
    out: list[dict] = []
    for line_number, line in enumerate(
        store_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid perf-eval JSONL at {store_path}:{line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise ValueError(
                f"invalid perf-eval JSONL at {store_path}:{line_number}: "
                "event must be a JSON object"
            )
        if event.get("event") == ARTIFACT_INDEX_EVENT:
            identities = event.get("identities")
            decoded = _indexed_artifact_rows(event)
            invalid_index = (
                event.get("schema_version") != ARTIFACT_INDEX_SCHEMA_VERSION
                or not isinstance(identities, list)
                or len(decoded) != len(identities)
                or any(
                    stamp is None or (key[0] == "metadata" and key[1] is None)
                    for key, stamp in decoded
                )
            )
            if invalid_index:
                raise ValueError(
                    f"invalid perf-eval JSONL at {store_path}:{line_number}: "
                    "artifact identity index is not canonical"
                )
        out.append(event)
    return out


def encoded_json(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def write_json_atomic(
    path: Path,
    payload: dict,
    *,
    max_bytes: int = PERF_EVAL_MAX_BYTES,
) -> None:
    _atomic_write_bytes(path, encoded_json(payload), max_bytes=max_bytes)


# ---------------------------------------------------------------------------
# HTTP receiver (optional standalone deployment)
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = os.getenv("PERF_EVAL_WEBHOOK_SECRET", "")
STORE_PATH = Path(
    os.getenv(
        "PERF_EVAL_EVENT_STORE",
        str(Path(__file__).resolve().parents[2] / "data" / "vllm" / "perf_eval" / "events.jsonl"),
    )
)
LISTEN_PORT = int(os.getenv("PERF_EVAL_WEBHOOK_PORT", "8090"))


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify a Buildkite-style HMAC signature when a secret is configured."""
    if not WEBHOOK_SECRET:
        log.warning("No PERF_EVAL_WEBHOOK_SECRET set, skipping signature verification")
        return True
    expected = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature or "")


class PerfEvalWebhookHandler(BaseHTTPRequestHandler):
    """Persist incoming perf-eval webhook payloads to the event store."""

    store_path = STORE_PATH

    def _reply(self, status: int, body: str) -> None:
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        signature = self.headers.get("X-Buildkite-Signature", "")
        if not verify_signature(raw, signature):
            self._reply(401, "invalid signature")
            return
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, "invalid json")
            return
        event = normalize(dict(self.headers), body)
        if event is None:
            self._reply(200, "ignored")
            return
        append_event(self.store_path, event)
        self._reply(200, f"stored {event['event']}")

    def log_message(self, fmt, *args):  # noqa: A003
        log.info(fmt, *args)


def main():  # pragma: no cover - thin server wrapper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), PerfEvalWebhookHandler)
    log.info("perf-eval webhook listening on :%d -> %s", LISTEN_PORT, STORE_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":  # pragma: no cover
    main()
