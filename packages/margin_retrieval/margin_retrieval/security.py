from __future__ import annotations

import re

BLOCK_PATTERNS = (
    r"ignore (all|previous) instructions",
    r"reveal (secrets|system prompt)",
    r"write to (database|db)",
    r"bypass rbac",
)


def sanitize_user_query(query: str) -> str:
    value = query.strip()
    value = re.sub(r"\s+", " ", value)
    return value[:2000]


def detect_prompt_injection(query: str) -> list[str]:
    lowered = query.casefold()
    findings: list[str] = []
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, lowered):
            findings.append(pattern)
    return findings
