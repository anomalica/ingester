"""Transcription via WhisperX with word-level timestamp alignment."""

from __future__ import annotations

from pathlib import Path

from models import Segment, Word

WHISPER_MODEL = "large-v3-turbo"
BATCH_SIZE = 16


def transcribe(audio_path: Path, language: str | None = None) -> list[Segment]:
    """Transcribe audio using WhisperX with word-level alignment.

    Loads the Whisper model, transcribes, then runs wav2vec2 alignment
    to refine word-level timestamps. Uses GPU if available, falls back to CPU.

    Args:
            audio_path: Path to the audio or video file. WhisperX uses ffmpeg
                    internally to handle format conversion.
            language: ISO 639-1 language code. If None, WhisperX auto-detects.

    Returns:
            List of Segments, each containing word-level timestamps.
    """
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    model = whisperx.load_model(
        WHISPER_MODEL, device, compute_type=compute_type, language=language
    )
    result = model.transcribe(str(audio_path), batch_size=BATCH_SIZE)

    detected_language = result.get("language", language or "en")

    model_a, metadata = whisperx.load_align_model(
        language_code=detected_language, device=device
    )
    result = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        str(audio_path),
        device,
        return_char_alignments=False,
    )

    segments = []
    for seg in result["segments"]:
        words = [
            Word(text=w["word"].strip(), start=w["start"], end=w["end"])
            for w in seg.get("words", [])
            if "start" in w and "end" in w
        ]
        segments.append(
            Segment(
                text=seg["text"].strip(),
                start=seg["start"],
                end=seg["end"],
                words=words,
            )
        )

    return segments
