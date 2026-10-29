from margin_ingest.structurizr import parse_structurizr_dsl


def test_structurizr_parser_retains_supported_entities_and_explicit_edges() -> None:
    result = parse_structurizr_dsl(
        """workspace {
model {
api = softwareSystem "API"
web = container "Web"
user = person "User"
api -> web "calls"
web -> user "not retained"
}
}"""
    )

    assert [(entity.id, entity.level) for entity in result.entities] == [("c4_api", "system"), ("c4_web", "container")]
    assert [(edge.source_id, edge.target_id, edge.relation_type) for edge in result.relationships] == [
        ("c4_api", "c4_web", "calls")
    ]
