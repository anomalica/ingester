# Media Ingester Design

Converts audio and video sources (podcasts, YouTube, broadcast interviews) into the Anomalica record format - markdown with YAML annotations, speaker turn boundaries, and timestamps.

## Scope

This spec covers the `formats/media/` format handler. It receives a pre-fetched audio or video file from the acquire/staging pipeline and produces a structured transcript as an Anomalica record.

The acquire layer already handles downloading. This handler receives a local file path via the staging directory, same as the webpage and PDF handlers.

## Pipeline

```
staging/{uuid}/
  asset.mp4 (or .mp3, .webm, etc.)
  manifest.json
      |
      v
  1. Transcribe (WhisperX)
      |  word-level timestamps, language detection
      v
  2. Diarise (pyannote.audio)
      |  segment speakers (SPEAKER_00, SPEAKER_01, ...)
      v
  3. Identify speakers (optional, phase 2)
      |  match voice embeddings to known speaker profiles
      v
  4. Build record
      |  frontmatter with speaker roster
      |  speaker turn annotations with timestamps
      |  content as transcript text
      v
  5. Validate and write
      output/store/{hash}.md
      output/records/{date}-{type}-{slug}.md
```

## Phased implementation

### Phase 1: Transcription and diarisation

The minimum useful pipeline. Produces accurate transcripts with anonymous speaker labels (SPEAKER_00, SPEAKER_01). No voice matching against known profiles.

- Transcribe with WhisperX (Whisper Large V3 Turbo, word-level timestamps)
- Diarise with pyannote.audio 4.0 (community-1 model, VBx clustering)
- Align WhisperX word timestamps to pyannote speaker segments
- Build record with anonymous speaker roster (`confirmed: false`)
- Validate and write to output

### Phase 2: Speaker identification

Adds voice embedding matching to identify known speakers. Builds on Phase 1 output.

- Extract voice embeddings per speaker segment (WeSpeaker ECAPA-TDNN)
- Compare against a profile database of known speaker embeddings
- Match above a confidence threshold (suggested: cosine similarity > 0.7)
- Update speaker roster with names and `confirmed: false` (human confirmation required)
- The profile database is populated over time as humans confirm identifications

Phase 2 is a separate piece of work and not covered in detail here.

## Record format

Follows the specification at `anomalica/architecture/record-format.md`.

### Frontmatter

```yaml
---
schema: anomalica/record/1
title: "Lex Fridman Podcast #122 - David Fravor"
date: 2020-09-08
source_type: audio
source_url: https://youtube.com/watch?v=aB8zcAttP1E
duration: 7200
content_hash: sha256:abc123...
speakers:
  - id: speaker_00
    name: Speaker 0
    confirmed: false
  - id: speaker_01
    name: Speaker 1
    confirmed: false
---
```

For Phase 1, speakers are anonymous. The `id` field is used in speaker turn annotations. `confirmed: false` signals that no human has verified the identity. `source_type` is `audio` or `video` depending on the original source (even if only the audio track is processed).

### Speaker turn annotations

```markdown
---
speaker: speaker_00
time: 00:01:23
---
So tell me about what happened in 2004. You were a Navy pilot stationed on the Nimitz.

---
speaker: speaker_01
time: 00:01:45
---
We had been at sea for roughly 2 weeks. I was the Commanding Officer of Strike Fighter Squadron Forty-One.
```

Each turn uses a block annotation with `speaker` (referencing the frontmatter roster `id`) and `time` in `HH:MM:SS` format. All content after a speaker annotation until the next speaker annotation belongs to that speaker.

### Inline annotations

Non-speech audio events use inline annotations:

```markdown
{{audience: laughter}}

{{action: shows photograph}}

{{music: intro jingle}}
```

These are detected heuristically during transcription alignment (Phase 1 can skip this and add it later).

## Architecture

```
formats/media/
  cm.yaml                    # container-magic config (heavy: PyTorch, WhisperX, pyannote)
  format.yaml                # declares handled MIME types
  workspace/
    ingest_media.py           # CLI entry point and orchestration
    transcription/
      whisperx_transcribe.py  # WhisperX transcription wrapper
    diarisation/
      pyannote_diarise.py     # pyannote.audio diarisation wrapper
    alignment/
      align.py                # Align WhisperX words to pyannote speaker segments
    tests/
      conftest.py
      test_ingest_media.py
      test_alignment.py
```

### ingest_media.py

Reads the staging directory manifest, runs the pipeline, writes the record. Same pattern as `ingest_webpage.py` and `ingest_pdf.py`.

```python
def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    manifest = read_manifest(staging_dir)
    audio_path = staging_dir / manifest["asset"]

    # Transcribe
    segments = transcribe(audio_path)

    # Diarise
    speaker_segments = diarise(audio_path)

    # Align transcription to speakers
    turns = align(segments, speaker_segments)

    # Build record
    content = build_record(manifest, turns)

    # Hash, validate, write
    ...
```

### transcription/whisperx_transcribe.py

Wraps WhisperX to produce word-level timestamps.

Input: audio file path
Output: list of segments, each with text, start time, end time, and word-level timestamps

```python
@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float    # seconds

@dataclass
class Segment:
    text: str
    start: float
    end: float
    words: list[Word]

def transcribe(audio_path: Path, language: str | None = None) -> list[Segment]:
    """Transcribe audio using WhisperX with word-level alignment."""
    ...
```

WhisperX handles language detection automatically if not specified. The Whisper Large V3 Turbo model supports 99+ languages.

### diarisation/pyannote_diarise.py

Wraps pyannote.audio to identify speaker segments.

Input: audio file path
Output: list of speaker segments with start, end, and speaker label

