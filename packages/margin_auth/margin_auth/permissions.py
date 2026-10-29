from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_db.user_models import CategoryPermission, DomainPermission

WILDCARD_SCOPE = "*"
_SUPER_ADMIN_GROUPS = frozenset({"margin-admin", "platform-admin"})
_ARCHITECTURE_ADMIN_GROUPS = frozenset({"architecture-admins"})
_ARCHITECTURE_REVIEWER_GROUPS = frozenset({"architecture-reviewers"})
_ARCHITECT_GROUPS = frozenset({"architect", "architects"})


@dataclass(frozen=True)
class PermissionSnapshot:
    principal_id: str
    group_ids: tuple[str, ...] = ()
    domain_permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    category_permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    sensitivity_permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    authz_backend: str = "postgres_permission_cache"

    def allows_domain(self, domain_id: str, permission: str) -> bool:
        return permission in self.domain_permissions.get(
            domain_id, frozenset()
        ) or permission in self.domain_permissions.get(WILDCARD_SCOPE, frozenset())

    def allows_category(self, category: str, permission: str) -> bool:
        return permission in self.category_permissions.get(
            category, frozenset()
        ) or permission in self.category_permissions.get(WILDCARD_SCOPE, frozenset())

    def allows_sensitivity(self, sensitivity: str, permission: str) -> bool:
        if not self.sensitivity_permissions:
            return True
        return permission in self.sensitivity_permissions.get(
            sensitivity, frozenset()
        ) or permission in self.sensitivity_permissions.get(WILDCARD_SCOPE, frozenset())


def _normalize_group(group_id: str) -> str:
    value = group_id.strip().lower()
    if not value:
        return ""
    segment = value.split("/")[-1]
    return segment.replace("_", "-").replace(" ", "-")


def is_platform_admin(group_ids: Iterable[str]) -> bool:
    return bool({_normalize_group(group_id) for group_id in group_ids} & _SUPER_ADMIN_GROUPS)


def _apply_implicit_group_permissions(
    *,
    normalized_groups: set[str],
    domain_permissions: dict[str, set[str]],
    category_permissions: dict[str, set[str]],
    sensitivity_permissions: dict[str, set[str]],
) -> None:
    if normalized_groups & _SUPER_ADMIN_GROUPS:
        grants = {"read", "write", "review"}
        for permissions in (domain_permissions, category_permissions, sensitivity_permissions):
            permissions.setdefault(WILDCARD_SCOPE, set()).update(grants)
        return
    if normalized_groups & _ARCHITECTURE_ADMIN_GROUPS:
        grants = {"read", "write", "review"}
        for permissions in (domain_permissions, category_permissions, sensitivity_permissions):
            permissions.setdefault(WILDCARD_SCOPE, set()).update(grants)
    if normalized_groups & _ARCHITECT_GROUPS:
        grants = {"read", "write"}
        for permissions in (domain_permissions, category_permissions, sensitivity_permissions):
            permissions.setdefault(WILDCARD_SCOPE, set()).update(grants)
    if normalized_groups & _ARCHITECTURE_REVIEWER_GROUPS:
        grants = {"read", "review"}
        for permissions in (domain_permissions, category_permissions, sensitivity_permissions):
            permissions.setdefault(WILDCARD_SCOPE, set()).update(grants)


def read_permissions(session: Session, *, principal_id: str, group_ids: Iterable[str] = ()) -> PermissionSnapshot:
    group_tuple = tuple(group_ids)
    principal_filters = [("user", principal_id), *(("group", group_id) for group_id in group_tuple)]
    domain_permissions: dict[str, set[str]] = {}
    category_permissions: dict[str, set[str]] = {}
    sensitivity_permissions: dict[str, set[str]] = {}

    for principal_type, current_principal_id in principal_filters:
        for row in session.execute(
            select(DomainPermission).where(
                DomainPermission.principal_type == principal_type, DomainPermission.principal_id == current_principal_id
            )
        ).scalars():
            domain_permissions.setdefault(row.domain_id, set()).add(row.permission)
        for row in session.execute(
            select(CategoryPermission).where(
                CategoryPermission.principal_type == principal_type,
                CategoryPermission.principal_id == current_principal_id,
            )
        ).scalars():
            if row.category.startswith("sensitivity:"):
                sensitivity = row.category.split(":", 1)[1]
                sensitivity_permissions.setdefault(sensitivity, set()).add(row.permission)
            else:
                category_permissions.setdefault(row.category, set()).add(row.permission)

    _apply_implicit_group_permissions(
        normalized_groups={_normalize_group(group_id) for group_id in group_tuple},
        domain_permissions=domain_permissions,
        category_permissions=category_permissions,
        sensitivity_permissions=sensitivity_permissions,
    )
    return PermissionSnapshot(
        principal_id=principal_id,
        group_ids=group_tuple,
        domain_permissions={key: frozenset(value) for key, value in domain_permissions.items()},
        category_permissions={key: frozenset(value) for key, value in category_permissions.items()},
        sensitivity_permissions={key: frozenset(value) for key, value in sensitivity_permissions.items()},
        authz_backend=os.getenv("MARGIN_AUTHZ_BACKEND", "postgres_permission_cache"),
    )
