from __future__ import annotations

from pathlib import Path

from margin_schema.vocabulary import dump_vocabulary_yaml, load_vocabulary_yaml


def test_vocabulary_yaml_round_trips_governance_fields() -> None:
    original = Path("tests/fixtures/tags.yaml").read_text(encoding="utf-8")

    vocabulary = load_vocabulary_yaml(original)
    round_tripped = load_vocabulary_yaml(dump_vocabulary_yaml(vocabulary))

    assert round_tripped == vocabulary

    oss = vocabulary.terms[1]
    assert oss.preferred_term == "OSS"
    assert oss.aliases == ("Operations Support Systems",)
    assert oss.deprecated_terms == ("Ops Support Systems",)
    assert oss.broader_terms == ("Enterprise Architecture",)
    assert oss.related_terms == ("NSS",)
    assert oss.steward == "oss-architecture"
