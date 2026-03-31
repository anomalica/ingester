"""Speaker diarisation via pyannote.audio."""

from __future__ import annotations

import os
from pathlib import Path

from models import SpeakerSegment

DIARISATION_MODEL = "pyannote/speaker-diarization-3.1"


def diarise(audio_path: Path) -> list[SpeakerSegment]:
    """Identify speaker segments using pyannote.audio.

    Requires HF_TOKEN environment variable for downloading the gated model.
    Uses GPU if available, falls back to CPU.

    Args:
        audio_path: Path to the audio or video file.

    Returns:
        List of SpeakerSegments with speaker labels and timestamps.

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
            "model licence at https://huggingface.co/pyannote/speaker-diarization-3.1"
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipeline = Pipeline.from_pretrained(DIARISATION_MODEL, token=hf_token)
    pipeline.to(torch.device(device))

    diarization = pipeline(str(audio_path))

    segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append(SpeakerSegment(speaker=speaker, start=turn.start, end=turn.end))

    return segments
