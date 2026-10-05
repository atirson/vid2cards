"""Flashcard + quiz generation via a local LLM (Ollama), with structured output."""

from __future__ import annotations

from typing import Literal

import ollama
from pydantic import BaseModel, Field, ValidationError

NUM_CTX = 16384
TEMPERATURE = 0.3
MAX_ATTEMPTS = 3  # 1 original + 2 retries, per spec

PROMPT = """You are a learning science expert. Based on the transcript
below, create study material in Brazilian Portuguese.

Rules for the flashcards:
- The transcript is automatically generated and may contain errors (swapped words,
  misspelled technical terms): infer the correct term from context and use it in the cards.
- Identify the concepts that are genuinely important (ignore jokes, ads, "subscribe" calls).
- For EACH concept, create 2 to 3 cards from different angles:
  * "definicao": a direct, short question about the concept;
  * "aplicacao": a new scenario/example where the concept must be applied;
  * "porque": a "why", "what would happen if", or "compare X with Y" question.
- One idea per card. Short answers (ideally up to 2 sentences).
- When it makes sense, include "cloze" cards (text field with {{{{c1::gap}}}}) for facts,
  formulas, or lists. Cloze cards don't use front/back, they use text/extra.

Rules for the quiz:
- 5 to 8 varied questions (multiple choice and open-ended), harder than the cards,
  mixing concepts together. A brief explanation for each answer.

Video/segment title: {title}

TRANSCRIPT:
{text}
"""


class Card(BaseModel):
    type: Literal["definicao", "aplicacao", "porque", "cloze"]
    concept: str
    front: str = ""
    back: str = ""
    text: str = ""
    extra: str = ""
    start_time: float = 0.0  # filled in by the pipeline, not returned by the LLM


class QuizQuestion(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str
    explanation: str = ""


class Material(BaseModel):
    topic: str
    cards: list[Card]
    quiz: list[QuizQuestion]


class GenerationError(Exception):
    pass


def generate_material(
    text: str,
    title: str,
    model: str,
    *,
    num_ctx: int = NUM_CTX,
    temperature: float = TEMPERATURE,
    keep_alive: int | None = None,
) -> Material:
    """Calls Ollama with structured output (JSON Schema) and validates it with Pydantic.

    Tries up to MAX_ATTEMPTS times before giving up.
    """
    schema = Material.model_json_schema()
    prompt = PROMPT.format(title=title, text=text)

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            format=schema,
            options={"temperature": temperature, "num_ctx": num_ctx},
        )
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        try:
            response = ollama.chat(**kwargs)
            raw = response["message"]["content"]
            return Material.model_validate_json(raw)
        except (ValidationError, ValueError) as e:
            last_error = e
            continue

    raise GenerationError(
        f"Failed to generate/validate JSON after {MAX_ATTEMPTS} attempts: {last_error}"
    )
