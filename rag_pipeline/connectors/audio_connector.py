"""
connectors/audio_connector.py
Handles: Audio recordings (.mp3, .wav, .m4a, .flac, .ogg, .opus)

Pipeline:
  1. Transcription: faster-whisper (local, CPU or GPU)
  2. Speaker diarization: pyannote-audio (optional, requires HF token)

If diarization is disabled (default), emits one envelope with the full
transcript. If enabled, aligns whisper segments with pyannote speaker
labels and emits one envelope per speaker turn.

citation_meta page_or_timestamp = "HH:MM:SS–HH:MM:SS"
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


# ── Helpers ──────────────────────────────────────────────────

def _seconds_to_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Transcription ─────────────────────────────────────────────

def _transcribe(
    file_path: str,
    model_size: str = "base",
    device: str = "cpu",
) -> List[Dict[str, Any]]:
    """
    Run faster-whisper transcription.
    Returns list of segment dicts: {start, end, text}
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper not installed.\n"
            "Install with: pip install faster-whisper"
        )

    logger.info("Loading Whisper model %r on %s ...", model_size, device)
    model = WhisperModel(model_size, device=device, compute_type="int8")
    segments, info = model.transcribe(file_path, beam_size=5)

    logger.info(
        "Detected language: %s (%.1f%% confidence)",
        info.language,
        info.language_probability * 100,
    )

    return [
        {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
        for seg in segments
    ]


# ── Diarization ───────────────────────────────────────────────

def _diarize(
    file_path: str,
    hf_token: str,
) -> Any:
    """
    Run pyannote speaker diarization.
    Returns a pyannote Annotation object.
    """
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        raise RuntimeError(
            "pyannote-audio not installed.\n"
            "Install with: pip install pyannote-audio\n"
            "Also requires a HuggingFace token with model access."
        )

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    return pipeline(file_path)


def _assign_speakers(
    segments: List[Dict[str, Any]],
    diarization: Any,
) -> List[Dict[str, Any]]:
    """
    Match whisper segments to speaker labels from pyannote.
    Uses simple midpoint matching: find which diarization turn contains
    the midpoint of each whisper segment.
    """
    result: List[Dict[str, Any]] = []
    for seg in segments:
        midpoint = (seg["start"] + seg["end"]) / 2
        speaker = "UNKNOWN"
        for turn, _, label in diarization.itertracks(yield_label=True):
            if turn.start <= midpoint <= turn.end:
                speaker = label
                break
        result.append({**seg, "speaker": speaker})
    return result


# ── Grouping into speaker turns ───────────────────────────────

def _group_by_speaker(
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge consecutive segments from the same speaker into turns.
    """
    if not segments:
        return []
    turns: List[Dict[str, Any]] = []
    current = {
        "speaker": segments[0].get("speaker", "SPEAKER_00"),
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "text": segments[0]["text"],
    }
    for seg in segments[1:]:
        spk = seg.get("speaker", "SPEAKER_00")
        if spk == current["speaker"]:
            current["end"] = seg["end"]
            current["text"] += " " + seg["text"]
        else:
            turns.append(current)
            current = {
                "speaker": spk,
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
            }
    turns.append(current)
    return turns


# ── Main extractor ────────────────────────────────────────────

def extract_audio(
    file_path: str,
    whisper_model_size: str = "base",
    device: str = "cpu",
    diarization_enabled: bool = False,
    hf_token: str = "",
) -> Generator[Dict[str, Any], None, None]:
    path = Path(file_path)
    source_id = make_source_id(file_path)

    # Step 1: Transcribe
    segments = _transcribe(file_path, model_size=whisper_model_size, device=device)
    if not segments:
        logger.warning("No transcription output for %s", path.name)
        return

    # Step 2: Optionally diarize
    if diarization_enabled and hf_token:
        try:
            diarization = _diarize(file_path, hf_token)
            segments = _assign_speakers(segments, diarization)
        except Exception as exc:
            logger.warning("Diarization failed (will emit without speakers): %s", exc)
            for seg in segments:
                seg.setdefault("speaker", "SPEAKER_00")
    else:
        for seg in segments:
            seg.setdefault("speaker", "SPEAKER_00")

    # Step 3: Collect unique speakers
    speakers = sorted({seg["speaker"] for seg in segments})

    # Step 4: Group into speaker turns
    turns = _group_by_speaker(segments)

    # Total duration
    total_duration = segments[-1]["end"] if segments else 0.0

    for idx, turn in enumerate(turns):
        ts_start = _seconds_to_timestamp(turn["start"])
        ts_end = _seconds_to_timestamp(turn["end"])
        timestamp_range = f"{ts_start}\u2013{ts_end}"

        yield envelope(
            source_type="audio",
            source_id=f"{source_id}_turn{idx}",
            raw_path=file_path,
            raw_metadata={
                "file_name": path.name,
                "turn_index": idx,
                "speaker": turn["speaker"],
                "speakers": speakers,
                "timestamp_start": turn["start"],
                "timestamp_end": turn["end"],
                "timestamp_range": timestamp_range,
                "total_duration_seconds": total_duration,
                "whisper_model": whisper_model_size,
                "diarization_enabled": diarization_enabled,
                "full_text": turn["text"].strip(),
            },
            modality="text",
        )


SUPPORTED_EXTENSIONS_AUDIO = SUPPORTED_EXTENSIONS


def extract(
    file_path: str,
    config: Optional[Dict[str, Any]] = None,
) -> Generator[Dict[str, Any], None, None]:
    cfg = config or {}
    audio_cfg = cfg.get("audio", {})
    yield from extract_audio(
        file_path,
        whisper_model_size=audio_cfg.get("whisper_model_size", "base"),
        device=audio_cfg.get("whisper_device", "cpu"),
        diarization_enabled=audio_cfg.get("diarization_enabled", False),
        hf_token=audio_cfg.get("huggingface_token", ""),
    )
