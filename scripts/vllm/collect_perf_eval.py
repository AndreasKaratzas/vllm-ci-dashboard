#!/usr/bin/env python3
"""Aggregate webhook-fed perf-eval events into the dashboard's ``perf_eval.json``.

This collector never talks to the Buildkite API. It folds the bounded rolling
event store written by ``vllm.ci.perf_eval_webhook`` (``events.jsonl``) into a
single payload the ``Perf Eval`` tab renders.

Design goals (mirroring the task requirements):

* **AMD only** — NVIDIA workloads are dropped at webhook-normalization time, so
  every event here is already AMD; we still guard defensively.
* **Nightly only** — only events flagged ``nightly`` feed the executive view;
  retention removes complete oldest nightlies so a partial nightly is never
  presented as a complete comparison.
* **Self-updating workload set** — models, perf configs and accuracy tasks are
  discovered from the data, never hard-coded, so adding/removing/renaming a
  workload in the perf-eval repo is reflected automatically while older runs
  stay in each metric's time series.
* **Traceable provenance** — every series point keeps the vLLM commit, image
  and Buildkite build URL that produced it.
* **Executive framing** — each metric carries its ``direction`` (higher/lower
  is better) and a red/green ``status`` derived from the latest-vs-previous
  nightly delta.

Output: ``data/vllm/perf_eval/perf_eval.json``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm.ci.perf_eval_webhook import (  # noqa: E402
    ACCURACY_DIRECTION,
    METRIC_META,
    PERF_EVAL_ARTIFACT_IDENTITY_DAYS,
    PERF_EVAL_HISTORY_DAYS,
    PERF_EVAL_MAX_BYTES,
    encoded_json,
    enforced_byte_budget,
    is_amd_workload,
    read_events_strict,
    write_json_atomic,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORE = ROOT / "data" / "vllm" / "perf_eval" / "events.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "vllm" / "perf_eval" / "perf_eval.json"

PIPELINE_URL = "https://buildkite.com/vllm/perf-eval"

# A metric must move more than this fraction to be called a win/regression;
# smaller moves are run-to-run noise and render neutral (gray).
PERF_REL_THRESHOLD = 0.02
# Accuracy is reported on a 0..1 scale; a half-point absolute move is the
# minimum we treat as a real change rather than sampling noise.
ACCURACY_ABS_THRESHOLD = 0.005

_DERIVED_NIGHTLY_LIMITS = (180, 120, 90, 60, 45, 30, 14, 7, 2)


def _parse_ts(event: dict) -> datetime:
    """Best-effort sortable timestamp for an event.

    Prefer the run's own ``date`` (``YYYY-MM-DD HH:MM:SS`` from ingest_perf or
    an ISO build timestamp); fall back to ``received_at``.
    """
    for raw in (event.get("date"), event.get("created_at"), event.get("received_at")):
        if not raw:
            continue
        text = str(raw).strip().replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
            except ValueError:
                continue
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return datetime.min.replace(tzinfo=timezone.utc)


def _nightly_key(event: dict) -> str:
    """Stable identity for the nightly run an event belongs to.

    Commit is the most meaningful axis ("which vLLM produced this"); fall back
    to build number, then the calendar day so distinct nightlies never collapse.
    """
    commit = (event.get("vllm_commit") or event.get("build_commit") or "").strip()
    if commit:
        return f"commit:{commit[:12]}"
    if event.get("build_number") is not None:
        return f"build:{event['build_number']}"
    return f"date:{_parse_ts(event).strftime('%Y-%m-%d')}"


def _provenance(event: dict) -> dict:
    return {
        "vllm_commit": (event.get("vllm_commit") or "").strip(),
        "build_commit": (event.get("build_commit") or "").strip(),
        "image": (event.get("image") or "").strip(),
        "build_url": event.get("build_url") or "",
        "build_number": event.get("build_number"),
    }


def _config_label(device: str, isl, osl, conc) -> str:
    def fmt_len(n):
        if n is None:
            return "?"
        return f"{n // 1024}K" if n and n % 1024 == 0 and n >= 1024 else str(n)

    dev = (device or "").upper()
    return f"{fmt_len(isl)} in / {fmt_len(osl)} out @ conc {conc} ({dev})"


def _status(direction: str, latest: float, previous: float | None, *, rel: bool) -> dict:
    """Compute delta + red/green status for the latest-vs-previous nightly."""
    out = {
        "latest": latest,
        "previous": previous,
        "direction": direction,
        "delta": None,
        "delta_pct": None,
        "status": "neutral",
    }
    if previous is None:
        return out
    delta = latest - previous
    out["delta"] = delta
    if previous != 0:
        out["delta_pct"] = (delta / abs(previous)) * 100.0

    if rel:
        moved = previous != 0 and abs(delta / previous) >= PERF_REL_THRESHOLD
    else:
        moved = abs(delta) >= ACCURACY_ABS_THRESHOLD
    if not moved:
        out["status"] = "neutral"
        return out

    improved = delta > 0 if direction == "higher" else delta < 0
    out["status"] = "good" if improved else "bad"
    return out


def _series_from_points(points: list[dict]) -> list[dict]:
    """One entry per nightly (latest event wins), sorted oldest-first."""
    by_night: dict[str, dict] = {}
    for p in points:
        by_night[p["nightly_key"]] = p  # later events overwrite earlier
    return sorted(by_night.values(), key=lambda p: p["_ts"])


def _strip_internal(series: list[dict]) -> list[dict]:
    return [{k: v for k, v in p.items() if not k.startswith("_") and k != "nightly_key"} for p in series]


def build_perf_configs(perf_events: list[dict]) -> list[dict]:
    """Group perf events into per-config metric time series."""
    configs: dict[tuple, dict] = {}
    for ev in perf_events:
        device = (ev.get("device") or "").strip()
        isl, osl, conc = ev.get("isl"), ev.get("osl"), ev.get("conc")
        key = (device, isl, osl, conc)
        cfg = configs.setdefault(key, {
            "device": device,
            "isl": isl,
            "osl": osl,
            "conc": conc,
            "tp": ev.get("tp"),
            "precision": ev.get("precision") or "",
            "label": _config_label(device, isl, osl, conc),
            "_metric_points": {},
        })
        ts = _parse_ts(ev)
        nk = _nightly_key(ev)
        prov = _provenance(ev)
        for metric, value in (ev.get("metrics") or {}).items():
            cfg["_metric_points"].setdefault(metric, []).append({
                "nightly_key": nk,
                "_ts": ts,
                "date": ev.get("date") or "",
                "value": value,
                **prov,
            })

    out = []
    for cfg in configs.values():
        metric_points = cfg.pop("_metric_points")
        metrics_out = {}
        for metric, points in metric_points.items():
            meta = METRIC_META.get(metric, {"direction": "higher"})
            series = _series_from_points(points)
            latest = series[-1]["value"]
            previous = series[-2]["value"] if len(series) >= 2 else None
            block = _status(meta["direction"], latest, previous, rel=True)
            block.update({
                "label": meta.get("label", metric),
                "unit": meta.get("unit", ""),
                "series": _strip_internal(series),
            })
            metrics_out[metric] = block
        cfg["metrics"] = metrics_out
        out.append(cfg)
    # Stable, human-friendly ordering: device, then concurrency, then ISL/OSL.
    out.sort(key=lambda c: (c["device"], c.get("conc") or 0, c.get("isl") or 0, c.get("osl") or 0))
    return out


def build_accuracy_tasks(eval_events: list[dict]) -> list[dict]:
    """Group accuracy events into per-(task, metric) time series."""
    tasks: dict[tuple, dict] = {}
    for ev in eval_events:
        ts = _parse_ts(ev)
        nk = _nightly_key(ev)
        prov = _provenance(ev)
        for row in ev.get("results") or []:
            key = (row["task"], row["metric"])
            entry = tasks.setdefault(key, {
                "task": row["task"],
                "metric": row["metric"],
                "primary": bool(row.get("primary")),
                "_points": [],
            })
            entry["primary"] = entry["primary"] or bool(row.get("primary"))
            entry["_points"].append({
                "nightly_key": nk,
                "_ts": ts,
                "date": ev.get("date") or ev.get("received_at") or "",
                "value": row["value"],
                **prov,
            })

    out = []
    for entry in tasks.values():
        series = _series_from_points(entry.pop("_points"))
        latest = series[-1]["value"]
        previous = series[-2]["value"] if len(series) >= 2 else None
        block = _status(ACCURACY_DIRECTION, latest, previous, rel=False)
        entry.update(block)
        entry["series"] = _strip_internal(series)
        out.append(entry)
    out.sort(key=lambda t: (not t["primary"], t["task"], t["metric"]))
    return out


def _latest_identity(events: list[dict]) -> dict:
    if not events:
        return {}
    latest = max(events, key=_parse_ts)
    return {
        "date": latest.get("date") or latest.get("received_at") or "",
        **_provenance(latest),
    }


def aggregate(events: list[dict], *, generated_at: datetime | None = None) -> dict:
    """Fold the event log into the frontend payload (AMD + nightly only)."""
    perf_by_model: dict[str, list[dict]] = {}
    eval_by_model: dict[str, list[dict]] = {}
    devices: set[str] = set()
    nightlies: set[str] = set()

    for ev in events:
        if ev.get("event") not in {"perf_result", "accuracy_result"}:
            continue
        if ev.get("nightly") is not True:
            continue
        if not is_amd_workload(
            workload=ev.get("workload"), image=ev.get("image"), device=ev.get("device")
        ):
            continue
        model = (ev.get("model") or "").strip() or "(unknown model)"
        if ev.get("device"):
            devices.add(ev["device"])
        nightlies.add(_nightly_key(ev))
        if ev["event"] == "perf_result":
            perf_by_model.setdefault(model, []).append(ev)
        else:
            eval_by_model.setdefault(model, []).append(ev)

    models = []
    perf_points = accuracy_points = 0
    for model in sorted(set(perf_by_model) | set(eval_by_model)):
        perf_events = perf_by_model.get(model, [])
        eval_events = eval_by_model.get(model, [])
        perf_configs = build_perf_configs(perf_events)
        accuracy_tasks = build_accuracy_tasks(eval_events)
        perf_points += sum(
            len(m["series"]) for c in perf_configs for m in c["metrics"].values()
        )
        accuracy_points += sum(len(t["series"]) for t in accuracy_tasks)
        model_devices = sorted({c["device"] for c in perf_configs if c["device"]} |
                               {e.get("device") for e in eval_events if e.get("device")})
        models.append({
            "model": model,
            "devices": model_devices,
            "latest": _latest_identity(perf_events + eval_events),
            "nightly_count": len({_nightly_key(e) for e in perf_events + eval_events}),
            "perf_configs": perf_configs,
            "accuracy_tasks": accuracy_tasks,
        })

    metric_meta = {k: dict(v) for k, v in METRIC_META.items()}
    metric_meta["accuracy"] = {"label": "Accuracy", "unit": "", "direction": ACCURACY_DIRECTION}

    return {
        "generated_at": (generated_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": {"org": "vllm", "slug": "perf-eval", "url": PIPELINE_URL},
        "metric_meta": metric_meta,
        "thresholds": {
            "perf_rel": PERF_REL_THRESHOLD,
            "accuracy_abs": ACCURACY_ABS_THRESHOLD,
        },
        "models": models,
        "summary": {
            "models": len(models),
            "amd_devices": sorted(devices),
            "nightlies": len(nightlies),
            "perf_points": perf_points,
            "accuracy_points": accuracy_points,
        },
    }


def bounded_aggregate(
    events: list[dict],
    *,
    generated_at: datetime | None = None,
    max_bytes: int = PERF_EVAL_MAX_BYTES,
) -> dict:
    """Build a complete payload, shortening whole-nightly history only if needed."""
    max_bytes = enforced_byte_budget(max_bytes)
    timestamp = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    nightly_latest: dict[str, datetime] = {}
    for event in events:
        if (
            event.get("event") not in {"perf_result", "accuracy_result"}
            or event.get("nightly") is not True
        ):
            continue
        identity = _nightly_key(event)
        observed_at = _parse_ts(event)
        nightly_latest[identity] = max(
            nightly_latest.get(identity, observed_at), observed_at
        )
    ordered_nightlies = [
        identity
        for identity, _ in sorted(
            nightly_latest.items(),
            key=lambda item: (item[1], item[0]),
        )
    ]

    for nightly_limit in _DERIVED_NIGHTLY_LIMITS:
        allowed = set(ordered_nightlies[-nightly_limit:])
        selected = [
            event
            for event in events
            if event.get("event") not in {"perf_result", "accuracy_result"}
            or event.get("nightly") is not True
            or _nightly_key(event) in allowed
        ]
        payload = aggregate(selected, generated_at=timestamp)
        payload["retention"] = {
            "event_history_days": PERF_EVAL_HISTORY_DAYS,
            "artifact_identity_days": PERF_EVAL_ARTIFACT_IDENTITY_DAYS,
            "max_bytes": max_bytes,
            "nightly_limit": nightly_limit,
            "adaptive": len(ordered_nightlies) > nightly_limit,
        }
        if len(encoded_json(payload)) <= max_bytes:
            return payload

    required = len(encoded_json(payload))
    raise RuntimeError(
        "perf_eval.json cannot fit the byte budget while preserving the latest "
        f"two complete nightlies: {required} > {max_bytes} bytes"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="Path to events.jsonl")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to perf_eval.json")
    args = parser.parse_args()

    events = read_events_strict(Path(args.store))
    generated_at = datetime.now(timezone.utc)
    payload = bounded_aggregate(events, generated_at=generated_at)
    out = Path(args.output)
    write_json_atomic(out, payload)
    log.info(
        "Wrote %s: %d models, %d nightlies, %d perf points, %d accuracy points",
        out,
        payload["summary"]["models"],
        payload["summary"]["nightlies"],
        payload["summary"]["perf_points"],
        payload["summary"]["accuracy_points"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
