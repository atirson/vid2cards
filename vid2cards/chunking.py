"""Splits long transcripts into chunks, respecting sentence boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transcribe import Segment

SENTENCE_END = re.compile(r"[.!?]\s*$")


@dataclass
class Chunk:
    text: str
    start_time: float
    end_time: float
    segments: list[Segment]


def split_into_chunks(segments: list[Segment], max_size: int) -> list[Chunk]:
    """Groups Whisper segments into chunks of up to `max_size` characters.

    Tries to cut at the nearest previous segment that ends a sentence (.!?);
    if none exists near the limit, cuts at the segment boundary anyway
    (that's already a natural pause in speech).
    """
    if not segments:
        return []

    chunks: list[Chunk] = []
    current: list[Segment] = []
    current_size = 0
    cut_candidate = -1  # index within `current` that ends a sentence

    def finalize(up_to: int):
        nonlocal current, current_size, cut_candidate
        part = current[: up_to + 1]
        rest = current[up_to + 1 :]
        text = " ".join(s.text.strip() for s in part)
        chunks.append(Chunk(text=text, start_time=part[0].start, end_time=part[-1].end, segments=part))
        current = rest
        current_size = sum(len(s.text) + 1 for s in current)
        cut_candidate = -1

    for seg in segments:
        current.append(seg)
        current_size += len(seg.text) + 1
        if SENTENCE_END.search(seg.text.strip()):
            cut_candidate = len(current) - 1

        if current_size >= max_size:
            if cut_candidate >= 0:
                finalize(cut_candidate)
            else:
                finalize(len(current) - 1)

    if current:
        text = " ".join(s.text.strip() for s in current)
        chunks.append(Chunk(text=text, start_time=current[0].start, end_time=current[-1].end, segments=current))

    return chunks


def find_time(term: str, segments: list[Segment], fallback_time: float, threshold: int = 55) -> float:
    """Finds the start of the segment where `term` (e.g. the card's concept) was
    probably spoken, using text similarity. Falls back to `fallback_time`
    (the chunk's start) if nothing similar enough is found."""
    from rapidfuzz import fuzz

    term_norm = term.strip().lower()
    if not term_norm or not segments:
        return fallback_time

    best_score = -1.0
    best_time = fallback_time
    for seg in segments:
        score = fuzz.partial_ratio(term_norm, seg.text.strip().lower())
        if score > best_score:
            best_score = score
            best_time = seg.start

    return best_time if best_score >= threshold else fallback_time
