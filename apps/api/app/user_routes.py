from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.authz import require_any_domain_permission
from app.source_routes import get_session_factory
from margin_auth.permissions import is_platform_admin
from margin_db.audit import write_audit_event
from margin_db.user_models import CategoryPermission, Domain, DomainPermission, Group, User, UserGroup


def _require_margin_admin(request: Request) -> None:
    if not is_platform_admin(getattr(request.state, "group_ids", ())):
        raise HTTPException(status_code=403, detail="margin-admin group is required for platform administration")


router = APIRouter(prefix="/api/admin", tags=["admin-users"], dependencies=[Depends(_require_margin_admin)])


class AccessCheckPayload(BaseModel):
    user: str | None = None
    scope: str | None = "all"


class AccessProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)


def _actor(request: Request) -> str:
    return str(getattr(request.state, "principal_id", "unknown"))


def _access_profile(session, user: User) -> dict[str, object]:
    groups = session.scalars(
        select(UserGroup.group_id).where(UserGroup.user_id == user.id).order_by(UserGroup.group_id)
    ).all()
    domains = session.scalars(
        select(DomainPermission.domain_id)
        .where(
            DomainPermission.principal_type == "user",
            DomainPermission.principal_id == user.id,
            DomainPermission.permission == "read",
        )
        .order_by(DomainPermission.domain_id)
    ).all()
    sensitivities = session.scalars(
        select(CategoryPermission.category)
        .where(
            CategoryPermission.principal_type == "user",
            CategoryPermission.principal_id == user.id,
            CategoryPermission.permission == "read",
            CategoryPermission.category.like("sensitivity:%"),
        )
        .order_by(CategoryPermission.category)
    ).all()
    categories = session.scalars(
        select(CategoryPermission.category)
        .where(
            CategoryPermission.principal_type == "user",
            CategoryPermission.principal_id == user.id,
            CategoryPermission.permission == "read",
            ~CategoryPermission.category.like("sensitivity:%"),
        )
        .order_by(CategoryPermission.category)
    ).all()
    return {
        "user": user.id,
        "groups": list(groups),
        "domains": list(domains),
        "categories": list(categories),
        "sensitivities": [value.split(":", 1)[1] for value in sensitivities],
    }


def _replace_access_profile(session, user: User, body: AccessProfilePatch) -> dict[str, object]:
    groups = sorted({value.strip() for value in body.groups if value.strip()})
    domains = sorted({value.strip() for value in body.domains if value.strip()})
    categories = sorted({value.strip() for value in body.categories if value.strip()})
    sensitivities = sorted({value.strip() for value in body.sensitivities if value.strip()})
    known_domains = set(session.scalars(select(Domain.id).where(Domain.id.in_(domains))).all()) if domains else set()
    unknown_domains = sorted(set(domains) - known_domains)
    if unknown_domains:
        raise HTTPException(status_code=422, detail=f"unknown domains: {', '.join(unknown_domains)}")

    session.execute(delete(UserGroup).where(UserGroup.user_id == user.id))
    session.execute(
        delete(DomainPermission).where(
            DomainPermission.principal_type == "user",
            DomainPermission.principal_id == user.id,
            DomainPermission.permission == "read",
        )
    )
    session.execute(
        delete(CategoryPermission).where(
            CategoryPermission.principal_type == "user",
            CategoryPermission.principal_id == user.id,
            CategoryPermission.permission == "read",
        )
    )
    for group_id in groups:
        group = session.get(Group, group_id)
        if group is None:
            group = Group(id=group_id, name=group_id)
            session.add(group)
        session.add(UserGroup(user_id=user.id, group_id=group_id))
    for domain_id in domains:
        session.add(
            DomainPermission(domain_id=domain_id, principal_type="user", principal_id=user.id, permission="read")
        )
    for sensitivity in sensitivities:
        session.add(
            CategoryPermission(
                category=f"sensitivity:{sensitivity}", principal_type="user", principal_id=user.id, permission="read"
            )
        )
    for category in categories:
        session.add(
            CategoryPermission(category=category, principal_type="user", principal_id=user.id, permission="read")
        )
    session.flush()
    return _access_profile(session, user)


def _require_admin_read(request: Request) -> None:
    require_any_domain_permission(
        permissions=request.state.permissions, action="read", detail="missing read access for admin data"
    )


def _require_admin_write(request: Request) -> None:
    require_any_domain_permission(
        permissions=request.state.permissions, action="write", detail="missing write access for admin data"
    )


def _scope_tokens(scope: str) -> list[str]:
    normalized = scope.strip()
    if not normalized or normalized.lower() == "all":
        return []
    return [token.strip() for token in normalized.split(",") if token.strip()]


def _match_scope_token(token: str, domains: set[str], sensitivities: set[str]) -> tuple[bool, str]:
    if ":" not in token:
        return token in domains, f"domain:{token}"
    prefix, value = token.split(":", 1)
    normalized_prefix = prefix.strip().lower()
    normalized_value = value.strip()
    if normalized_prefix == "domain":
        return normalized_value in domains, f"domain:{normalized_value}"
    if normalized_prefix == "sensitivity":
        return normalized_value in sensitivities, f"sensitivity:{normalized_value}"
    return False, token


