"""SQLite: source of truth for each video's state. Avoids reprocessing."""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
from collections.abc import Iterator
from pathlib import Path

STATES = ("new", "transcribed", "cards_generated", "exported", "error", "unavailable")

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    title           TEXT,
    duration_s      INTEGER,
    playlist_url    TEXT,
    playlist_title  TEXT,
    state           TEXT NOT NULL DEFAULT 'new',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    whisper_model   TEXT,
    llm_model       TEXT,
    prompt_version  TEXT,
    n_cards         INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    transcription_time_s REAL,
    generation_time_s    REAL,
    exported_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_state ON videos(state);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- reads ----

    def video(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()

    def pending_videos(self, limit: int) -> list[sqlite3.Row]:
        """Videos not yet exported and not in an error/unavailable state."""
        return self.conn.execute(
            """
            SELECT * FROM videos
            WHERE state NOT IN ('exported', 'error', 'unavailable')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def all_videos(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos ORDER BY created_at ASC").fetchall()

    def videos_with_errors(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM videos WHERE state = 'error'").fetchall()

    # ---- writes ----

    def register_new(
        self, video_id: str, title: str, duration_s: int,
        playlist_url: str = "", playlist_title: str = "Avulsos",
    ):
        self.conn.execute(
            """
            INSERT INTO videos (video_id, title, duration_s, playlist_url, playlist_title, state)
            VALUES (?, ?, ?, ?, ?, 'new')
            ON CONFLICT(video_id) DO NOTHING
            """,
            (video_id, title, duration_s, playlist_url, playlist_title),
        )
        self.conn.commit()

    def update_state(self, video_id: str, state: str, **fields):
        assert state in STATES, f"invalid state: {state}"
        fields["state"] = state
        sets = ", ".join(f"{k} = ?" for k in fields)
        sets += ", updated_at = datetime('now')"
        values = list(fields.values())
        self.conn.execute(
            f"UPDATE videos SET {sets} WHERE video_id = ?", (*values, video_id)
        )
        self.conn.commit()

    def register_error(self, video_id: str, message: str, max_retries: int = 3):
        row = self.video(video_id)
        attempts = (row["attempts"] if row else 0) + 1
        state = "error" if attempts >= max_retries else "new"
        self.conn.execute(
            """
            UPDATE videos
            SET attempts = ?, last_error = ?, state = ?, updated_at = datetime('now')
            WHERE video_id = ?
            """,
            (attempts, message, state, video_id),
        )
        self.conn.commit()

    def mark_unavailable(self, video_id: str, reason: str):
        self.conn.execute(
            """
            UPDATE videos SET state = 'unavailable', last_error = ?, updated_at = datetime('now')
            WHERE video_id = ?
            """,
            (reason, video_id),
        )
        self.conn.commit()

    def retry_errors(self, video_id: str | None = None):
        if video_id:
            self.conn.execute(
                "UPDATE videos SET state = 'new', attempts = 0 WHERE video_id = ? AND state = 'error'",
                (video_id,),
            )
        else:
            self.conn.execute(
                "UPDATE videos SET state = 'new', attempts = 0 WHERE state = 'error'"
            )
        self.conn.commit()


@contextlib.contextmanager
def run_lock(lock_path: Path) -> Iterator[None]:
    """Prevents two simultaneous runs (timer + manual, for example)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                f"A run is already in progress (lock: {lock_path})"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
