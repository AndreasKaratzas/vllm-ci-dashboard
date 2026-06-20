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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vllm.pipelines import NIGHTLY_NAME_PATTERNS_BY_SLUG  # noqa: E402

log = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("BUILDKITE_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "AndreasKaratzas/vllm-ci-dashboard")
LISTEN_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# Only process these pipelines for the heavier CI collection flow.
WATCHED_PIPELINES = {"amd-ci", "ci"}
QUEUE_EVENT_TYPES = {
    "job.scheduled",
    "job.started",
    "job.finished",
    "agent.connected",
    "agent.disconnected",
    "agent.lost",
    "agent.stopping",
}


def is_nightly_build(build: dict, pipeline_slug: str = "") -> bool:
    """Check if this build is a nightly/daily build we care about."""
    pattern = NIGHTLY_NAME_PATTERNS_BY_SLUG.get(pipeline_slug)
    if not pattern:
        return False
    message = build.get("message") or ""
    return re.search(pattern, message, flags=re.IGNORECASE) is not None


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
        build = event.get("build", {}) or {}
        pipeline = event.get("pipeline", {}) or {}
        job = event.get("job", {}) or {}
        pipeline_slug = pipeline.get("slug", "")
        build_number = build.get("number", 0)

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
            log.info(
                "Received queue event %s for pipeline=%s build=%s queue=%s",
                event_type,
                pipeline_slug,
                build_number,
                ((job.get("cluster_queue") or {}).get("key")) or "",
            )
            success = trigger_github_dispatch(
                "buildkite_queue_changed",
                {
                    "buildkite_event": event_type,
                    "pipeline": pipeline_slug,
                    "build_number": build_number,
                },
            )
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
    log.info("Watching queue events: %s", sorted(QUEUE_EVENT_TYPES))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
