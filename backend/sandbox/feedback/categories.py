"""Mechanical error categorization rules based on observable error signatures."""

from __future__ import annotations

import re
from typing import List, Tuple
from backend.sandbox.api.schemas import ErrorCategory

# Ordered pattern table: (Category, list of compiled regexes)
CATEGORY_RULES: List[Tuple[ErrorCategory, List[re.Pattern]]] = [
    (
        ErrorCategory.TIMEOUT,
        [
            re.compile(r"timed\s*out|execution\s+timed\s+out|command\s+timed\s+out", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.OOM,
        [
            re.compile(r"out\s+of\s+memory|killed\s+process|memoryerror|oom-killer", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.DISK_FULL,
        [
            re.compile(r"no\s+space\s+left\s+on\s+device|disk\s+quota\s+exceeded", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.IMPORT_ERROR,
        [
            re.compile(r"ModuleNotFoundError:\s+No module named", re.IGNORECASE),
            re.compile(r"ImportError:\s+cannot import name", re.IGNORECASE),
            re.compile(r"Cannot find module\s+'[^']+'", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.DEPENDENCY_MISSING,
        [
            re.compile(r"No module named\s+'[^']+'", re.IGNORECASE),
            re.compile(r"pkg_resources\.DistributionNotFound", re.IGNORECASE),
            re.compile(r"Could not find a version that satisfies the requirement", re.IGNORECASE),
            re.compile(r"error: externally-managed-environment", re.IGNORECASE),
            re.compile(r"error:\s+command\s+'[^']+'\s+failed:\s+No such file", re.IGNORECASE),
            re.compile(r"npm ERR!\s+code\s+ENOENT", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.DEPENDENCY_CONFLICT,
        [
            re.compile(r"ResolutionImpossible|ContextualVersionConflict|pip's dependency resolver does not currently take into account", re.IGNORECASE),
            re.compile(r"npm ERR!\s+code\s+ERESOLVE|Conflicting peer dependency", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.SYNTAX_ERROR,
        [
            re.compile(r"SyntaxError:\s+", re.IGNORECASE),
            re.compile(r"IndentationError:\s+", re.IGNORECASE),
            re.compile(r"Parsing error:\s+", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.ENV_VAR_MISSING,
        [
            re.compile(r"KeyError:\s+'[A-Z0-9_]*(?:KEY|SECRET|TOKEN|URL|HOST|PORT|PASSWORD|API)[A-Z0-9_]*'", re.IGNORECASE),
            re.compile(r"environment variable (?:'[A-Z0-9_]+'|[A-Z0-9_]+) (?:is not set|missing|required)", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.PORT_IN_USE,
        [
            re.compile(r"address already in use|EADDRINUSE|errno\s+98", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.PERMISSION_DENIED,
        [
            re.compile(r"PermissionDenied|PermissionError|EACCES|operation not permitted", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.TEST_FAILURE,
        [
            re.compile(r"===+ FAILURES ===+|FAILED\s+tests/|FAIL:\s+test_", re.IGNORECASE),
            re.compile(r"npm test.*?failed|npm ERR!\s+Test failed", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.BUILD_FAILURE,
        [
            re.compile(r"error: failed building wheel|setup\.py\s+bdist_wheel\s+did not run successfully", re.IGNORECASE),
            re.compile(r"npm ERR!\s+code\s+ELIFECYCLE", re.IGNORECASE),
        ],
    ),
    (
        ErrorCategory.RUNTIME_EXCEPTION,
        [
            re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
            re.compile(r"Uncaught Error:|UnhandledPromiseRejectionWarning:", re.IGNORECASE),
        ],
    ),
]


def classify_error(exit_code: int, stderr: str, stdout: str) -> ErrorCategory:
    """
    Classify error using fixed deterministic rule table.
    Returns ErrorCategory (NONE if exit_code == 0, UNKNOWN if no pattern matches).
    """
    if exit_code == 0:
        return ErrorCategory.NONE

    combined = f"{stderr}\n{stdout}"

    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if pat.search(combined):
                return category

    return ErrorCategory.UNKNOWN
