"""Loads config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    playlists_urls: list[str]
    cookies_file: str
    videos_per_run: int
    max_retries: int
    max_chars_per_chunk: int
    whisper_gpu_model: str
    whisper_gpu_compute_type: str
    whisper_cpu_model: str
    whisper_cpu_compute_type: str
    llm_model: str
    llm_alternative_model: str
    llm_num_ctx: int
    llm_temperature: float
    prompt_version: str
    min_similarity: int
    ankiconnect_enabled: bool
    ankiconnect_url: str
    ankiconnect_sync: bool
    data_dir: Path
    output_dir: Path
    logs_dir: Path

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def cards_dir(self) -> Path:
        return self.data_dir / "cards"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vid2cards.db"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "vid2cards.lock"


def load_config(path: Path | None = None) -> Config:
    path = path or (PROJECT_ROOT / "config.toml")
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    return Config(
        playlists_urls=raw["playlists"]["urls"],
        cookies_file=raw["playlists"].get("cookies_file", ""),
        videos_per_run=raw["limits"]["videos_per_run"],
        max_retries=raw["limits"]["max_retries"],
        max_chars_per_chunk=raw["limits"]["max_chars_per_chunk"],
        whisper_gpu_model=raw["whisper"]["gpu_model"],
        whisper_gpu_compute_type=raw["whisper"]["gpu_compute_type"],
        whisper_cpu_model=raw["whisper"]["cpu_fallback_model"],
        whisper_cpu_compute_type=raw["whisper"]["cpu_compute_type"],
        llm_model=raw["llm"]["model"],
        llm_alternative_model=raw["llm"]["alternative_model"],
        llm_num_ctx=raw["llm"]["num_ctx"],
        llm_temperature=raw["llm"]["temperature"],
        prompt_version=raw["llm"]["prompt_version"],
        min_similarity=raw["dedup"]["min_similarity"],
        ankiconnect_enabled=raw.get("ankiconnect", {}).get("enabled", False),
        ankiconnect_url=raw.get("ankiconnect", {}).get("url", "http://127.0.0.1:8765"),
        ankiconnect_sync=raw.get("ankiconnect", {}).get("sync_after_export", False),
        data_dir=PROJECT_ROOT / raw["folders"]["data"],
        output_dir=PROJECT_ROOT / raw["folders"]["output"],
        logs_dir=PROJECT_ROOT / raw["folders"]["logs"],
    )
