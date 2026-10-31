"""Stream output truncation, ANSI stripping, and secret redaction."""

from __future__ import annotations

import re
from typing import Iterable, Tuple

ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
DEFAULT_MAX_BYTES = 256 * 1024  # 256 KiB
DEFAULT_HEAD_BYTES = 64 * 1024
DEFAULT_TAIL_BYTES = 192 * 1024


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes and terminal control sequences."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


def redact_secrets(text: str, secrets: Iterable[str]) -> str:
    """Redact known secret values from text."""
    redacted = text
    for secret in secrets:
        if secret and len(secret) >= 3:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def truncate_stream(
    content: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    head_bytes: int = DEFAULT_HEAD_BYTES,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
) -> Tuple[str, bool]:
    """
    Truncate content to max_bytes keeping head and tail.
    Returns (truncated_content, was_truncated).
    """
    raw_bytes = content.encode("utf-8", errors="replace")
    if len(raw_bytes) <= max_bytes:
        return content, False

    head = raw_bytes[:head_bytes].decode("utf-8", errors="ignore")
    tail = raw_bytes[-tail_bytes:].decode("utf-8", errors="ignore")
    omitted = len(raw_bytes) - (head_bytes + tail_bytes)

    marker = f"\n\n... [TRUNCATED {omitted} BYTES] ...\n\n"
    return f"{head}{marker}{tail}", True


def process_output(
    stdout: str,
    stderr: str,
    secrets: Iterable[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Tuple[str, str, bool]:
    """Clean, redact, and truncate both stdout and stderr streams."""
    clean_out = strip_ansi(stdout)
    clean_err = strip_ansi(stderr)

    redacted_out = redact_secrets(clean_out, secrets)
    redacted_err = redact_secrets(clean_err, secrets)

    trunc_out, out_trunc = truncate_stream(redacted_out, max_bytes=max_bytes)
    trunc_err, err_trunc = truncate_stream(redacted_err, max_bytes=max_bytes)

    return trunc_out, trunc_err, (out_trunc or err_trunc)
