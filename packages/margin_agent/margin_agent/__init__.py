"""The governed agent: playbooks, bounded runs, and proposals.

The agent reads the corpus and proposes. It does not decide. A playbook
describes how a domain is read; a run is a bounded, cancellable unit of work
with a visible trail; a proposal is a change a human still has to accept.
"""

from __future__ import annotations

from margin_agent.playbooks import (
    Playbook,
    PlaybookError,
    discover_playbooks,
    discovery_roots,
    load_supporting_file,
    select_playbooks,
)

__all__ = [
    "Playbook",
    "PlaybookError",
    "discover_playbooks",
    "discovery_roots",
    "load_supporting_file",
    "select_playbooks",
]
