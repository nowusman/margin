from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from margin_schema.text_normalization import normalize_entity_name

ID_VERSION = "margin-id-v1"

SOURCE_PREFIX = "src_"
SOURCE_VERSION_PREFIX = "srcv_"
PAGE_PREFIX = "page_"
ELEMENT_PREFIX = "elem_"
IDEA_BLOCK_PREFIX = "ib_"
IDEA_BLOCK_REVISION_PREFIX = "ibr_"
ENTITY_PREFIX = "ent_"
RELATIONSHIP_PREFIX = "rel_"
GRAPH_DECISION_PREFIX = "gdec_"
DOMAIN_CHARTER_PREFIX = "charter_"
PERSONAL_NOTE_PREFIX = "pnote_"
SAVED_ITEM_PREFIX = "saved_"
PERSONAL_VIEW_PREFIX = "pview_"
ASSISTANT_THREAD_PREFIX = "thread_"
ASSISTANT_MESSAGE_PREFIX = "amsg_"
EXPORT_BUNDLE_PREFIX = "export_"
AGENT_RUN_PREFIX = "run_"
AGENT_EVENT_PREFIX = "runevt_"
AGENT_PROPOSAL_PREFIX = "proposal_"
AGENT_INPUT_PREFIX = "runq_"

ALLOWED_PREFIXES = frozenset(
    {
        SOURCE_PREFIX,
        SOURCE_VERSION_PREFIX,
        PAGE_PREFIX,
        ELEMENT_PREFIX,
        IDEA_BLOCK_PREFIX,
        ENTITY_PREFIX,
        RELATIONSHIP_PREFIX,
        GRAPH_DECISION_PREFIX,
        DOMAIN_CHARTER_PREFIX,
        PERSONAL_NOTE_PREFIX,
        SAVED_ITEM_PREFIX,
        PERSONAL_VIEW_PREFIX,
        ASSISTANT_THREAD_PREFIX,
        ASSISTANT_MESSAGE_PREFIX,
        EXPORT_BUNDLE_PREFIX,
        AGENT_RUN_PREFIX,
        AGENT_EVENT_PREFIX,
        AGENT_PROPOSAL_PREFIX,
        AGENT_INPUT_PREFIX,
    }
)


def canonicalize_payload(payload: Any) -> Any:
    """Return a JSON-serializable, deterministic representation of a payload."""
    if payload is None or isinstance(payload, bool | int):
        return payload

    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise ValueError("non-finite floats cannot be used in deterministic IDs")
        return payload

    if isinstance(payload, Decimal):
        return {"$decimal": format(payload, "f")}

    if isinstance(payload, str):
        return unicodedata.normalize("NFC", payload)

    if isinstance(payload, bytes | bytearray | memoryview):
        data = bytes(payload)
        return {"$bytes_sha256": hashlib.sha256(data).hexdigest(), "$bytes_len": len(data)}

    if isinstance(payload, Path):
        return unicodedata.normalize("NFC", payload.as_posix())

    if isinstance(payload, datetime):
        return {"$datetime": payload.isoformat()}

    if isinstance(payload, date):
        return {"$date": payload.isoformat()}

    if isinstance(payload, Mapping):
        normalized_items = []
        for key, value in payload.items():
            normalized_key = canonicalize_payload(key)
            if not isinstance(normalized_key, str):
                raise TypeError("mapping keys used for deterministic IDs must normalize to strings")
            normalized_items.append((normalized_key, canonicalize_payload(value)))
        return {key: value for key, value in sorted(normalized_items, key=lambda item: item[0])}

    if isinstance(payload, tuple | list):
        return [canonicalize_payload(item) for item in payload]

    if isinstance(payload, set | frozenset):
        normalized_items = [canonicalize_payload(item) for item in payload]
        return sorted(normalized_items, key=_canonical_json)

    raise TypeError(f"unsupported deterministic ID payload type: {type(payload).__name__}")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def canonical_json(payload: Any) -> str:
    """Serialize a payload to canonical JSON for hashing and tests."""
    return _canonical_json(canonicalize_payload(payload))


