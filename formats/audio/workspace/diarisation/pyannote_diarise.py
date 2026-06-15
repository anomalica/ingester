"""Speaker diarisation via pyannote.audio."""

from __future__ import annotations

import os
from pathlib import Path

from models import SpeakerSegment

DIARISATION_MODEL = "pyannote/speaker-diarization-community-1"


def diarise(audio_path: Path) -> tuple[list[SpeakerSegment], dict]:
    """Identify speaker segments using pyannote.audio.

    Requires HF_TOKEN environment variable for downloading the gated model.
    Uses GPU if available, falls back to CPU.

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

    device = "cuda" if torch.cuda.is_available() else "cpu"

    pipeline = Pipeline.from_pretrained(DIARISATION_MODEL, token=hf_token)
    pipeline.to(torch.device(device))

    import torchaudio

    waveform, sample_rate = torchaudio.load(str(audio_path))
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}
    result = pipeline(audio_input)

    # pyannote 4.x returns DiarizeOutput; extract the Annotation object
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    tracks = []
    for turn, track, speaker in annotation.itertracks(yield_label=True):
        segments.append(SpeakerSegment(speaker=speaker, start=turn.start, end=turn.end))
        tracks.append(
            {
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
                "track": str(track),
            }
        )

    raw = {"model": DIARISATION_MODEL, "tracks": tracks}
    return segments, raw
