from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_DOCUMENT_CLASSES = frozenset(
    {
        "adr",
        "architecture_principle",
        "risk_register",
        "ownership_matrix",
        "interface_catalog",
        "openapi_contract",
        "event_contract",
        "structurizr_diagram",
        "mermaid_diagram",
        "plantuml_diagram",
        "pdf_tables_images",
        "contradictory_versioned",
    }
)

EVIDENCE_POLARITIES = frozenset({"supports", "refutes"})
RELATIONSHIP_POLARITIES = frozenset({"supports", "refutes"})
EVALUATION_MODES = frozenset({"deterministic", "operational"})


@dataclass(frozen=True)
class GoldEvidenceAnchor:
    """A exact evidence anchor that a persisted claim or relationship must cite."""

    quote: str
    section_path: str | None = None
    page_number: int | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "quote": self.quote,
            "section_path": self.section_path,
            "page_number": self.page_number,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class GoldClaimExpectation:
    id: str
    statement: str
    claim_type: str
    polarity: str = "supports"
    evidence_anchors: tuple[GoldEvidenceAnchor, ...] = ()
    qualifiers: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "statement": self.statement,
            "claim_type": self.claim_type,
            "polarity": self.polarity,
            "evidence_anchors": [anchor.to_dict() for anchor in self.evidence_anchors],
            "qualifiers": dict(self.qualifiers),
        }


@dataclass(frozen=True)
class GoldStructuredFieldExpectation:
    field_path: str
    expected_value: object


@dataclass(frozen=True)
class GoldEntityExpectation:
    name: str
    kind: str
    external_id: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoldRelationshipExpectation:
    id: str
    source_name: str
    target_name: str
    relation_type: str
    polarity: str = "supports"
    evidence_anchor: GoldEvidenceAnchor | None = None
    temporal_group: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass(frozen=True)
class GoldNoClaimExpectation:
    element_id: str
    verification_method: str
    verifier_version: str


@dataclass(frozen=True)
class GoldRetrievalExpectation:
    """A retrieval assertion derived from one canonical corpus fixture."""

    fixture_id: str
    query: str
    expected_idea_block_ids: tuple[str, ...]
    scope: dict[str, object]


@dataclass(frozen=True)
class GoldFixture:
    """Typed expected records for a single gold-corpus fixture document."""

    fixture_id: str
    document_class: str
    path: Path
    domain_id: str
    category: str
    sensitivity: str
    source_uri: str | None = None
    evaluation_mode: str = "deterministic"
    claims: tuple[GoldClaimExpectation, ...] = ()
    fields: tuple[GoldStructuredFieldExpectation, ...] = ()
    entities: tuple[GoldEntityExpectation, ...] = ()
    relationships: tuple[GoldRelationshipExpectation, ...] = ()
    no_claim_elements: tuple[GoldNoClaimExpectation, ...] = ()
    version_group: str | None = None
    version: int | None = None
    supersedes: tuple[str, ...] = ()
    expected_assertions: tuple[str, ...] = ()
    expected_pages: int | None = None
    expected_elements: tuple[dict[str, object], ...] = ()
    evidence_locations: tuple[dict[str, object], ...] = ()
    entity_outcomes: tuple[dict[str, object], ...] = ()
    semantic_decisions: tuple[dict[str, object], ...] = ()
    governance_counts: dict[str, int] = field(default_factory=dict)
    governance_records: dict[str, object] | None = None
    search_expectations: dict[str, object] | None = None
    citations: tuple[dict[str, object], ...] = ()

    def source_identity(self) -> str:
        """Return the source identity this fixture is ingested under.

        The harness and the evaluation must agree on it, or a fixture would be
        ingested as one document and measured against another.
        """

        return self.source_uri or f"internal://{self.domain_id}/{self.fixture_id}"


@dataclass(frozen=True)
class GoldCorpus:
    version: str
    fixtures: tuple[GoldFixture, ...]
    evaluation_modes: tuple[str, ...]

    def fixture_ids(self) -> tuple[str, ...]:
        return tuple(fixture.fixture_id for fixture in self.fixtures)

    def retrieval_expectations(self) -> tuple[GoldRetrievalExpectation, ...]:
        expectations: list[GoldRetrievalExpectation] = []
        for fixture in self.fixtures:
            search = fixture.search_expectations
            if search is None:
                continue
            query = search.get("query")
            expected_idea_block_id = search.get("top_result_id")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"gold corpus fixture {fixture.fixture_id} has an invalid retrieval query")
            if not isinstance(expected_idea_block_id, str) or not expected_idea_block_id:
                raise ValueError(f"gold corpus fixture {fixture.fixture_id} has an invalid retrieval target")
            expectations.append(
                GoldRetrievalExpectation(
                    fixture_id=fixture.fixture_id,
                    query=query,
                    expected_idea_block_ids=(expected_idea_block_id,),
                    scope={"domain_ids": [fixture.domain_id]},
                )
            )
        return tuple(expectations)