```python
@dataclass
class SpeakerSegment:
    speaker: str   # "SPEAKER_00", "SPEAKER_01", etc.
    start: float   # seconds
    end: float     # seconds

def diarise(audio_path: Path) -> list[SpeakerSegment]:
    """Identify speaker segments using pyannote.audio."""
    ...
```

pyannote.audio 4.0 with the community-1 model uses VBx clustering and WeSpeaker embeddings. Requires a HuggingFace token for model download (gated model).

### alignment/align.py

Aligns WhisperX word timestamps to pyannote speaker segments to produce speaker-attributed turns.

Input: WhisperX segments (with word timestamps) and pyannote speaker segments
Output: list of speaker turns with text and timestamps

```python
@dataclass
class Turn:
    speaker: str    # "SPEAKER_00", etc.
    time: float     # seconds (start of turn)
    text: str       # transcript text for this turn

def align(segments: list[Segment], speaker_segments: list[SpeakerSegment]) -> list[Turn]:
    """Align transcribed words to speaker segments.

    For each word, find which speaker segment it falls within (by timestamp overlap).
    Consecutive words from the same speaker are grouped into turns.
    """
    ...
```

The alignment logic:
1. For each WhisperX word, find the overlapping pyannote speaker segment
2. Assign the word to that speaker
3. Group consecutive words from the same speaker into turns
4. The turn's timestamp is the start time of the first word in the turn

## Container config

The media container is heavy - it needs PyTorch, WhisperX, and pyannote.audio. GPU access (NVIDIA Container Toolkit) is strongly recommended for reasonable transcription speed.

```yaml
# formats/media/cm.yaml
names:
  image: anomalica-ingester-media
  workspace: workspace
  user: nonroot

runtime:
  features:
    - gpu  # NVIDIA Container Toolkit for CUDA access
  volumes:
    - ../../staging:/mnt/staging:ro
    - ../../output:/mnt/output:rw
    - ../../shared:/mnt/shared:ro

stages:
  base:
    from: pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime
    steps:
      - apt-get:
          install:
            - ffmpeg
            - git
      - pip:
          install:
            - whisperx
            - pyannote.audio
            - pyyaml

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  ingest:
    command: python workspace/ingest_media.py
    description: Transcribe and diarise audio/video into Anomalica record format
    env:
      PYTHONUNBUFFERED: "1"
      PYTHONPATH: "/mnt/shared"
      HF_TOKEN: "${HF_TOKEN}"
```

The PyTorch base image is ~6 GB. The WhisperX model (Large V3 Turbo, 809M parameters) is ~1.5 GB. The pyannote models add another few hundred MB. Total container image will be around 8-10 GB.

## format.yaml

Already exists at `formats/media/format.yaml`:

```yaml
name: media
handles:
  - audio/mpeg
  - audio/wav
  - audio/ogg
  - video/mp4
  - video/webm
```

## Host script integration

The `ingest` host script needs a new case in its routing block:

```bash
media)
    (cd "${SCRIPT_DIR}/formats/media" && cm run ingest "/mnt/staging/${UUID}" -- $FORCE)
    ;;
```

## Acquire layer: audio/video download

The acquire layer currently fetches URLs via HTTP, Wayback, and Patchright. For YouTube and some other video platforms, it would need yt-dlp support. This could be:

1. A new fetcher in `acquire/workspace/fetch/ytdlp.py` that handles YouTube (and other yt-dlp-supported) URLs
2. Triggered when the URL matches a known video platform pattern, or when HTTP fetch returns a video page rather than a direct media file

The yt-dlp fetcher would download the audio track (not the video - smaller, faster, and the video isn't needed for transcription) and save it to staging.

This is a change to the acquire container, not the media format handler. The acquire container would need yt-dlp added to its dependencies, and the `detect.py` module would need to handle audio MIME types.

For local files (already downloaded audio/video), the existing host script local-file path works as-is.

## Environment variables

- `HF_TOKEN` - HuggingFace token for downloading gated pyannote models. Added to `.env` alongside `ANTHROPIC_API_KEY`.

## Test corpus

From `anomalica/context/test-corpus.md`, the audio/video sources are:

**YouTube (3):**
- Lex Fridman Podcast #122 - David Fravor (2020-09-08) - ~2 hours
- 60 Minutes - Navy pilots recall UAP sighting (2021-05-16) - ~13 minutes
- 7NEWS Spotlight: The UFO Phenomenon (2021-05) - ~45 minutes

**Broadcast/Archive (4):**
- NewsNation: "We Are Not Alone" - Grusch (2023-06-11)
- Jesse Michels: American Alchemy - Grusch (2023-10-09)
- NewsNation: "Confessions of a UFO Hunter" - Elizondo (2024-08-26)
- House UAP hearing coverage (2024-11)

**Spotify (3):**
- Joe Rogan Experience #1361 - Fravor and Corbell (2019-10-05) - ~2 hours
- Joe Rogan Experience #2065 - Grusch (2023-11-22) - ~3 hours
- Joe Rogan Experience #2194 - Elizondo (2024-08-23) - ~3 hours

For Phase 1 development, start with the shortest YouTube video (60 Minutes, ~13 minutes). It has 2-3 speakers, manageable length, and is freely accessible.

Spotify sources cannot be downloaded via yt-dlp. These are deferred until a download strategy is determined.

## What this design does not include

- **Speaker identification** (Phase 2) - matching voices to known profiles
- **Spotify download strategy** - yt-dlp does not support Spotify
- **Video frame extraction** - only the audio track is processed
- **Translation** - WhisperX transcribes in the source language
- **Non-speech audio event detection** - music, applause, etc. (future enhancement)
- **Subtitle/caption extraction** - using existing subtitles instead of transcribing (could be a future optimisation for YouTube videos with good captions)
