"""Speaker diarisation via pyannote.audio."""

from __future__ import annotations

import os
from pathlib import Path

from models import SpeakerSegment

DIARISATION_MODEL = "pyannote/speaker-diarization-community-1"


def _serialise_result(result) -> tuple[list[SpeakerSegment], dict]:
    """Serialise Community-1 output while keeping regular tracks as the default."""

    def tracks(annotation) -> tuple[list[SpeakerSegment], list[dict]]:
        segments = []
        raw_tracks = []
        for turn, track, speaker in annotation.itertracks(yield_label=True):
            segments.append(
                SpeakerSegment(speaker=speaker, start=turn.start, end=turn.end)
            )
            raw_tracks.append(
                {
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker,
                    "track": str(track),
                }
            )
        return segments, raw_tracks

    annotation = getattr(result, "speaker_diarization", result)
    segments, regular_tracks = tracks(annotation)
    raw = {"model": DIARISATION_MODEL, "tracks": regular_tracks}

    # Community-1 also emits a non-overlapping annotation designed for
    # transcript reconciliation. Preserve it for model-free A/B evaluation,
    # but keep the established regular annotation on the production path.
    exclusive = getattr(result, "exclusive_speaker_diarization", None)
    if exclusive is not None:
        _, raw["exclusive_tracks"] = tracks(exclusive)
    return segments, raw


def diarise(audio_path: Path) -> tuple[list[SpeakerSegment], dict]:
    """Identify speaker segments using pyannote.audio.

    Requires HF_TOKEN environment variable for downloading the gated model.
    Defaults to CUDA and requires an explicit ``DIARISE_DEVICE=cpu`` override
    when no CUDA device is available.

    Args:
        audio_path: Path to the audio or video file.

    Returns:
        (speaker_segments, raw) - the processed SpeakerSegments, and the
        complete diarisation output kept verbatim for durable archival
        (every track: start, end, speaker, track label).

    Raises:
        RuntimeError: If HF_TOKEN is not set.
    """
    import torch
    from pyannote.audio import Pipeline

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise RuntimeError(
            "HF_TOKEN environment variable required for pyannote model download. "
            "Get a token at https://huggingface.co/settings/tokens and accept the "
            f"model licence at https://huggingface.co/{DIARISATION_MODEL}"
        )

    # Same rule as transcription: no silent CPU fallback for GPU work.
    device = os.environ.get("DIARISE_DEVICE")
    if not device:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "no CUDA device available for diarisation. Refusing the CPU "
                "fallback - set DIARISE_DEVICE=cpu to override deliberately."
            )
        device = "cuda"

    pipeline = Pipeline.from_pretrained(DIARISATION_MODEL, token=hf_token)
    pipeline.to(torch.device(device))

    import torchaudio

    waveform, sample_rate = torchaudio.load(str(audio_path))
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}
    result = pipeline(audio_input)

    return _serialise_result(result)
