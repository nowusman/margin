from margin_db.base import Base


def test_projection_and_compiled_tables_are_absent_from_metadata() -> None:
    projection_tables = {
        "graph_community_snapshots",
        "graph_community_members",
        "graph_similarity_snapshots",
        "graph_similarity_edges",
        "topic_community_snapshots",
        "topic_community_members",
        "topic_community_edges",
    }

    assert projection_tables.isdisjoint(Base.metadata.tables)
    assert "compiled_articles" not in Base.metadata.tables
