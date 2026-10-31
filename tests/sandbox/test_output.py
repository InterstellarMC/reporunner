"""Unit tests for output stream processing, truncation, ANSI stripping, and redaction."""

from backend.sandbox.exec.output import (
    process_output,
    redact_secrets,
    strip_ansi,
    truncate_stream,
)


def test_strip_ansi():
    colored = "\x1b[31mError:\x1b[0m \x1b[1;32mTest failed\x1b[0m"
    assert strip_ansi(colored) == "Error: Test failed"


def test_secret_redaction():
    text = "Connecting with API_KEY=sk-proj-supersecret12345 to server"
    redacted = redact_secrets(text, secrets=["sk-proj-supersecret12345"])
    assert "sk-proj-supersecret12345" not in redacted
    assert "[REDACTED]" in redacted


def test_output_truncation():
    # Generate 300 KiB content
    large_text = "A" * (300 * 1024)
    trunc, was_trunc = truncate_stream(large_text, max_bytes=256 * 1024, head_bytes=64 * 1024, tail_bytes=64 * 1024)
    assert was_trunc
    assert "[TRUNCATED" in trunc
    assert len(trunc) < len(large_text)


def test_process_output_integration():
    stdout = "\x1b[32mSuccess\x1b[0m with key my-secret-token"
    stderr = "Warning: low disk"
    out, err, truncated = process_output(stdout, stderr, secrets=["my-secret-token"])
    assert out == "Success with key [REDACTED]"
    assert err == "Warning: low disk"
    assert not truncated
