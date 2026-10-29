from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.authz import require_permission
from margin_auth.permissions import PermissionSnapshot


def test_require_permission_denies_missing_sensitivity_access() -> None:
    snapshot = PermissionSnapshot(
        principal_id="alice",
        domain_permissions={"OSS": frozenset({"read"})},
        category_permissions={"Risk": frozenset({"read"})},
        sensitivity_permissions={"internal": frozenset({"read"})},
    )
    with pytest.raises(HTTPException) as exc:
        require_permission(
            permissions=snapshot, domain_id="OSS", category="Risk", sensitivity="confidential", action="read"
        )

    assert exc.value.status_code == 403


def test_require_permission_allows_granted_scope() -> None:
    snapshot = PermissionSnapshot(
        principal_id="alice",
        domain_permissions={"OSS": frozenset({"read"})},
        category_permissions={"Risk": frozenset({"read"})},
        sensitivity_permissions={"confidential": frozenset({"read"})},
    )
    require_permission(
        permissions=snapshot, domain_id="OSS", category="Risk", sensitivity="confidential", action="read"
    )
