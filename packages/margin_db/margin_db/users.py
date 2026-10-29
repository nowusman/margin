from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from margin_db.user_models import User


def ensure_user(session: Session, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    normalized = user_id.strip()
    if not normalized or normalized == "anonymous":
        return None
    if session.get(User, normalized) is not None:
        return normalized
    try:
        with session.begin_nested():
            session.add(User(id=normalized, display_name=normalized, external_subject=normalized, status="active"))
            session.flush()
    except IntegrityError:
        # Another API worker created this user between the read and insert.
        if session.get(User, normalized) is None:
            raise
    return normalized
