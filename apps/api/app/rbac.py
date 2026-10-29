from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from margin_auth.permissions import PermissionSnapshot, read_permissions
from margin_auth.principals import resolve_principal
from margin_db.users import ensure_user


async def rbac_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    settings = getattr(request.app.state, "settings", None)
    identity = resolve_principal(dict(request.headers), settings=settings)
    principal_id = identity.principal_id
    group_ids = identity.group_ids
    session_factory = getattr(request.app.state, "session_factory", None)

    if request.url.path in {"/health", "/ready"} or session_factory is None:
        snapshot = PermissionSnapshot(principal_id=principal_id, group_ids=group_ids)
    else:
        with session_factory() as session:
            snapshot = read_permissions(session, principal_id=principal_id, group_ids=group_ids)
            if principal_id and principal_id != "anonymous":
                ensure_user(session, principal_id)
                session.commit()

    request.state.principal_id = principal_id
    request.state.group_ids = group_ids
    request.state.auth_source = identity.auth_source
    request.state.permissions = snapshot
    return await call_next(request)
