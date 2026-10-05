"""Playlist listing and audio download via yt-dlp."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlaylistVideo:
    video_id: str
    title: str
    duration_s: int


class VideoUnavailable(Exception):
    pass


def extract_id(url_or_id: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url_or_id):
        return url_or_id
    raise ValueError(f"Could not recognize the link/ID: {url_or_id}")


def _base_cmd(cookies_file: str = "") -> list[str]:
    cmd = ["yt-dlp"]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    return cmd


def list_playlist(url: str, cookies_file: str = "") -> tuple[str, list[PlaylistVideo]]:
    """Lists the videos in a playlist without downloading anything (--flat-playlist -J).

    Returns (playlist_title, list_of_videos).
    """
    cmd = _base_cmd(cookies_file) + ["--flat-playlist", "-J", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list playlist {url}: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    playlist_title = data.get("title", "Playlist")
    entries = data.get("entries", [data])
    videos = []
    for e in entries:
        if not e:
            continue
        videos.append(PlaylistVideo(
            video_id=e["id"],
            title=e.get("title", e["id"]),
            duration_s=int(e.get("duration") or 0),
        ))
    return playlist_title, videos


def update_yt_dlp():
    subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], check=False, capture_output=True)


def download_audio(video_id: str, dest_dir: Path, cookies_file: str = "") -> Path:
    """Downloads only the video's audio. Raises VideoUnavailable if it can't."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(dest_dir / f"{video_id}.%(ext)s")
    cmd = _base_cmd(cookies_file) + [
        "-f", "bestaudio", "-x", "--audio-format", "mp3",
        "-o", output_template,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error = result.stderr.lower()
        unavailable_markers = ("private video", "video unavailable", "has been removed", "not available")
        if any(marker in error for marker in unavailable_markers):
            raise VideoUnavailable(result.stderr.strip())
        raise RuntimeError(f"Failed to download audio for {video_id}: {result.stderr.strip()}")

    path = dest_dir / f"{video_id}.mp3"
    if not path.exists():
        raise RuntimeError(f"yt-dlp finished without error but the expected file does not exist: {path}")
    return path


def get_metadata(video_id: str, cookies_file: str = "") -> PlaylistVideo:
    """Used by `add <url>`, when the video doesn't come from a playlist."""
    cmd = _base_cmd(cookies_file) + ["-J", "--flat-playlist", f"https://www.youtube.com/watch?v={video_id}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoUnavailable(result.stderr.strip())
    data = json.loads(result.stdout)
    return PlaylistVideo(
        video_id=data["id"],
        title=data.get("title", data["id"]),
        duration_s=int(data.get("duration") or 0),
    )
