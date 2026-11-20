"""JSON-LD helpers. (The JSON-LD fetcher itself is added in step 4.)"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup


def extract_blocks(html: str) -> tuple[list, int]:
    """Return (parsed JSON-LD blocks, number of blocks that failed to parse)."""
    soup = BeautifulSoup(html, "html.parser")
    blocks, broken = [], 0
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            blocks.append(json.loads(tag.get_text(), strict=False))
        except ValueError:
            broken += 1
    return blocks, broken


def iter_nodes(data):
    """Yield every dict in a JSON-LD tree (handles lists and @graph)."""
    if isinstance(data, list):
        for item in data:
            yield from iter_nodes(item)
    elif isinstance(data, dict):
        yield data
        for value in data.values():
            if isinstance(value, (list, dict)):
                yield from iter_nodes(value)


def node_types(blocks: list) -> list[str]:
    types = set()
    for node in iter_nodes(blocks):
        t = node.get("@type")
        if isinstance(t, str):
            types.add(t)
        elif isinstance(t, list):
            types.update(x for x in t if isinstance(x, str))
    return sorted(types)