"""Pipeline orchestration: sync -> transcribe -> generate cards -> export."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

from . import anki, playlist
from .chunking import find_time, split_into_chunks
from .config import Config
from .db import Database
from .dedup import deduplicate_cards
from .generate import Card, GenerationError, Material, QuizQuestion, generate_material
from .transcribe import Transcript, transcribe

logger = logging.getLogger("vid2cards")


def sync(config: Config, db: Database):
    for url in config.playlists_urls:
        playlist_title, videos = playlist.list_playlist(url, config.cookies_file)
        new_count = 0
        for v in videos:
            existed = db.video(v.video_id) is not None
            db.register_new(v.video_id, v.title, v.duration_s, url, playlist_title)
            if not existed:
                new_count += 1
        logger.info(f"Sync '{playlist_title}': {len(videos)} videos in playlist, {new_count} new")


def transcript_path_for(config: Config, video_id: str) -> Path:
    return config.transcripts_dir / f"{video_id}.json"


def cards_path_for(config: Config, video_id: str) -> Path:
    return config.cards_dir / f"{video_id}.json"


def transcription_phase(config: Config, db: Database, videos: list) -> None:
    """Transcribes videos in the 'new' state. Skips ones that already have a checkpoint on disk."""
    audio_tmp_dir = config.data_dir / "audio_tmp"

    for row in videos:
        video_id = row["video_id"]
        if row["state"] != "new":
            continue

        transcript_path = transcript_path_for(config, video_id)
        if transcript_path.exists():
            logger.info(f"{video_id}: transcript already exists on disk, skipping download/whisper")
            db.update_state(video_id, "transcribed")
            continue

        t0 = time.time()
        try:
            audio_path = playlist.download_audio(video_id, audio_tmp_dir, config.cookies_file)
            transcript, model_used = transcribe(video_id, audio_path)
            audio_path.unlink(missing_ok=True)

            config.transcripts_dir.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(
                json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            elapsed = time.time() - t0
            db.update_state(
                video_id, "transcribed",
                whisper_model=model_used, transcription_time_s=elapsed,
            )
            logger.info(f"{video_id}: transcribed in {elapsed:.1f}s ({model_used})")
        except playlist.VideoUnavailable as e:
            db.mark_unavailable(video_id, str(e))
            logger.warning(f"{video_id}: unavailable ({e})")
        except Exception as e:
            db.register_error(video_id, str(e), config.max_retries)
            logger.error(f"{video_id}: error during transcription ({e})")


def generation_phase(config: Config, db: Database, video_ids: list[str]) -> None:
    """Generates cards for the given videos (must be in the 'transcribed' state).

    Skips videos whose cards checkpoint already exists on disk.
    """
    targets = [db.video(vid) for vid in video_ids]
    targets = [row for row in targets if row and row["state"] == "transcribed"]

    for idx, row in enumerate(targets):
        video_id = row["video_id"]
        cards_path = cards_path_for(config, video_id)
        if cards_path.exists():
            logger.info(f"{video_id}: cards already exist on disk, skipping generation")
            db.update_state(video_id, "cards_generated")
            continue

        transcript_path = transcript_path_for(config, video_id)
        transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
        transcript = Transcript.from_dict(transcript_data)
        chunks = split_into_chunks(transcript.segments, config.max_chars_per_chunk)

        t0 = time.time()
        try:
            raw_cards: list[Card] = []
            questions: list[QuizQuestion] = []
            video_topic = row["title"]

            for i, chunk in enumerate(chunks):
                is_last_chunk_of_run = (idx == len(targets) - 1) and (i == len(chunks) - 1)
                material: Material = generate_material(
                    chunk.text, f"{row['title']}, part {i + 1}/{len(chunks)}",
                    config.llm_model,
                    num_ctx=config.llm_num_ctx, temperature=config.llm_temperature,
                    keep_alive=0 if is_last_chunk_of_run else None,
                )
                for c in material.cards:
                    c.start_time = find_time(c.concept, chunk.segments, chunk.start_time)
                raw_cards += material.cards
                questions += material.quiz
                if i == 0:
                    video_topic = material.topic

            deduped_cards = deduplicate_cards(raw_cards, config.min_similarity)
            elapsed = time.time() - t0

            config.cards_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "topic": video_topic,
                "cards": [c.model_dump() for c in deduped_cards],
                "quiz": [q.model_dump() for q in questions],
            }
            cards_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            db.update_state(
                video_id, "cards_generated",
                llm_model=config.llm_model, prompt_version=config.prompt_version,
                n_cards=len(deduped_cards), generation_time_s=elapsed,
            )
            logger.info(f"{video_id}: {len(deduped_cards)} cards generated in {elapsed:.1f}s")
        except GenerationError as e:
            db.register_error(video_id, str(e), config.max_retries)
            logger.error(f"{video_id}: error generating cards ({e})")


def export_phase(config: Config, db: Database, video_ids: list[str]) -> Path | None:
    """Builds today's .apkg + quiz using only the videos processed in this run."""
    targets = [db.video(vid) for vid in video_ids]
    targets = [row for row in targets if row and row["state"] == "cards_generated"]
    if not targets:
        logger.info("No new videos to export today.")
        return None

    import genanki

    from . import ankiconnect

    send_via_ankiconnect = config.ankiconnect_enabled and ankiconnect.available(config.ankiconnect_url)
    if config.ankiconnect_enabled and not send_via_ankiconnect:
        logger.warning(f"AnkiConnect enabled but unavailable at {config.ankiconnect_url}; continuing with .apkg only")
    elif send_via_ankiconnect:
        ankiconnect.ensure_models(config.ankiconnect_url)

    quiz_blocks = []
    deck_package = []
    ankiconnect_notes_sent = 0

    for row in targets:
        video_id = row["video_id"]
        cards_data = json.loads(cards_path_for(config, video_id).read_text(encoding="utf-8"))
        cards = [Card(**c) for c in cards_data["cards"]]
        questions = [QuizQuestion(**q) for q in cards_data["quiz"]]
        topic = cards_data["topic"]

        notes = anki.build_notes(cards, topic, video_id)

        deck_name = f"Vid2Cards::{row['playlist_title']}::{row['title']}"
        deck = genanki.Deck(anki._stable_id(deck_name), deck_name)
        for n in notes:
            deck.add_note(n)
        deck_package.append(deck)
        quiz_blocks.append((row["title"], questions))

        if send_via_ankiconnect:
            try:
                ankiconnect_notes_sent += ankiconnect.send_notes(
                    cards, topic, video_id, deck_name, config.ankiconnect_url
                )
            except Exception as e:
                logger.error(f"{video_id}: failed to send via AnkiConnect ({e}); .apkg is still available")

    if send_via_ankiconnect and ankiconnect_notes_sent and config.ankiconnect_sync:
        try:
            ankiconnect.sync(config.ankiconnect_url)
        except Exception as e:
            logger.error(f"Failed to sync with AnkiWeb: {e}")

    today = date.today().isoformat()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    apkg_path = config.output_dir / f"vid2cards_{today}.apkg"
    genanki.Package(deck_package).write_to_file(str(apkg_path))

    from .quiz import write_quiz
    quiz_path = config.output_dir / f"quiz_{today}.md"
    write_quiz(quiz_blocks, today, quiz_path)

    now = datetime.now().isoformat(timespec="seconds")
    for row in targets:
        db.update_state(row["video_id"], "exported", exported_at=now)

    logger.info(f"Exported: {apkg_path} ({sum(len(d.notes) for d in deck_package)} notes)")
    return apkg_path


def run(config: Config, db: Database) -> Path | None:
    sync(config, db)
    pending = db.pending_videos(config.videos_per_run)
    video_ids = [row["video_id"] for row in pending]

    logger.info(f"Processing {len(video_ids)} video(s) in this run")

    transcription_phase(config, db, pending)
    generation_phase(config, db, video_ids)
    return export_phase(config, db, video_ids)
