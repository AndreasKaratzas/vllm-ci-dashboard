"""Adapt read-only GitHub CLI calls to the shared bounded HTTP transport."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

import requests

from github_transport import GitHubResponse, GitHubTransport


_TRANSPORT = GitHubTransport()
CLI_TIMEOUT_SECONDS = 45
_STATUS_LINE = re.compile(r"^HTTP/[\d.]+\s+(\d{3})\b")
_STDERR_STATUS = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)


def _explicit_rate_limit(message: str) -> bool:
    return any(
        part in message.lower()
        for part in (
            "secondary rate limit", "rate limit exceeded", "rate limit was exceeded",
            "abuse detection mechanism",
        )
    )


def _response_from_process(result: subprocess.CompletedProcess) -> GitHubResponse:
    """Preserve HTTP status and headers even when ``gh`` exits nonzero."""
    output = str(result.stdout or "").replace("\r\n", "\n")
    match = _STATUS_LINE.match(output)
    if match:
        head, separator, body = output.partition("\n\n")
        if not separator:
            raise requests.RequestException("GitHub CLI returned incomplete HTTP headers")
        headers = {}
        for line in head.splitlines()[1:]:
            key, colon, value = line.partition(":")
            if colon:
                headers[key.strip().lower()] = value.strip()
        return GitHubResponse(int(match.group(1)), headers, body)

    # Some gh failures have no HTTP output, but retain an explicit status in
    # stderr. Ordinary authorization failures must remain permanent errors;
    # rate-limit responses still go through the transport's cooldown policy.
    stderr = str(result.stderr or "").strip()
    status = _STDERR_STATUS.search(stderr)
    if status:
        return GitHubResponse(int(status.group(1)), {}, stderr)
    # GraphQL and older gh versions can omit the status entirely. Explicit
    # throttling still requires the shared cooldown, not network backoff.
    if _explicit_rate_limit(stderr):
        return GitHubResponse(429, {}, stderr)
    raise requests.RequestException(stderr or "GitHub CLI returned no HTTP response")


def github_cli_json(
    command: list[str],
    *,
    endpoint: str,
    env: dict[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    transport: GitHubTransport | None = None,
) -> Any:
    """Run an authorized read with one shared retry and cooldown budget."""
    url = endpoint if endpoint.startswith("https://") else (
        f"https://api.github.com/{endpoint.lstrip('/')}"
    )

    def send() -> GitHubResponse:
        kwargs = {
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": CLI_TIMEOUT_SECONDS,
        }
        if env is not None:
            kwargs["env"] = env
        try:
            result = runner([*command, "--include"], **kwargs)
        except subprocess.CalledProcessError as exc:
            result = subprocess.CompletedProcess(
                exc.cmd, exc.returncode, stdout=exc.stdout, stderr=exc.stderr
            )
        except subprocess.TimeoutExpired as exc:
            raise requests.Timeout("GitHub CLI request timed out") from exc
        except OSError as exc:
            raise requests.RequestException("GitHub CLI transport unavailable") from exc
        response = _response_from_process(result)
        if 200 <= response.status_code < 300:
            try:
                payload = response.json()
            except ValueError as exc:
                raise requests.RequestException("GitHub CLI returned invalid JSON") from exc
            # GraphQL may report throttling inside HTTP 200. Only explicit
            # rate-limit errors are retried; other errors remain visible to
            # the caller's existing GraphQL shape/completeness checks.
            if url.endswith("/graphql") and isinstance(payload, dict):
                for error in payload.get("errors") or []:
                    if not isinstance(error, dict):
                        continue
                    message = str(error.get("message") or "")
                    if error.get("type") == "RATE_LIMITED" or _explicit_rate_limit(message):
                        return GitHubResponse(429, response.headers, response.text)
        return response

    return (transport or _TRANSPORT).request(url, send).json()
