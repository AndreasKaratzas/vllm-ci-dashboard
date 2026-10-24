"""Hardware-neutral label matching shared by CI ownership consumers."""

from __future__ import annotations

import re
from typing import Any


_MULTISPACE = re.compile(r"\s+")
_AMD_PREFIX = re.compile(r"^AMD:\s*", re.IGNORECASE)
_INTERNAL_AMD_PREFIX = re.compile(r"^mi\d{3,4}b?_\d+:\s*", re.IGNORECASE)
_PLATFORM_PREFIX = re.compile(
    r"^:(?:amd|nvidia|computer):\s*\(\s*[a-z0-9][a-z0-9._-]*(?:\s+[a-z0-9][a-z0-9._-]*)*\s*\)\s*",
    re.IGNORECASE,
)
_AMD_DEVICE_SUFFIX = re.compile(r"\s*\((mi\d{3,4}b?_\d+)\)\s*$", re.IGNORECASE)


def _clean_job_label(value: Any) -> str:
    text = _MULTISPACE.sub(" ", str(value or "").strip()).strip()
    text = _AMD_PREFIX.sub("", text)
    text = _INTERNAL_AMD_PREFIX.sub("", text)
    text = _PLATFORM_PREFIX.sub("", text)
    text = _AMD_DEVICE_SUFFIX.sub("", text)
    return _MULTISPACE.sub(" ", text.replace(r"\%N", "%N")).strip()


def hardware_fold_key(value: Any) -> str:
    """Fold equivalent device decorations without merging distinct GPU widths."""
    text = _clean_job_label(value).lower()
    text = text.replace("%n", "%N")
    text = re.sub(r"\s+nightly\s+b200\b", "", text)
    text = re.sub(r"\((\d+)x(?:h100|h200|a100|b200|gh200)(?:\s*-\s*\d+xmi\d{3,4}b?)?\)", r"(\1 gpus)", text)
    text = re.sub(r"\((\d+)\s*(?:h100s?|h200s?|a100s?|b200s?|gh200s?)\)", r"(\1 gpus)", text)
    text = re.sub(r"\((\d+)\s*gpus?\)\s*\((?:h100|h200|a100|b200|gh200|mi\d{3,4}b?)\)", r"(\1 gpus)", text)
    text = re.sub(r"\((?:h100|h200|a100|b200|gh200|cuda)\s*-\s*mi\d{3,4}b?\)", "", text)
    text = re.sub(r"\((?:h100|h200|a100|b200|gh200|cuda|mi\d{3,4}b?)\)", "", text)
    text = re.sub(r"\btests\b", "test", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()
