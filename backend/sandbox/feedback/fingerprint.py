"""Normalized failure fingerprints for repeated error detection."""

from __future__ import annotations

import hashlib
import re

# Regex patterns to strip variable details: paths, line numbers, memory addresses, timestamps, versions
PATH_PATTERN = re.compile(r"(?:/[a-zA-Z0-9_\.\-]+)+|(?:[a-zA-Z]:\\[a-zA-Z0-9_\.\-\\]+)")
LINE_NO_PATTERN = re.compile(r"(?:\bline\s+\d+\b|:\d+:|:\d+\b)", re.IGNORECASE)
HEX_ADDR_PATTERN = re.compile(r"0x[0-9a-fA-F]+")
TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
VERSION_PATTERN = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9_\.\-]+)?\b")


def normalize_error_text(text: str) -> str:
    """Normalize error string by removing volatile context (paths, line numbers, timestamps)."""
    norm = text.strip()
    norm = TIMESTAMP_PATTERN.sub("<TS>", norm)
    # Strip line numbers first (e.g. /path/to/file.py:42 -> /path/to/file.py<LINE>)
    norm = LINE_NO_PATTERN.sub("<LINE>", norm)
    norm = PATH_PATTERN.sub("<PATH>", norm)
    norm = HEX_ADDR_PATTERN.sub("<HEX>", norm)
    norm = VERSION_PATTERN.sub("<VER>", norm)
    norm = re.sub(r"\s+", " ", norm)
    return norm


def compute_fingerprint(category: str, raw_error: str) -> str:
    """
    Generate deterministic error fingerprint from error category and normalized key lines.
    """
    if not raw_error or not raw_error.strip():
        return f"{category}:none"

    lines = [ln.strip() for ln in raw_error.strip().splitlines() if ln.strip()]
    key_line = lines[-1] if lines else ""

    norm_line = normalize_error_text(key_line)
    digest = hashlib.sha256(norm_line.encode("utf-8")).hexdigest()[:12]
    return f"{category}:{digest}"