def load_gold_corpus(path: Path = Path("tests/fixtures/gold_corpus/manifest.json")) -> GoldCorpus:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("gold corpus manifest must be an object")
    version = _required_string(payload, "version")
    rows = payload.get("fixtures")
    if not isinstance(rows, list) or not rows:
        raise ValueError("gold corpus manifest must contain a non-empty 'fixtures' list")
    fixtures = tuple(_fixture(row) for row in rows)
    _validate_fixture_ids(fixtures)
    _validate_required_classes(fixtures)
    _validate_version_groups(fixtures)
    evaluation_modes = _collect_evaluation_modes(fixtures)
    return GoldCorpus(version=version, fixtures=fixtures, evaluation_modes=evaluation_modes)


def _validate_fixture_ids(fixtures: tuple[GoldFixture, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for fixture in fixtures:
        if not fixture.fixture_id:
            raise ValueError("gold corpus fixtures must declare a non-empty fixture_id")
        if fixture.fixture_id in seen:
            duplicates.append(fixture.fixture_id)
        seen.add(fixture.fixture_id)
    if duplicates:
        raise ValueError(f"gold corpus has duplicate fixture_id values: {duplicates}")
    for fixture in fixtures:
        for claim in fixture.claims:
            full_id = f"{fixture.fixture_id}::{claim.id}"
            if full_id in seen:
                raise ValueError(f"gold corpus has duplicate claim id: {full_id}")
            seen.add(full_id)
        for relationship in fixture.relationships:
            full_id = f"{fixture.fixture_id}::{relationship.id}"
            if full_id in seen:
                raise ValueError(f"gold corpus has duplicate relationship id: {full_id}")
            seen.add(full_id)


def _fixture(row: object) -> GoldFixture:
    if not isinstance(row, dict):
        raise ValueError("gold corpus fixture entries must be objects")
    fixture_id = _required_string(row, "fixture_id")
    document_class = _required_string(row, "class")
    source_path = Path(_required_string(row, "path"))
    if not source_path.is_file():
        raise ValueError(f"gold corpus fixture {fixture_id} does not exist: {source_path}")
    domain_id = _required_string(row, "domain_id")
    category = _required_string(row, "category")
    sensitivity = _required_string(row, "sensitivity")
    source_uri = row.get("source_uri")
    evaluation_mode = str(row.get("evaluation_mode") or "deterministic")
    if evaluation_mode not in EVALUATION_MODES:
        raise ValueError(f"gold corpus fixture {fixture_id} has unsupported evaluation_mode {evaluation_mode!r}")
    version_group = row.get("version_group")
    version_value = row.get("version")
    claims = _claims(row.get("claims") or [], fixture_id)
    fields = _fields(row.get("fields") or [], fixture_id)
    entities = _entities(row.get("entities") or [], fixture_id)
    relationships = _relationships(row.get("relationships") or [], fixture_id)
    no_claim_elements = _no_claim_elements(row.get("no_claim_elements") or [], fixture_id)
    supersedes = _string_tuple(row.get("supersedes"))
    expected_assertions = _string_tuple(row.get("expected_assertions"))
    expected_pages = _optional_nonnegative_int(row.get("expected_pages"), fixture_id, "expected_pages")
    expected_elements = _record_tuple(row.get("expected_elements"), fixture_id, "expected_elements")
    evidence_locations = _record_tuple(row.get("evidence_locations"), fixture_id, "evidence_locations")
    entity_outcomes = _record_tuple(row.get("entity_outcomes"), fixture_id, "entity_outcomes")
    semantic_decisions = _record_tuple(row.get("semantic_decisions"), fixture_id, "semantic_decisions")
    governance_counts = _governance_counts(row.get("governance_counts"), fixture_id)
    governance_records = _optional_record(row.get("governance_records"), fixture_id, "governance_records")
    search_expectations = _optional_record(row.get("search_expectations"), fixture_id, "search_expectations")
    citations = _record_tuple(row.get("citations"), fixture_id, "citations")
    return GoldFixture(
        fixture_id=fixture_id,
        document_class=document_class,
        path=source_path,
        domain_id=domain_id,
        category=category,
        sensitivity=sensitivity,
        source_uri=source_uri if isinstance(source_uri, str) else None,
        evaluation_mode=evaluation_mode,
        claims=claims,
        fields=fields,
        entities=entities,
        relationships=relationships,
        no_claim_elements=no_claim_elements,
        version_group=version_group if isinstance(version_group, str) else None,
        version=version_value if isinstance(version_value, int) else None,
        supersedes=supersedes,
        expected_assertions=expected_assertions,
        expected_pages=expected_pages,
        expected_elements=expected_elements,
        evidence_locations=evidence_locations,
        entity_outcomes=entity_outcomes,
        semantic_decisions=semantic_decisions,
        governance_counts=governance_counts,
        governance_records=governance_records,
        search_expectations=search_expectations,
        citations=citations,
    )


def _claims(rows: list[object], fixture_id: str) -> tuple[GoldClaimExpectation, ...]:
    claims: list[GoldClaimExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus claim entries in {fixture_id} must be objects")
        claim_id = _required_string(row, "id")
        statement = _required_string(row, "statement")
        claim_type = _required_string(row, "type")
        polarity = str(row.get("polarity") or "supports")
        if polarity not in EVIDENCE_POLARITIES:
            raise ValueError(f"gold corpus claim {claim_id} in {fixture_id} has unsupported polarity {polarity!r}")
        anchors = _evidence_anchors(row.get("evidence") or [], fixture_id, claim_id)
        qualifiers_value = row.get("qualifiers")
        qualifiers: dict[str, object] = dict(qualifiers_value) if isinstance(qualifiers_value, dict) else {}
        claims.append(
            GoldClaimExpectation(
                id=claim_id,
                statement=statement,
                claim_type=claim_type,
                polarity=polarity,
                evidence_anchors=anchors,
                qualifiers=qualifiers,
            )
        )
    return tuple(claims)


def _fields(rows: list[object], fixture_id: str) -> tuple[GoldStructuredFieldExpectation, ...]:
    fields: list[GoldStructuredFieldExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus field entries in {fixture_id} must be objects")
        field_path = _required_string(row, "path")
        if "expected_value" not in row:
            raise ValueError(f"gold corpus field {field_path} in {fixture_id} is missing expected_value")
        fields.append(GoldStructuredFieldExpectation(field_path=field_path, expected_value=row["expected_value"]))
    return tuple(fields)


def _entities(rows: list[object], fixture_id: str) -> tuple[GoldEntityExpectation, ...]:
    entities: list[GoldEntityExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus entity entries in {fixture_id} must be objects")
        name = _required_string(row, "name")
        kind = _required_string(row, "kind")
        external_id = row.get("external_id")
        aliases = _string_tuple(row.get("aliases"))
        entities.append(
            GoldEntityExpectation(
                name=name, kind=kind, external_id=external_id if isinstance(external_id, str) else None, aliases=aliases
            )
        )
    return tuple(entities)


def _relationships(rows: list[object], fixture_id: str) -> tuple[GoldRelationshipExpectation, ...]:
    relationships: list[GoldRelationshipExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus relationship entries in {fixture_id} must be objects")
        relationship_id = _required_string(row, "id")
        source_name = _required_string(row, "source")
        target_name = _required_string(row, "target")
        relation_type = _required_string(row, "type")
        polarity = str(row.get("polarity") or "supports")
        if polarity not in RELATIONSHIP_POLARITIES:
            raise ValueError(f"gold corpus relationship {relationship_id} in {fixture_id} has unsupported polarity")
        evidence_row = row.get("evidence")
        evidence: GoldEvidenceAnchor | None = None
        if evidence_row is not None:
            if not isinstance(evidence_row, dict):
                raise ValueError(f"gold corpus relationship {relationship_id} in {fixture_id} evidence must be object")
            evidence = _evidence_anchor(evidence_row, fixture_id, relationship_id)
        temporal_group = row.get("temporal_group")
        valid_from = row.get("valid_from")
        valid_to = row.get("valid_to")
        relationships.append(
            GoldRelationshipExpectation(
                id=relationship_id,
                source_name=source_name,
                target_name=target_name,
                relation_type=relation_type,
                polarity=polarity,
                evidence_anchor=evidence,
                temporal_group=temporal_group if isinstance(temporal_group, str) else None,
                valid_from=valid_from if isinstance(valid_from, str) else None,
                valid_to=valid_to if isinstance(valid_to, str) else None,
            )
        )
    return tuple(relationships)


def _no_claim_elements(rows: list[object], fixture_id: str) -> tuple[GoldNoClaimExpectation, ...]:
    elements: list[GoldNoClaimExpectation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus no-claim entries in {fixture_id} must be objects")
        element_id = _required_string(row, "element_id")
        method = _required_string(row, "verification_method")
        version = _required_string(row, "verifier_version")
        elements.append(
            GoldNoClaimExpectation(element_id=element_id, verification_method=method, verifier_version=version)
        )
    return tuple(elements)


def _evidence_anchors(rows: list[object], fixture_id: str, owner_id: str) -> tuple[GoldEvidenceAnchor, ...]:
    anchors: list[GoldEvidenceAnchor] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"gold corpus evidence anchor in {owner_id} of {fixture_id} must be an object")
        anchors.append(_evidence_anchor(row, fixture_id, owner_id))
    return tuple(anchors)


def _evidence_anchor(row: dict[str, object], fixture_id: str, owner_id: str) -> GoldEvidenceAnchor:
    quote = _required_string(row, "quote")
    section_path = row.get("section_path")
    page_value = row.get("page_number")
    kind = row.get("kind")
    return GoldEvidenceAnchor(
        quote=quote,
        section_path=section_path if isinstance(section_path, str) else None,
        page_number=page_value if isinstance(page_value, int) else None,
        kind=kind if isinstance(kind, str) else None,
    )


def _validate_required_classes(fixtures: tuple[GoldFixture, ...]) -> None:
    classes = {fixture.document_class for fixture in fixtures}
    missing = REQUIRED_DOCUMENT_CLASSES - classes
    if missing:
        raise ValueError(f"gold corpus is missing document classes: {sorted(missing)}")


def _validate_version_groups(fixtures: tuple[GoldFixture, ...]) -> None:
    versioned = [fixture for fixture in fixtures if fixture.document_class == "contradictory_versioned"]
    groups: dict[str, list[GoldFixture]] = {}
    for fixture in versioned:
        if not fixture.version_group:
            raise ValueError(f"contradictory_versioned fixture {fixture.fixture_id} is missing version_group")
        groups.setdefault(fixture.version_group, []).append(fixture)
    for group, members in groups.items():
        if len(members) < 2:
            raise ValueError(f"contradictory_versioned group {group!r} needs at least two members")
    for group, members in groups.items():
        seen_versions: set[int] = set()
        for member in members:
            if member.version is None:
                raise ValueError(
                    f"contradictory_versioned fixture {member.fixture_id} in group {group!r} needs version"
                )
            if member.version in seen_versions:
                raise ValueError(f"contradictory_versioned group {group!r} has duplicate version {member.version}")
            seen_versions.add(member.version)


def _collect_evaluation_modes(fixtures: tuple[GoldFixture, ...]) -> tuple[str, ...]:
    return tuple(sorted({fixture.evaluation_mode for fixture in fixtures}))


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    raise ValueError("expected list of strings")


def _optional_nonnegative_int(value: object, fixture_id: str, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"gold corpus fixture {fixture_id} field {field_name} must be a non-negative integer")
    return value


def _record_tuple(value: object, fixture_id: str, field_name: str) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"gold corpus fixture {fixture_id} field {field_name} must be a list of objects")
    return tuple(dict(item) for item in value)


def _optional_record(value: object, fixture_id: str, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"gold corpus fixture {fixture_id} field {field_name} must be an object")
    return dict(value)


def _governance_counts(value: object, fixture_id: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"gold corpus fixture {fixture_id} field governance_counts must be an object")
    required = {"adrs", "risks", "waivers", "debt", "backlog"}
    if set(value) != required:
        raise ValueError(f"gold corpus fixture {fixture_id} governance_counts must contain exactly {sorted(required)}")
    counts: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"gold corpus fixture {fixture_id} governance_counts.{key} must be non-negative")
        counts[key] = count
    return counts


def _required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"gold corpus entry is missing required string field: {key}")
    return value
