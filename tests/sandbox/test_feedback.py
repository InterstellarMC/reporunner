"""Unit tests for error categorization and normalized fingerprints."""

import pytest
from backend.sandbox.api.schemas import ErrorCategory
from backend.sandbox.feedback.categories import classify_error
from backend.sandbox.feedback.fingerprint import compute_fingerprint, normalize_error_text


def test_classify_import_error():
    stderr = "Traceback (most recent call last):\n  File 'app.py', line 5\nModuleNotFoundError: No module named 'flask'"
    assert classify_error(exit_code=1, stderr=stderr, stdout="") == ErrorCategory.IMPORT_ERROR


def test_classify_syntax_error():
    stderr = "  File 'server.py', line 12\n    def foo(\n            ^\nSyntaxError: unexpected EOF while parsing"
    assert classify_error(exit_code=1, stderr=stderr, stdout="") == ErrorCategory.SYNTAX_ERROR


def test_classify_missing_env_var():
    stderr = "KeyError: 'DATABASE_URL'"
    assert classify_error(exit_code=1, stderr=stderr, stdout="") == ErrorCategory.ENV_VAR_MISSING


def test_classify_port_in_use():
    stderr = "Error: listen EADDRINUSE: address already in use 0.0.0.0:8000"
    assert classify_error(exit_code=1, stderr=stderr, stdout="") == ErrorCategory.PORT_IN_USE


def test_classify_test_failure():
    stdout = "=== FAILURES ===\n____ test_login ____\nAssertionError: expected 200 got 500"
    assert classify_error(exit_code=1, stderr="", stdout=stdout) == ErrorCategory.TEST_FAILURE


def test_classify_clean_success():
    assert classify_error(exit_code=0, stderr="", stdout="All tests passed") == ErrorCategory.NONE


def test_fingerprint_invariance_across_paths_and_lines():
    """Ensure identical error on different line numbers and paths produces the identical fingerprint."""
    err1 = "ModuleNotFoundError: No module named 'flask' at /home/user/proj/main.py:42"
    err2 = "ModuleNotFoundError: No module named 'flask' at /workspace/sub/app.py:108"

    fp1 = compute_fingerprint("IMPORT_ERROR", err1)
    fp2 = compute_fingerprint("IMPORT_ERROR", err2)

    assert fp1 == fp2
    assert fp1.startswith("IMPORT_ERROR:")