@router.get("/access")
def access(request: Request) -> list[dict[str, object]]:
    _require_admin_read(request)
    with get_session_factory(request)() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        profiles = [_access_profile(session, user) for user in users]
        return [
            profile
            for profile in profiles
            if profile["groups"] or profile["domains"] or profile["categories"] or profile["sensitivities"]
        ]


@router.post("/access", status_code=201)
def create_access_profile(body: AccessProfilePatch, request: Request, user: str) -> dict[str, object]:
    _require_admin_write(request)
    user = user.strip()
    if not user:
        raise HTTPException(status_code=422, detail="user is required")
    with get_session_factory(request)() as session:
        existing = session.get(User, user)
        if existing is not None:
            existing_profile = _access_profile(session, existing)
            if any(existing_profile[key] for key in ("groups", "domains", "categories", "sensitivities")):
                raise HTTPException(status_code=409, detail="access profile already exists")
            profile_user = existing
        else:
            profile_user = User(id=user, display_name=user)
            session.add(profile_user)
            session.flush()
        profile = _replace_access_profile(session, profile_user, body)
        write_audit_event(
            session,
            actor=_actor(request),
            action="access_profile.created",
            target_type="user",
            target_id=user,
            after=profile,
        )
        session.commit()
        return {"status": "created", "user": user, "profile": profile}


@router.patch("/access/{user}")
def patch_access_profile(user: str, body: AccessProfilePatch, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    with get_session_factory(request)() as session:
        profile_user = session.get(User, user)
        if profile_user is None:
            raise HTTPException(status_code=404, detail="access profile not found")
        before = _access_profile(session, profile_user)
        if not any(before[key] for key in ("groups", "domains", "categories", "sensitivities")):
            raise HTTPException(status_code=404, detail="access profile not found")
        profile = _replace_access_profile(session, profile_user, body)
        write_audit_event(
            session,
            actor=_actor(request),
            action="access_profile.updated",
            target_type="user",
            target_id=user,
            before=before,
            after=profile,
        )
        session.commit()
        return {"status": "updated", "user": user, "profile": profile}


@router.delete("/access/{user}")
def delete_access_profile(user: str, request: Request) -> dict[str, object]:
    _require_admin_write(request)
    if user == str(request.state.principal_id):
        raise HTTPException(status_code=409, detail="current administrator access profile cannot be deleted")
    with get_session_factory(request)() as session:
        profile_user = session.get(User, user)
        if profile_user is None:
            raise HTTPException(status_code=404, detail="access profile not found")
        before = _access_profile(session, profile_user)
        if not any(before[key] for key in ("groups", "domains", "categories", "sensitivities")):
            raise HTTPException(status_code=404, detail="access profile not found")
        session.execute(delete(UserGroup).where(UserGroup.user_id == user))
        session.execute(
            delete(DomainPermission).where(
                DomainPermission.principal_type == "user", DomainPermission.principal_id == user
            )
        )
        session.execute(
            delete(CategoryPermission).where(
                CategoryPermission.principal_type == "user", CategoryPermission.principal_id == user
            )
        )
        write_audit_event(
            session,
            actor=_actor(request),
            action="access_profile.deleted",
            target_type="user",
            target_id=user,
            before=before,
        )
        session.commit()
        return {"status": "deleted", "user": user}


@router.post("/access/check")
def access_check(body: AccessCheckPayload, request: Request) -> dict[str, object]:
    _require_admin_read(request)
    user = (body.user or str(request.state.principal_id)).strip()
    scope = (body.scope or "all").strip()
    with get_session_factory(request)() as session:
        profile_user = session.get(User, user)
        profile = _access_profile(session, profile_user) if profile_user is not None else None
    if profile is None or not any(profile[key] for key in ("groups", "domains", "categories", "sensitivities")):
        tokens = _scope_tokens(scope)
        return {
            "user": user,
            "canSee": [],
            "denied": tokens if tokens else ["all"],
            "decisionTrace": f"No profile found for '{user}'. Scope '{scope or 'all'}' denied pending RBAC onboarding.",
        }
    domains = set(cast(list[str], profile["domains"]))
    sensitivities = set(cast(list[str], profile["sensitivities"]))
    tokens = _scope_tokens(scope)
    if not tokens:
        return {
            "user": user,
            "canSee": [f"domain:{domain}" for domain in sorted(domains)]
            + [f"sensitivity:{sensitivity}" for sensitivity in sorted(sensitivities)],
            "denied": [],
            "decisionTrace": f"Full visibility probe for '{user}' across domains and sensitivities.",
        }
    can_see: list[str] = []
    denied: list[str] = []
    for token in tokens:
        allowed, normalized = _match_scope_token(token, domains, sensitivities)
        (can_see if allowed else denied).append(normalized)
    return {
        "user": user,
        "canSee": can_see,
        "denied": denied,
        "decisionTrace": f"Evaluated {len(tokens)} scope token(s) for '{user}' against profile grants.",
    }
