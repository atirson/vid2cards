"""Optional AnkiConnect integration: pushes notes directly into a headless
Anki Desktop running on the server, which syncs with AnkiWeb (and from there,
the phone).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .anki import CSS, tag, timestamp_link
from .generate import Card

logger = logging.getLogger("vid2cards")

DEFAULT_URL = "http://127.0.0.1:8765"

# Left untranslated: these are the Anki Note Type names already created in the
# live collection. Changing them would make this code create duplicate models
# instead of reusing the existing ones.
BASIC_MODEL_NAME = "Vid2Cards - Básico"
CLOZE_MODEL_NAME = "Vid2Cards - Cloze"


class AnkiConnectUnavailable(Exception):
    pass


def _request(url: str, action: str, **params) -> dict:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise AnkiConnectUnavailable(f"AnkiConnect did not respond at {url}: {e}") from e

    if data.get("error"):
        raise RuntimeError(f"AnkiConnect error in '{action}': {data['error']}")
    return data["result"]


def available(url: str = DEFAULT_URL) -> bool:
    try:
        _request(url, "version")
        return True
    except AnkiConnectUnavailable:
        return False


def ensure_models(url: str = DEFAULT_URL):
    """Creates the Vid2Cards Note Types in Anki, if they don't exist yet."""
    existing = _request(url, "modelNames")

    if BASIC_MODEL_NAME not in existing:
        _request(
            url, "createModel",
            modelName=BASIC_MODEL_NAME,
            inOrderFields=["Frente", "Verso", "Tipo", "Fonte"],
            css=CSS,
            cardTemplates=[{
                "Name": "Card",
                "Front": '<div class="tipo">{{Tipo}}</div>{{Frente}}',
                "Back": '{{FrontSide}}<hr id="answer">{{Verso}}<br><br><small>{{Fonte}}</small>',
            }],
        )

    if CLOZE_MODEL_NAME not in existing:
        _request(
            url, "createModel",
            modelName=CLOZE_MODEL_NAME,
            inOrderFields=["Texto", "Extra", "Fonte"],
            css=CSS,
            isCloze=True,
            cardTemplates=[{
                "Name": "Cloze",
                "Front": "{{cloze:Texto}}",
                "Back": "{{cloze:Texto}}<br>{{Extra}}<br><br><small>{{Fonte}}</small>",
            }],
        )


def send_notes(
    cards: list[Card],
    topic: str,
    video_id: str,
    deck_name: str,
    url: str = DEFAULT_URL,
) -> int:
    """Sends the cards to Anki via AnkiConnect. Returns how many notes were created.

    Notes whose content already exists in the deck are skipped (Anki's native
    dedup by the first field's content), avoiding duplicates on re-export.
    """
    _request(url, "createDeck", deck=deck_name)
    topic_tag = tag(topic)

    notes = []
    for card in cards:
        link = timestamp_link(video_id, card.start_time)
        source_html = f'<a href="{link}">{link}</a>'
        tags = [f"tema::{topic_tag}", f"conceito::{tag(card.concept)}", f"tipo::{card.type}"]

        if card.type == "cloze" and "{{c" in card.text:
            notes.append({
                "deckName": deck_name,
                "modelName": CLOZE_MODEL_NAME,
                "fields": {"Texto": card.text, "Extra": card.extra, "Fonte": source_html},
                "tags": tags,
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            })
        elif card.front and card.back:
            notes.append({
                "deckName": deck_name,
                "modelName": BASIC_MODEL_NAME,
                "fields": {"Frente": card.front, "Verso": card.back, "Tipo": card.type, "Fonte": source_html},
                "tags": tags,
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            })

    if not notes:
        return 0

    results = _request(url, "addNotes", notes=notes)
    note_ids = [r for r in results if r is not None]

    if note_ids:
        # Workaround: on this combination of versions, addNotes ignores "deckName"
        # and creates everything in the "Default" deck. Explicitly move to the right deck.
        infos = _request(url, "notesInfo", notes=note_ids)
        card_ids = [cid for info in infos for cid in info["cards"]]
        if card_ids:
            _request(url, "changeDeck", cards=card_ids, deck=deck_name)

    created = len(note_ids)
    logger.info(f"AnkiConnect: {created}/{len(notes)} notes created in deck '{deck_name}'")
    return created


def sync(url: str = DEFAULT_URL):
    """Triggers a sync with AnkiWeb (requires prior login done manually in the GUI)."""
    _request(url, "sync")
