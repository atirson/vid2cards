"""Builds Anki models, notes, and packages (genanki)."""

from __future__ import annotations

import hashlib
import re

import genanki

from .generate import Card

CSS = """
.card { font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 20px;
  text-align: left; max-width: 640px; margin: auto; line-height: 1.45; }
.tipo { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; opacity: .55; }
.cloze { font-weight: bold; color: #2a7ae2; }
"""


def _stable_id(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


# Note: the model/field names below ("Frente", "Verso", "Tipo", "Fonte"...) and the
# `_stable_id` seed strings are intentionally left as-is (not translated) because they
# are keys into the Anki Note Types already created in the live collection via
# AnkiConnect. Changing them would desync from previously exported/synced notes.
BASIC_MODEL = genanki.Model(
    _stable_id("vid2cards_basico_v1"), "Vid2Cards - Básico",
    fields=[{"name": "Frente"}, {"name": "Verso"}, {"name": "Tipo"}, {"name": "Fonte"}],
    templates=[{
        "name": "Card",
        "qfmt": '<div class="tipo">{{Tipo}}</div>{{Frente}}',
        "afmt": '{{FrontSide}}<hr id="answer">{{Verso}}<br><br><small>{{Fonte}}</small>',
    }],
    css=CSS,
)

CLOZE_MODEL = genanki.Model(
    _stable_id("vid2cards_cloze_v1"), "Vid2Cards - Cloze",
    fields=[{"name": "Texto"}, {"name": "Extra"}, {"name": "Fonte"}],
    templates=[{
        "name": "Cloze",
        "qfmt": "{{cloze:Texto}}",
        "afmt": "{{cloze:Texto}}<br>{{Extra}}<br><br><small>{{Fonte}}</small>",
    }],
    css=CSS,
    model_type=genanki.Model.CLOZE,
)


def tag(s: str) -> str:
    return re.sub(r"[^\w-]+", "_", s.strip())[:40] or "geral"


def timestamp_link(video_id: str, time_s: float) -> str:
    seconds = max(0, int(time_s))
    return f"https://youtu.be/{video_id}?t={seconds}"


def build_notes(
    cards: list[Card],
    topic: str,
    video_id: str,
) -> list[genanki.Note]:
    """Converts validated Cards into genanki Notes, with a stable source link and GUID.

    The GUID is derived from (video_id, front_text) so reimporting the .apkg
    never creates duplicate cards in Anki, even on future runs. The source
    timestamp comes from `card.start_time` (filled in by the pipeline with the
    start of the transcript chunk that produced the card).
    """
    notes = []
    topic_tag = tag(topic)

    for card in cards:
        link = timestamp_link(video_id, card.start_time)
        source_html = f'<a href="{link}">{link}</a>'
        tags = [f"tema::{topic_tag}", f"conceito::{tag(card.concept)}", f"tipo::{card.type}"]

        if card.type == "cloze" and "{{c" in card.text:
            guid = genanki.guid_for(video_id, card.text)
            notes.append(genanki.Note(
                model=CLOZE_MODEL,
                fields=[card.text, card.extra, source_html],
                tags=tags,
                guid=guid,
            ))
        elif card.front and card.back:
            guid = genanki.guid_for(video_id, card.front)
            notes.append(genanki.Note(
                model=BASIC_MODEL,
                fields=[card.front, card.back, card.type, source_html],
                tags=tags,
                guid=guid,
            ))

    return notes


def build_package(deck_name: str, notes: list[genanki.Note]) -> genanki.Package:
    deck = genanki.Deck(_stable_id(deck_name), deck_name)
    for note in notes:
        deck.add_note(note)
    return genanki.Package(deck)
