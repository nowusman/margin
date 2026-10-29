"""Explicit allocation of a context budget.

When a large domain is read in one pass, something has to give: the index, the
records, or the space left for the answer. Truncating arbitrarily produces
answers that depend on where the cut happened, so the allocation is stated
up front, reported back, and configurable.

The budget governs surrounding context. It never truncates evidence text,
because a shortened passage stops matching the quote a citation points at.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_CONTEXT_BUDGET_CHARS = 32_000
CONTEXT_BUDGET_ENVIRONMENT_VARIABLE = "MARGIN_CONTEXT_BUDGET_CHARS"
INDEX_SHARE = 0.05
RESPONSE_RESERVE_SHARE = 0.15
PER_PAGE_SHARE = 0.30
MINIMUM_PAGE_CHARS = 5_000

CONTEXT_BUDGET_PRESETS: dict[str, int] = {
    "4k": 4_096,
    "8k": 8_192,
    "16k": 16_384,
    "32k": 32_768,
    "64k": 65_536,
    "128k": 131_072,
    "256k": 262_144,
    "1m": 1_048_576,
}


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """A resolved allocation of context characters."""

    total_chars: int
    index_chars: int
    pages_chars: int
    response_reserve_chars: int
    per_page_chars: int

    def as_dict(self) -> dict[str, int]:
        return {
            "total_chars": self.total_chars,
            "index_chars": self.index_chars,
            "pages_chars": self.pages_chars,
            "response_reserve_chars": self.response_reserve_chars,
            "per_page_chars": self.per_page_chars,
        }


def compute_context_budget(
    total_chars: int = DEFAULT_CONTEXT_BUDGET_CHARS,
    *,
    index_share: float = INDEX_SHARE,
    response_reserve_share: float = RESPONSE_RESERVE_SHARE,
) -> ContextBudget:
    """Resolve a total character budget into its parts.

    The index takes a small fixed share, the answer keeps a reserve so that a
    large domain cannot crowd out the response, and the remainder is available
    for records. A single record may use up to a third of the record budget,
    with a floor so that one substantial page is always readable.
    """

    total = max(1, int(total_chars))
    index_chars = int(total * index_share)
    response_reserve_chars = int(total * response_reserve_share)
    pages_chars = max(0, total - index_chars - response_reserve_chars)
    per_page_chars = min(pages_chars, max(MINIMUM_PAGE_CHARS, int(pages_chars * PER_PAGE_SHARE)))
    return ContextBudget(
        total_chars=total,
        index_chars=index_chars,
        pages_chars=pages_chars,
        response_reserve_chars=response_reserve_chars,
        per_page_chars=per_page_chars,
    )


def configured_context_budget_chars() -> int:
    """Return the globally configured budget.

    The global setting is an environment variable so that a deployment can
    change it without a code change. A malformed or absent value falls back to
    the built-in default rather than failing a question.
    """

    raw = os.getenv(CONTEXT_BUDGET_ENVIRONMENT_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_CONTEXT_BUDGET_CHARS
    try:
        configured = int(raw)
    except ValueError:
        return DEFAULT_CONTEXT_BUDGET_CHARS
    return configured if configured > 0 else DEFAULT_CONTEXT_BUDGET_CHARS


def resolve_context_budget(total_chars: int | None, override: int | None = None) -> ContextBudget:
    """Resolve the budget, preferring the most authoritative setting.

    A domain's configured budget is a governance decision for that domain, so it
    wins over a budget asked for with one question. The global configuration is
    used when neither applies.
    """

    if override is not None and override > 0:
        return compute_context_budget(override)
    if total_chars is not None and total_chars > 0:
        return compute_context_budget(total_chars)
    return compute_context_budget(configured_context_budget_chars())
