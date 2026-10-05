"""Removes duplicate cards generated across different chunks of the same video."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from .generate import Card

DEFAULT_THRESHOLD = 90


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\{\{c\d+::|\}\}", "", text)  # strip cloze markup
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


def _key_text(card: Card) -> str:
    return _normalize(card.front or card.text)


def deduplicate_cards(cards: list[Card], threshold: int = DEFAULT_THRESHOLD) -> list[Card]:
    """Keeps the first occurrence of each card; drops the ones that are too similar."""
    kept: list[Card] = []
    kept_keys: list[str] = []

    for card in cards:
        key = _key_text(card)
        if not key:
            kept.append(card)
            continue
        is_duplicate = any(fuzz.ratio(key, other) >= threshold for other in kept_keys)
        if not is_duplicate:
            kept.append(card)
            kept_keys.append(key)

    return kept
