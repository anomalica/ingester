"""Speaker diarisation via pyannote.audio."""

from __future__ import annotations

import os
from pathlib import Path

from models import SpeakerSegment

DIARISATION_MODEL = "pyannote/speaker-diarization-3.1"

# Default clustering threshold: 0.7045 (model default).
# Higher = stricter merging = more speakers (over-split).
# Lower = looser merging = fewer speakers (under-split).
# Over-splitting is preferable: easier to merge than to untangle.
CLUSTERING_THRESHOLD = 0.85


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
    pipeline.instantiate(
        {
            "clustering": {
                "method": "centroid",
                "min_cluster_size": 12,
                "threshold": CLUSTERING_THRESHOLD,
            },
        }
    )
    pipeline.to(torch.device(device))

    import torchaudio

    waveform, sample_rate = torchaudio.load(str(audio_path))
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}
    result = pipeline(audio_input)

    # pyannote 4.x returns DiarizeOutput; extract the Annotation object
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append(SpeakerSegment(speaker=speaker, start=turn.start, end=turn.end))

    return segments
