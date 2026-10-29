"""Normalized conversion contracts and deterministic uploaded contract parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from margin_schema.extraction import (
    ExtractedEntity,
    ExtractedIdeaBlock,
    ExtractedRelationship,
    ExtractorOutput,
    single_claim_evidence,
)

_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})


@dataclass(frozen=True)
class ConversionWarning:
    code: str
    message: str
    page_number: int | None = None
    element_id: str | None = None


@dataclass(frozen=True)
class VisualAsset:
    asset_id: str
    asset_key: str
    media_type: str
    checksum: str
    page_number: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    data: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedElement:
    element_id: str
    kind: str
    text: str
    ordinal: int
    page_number: int | None = None
    parent_id: str | None = None
    char_start: int = 0
    char_end: int = 0
    bounding_box: tuple[float, float, float, float] | None = None
    asset_key: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedPage:
    page_number: int
    checksum: str
    text: str
    elements: tuple[NormalizedElement, ...] = ()
    width: float | None = None
    height: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedDocument:
    source_name: str
    format: str
    text: str
    pages: tuple[NormalizedPage, ...] = ()
    elements: tuple[NormalizedElement, ...] = ()
    visual_assets: tuple[VisualAsset, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversionResult:
    normalized_document: NormalizedDocument
    converter: str
    converter_version: str
    raw_json: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def markdown(self) -> str:
        return self.normalized_document.text

    @property
    def metadata(self) -> dict[str, Any]:
        return self.normalized_document.metadata

    @property
    def source_format(self) -> str:
        return self.normalized_document.format

    @property
    def normalized_text(self) -> str:
        return self.normalized_document.text

    @property
    def converter_name(self) -> str:
        return self.converter


@dataclass(frozen=True, slots=True)
class ContractParseResult:
    contract_type: str
    entities: tuple[dict[str, Any], ...] = ()
    operations: tuple[dict[str, Any], ...] = ()
    schemas: tuple[dict[str, Any], ...] = ()
    security_schemes: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    channels: tuple[dict[str, Any], ...] = ()
    producers: tuple[dict[str, Any], ...] = ()
    consumers: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_contract_file(path: str | Path) -> ContractParseResult:
    """Parse a local uploaded contract file without scanning or synchronizing a repository."""

    source = Path(path)
    return parse_contract_text(source.read_text(encoding="utf-8"), source_name=source.name)


def parse_contract_text(text: str, *, source_name: str = "<memory>") -> ContractParseResult:
    """Parse one OpenAPI or AsyncAPI document."""

    payload = _load_mapping(text)
    if "openapi" in payload or "swagger" in payload:
        return _parse_openapi(payload, source_name=source_name)
    if "asyncapi" in payload:
        return _parse_asyncapi(payload, source_name=source_name)
    raise ValueError(f"{source_name} is not an OpenAPI or AsyncAPI contract")


def contract_output(
    *, relative_path: str, parsed: ContractParseResult, source_element_id: str, text: str
) -> ExtractorOutput:
    """Build citable candidate assertions from one uploaded parsed contract."""

    title = str(parsed.metadata.get("title") or relative_path)
    blocks = [
        ExtractedIdeaBlock(
            statement=f"Contract {title} is described by {relative_path}.",
            type=f"{parsed.contract_type}_contract",
            confidence="extracted",
            evidence=single_claim_evidence(source_element_id=source_element_id, source_quote=_quote(text, title)),
            extraction_method="deterministic_contract",
            entities=[ExtractedEntity(name=title, kind="api", role="subject")],
            tags=["contract", parsed.contract_type],
            metadata={
                "relative_path": relative_path,
                "verification_policy": "validated_contract",
                "version": str(parsed.metadata.get("version") or ""),
            },
        )
    ]
    for operation in parsed.operations:
        operation_id = str(operation.get("operation_id") or "")
        if operation_id:
            blocks.append(
                _relationship_block(
                    source_element_id=source_element_id,
                    source_quote=_quote(text, operation_id),
                    statement=f"{title} exposes API operation {operation_id}.",
                    block_type="api_operation",
                    source_name=title,
                    relation_type="exposes",
                    target_name=operation_id,
                    tags=["contract", "openapi", "operation"],
                    metadata={"method": str(operation.get("method") or ""), "path": str(operation.get("path") or "")},
                )
            )
    for scheme in parsed.security_schemes:
        scheme_name = str(scheme.get("name") or "")
        if scheme_name:
            blocks.append(
                _relationship_block(
                    source_element_id=source_element_id,
                    source_quote=_quote(text, scheme_name),
                    statement=f"{title} is secured by {scheme_name}.",
                    block_type="api_security_scheme",
                    source_name=title,
                    relation_type="secured_by",
                    target_name=scheme_name,
                    target_kind="security_scheme",
                    tags=["contract", "openapi", "security"],
                    metadata={"scheme_type": str(scheme.get("type") or "")},
                )
            )
    for event in parsed.events:
        event_name = str(event.get("name") or "")
        if event_name:
            blocks.append(
                _relationship_block(
                    source_element_id=source_element_id,
                    source_quote=_quote(text, event_name),
                    statement=f"{title} produces event {event_name}.",
                    block_type="contract_event",
                    source_name=title,
                    relation_type="produces",
                    target_name=event_name,
                    target_kind="event",
                    tags=["contract", "asyncapi", "event"],
                    metadata={"payload_schema": str(event.get("payload_schema") or "")},
                )
            )
    for producer in parsed.producers:
        _append_channel_block(blocks, title, producer, source_element_id, text, "produces", "producer")
    for consumer in parsed.consumers:
        _append_channel_block(blocks, title, consumer, source_element_id, text, "consumes", "consumer")
    return ExtractorOutput(idea_blocks=blocks)


def _append_channel_block(
    blocks: list[ExtractedIdeaBlock],
    title: str,
    participant: dict[str, Any],
    source_element_id: str,
    text: str,
    relation_type: str,
    role: str,
) -> None:
    channel = str(participant.get("channel") or "")
    if not channel:
        return
    blocks.append(
        _relationship_block(
            source_element_id=source_element_id,
            source_quote=_quote(text, channel),
            statement=f"{title} {relation_type} messages {'on' if role == 'producer' else 'from'} channel {channel}.",
            block_type=f"event_{role}",
            source_name=title,
            relation_type=relation_type,
            target_name=channel,
            target_kind="event",
            tags=["contract", "asyncapi", role],
            metadata={"operation_id": str(participant.get("operation_id") or "")},
        )
    )


def _relationship_block(
    *,
    source_element_id: str,
    source_quote: str,
    statement: str,
    block_type: str,
    source_name: str,
    relation_type: str,
    target_name: str,
    target_kind: str = "api",
    tags: list[str],
    metadata: dict[str, str],
) -> ExtractedIdeaBlock:
    return ExtractedIdeaBlock(
        statement=statement,
        type=block_type,
        confidence="extracted",
        evidence=single_claim_evidence(source_element_id=source_element_id, source_quote=source_quote),
        extraction_method="deterministic_contract",
        entities=[
            ExtractedEntity(name=source_name, kind="api", role="subject"),
            ExtractedEntity(name=target_name, kind=target_kind, role="object"),
        ],
        relationships=[
            ExtractedRelationship(
                source=source_name, relation_type=relation_type, target=target_name, confidence="extracted"
            )
        ],
        tags=tags,
        metadata={**metadata, "verification_policy": "validated_contract"},
    )


def _parse_openapi(payload: dict[str, Any], *, source_name: str) -> ContractParseResult:
    info = _mapping(payload.get("info"))
    title = str(info.get("title") or source_name)
    operations: list[dict[str, Any]] = []
    for path, path_item in _mapping(payload.get("paths")).items():
        for method, operation in _mapping(path_item).items():
            if method.casefold() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operations.append(
                {
                    "operation_id": operation.get("operationId") or f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "request_schemas": tuple(_request_schema_refs(operation)),
                    "response_schemas": tuple(_response_schema_refs(operation)),
                }
            )
    components = _mapping(payload.get("components"))
    return ContractParseResult(
        contract_type="openapi",
        entities=({"kind": "API", "name": title},),
        operations=tuple(operations),
        schemas=tuple(
            {"kind": "Schema", "name": name, "contract": title, "schema": schema}
            for name, schema in _mapping(components.get("schemas")).items()
        ),
        security_schemes=tuple(
            {"name": name, "type": str(_mapping(scheme).get("type") or "")}
            for name, scheme in _mapping(components.get("securitySchemes")).items()
        ),
        metadata={"title": title, "version": str(info.get("version") or "")},
    )


def _parse_asyncapi(payload: dict[str, Any], *, source_name: str) -> ContractParseResult:
    info = _mapping(payload.get("info"))
    title = str(info.get("title") or source_name)
    channels: list[dict[str, Any]] = []
    producers: list[dict[str, Any]] = []
    consumers: list[dict[str, Any]] = []
    for channel_name, channel in _mapping(payload.get("channels")).items():
        channel_data = _mapping(channel)
        channels.append({"name": channel_name, "event_api": title})
        if "publish" in channel_data:
            producers.append(
                {
                    "channel": channel_name,
                    "operation_id": _operation_id(channel_data["publish"], "publish", channel_name),
                }
            )
        if "subscribe" in channel_data:
            consumers.append(
                {
                    "channel": channel_name,
                    "operation_id": _operation_id(channel_data["subscribe"], "subscribe", channel_name),
                }
            )
    components = _mapping(payload.get("components"))
    messages = _mapping(components.get("messages"))
    return ContractParseResult(
        contract_type="asyncapi",
        entities=({"kind": "EventAPI", "name": title},),
        schemas=tuple(
            {"kind": "Schema", "name": name, "contract": title, "schema": schema}
            for name, schema in _mapping(components.get("schemas")).items()
        ),
        events=tuple(
            {"kind": "Event", "name": name, "event_api": title, "payload_schema": _message_payload_schema(message)}
            for name, message in messages.items()
        ),
        channels=tuple(channels),
        producers=tuple(producers),
        consumers=tuple(consumers),
        metadata={"title": title, "version": str(info.get("version") or "")},
    )


def _load_mapping(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"contract payload cannot be parsed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("contract payload must be a mapping")
    return payload


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _request_schema_refs(operation: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for media in _mapping(_mapping(operation.get("requestBody")).get("content")).values():
        refs.extend(_schema_refs(_mapping(media).get("schema")))
    return refs


def _response_schema_refs(operation: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for response in _mapping(operation.get("responses")).values():
        for media in _mapping(_mapping(response).get("content")).values():
            refs.extend(_schema_refs(_mapping(media).get("schema")))
    return refs


def _schema_refs(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    refs = [str(schema["$ref"]).rsplit("/", 1)[-1]] if "$ref" in schema else []
    for key in ("allOf", "anyOf", "items", "oneOf", "properties"):
        value = schema.get(key)
        if isinstance(value, dict):
            refs.extend(_schema_refs(value))
        elif isinstance(value, list):
            for item in value:
                refs.extend(_schema_refs(item))
    return refs


def _message_payload_schema(message: Any) -> str | None:
    payload = _mapping(message).get("payload")
    return str(payload["$ref"]).rsplit("/", 1)[-1] if isinstance(payload, dict) and "$ref" in payload else None


def _operation_id(operation: Any, verb: str, channel: str) -> str:
    value = _mapping(operation).get("operationId")
    return str(value) if value else f"{verb}:{channel}"


def _quote(text: str, hint: str) -> str:
    for line in text.splitlines():
        if hint and hint in line:
            return line.strip()
    return text.strip().splitlines()[0] if text.strip() else hint
