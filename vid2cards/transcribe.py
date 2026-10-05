"""Audio transcription with faster-whisper on GPU (with CPU fallback)."""

from __future__ import annotations

import gc
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

GPU_MODEL = "large-v3-turbo"
GPU_COMPUTE_TYPE = "int8_float16"
CPU_FALLBACK_MODEL = "small"
CPU_COMPUTE_TYPE = "int8"


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    video_id: str
    language: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "language": self.language,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in self.segments
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            video_id=d["video_id"],
            language=d["language"],
            segments=[Segment(**s) for s in d["segments"]],
        )


def _load_audio_ffmpeg(path: Path, sr: int = 16000) -> np.ndarray:
    """Decodes audio into mono float32 PCM via the ffmpeg CLI.

    We don't use faster-whisper's internal decode_audio (the `av` library)
    because the prebuilt `av` wheel available for this Python is incompatible
    with the API faster-whisper expects, and older `av` versions don't
    compile against the system ffmpeg (the API changed too much).
    """
    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0", "-i", str(path),
        "-f", "f32le", "-ac", "1", "-ar", str(sr), "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _create_model() -> tuple[WhisperModel, str]:
    try:
        model = WhisperModel(GPU_MODEL, device="cuda", compute_type=GPU_COMPUTE_TYPE)
        return model, GPU_MODEL
    except Exception:
        model = WhisperModel(CPU_FALLBACK_MODEL, device="cpu", compute_type=CPU_COMPUTE_TYPE)
        return model, CPU_FALLBACK_MODEL


def transcribe(video_id: str, audio_path: Path) -> tuple[Transcript, str]:
    """Transcribes an audio file. Returns the transcript and the model name used.

    Uses vad_filter=True by default (trims silence, faster), but faster-whisper's
    VAD can classify segments that are sung or have heavy music as "no speech"
    and discard the whole audio. If that happens (0 segments for audio that
    clearly has content), redo without VAD as a fallback.
    """
    model, model_name = _create_model()
    try:
        audio = _load_audio_ffmpeg(audio_path)

        raw_segments, info = model.transcribe(audio, vad_filter=True)
        segments = [Segment(start=s.start, end=s.end, text=s.text) for s in raw_segments]

        duration_s = len(audio) / 16000
        if not segments and duration_s > 5:
            raw_segments, info = model.transcribe(audio, vad_filter=False)
            segments = [Segment(start=s.start, end=s.end, text=s.text) for s in raw_segments]

        transcript = Transcript(video_id=video_id, language=info.language, segments=segments)
        return transcript, model_name
    finally:
        del model
        gc.collect()
