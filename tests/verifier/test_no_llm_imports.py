"""Lint test: asserts NO LLM / model SDK is imported in the independent verifier."""

import os
import re

FORBIDDEN_SDK_PATTERNS = [
    re.compile(r"\bimport\s+(?:litellm|openai|anthropic|google\.generativeai|langchain|transformers)\b"),
    re.compile(r"\bfrom\s+(?:litellm|openai|anthropic|google\.generativeai|langchain|transformers)\b"),
]


def test_independent_verifier_has_zero_llm_dependencies():
    verifier_dir = os.path.join("backend", "verifier")
    assert os.path.exists(verifier_dir), f"{verifier_dir} must exist"

    violations = []
    for root, _, files in os.walk(verifier_dir):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fp:
                    content = fp.read()
                    for pattern in FORBIDDEN_SDK_PATTERNS:
                        match = pattern.search(content)
                        if match:
                            violations.append(f"{path}: forbidden LLM import '{match.group(0)}'")

    assert not violations, f"Independent verifier must contain NO LLM imports:\n" + "\n".join(violations)
