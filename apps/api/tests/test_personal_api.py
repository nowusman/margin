from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.personal_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.personal import PersonalSignal, apply_personal_boost
from margin_db.user_models import Domain


def test_personal_notes_are_private_to_their_owner() -> None:
    client = _client()

    written = client.put(
        "/api/personal/notes",
        json={
            "target_type": "idea_block",
            "target_id": "block-1",
            "body": "This is the part I always get asked about.",
        },
    )
    assert written.status_code == 200
    note = written.json()["note"]
    assert note["body"] == "This is the part I always get asked about."
    assert note["visibility"] == "private"
    assert note["target_label"] == "The payment API writes settled payments to the ledger."

    listed = client.get("/api/personal/notes")
    assert listed.status_code == 200
    assert len(listed.json()["notes"]) == 1

    other = _client(principal_id="other-reader")
    assert other.get("/api/personal/notes").json()["notes"] == []

    with _session_factory_for(client)() as session:
        from sqlalchemy import func, select

        from margin_db.knowledge_models import IdeaBlockRevision as Revision

        revisions = session.scalar(select(func.count(Revision.id)))
    assert revisions == 2

    removed = client.delete(f"/api/personal/notes/{note['id']}")
    assert removed.status_code == 200
    assert client.get("/api/personal/notes").json()["notes"] == []


def test_personal_saved_items_are_kept_once_and_can_be_relabelled() -> None:
    client = _client()

    first = client.put(
        "/api/personal/saved", json={"target_type": "idea_block", "target_id": "block-1", "label": "Read this again"}
    )
    assert first.status_code == 200
    assert first.json()["saved_item"]["label"] == "Read this again"

    second = client.put(
        "/api/personal/saved", json={"target_type": "idea_block", "target_id": "block-1", "tags": ["payments"]}
    )
    assert second.status_code == 200
    assert second.json()["saved_item"]["id"] == first.json()["saved_item"]["id"]
    assert second.json()["saved_item"]["tags"] == ["payments"]

    listed = client.get("/api/personal/saved")
    assert len(listed.json()["saved_items"]) == 1

    removed = client.delete(f"/api/personal/saved/{first.json()['saved_item']['id']}")
    assert removed.status_code == 200
    assert client.get("/api/personal/saved").json()["saved_items"] == []


def test_personal_feed_reports_what_was_read_and_what_is_left_to_read() -> None:
    client = _client()

    recorded = client.post(
        "/api/personal/views", json={"target_type": "idea_block", "target_id": "block-1", "domain_id": "domain-a"}
    )
    assert recorded.status_code == 200

    feed = client.get("/api/personal/feed")
    assert feed.status_code == 200
    payload = feed.json()
    assert payload["permitted_domains"] == ["domain-a"]
    assert len(payload["feed"]) == 1
    assert payload["feed"][0]["target_id"] == "block-1"
    assert payload["feed"][0]["reason"] == "you read this"
    assert [entry["target_id"] for entry in payload["queue"]] == ["block-2"]


def test_a_personal_record_cannot_reference_a_record_the_reader_cannot_read() -> None:
    client = _client()

    refused = client.put(
        "/api/personal/notes",
        json={"target_type": "entity", "target_id": "entity-outside-scope", "body": "What does the hidden record say?"},
    )
    assert refused.status_code == 403

    refused_view = client.post(
        "/api/personal/views", json={"target_type": "entity", "target_id": "entity-outside-scope"}
    )
    assert refused_view.status_code == 403

    refused_saved = client.put(
        "/api/personal/saved", json={"target_type": "entity", "target_id": "entity-outside-scope"}
    )
    assert refused_saved.status_code == 403


def test_a_personal_record_does_not_disclose_a_record_the_reader_cannot_read() -> None:
    client = _client()

    with _session_factory_for(client)() as session:
        from margin_db.personal import save_note

        save_note(
            session,
            owner_principal_id="reader",
            target_type="entity",
            target_id="entity-outside-scope",
            body="A note against a record the reader may not read.",
        )
        session.commit()

    listed = client.get("/api/personal/notes")
    assert listed.status_code == 200
    note = listed.json()["notes"][0]
    assert note["target_id"] == "entity-outside-scope"
    assert note["target_label"] == "entity-outside-scope"
    assert note["target_kind"] == ""
    assert note["provenance"] == []


