from __future__ import annotations

import json
from hashlib import sha256

from sqlalchemy.orm import Session

from margin_db.knowledge_models import ClaimEvidence, IdeaBlockRevision
from margin_db.source_models import SourceElement
from margin_schema.extraction import ExtractedClaimEvidence, ExtractedIdeaBlock


def validate_claim_evidence(
    evidence: ExtractedClaimEvidence, source_element: SourceElement | None
) -> tuple[int | None, int | None]:
    """Validate evidence against the cited element without remapping its source."""

    if source_element is None:
        raise ValueError(f"source element not found: {evidence.source_element_id}")
    if evidence.source_element_id != source_element.id:
        raise ValueError("claim evidence source_element_id does not match the cited element")
    if evidence.evidence_kind == "derived_visual":
        _validate_visual_evidence(evidence, source_element)
        return None, None

    source_text = source_element.text or ""
    quote = evidence.source_quote
    local_start = source_text.find(quote)
    if local_start < 0:
        raise ValueError(f"source quote is not supported by element {source_element.id}")
    local_end = local_start + len(quote)
    base_offset = source_element.char_start or 0
    expected_start = base_offset + local_start
    expected_end = base_offset + local_end
    if evidence.quote_char_start is not None and evidence.quote_char_start != expected_start:
        raise ValueError(f"source quote start does not match element {source_element.id}")
    if evidence.quote_char_end is not None and evidence.quote_char_end != expected_end:
        raise ValueError(f"source quote end does not match element {source_element.id}")
    if source_element.char_end is not None and expected_end > source_element.char_end:
        raise ValueError(f"source quote exceeds element {source_element.id} bounds")
    return expected_start, expected_end


def upsert_claim_evidence(
    session: Session,
    *,
    idea_block_id: str,
    idea_block_revision_id: str,
    candidate: ExtractedIdeaBlock,
    evidence: ExtractedClaimEvidence,
    extraction_run_id: str | None,
    source_element: SourceElement | None = None,
) -> ClaimEvidence:
    revision = session.get(IdeaBlockRevision, idea_block_revision_id)
    if revision is None:
        raise ValueError(f"idea block revision not found: {idea_block_revision_id}")
    if revision.idea_block_id != idea_block_id:
        raise ValueError("claim evidence revision does not belong to the cited IdeaBlock")
    element_id = evidence.source_element_id
    if source_element is None:
        source_element = session.get(SourceElement, element_id)
    quote_start, quote_end = validate_claim_evidence(evidence, source_element)
    payload = {
        "extraction_run_id": extraction_run_id,
        "idea_block_revision_id": idea_block_revision_id,
        "source_element_id": element_id,
        "quote_char_start": quote_start,
        "quote_char_end": quote_end,
        "evidence_kind": evidence.evidence_kind,
        "polarity": evidence.polarity,
        "qualifiers": evidence.qualifiers,
        "valid_from": evidence.valid_from.isoformat() if evidence.valid_from else None,
        "valid_to": evidence.valid_to.isoformat() if evidence.valid_to else None,
        "region": evidence.region.model_dump(mode="json") if evidence.region is not None else None,
    }
    evidence_id = "ce_" + sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    row = session.get(ClaimEvidence, evidence_id)
    if row is None:
        row = ClaimEvidence(
            id=evidence_id,
            idea_block_id=idea_block_id,
            idea_block_revision_id=idea_block_revision_id,
            source_element_id=element_id,
        )
        session.add(row)
    row.idea_block_id = idea_block_id
    row.idea_block_revision_id = idea_block_revision_id
    row.source_element_id = element_id
    row.quote_char_start = quote_start
    row.quote_char_end = quote_end
    row.evidence_kind = evidence.evidence_kind
    row.extraction_run_id = extraction_run_id
    row.source_quote = evidence.source_quote
    row.polarity = evidence.polarity
    row.confidence = candidate.confidence
    row.extraction_method = candidate.extraction_method
    row.qualifiers = evidence.qualifiers
    row.valid_from = evidence.valid_from
    row.valid_to = evidence.valid_to
    row.status = "active"
    metadata = dict(candidate.metadata or {})
    if evidence.region is not None:
        metadata["evidence_region"] = evidence.region.model_dump(mode="json")
    row.metadata_ = metadata
    session.flush()
    return row


def _validate_visual_evidence(evidence: ExtractedClaimEvidence, source_element: SourceElement) -> None:
    region = evidence.region
    if region is None:
        raise ValueError("derived visual evidence requires an asset checksum and bounding box")
    source_metadata = source_element.metadata_ or {}
    source_checksum = source_element.checksum or source_metadata.get("asset_checksum")
    if not source_element.asset_key or not source_checksum:
        raise ValueError(f"visual element {source_element.id} has no validated asset reference")
    if region.asset_checksum != source_checksum:
        raise ValueError(f"visual evidence checksum does not match element {source_element.id}")
    source_box = _bounding_box(source_element.bounding_box)
    if source_box is None or tuple(region.bounding_box) != source_box:
        raise ValueError(f"visual evidence bounding box does not match element {source_element.id}")


def _bounding_box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    coordinates = value.get("coords")
    if not isinstance(coordinates, list | tuple) or len(coordinates) != 4:
        return None
    if not all(isinstance(coordinate, int | float) for coordinate in coordinates):
        return None
    left, top, width, height = (float(coordinate) for coordinate in coordinates)
    return left, top, width, height
