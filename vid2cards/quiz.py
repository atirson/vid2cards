"""Builds the daily quiz in Markdown, with the answer key at the end."""

from __future__ import annotations

from pathlib import Path

from .generate import QuizQuestion


def write_quiz(
    blocks_by_video: list[tuple[str, list[QuizQuestion]]],
    date: str,
    path: Path,
):
    """blocks_by_video: list of (video_title, questions)."""
    lines = [
        f"# Vid2Cards Quiz — {date}",
        "",
        "Take it before opening Anki, without looking anything up. Check the answer key at the end.",
        "",
    ]

    counter = 0
    answer_key: list[str] = []

    for title, questions in blocks_by_video:
        if not questions:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for q in questions:
            counter += 1
            lines.append(f"**{counter}.** {q.question}")
            lines += [f"   {o}" for o in q.options]
            lines.append("")
            answer_key.append(f"**{counter}.** {q.answer} — {q.explanation}")

    lines += ["---", "## Answer Key", ""]
    lines += answer_key

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