def test_personal_conversations_are_created_and_read_back_in_order() -> None:
    client = _client()

    created = client.post("/api/personal/threads", json={"title": "Payment questions"})
    assert created.status_code == 200
    thread_id = created.json()["thread"]["id"]

    with _session_factory_for(client)() as session:
        from margin_db.personal import append_message, get_thread

        thread = get_thread(session, "reader", thread_id)
        assert thread is not None
        append_message(session, thread=thread, role="user", content="What writes to the ledger?")
        append_message(
            session,
            thread=thread,
            role="assistant",
            content="The payment API writes settled payments to the ledger.",
            citation_ids=["idea_block:block-1"],
        )
        session.commit()

    read = client.get(f"/api/personal/threads/{thread_id}")
    assert read.status_code == 200
    messages = read.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]

    other = _client(principal_id="other-reader")
    assert other.get(f"/api/personal/threads/{thread_id}").status_code == 404
    assert messages[1]["citation_ids"] == ["idea_block:block-1"]
    assert [message["position"] for message in messages] == [0, 1]

    removed = client.delete(f"/api/personal/threads/{thread_id}")
    assert removed.status_code == 200
    assert client.get(f"/api/personal/threads/{thread_id}").status_code == 404


def test_personal_boost_only_reorders_within_a_narrow_band() -> None:
    strong = ("revision-strong", 1.0)
    weak = ("revision-weak", 0.2)
    boosted = apply_personal_boost(
        (strong, weak),
        {
            "revision-weak": PersonalSignal(
                revision_id="revision-weak", saved=True, noted=False, viewed_recently=False, domain_affinity=False
            )
        },
    )
    assert boosted.candidates[0] == ("revision-strong", 1.0)
    assert boosted.candidates[1][0] == "revision-weak"
    assert boosted.reordered is False
    assert boosted.largest_position_shift == 0

    near = ("revision-near", 0.9)
    reordered = apply_personal_boost(
        (strong, near),
        {
            "revision-near": PersonalSignal(
                revision_id="revision-near", saved=True, noted=False, viewed_recently=False, domain_affinity=False
            )
        },
    )
    assert reordered.candidates[0][0] == "revision-near"
    assert reordered.reordered is True
    assert reordered.largest_position_shift == 1
    assert reordered.largest_score_change > 0
    assert reordered.candidates[0][1] > strong[1]


def test_personal_api_refuses_anonymous_callers() -> None:
    client = _client(principal_id="")
    assert client.get("/api/personal/notes").status_code == 403
    assert client.get("/api/personal/feed").status_code == 403


_session_factory: sessionmaker = sessionmaker()


def _session_factory_for(client: TestClient) -> sessionmaker:
    """Return the session factory backing a test client."""

    app = client.app
    assert isinstance(app, FastAPI)
    return app.state.session_factory


def _client(principal_id: str = "reader") -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            (
                Domain(id="domain-a", name="Domain A"),
                Entity(
                    id="entity-a",
                    preferred_label="Payment API",
                    normalized_label="payment api",
                    ontology_type="service",
                    domain_id="domain-a",
                    lifecycle_status="active",
                ),
                IdeaBlock(id="block-1", domain_id="domain-a"),
                IdeaBlock(id="block-2", domain_id="domain-a"),
                IdeaBlockRevision(
                    id="revision-1",
                    idea_block_id="block-1",
                    revision_number=1,
                    statement="The payment API writes settled payments to the ledger.",
                    normalized_statement="the payment api writes settled payments to the ledger",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="payment-api-writes-settled-payments",
                ),
                IdeaBlockRevision(
                    id="revision-2",
                    idea_block_id="block-2",
                    revision_number=1,
                    statement="Settlement reconciles the ledger nightly.",
                    normalized_statement="settlement reconciles the ledger nightly",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="settlement-reconciles-the-ledger-nightly",
                ),
                IdeaBlockEntity(idea_block_id="block-1", entity_id="entity-a", role="mentions"),
                IdeaBlockEntity(idea_block_id="block-2", entity_id="entity-a", role="mentions"),
            )
        )
        for block_id, revision_id in (("block-1", "revision-1"), ("block-2", "revision-2")):
            block = session.get(IdeaBlock, block_id)
            assert block is not None
            block.current_revision_id = revision_id
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.principal_id = principal_id
        request.state.permissions = PermissionSnapshot(
            principal_id=principal_id, domain_permissions={"domain-a": frozenset(("read",))}
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app)
