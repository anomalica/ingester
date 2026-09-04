"""Transcription via WhisperX with word-level timestamp alignment."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from models import Segment, Word
from whisper_prompt import load_prompt

WHISPER_MODEL = "large-v3-turbo"
BATCH_SIZE = int(os.environ.get("WHISPER_BATCH", "8"))


def _env_float(name: str, default: float) -> float:
    """A float from the environment, or the default when unset or unparseable."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def transcribe(
    audio_path: Path, language: str | None = None
) -> tuple[list[Segment], dict]:
    """Transcribe audio using WhisperX with word-level alignment.

    Loads the Whisper model, transcribes, then runs wav2vec2 alignment
    to refine word-level timestamps. Uses GPU if available, falls back to CPU.

    Args:
            audio_path: Path to the audio or video file. WhisperX uses ffmpeg
                    internally to handle format conversion.
            language: ISO 639-1 language code. If None, WhisperX auto-detects.

    Returns:
            (segments, raw) - the processed Segments (text + per-word
            start/end), and the COMPLETE raw whisperx output kept verbatim for
            durable archival: detected language, the pre-align transcribe
            result (segment-level confidences: avg_logprob, no_speech_prob,
            compression_ratio) and the post-align result (per-word timings +
            alignment scores). Nothing is discarded.
    """
    import torch
    import whisperx

    # Device is overridable via WHISPER_DEVICE. On this machine the 6GB GPU is
    # shared with other always-on services (~2.3GB committed), leaving too
    # little for the full large-v3 encoder (turbo only shrinks the decoder), so
    # transcription is pinned to CPU here and the GPU is left for diarisation.
    device = os.environ.get("WHISPER_DEVICE") or (
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    compute_type = os.environ.get("WHISPER_COMPUTE") or (
        "float16" if device == "cuda" else "int8"
    )

    # Custom vocabulary: bias the model toward the corpus's names, acronyms and
    # places (spelled and cased correctly at source) via Whisper's initial_prompt.
    # Reviewable list at shared/whisper_prompt.txt; disable with INGEST_WHISPER_PROMPT=0.
    initial_prompt = load_prompt()
    asr_options = {"initial_prompt": initial_prompt} if initial_prompt else None

    # Speech detection decides what the model is even shown, so a quiet voice
    # under its threshold is not transcribed badly - it is cut out, and the
    # model fills the hole with something plausible. That is the failure mode
    # in an interview where the guest is close to the microphone and the people
    # asking questions are not: whole questions replaced by invented sentences,
    # while the guest reads perfectly.
    #
    # whisperx's defaults (onset 0.500, offset 0.363) are tuned for clean audio.
    # Measured on a 4-speaker interview whose quietest participant sits 9 dB
    # below the guest, over two windows, the same model and audio throughout:
    #
    #   window            default   these values   most sensitive (0.15/0.10)
    #   quiet questions    -0.258      -0.200          -0.175
    #   clean passage      -0.128      -0.124          -0.155
    #
    # (median avg_logprob, higher is better.) At the default the model produced
    # MORE words with WORSE confidence in the quiet window - padding, not
    # transcription - and lost a 50-word question entirely. The most sensitive
    # values recover the most there but cost accuracy on clean audio, because
    # more room noise is admitted and Whisper invents text on noise. These
    # values are the ones that improve the bad case without hurting the good.
    #
    # Raising the volume does NOT fix this: the same quiet turns transcribe
    # identically at +12 dB, and normalising the file to -16 LUFS changed the
    # transcript almost not at all. The audio was always audible; it was being
    # discarded before the model saw it.
    vad_options = {
        "vad_onset": _env_float("INGEST_VAD_ONSET", 0.30),
        "vad_offset": _env_float("INGEST_VAD_OFFSET", 0.20),
    }
    if initial_prompt:
        print(
            f"[whisper] initial_prompt bias on ({len(initial_prompt.split())} terms)",
            file=sys.stderr,
        )

    model = whisperx.load_model(
        WHISPER_MODEL,
        device,
        compute_type=compute_type,
        language=language,
        asr_options=asr_options,
        vad_options=vad_options,
    )
    transcribe_result = model.transcribe(str(audio_path), batch_size=BATCH_SIZE)

    detected_language = transcribe_result.get("language", language or "en")

    del model
    torch.cuda.empty_cache()

    model_a, metadata = whisperx.load_align_model(
        language_code=detected_language, device=device
    )
    aligned_result = whisperx.align(
        transcribe_result["segments"],
        model_a,
        metadata,
        str(audio_path),
        device,
        return_char_alignments=False,
    )

    segments = []
    for seg in aligned_result["segments"]:
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

    raw = {
        "language": detected_language,
        "transcribe": transcribe_result,
        "aligned": aligned_result,
    }
    return segments, raw
