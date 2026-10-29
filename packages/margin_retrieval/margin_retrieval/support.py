"""Shared immutable retrieval result and trace serialization helpers."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from margin_retrieval.interfaces import Citation, RetrievalScope


def allowed_keys(values: dict[str, frozenset[str]], permission: str) -> set[str]:
    return {key for key, permissions in values.items() if permission in permissions}


def is_postgresql(session: Session) -> bool:
    return bool(session.bind is not None and session.bind.dialect.name == "postgresql")


def exclusive_date_to(value: date) -> date:
    return value + timedelta(days=1)


def scope_dict(scope: RetrievalScope) -> dict[str, object]:
    data = asdict(scope)
    for key in ("date_from", "date_to"):
        value = data[key]
        if isinstance(value, date):
            data[key] = value.isoformat()
    return data


def citation_dict(citation: Citation) -> dict[str, object]:
    """Serialize the shared plural-citation DTO without legacy route assembly."""

    return {key: value for key, value in asdict(citation).items() if value is not None}


def snippet_for(text: str, query: str, *, limit: int = 240) -> str:
    """Return the passage of a statement that matches the query.

    A snippet lets a reader judge relevance without opening every result. It
    is a window onto the statement, never a rewrite of it, so the wording of
    the record is preserved exactly.
    """

    statement = text.strip()
    if len(statement) <= limit:
        return statement
    tokens = [token for token in _tokenize(query) if len(token) > 2]
    lowered = statement.casefold()
    position = -1
    for token in tokens:
        found = lowered.find(token)
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return statement[:limit].rstrip() + "…"
    start = max(0, position - limit // 3)
    end = min(len(statement), start + limit)
    start = max(0, end - limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(statement) else ""
    return f"{prefix}{statement[start:end].strip()}{suffix}"


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token for token in "".join(c if c.isalnum() else " " for c in value.casefold()).split())
