"""Buildkite webhook handler for triggering CI and queue data collection.

This script can be used as a lightweight webhook endpoint that receives
Buildkite events and triggers GitHub Actions via ``repository_dispatch``.

Two deployment options:

Option A: GitHub Actions repository_dispatch (recommended)
  - Configure Buildkite notification services to call the GitHub API directly
  - No separate server needed

Option B: Standalone webhook receiver (for advanced setups)
  - Run this script as a small HTTP server
  - It validates Buildkite webhook signatures and triggers GitHub Actions

Queue-changing events (``job.*`` / ``agent.*``) dispatch the lightweight queue
monitor workflow. Nightly ``build.finished`` events still dispatch the heavier
CI collection workflow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vllm.ci.utils import queue_from_rules  # noqa: E402
from vllm.constants import TRACKED_QUEUES, is_excluded_queue  # noqa: E402
from vllm.pipelines import NIGHTLY_NAME_PATTERNS_BY_SLUG, PIPELINES  # noqa: E402

log = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("BUILDKITE_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "AndreasKaratzas/vllm-ci-dashboard")
LISTEN_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# Only process configured CI pipelines for the heavier collection flow.
WATCHED_PIPELINES = {str(config["slug"]) for config in PIPELINES.values()}
# The AMD perf-eval pipeline drives the separate Perf Eval collection flow.
PERF_EVAL_PIPELINE = "perf-eval"
PERF_EVAL_NIGHTLY_MESSAGE_RE = re.compile(
    r"^\s*nightly\s+run\s+\d{4}-\d{2}-\d{2}\s*:\s*"
    r"commit\s+[0-9a-f]{7,40}(?:\s|$)",
    re.IGNORECASE,
)
QUEUE_WATCHED_PIPELINES = frozenset(WATCHED_PIPELINES | {PERF_EVAL_PIPELINE})
QUEUE_EVENT_TYPES = {
    "job.scheduled",
    "job.started",
    "job.finished",
    "agent.connected",
    "agent.disconnected",
    "agent.lost",
    "agent.stopping",
}
AGENT_QUEUE_EVENT_TYPES = frozenset(
    event_type for event_type in QUEUE_EVENT_TYPES if event_type.startswith("agent.")
)
TRACKED_QUEUE_KEYS = {str(queue).casefold(): str(queue) for queue in TRACKED_QUEUES}


def _nonnegative_env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("Ignoring invalid %s=%r; using %.1fs", name, raw, default)
        return default


QUEUE_EVENT_DEBOUNCE_SECONDS = _nonnegative_env_seconds(
    "BUILDKITE_QUEUE_WEBHOOK_DEBOUNCE_SECONDS", 60.0
)
QUEUE_EVENT_DUPLICATE_TTL_SECONDS = _nonnegative_env_seconds(
    "BUILDKITE_QUEUE_WEBHOOK_DUPLICATE_TTL_SECONDS", 10 * 60.0
)


class QueueDispatchCoalescer:
    """Bound queue dispatch bursts after a successful repository dispatch.

    The first relevant event dispatches immediately. Distinct events arriving
    during the short debounce window are covered by that organization-wide
    snapshot and do not enqueue redundant GitHub workflows. Exact successful
    deliveries remain deduplicated for a longer TTL. Failed dispatch attempts
    never start either suppression window, so a Buildkite retry can recover.
    """

    def __init__(
        self,
        debounce_seconds: float,
        duplicate_ttl_seconds: float,
        *,
        clock=None,
    ):
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.duplicate_ttl_seconds = max(
            self.debounce_seconds, float(duplicate_ttl_seconds)
        )
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_success_at: float | None = None
        self._recent_successes: dict[str, float] = {}
        self._in_flight: str | None = None

    def claim(self, identity: str) -> str:
        """Return ``dispatch`` or a reason this event is already covered."""
        now = self.clock()
        with self._lock:
            cutoff = now - self.duplicate_ttl_seconds
            self._recent_successes = {
                key: observed_at
                for key, observed_at in self._recent_successes.items()
                if observed_at > cutoff
            }
            if identity in self._recent_successes:
                return "duplicate"
            if self._in_flight is not None:
                return "in_flight"
            if (
                self._last_success_at is not None
                and now - self._last_success_at < self.debounce_seconds
            ):
                # This event is covered by the successful org-wide snapshot
                # already enqueued for the burst. Remember its identity so an
                # exact redelivery after the short window stays coalesced.
                self._recent_successes[identity] = now
                return "debounced"
            self._in_flight = identity
            return "dispatch"

    def finish(self, identity: str, success: bool) -> None:
        """Complete a claim; only success activates deduplication."""
        with self._lock:
            if self._in_flight == identity:
                self._in_flight = None
            if success:
                observed_at = self.clock()
                self._last_success_at = observed_at
                self._recent_successes[identity] = observed_at


QUEUE_DISPATCH_COALESCER = QueueDispatchCoalescer(
    QUEUE_EVENT_DEBOUNCE_SECONDS,
    QUEUE_EVENT_DUPLICATE_TTL_SECONDS,
)


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _queue_from_container(container: dict | None) -> str:
    if not isinstance(container, dict):
        return ""
    for field in ("cluster_queue", "clusterQueue", "queue"):
        value = container.get(field)
        if isinstance(value, dict):
            value = value.get("key") or value.get("slug") or value.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    for field in ("agent_query_rules", "agentQueryRules", "query_rules", "meta_data"):
        queue = queue_from_rules(container.get(field))
        if queue:
            return queue
    return ""


def queue_for_event(event: dict) -> str:
    """Return the canonical configured queue represented by a webhook."""
    job = _as_dict(event.get("job"))
    agent = _as_dict(event.get("agent"))
    candidates = (
        _queue_from_container(job),
        _queue_from_container(job.get("agent") if isinstance(job, dict) else None),
        _queue_from_container(agent),
        _queue_from_container(event),
    )
    for candidate in candidates:
        canonical = TRACKED_QUEUE_KEYS.get(str(candidate or "").casefold())
        if canonical and not is_excluded_queue(canonical):
            return canonical
    return ""


def pipeline_for_event(event: dict) -> str:
    """Extract a pipeline slug from common Buildkite webhook shapes."""
    job = _as_dict(event.get("job"))
    build = _as_dict(event.get("build"))
    containers = (
        event.get("pipeline"),
        job.get("pipeline"),
        build.get("pipeline"),
    )
    for container in containers:
        if isinstance(container, dict) and container.get("slug"):
            return str(container["slug"])
    return ""


def queue_event_context(event_type: str, event: dict) -> dict | None:
    """Return dispatch context only for configured pipeline/queue events."""
    if event_type not in QUEUE_EVENT_TYPES or not isinstance(event, dict):
        return None
    pipeline_slug = pipeline_for_event(event)
    if event_type not in AGENT_QUEUE_EVENT_TYPES:
        # Job events are build-scoped and must identify one of our configured
        # pipelines. This drops unrelated organization webhook traffic.
        if pipeline_slug not in QUEUE_WATCHED_PIPELINES:
            return None
    elif pipeline_slug and pipeline_slug not in QUEUE_WATCHED_PIPELINES:
        return None

    queue = queue_for_event(event)
    if not queue:
        return None
    build = _as_dict(event.get("build"))
    job = _as_dict(event.get("job"))
    agent = _as_dict(event.get("agent"))
    job_build = _as_dict(job.get("build"))
    return {
        "pipeline": pipeline_slug,
        "queue": queue,
        "build_number": build.get("number") or job_build.get("number") or 0,
        "job_id": job.get("id") or job.get("uuid") or "",
        "agent_id": agent.get("id") or agent.get("uuid") or "",
    }


def queue_event_identity(event_type: str, event: dict, context: dict) -> str:
    """Build a stable exact-delivery identity without retaining payload data."""
    serialized = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return "|".join(
        (
            event_type,
            str(context.get("pipeline") or ""),
            str(context.get("queue") or ""),
            str(context.get("build_number") or 0),
            str(context.get("job_id") or context.get("agent_id") or ""),
            digest,
        )
    )


def is_nightly_build(build: dict, pipeline_slug: str = "") -> bool:
    """Check if this build is a nightly/daily build we care about."""
    pattern = NIGHTLY_NAME_PATTERNS_BY_SLUG.get(pipeline_slug)
    if not pattern:
        return False
    message = build.get("message") or ""
    return re.search(pattern, message, flags=re.IGNORECASE) is not None


def is_perf_eval_nightly_build(build: dict) -> bool:
    """Return whether a perf-eval build is from the canonical nightly cohort.

    Ad-hoc perf-eval builds also upload the results tree, so dispatching on
    every ``build.finished`` event needlessly starts artifact collection.  The
    scheduled cohort has a stable message containing its date and exact vLLM
    commit; require that signal on the main branch before dispatching.
    """
    if not isinstance(build, dict):
        return False
    branch = str(build.get("branch") or "").strip().casefold()
    if branch != "main":
        return False
    message = str(build.get("message") or "")
    return PERF_EVAL_NIGHTLY_MESSAGE_RE.search(message) is not None


def trigger_github_dispatch(event_type: str, payload: dict | None = None) -> bool:
    """Trigger a GitHub workflow via repository_dispatch."""
    if not GITHUB_TOKEN:
        log.error("GITHUB_TOKEN not set, cannot trigger GitHub Action")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    body = {
        "event_type": event_type,
        "client_payload": payload or {},
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        log.info("Triggered GitHub dispatch %s", event_type)
        return True
    except Exception as exc:
        log.error("Failed to trigger GitHub Action: %s", exc)
        return False


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the Buildkite webhook HMAC signature."""
    if not WEBHOOK_SECRET:
        log.warning("No BUILDKITE_WEBHOOK_SECRET set, skipping signature verification")
        return True

    expected = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for Buildkite webhook events."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        signature = self.headers.get("X-Buildkite-Signature", "")
        if not verify_signature(body, signature):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid JSON")
            return

        event_type = self.headers.get("X-Buildkite-Event", "")
        build = _as_dict(event.get("build"))
        job = _as_dict(event.get("job"))
        pipeline_slug = pipeline_for_event(event)
        build_number = build.get("number", 0)

        if event_type == "build.finished" and pipeline_slug == PERF_EVAL_PIPELINE:
            # Perf-eval nightlies feed the Perf Eval tab. Dispatch a dedicated
            # event so the collector reprocesses the webhook event log promptly
            # instead of waiting for the next cron tick.
            if not is_perf_eval_nightly_build(build):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Ignored (not canonical perf-eval nightly)")
                return
            log.info("Received perf-eval build.finished #%d: %s", build_number, build.get("state"))
            success = trigger_github_dispatch(
                "perf_eval_build_finished",
                {
                    "pipeline": pipeline_slug,
                    "build_number": build_number,
                    "state": build.get("state", ""),
                },
            )
            self.send_response(200 if success else 500)
            self.end_headers()
            self.wfile.write(b"Triggered perf-eval collection" if success else b"Failed to trigger")
            return

        if event_type == "build.finished":
            if pipeline_slug not in WATCHED_PIPELINES:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Ignored (unwatched pipeline)")
                return
            if not is_nightly_build(build, pipeline_slug):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Ignored (not nightly)")
                return

            log.info(
                "Received build.finished for %s #%d: %s",
                pipeline_slug,
                build_number,
                build.get("state"),
            )
            success = trigger_github_dispatch(
                "buildkite_build_finished",
                {
                    "pipeline": pipeline_slug,
                    "build_number": build_number,
                    "state": build.get("state", ""),
                },
            )
            self.send_response(200 if success else 500)
            self.end_headers()
            self.wfile.write(b"Triggered CI collection" if success else b"Failed to trigger")
            return

        if event_type in QUEUE_EVENT_TYPES:
            context = queue_event_context(event_type, event)
            if context is None:
                log.info(
                    "Ignored unrelated queue event %s for pipeline=%s queue=%s",
                    event_type,
                    pipeline_slug,
                    _queue_from_container(job) or _queue_from_container(event.get("agent")),
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Ignored (unwatched pipeline or queue)")
                return

            identity = queue_event_identity(event_type, event, context)
            disposition = QUEUE_DISPATCH_COALESCER.claim(identity)
            if disposition != "dispatch":
                log.info(
                    "Coalesced queue event %s for pipeline=%s queue=%s (%s)",
                    event_type,
                    context["pipeline"],
                    context["queue"],
                    disposition,
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(f"Coalesced ({disposition})".encode())
                return

            log.info(
                "Received queue event %s for pipeline=%s build=%s queue=%s",
                event_type,
                context["pipeline"],
                context["build_number"],
                context["queue"],
            )
            success = False
            try:
                success = trigger_github_dispatch(
                    "buildkite_queue_changed",
                    {
                        "buildkite_event": event_type,
                        "pipeline": context["pipeline"],
                        "build_number": context["build_number"],
                        "queue": context["queue"],
                    },
                )
            finally:
                QUEUE_DISPATCH_COALESCER.finish(identity, success)
            self.send_response(200 if success else 500)
            self.end_headers()
            self.wfile.write(b"Triggered queue collection" if success else b"Failed to trigger")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ignored")

    def log_message(self, format, *args):
        log.info(format, *args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set - webhook will not be able to trigger GitHub Actions")

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), WebhookHandler)
    log.info("Webhook server listening on port %d", LISTEN_PORT)
    log.info("Watching nightly pipelines for CI dispatch: %s", WATCHED_PIPELINES)
    log.info(
        "Watching queue events for %d configured queues in pipelines %s: %s",
        len(TRACKED_QUEUE_KEYS),
        sorted(QUEUE_WATCHED_PIPELINES),
        sorted(QUEUE_EVENT_TYPES),
    )
    log.info(
        "Queue dispatch coalescing: %.1fs burst window, %.1fs duplicate TTL",
        QUEUE_EVENT_DEBOUNCE_SECONDS,
        QUEUE_EVENT_DUPLICATE_TTL_SECONDS,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
