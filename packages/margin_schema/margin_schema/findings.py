"""Shared vocabulary for deterministic lint findings.

A finding is the unit of work that every lint rule produces, whether the rule
checks record integrity or graph discoverability. It lives here so that the
persistence layer and the pure computation layer can both speak about findings
without depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HygieneFinding:
    """A deterministic condition that a human may need to review."""

    rule_id: str
    severity: str
    target_type: str
    target_id: str
    message: str
    evidence: tuple[dict[str, Any], ...] = ()
    recommendation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "message": self.message,
            "evidence": [dict(item) for item in self.evidence],
            "recommendation": self.recommendation,
        }
