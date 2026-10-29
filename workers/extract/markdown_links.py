from __future__ import annotations

import re

WIKI_LINK_PATTERN = re.compile(r"\[\[(?P<target>[^\]|]+)(?:\|(?P<alias>[^\]|]+))?\]\]")
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)")


def normalize_link_text(text: str) -> str:
    """Preserve linked source text without treating links as relationships."""

    def replace_wiki(match: re.Match[str]) -> str:
        target = _clean_link_target(match.group("target"))
        if not target:
            return ""
        return (match.group("alias") or target).strip()

    def replace_markdown(match: re.Match[str]) -> str:
        if _is_external_url(match.group("url")):
            return match.group(0)
        label = match.group("label").strip()
        target = _clean_link_target(match.group("url"))
        if not target:
            return ""
        return label or target

    normalized = WIKI_LINK_PATTERN.sub(replace_wiki, text)
    return MARKDOWN_LINK_PATTERN.sub(replace_markdown, normalized)


def _clean_link_target(value: str) -> str:
    target = value.strip()
    target = target.split("#", 1)[0]
    target = target.replace("\\", "/").rsplit("/", 1)[-1]
    target = re.sub(r"\.(?:md|markdown|adoc|txt)$", "", target, flags=re.IGNORECASE)
    target = target.replace("_", " ").replace("-", " ").strip()
    return target


def _is_external_url(value: str) -> bool:
    return bool(re.match(r"^(?:[a-z][a-z0-9+.-]*://|mailto:|tel:)", value, re.IGNORECASE))
