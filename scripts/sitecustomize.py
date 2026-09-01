"""Fail-closed activation for the workflow's Buildkite request guard."""

from __future__ import annotations

import os
import sys


if any(
    name in os.environ
    for name in (
        "BUILDKITE_TOKEN",
        "BUILDKITE_API_TOKEN",
        "BUILDKITE_REQUEST_GUARD_FILE",
        "BUILDKITE_REQUEST_GUARD_ATTEMPT_ID",
        "BUILDKITE_REQUEST_GUARD_ALLOWANCE",
    )
):
    try:
        from vllm.buildkite_request_guard import install_from_environment

        install_from_environment()
    except BaseException as exc:  # sitecustomize exceptions are otherwise ignored.
        sys.stderr.write(f"fatal Buildkite request guard activation error: {exc}\n")
        sys.stderr.flush()
        os._exit(78)
