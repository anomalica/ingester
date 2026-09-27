"""Speaker diarisation via pyannote.audio."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from models import SpeakerSegment

DIARISATION_MODEL = "pyannote/speaker-diarization-community-1"


def _load_audio(audio_path: Path):
    """Decode MP4/AAC with ffmpeg before passing a waveform to pyannote.

    torchaudio's soundfile backend cannot open MP4, even when its AAC track is
    valid. The archived input remains the original video; this WAV is temporary.
    """
    import torchaudio

    if audio_path.suffix.lower() not in {".mp4", ".m4v", ".mov", ".m4a"}:
        return torchaudio.load(str(audio_path))
    with tempfile.TemporaryDirectory(prefix="diarise-audio-") as directory:
        wav = Path(directory) / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(audio_path),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav),
            ],
            check=True,
        )
        return torchaudio.load(str(wav))


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

    waveform, sample_rate = _load_audio(audio_path)
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}
    result = pipeline(audio_input)

    return _serialise_result(result)