def canonical_bytes(payload: Any) -> bytes:
    """Serialize a payload to stable UTF-8 bytes."""
    return canonical_json(payload).encode("utf-8")


def deterministic_id(prefix: str, payload: Any) -> str:
    """Create a stable, prefixed Margin identifier from canonicalized payload content."""
    if prefix not in ALLOWED_PREFIXES:
        allowed = ", ".join(sorted(ALLOWED_PREFIXES))
        raise ValueError(f"unknown deterministic ID prefix {prefix!r}; expected one of: {allowed}")

    digest = hashlib.sha256()
    digest.update(ID_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(prefix.encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical_bytes(payload))
    return f"{prefix}{digest.hexdigest()}"


def source_id(payload: Any) -> str:
    return deterministic_id(SOURCE_PREFIX, payload)


def source_version_id(payload: Any) -> str:
    return deterministic_id(SOURCE_VERSION_PREFIX, payload)


def page_id(payload: Any) -> str:
    return deterministic_id(PAGE_PREFIX, payload)


def element_id(payload: Any) -> str:
    return deterministic_id(ELEMENT_PREFIX, payload)


def idea_block_id(payload: Any) -> str:
    return deterministic_id(IDEA_BLOCK_PREFIX, payload)


def idea_block_revision_id(payload: Any) -> str:
    return IDEA_BLOCK_REVISION_PREFIX + deterministic_id(IDEA_BLOCK_PREFIX, payload)[len(IDEA_BLOCK_PREFIX) :]


def entity_id(payload: Any) -> str:
    if isinstance(payload, Mapping) and "normalized_label" in payload:
        normalized_label = payload["normalized_label"]
        if not isinstance(normalized_label, str) or not normalize_entity_name(normalized_label):
            raise ValueError("entity ID requires a non-empty normalized label")
    return deterministic_id(ENTITY_PREFIX, payload)


def relationship_id(payload: Any) -> str:
    return deterministic_id(RELATIONSHIP_PREFIX, payload)


def graph_decision_id(payload: Any) -> str:
    return deterministic_id(GRAPH_DECISION_PREFIX, payload)


def domain_charter_id(payload: Any) -> str:
    return deterministic_id(DOMAIN_CHARTER_PREFIX, payload)


def personal_note_id(payload: Any) -> str:
    return deterministic_id(PERSONAL_NOTE_PREFIX, payload)


def saved_item_id(payload: Any) -> str:
    return deterministic_id(SAVED_ITEM_PREFIX, payload)


def personal_view_id(payload: Any) -> str:
    return deterministic_id(PERSONAL_VIEW_PREFIX, payload)


def assistant_thread_id(payload: Any) -> str:
    return deterministic_id(ASSISTANT_THREAD_PREFIX, payload)


def assistant_message_id(payload: Any) -> str:
    return deterministic_id(ASSISTANT_MESSAGE_PREFIX, payload)


def export_bundle_id(payload: Any) -> str:
    return deterministic_id(EXPORT_BUNDLE_PREFIX, payload)


def agent_run_id(payload: Any) -> str:
    return deterministic_id(AGENT_RUN_PREFIX, payload)


def agent_event_id(payload: Any) -> str:
    return deterministic_id(AGENT_EVENT_PREFIX, payload)


def agent_proposal_id(payload: Any) -> str:
    return deterministic_id(AGENT_PROPOSAL_PREFIX, payload)


def agent_human_input_id(payload: Any) -> str:
    return deterministic_id(AGENT_INPUT_PREFIX, payload)


__all__ = [
    "ALLOWED_PREFIXES",
    "ELEMENT_PREFIX",
    "ENTITY_PREFIX",
    "IDEA_BLOCK_PREFIX",
    "ID_VERSION",
    "PAGE_PREFIX",
    "RELATIONSHIP_PREFIX",
    "SOURCE_PREFIX",
    "SOURCE_VERSION_PREFIX",
    "canonical_bytes",
    "canonical_json",
    "canonicalize_payload",
    "deterministic_id",
    "element_id",
    "entity_id",
    "idea_block_id",
    "idea_block_revision_id",
    "page_id",
    "relationship_id",
    "source_id",
    "source_version_id",
]
