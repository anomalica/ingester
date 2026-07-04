#!/usr/bin/env python3
"""Audio/video ingester - transcribes and diarises into Anomalica record format."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from alignment.align import align
from diarisation.pyannote_diarise import diarise, DIARISATION_MODEL
from hashing import content_hash_label, hash_file, store_exists, store_path
from models import TimedSentence, Turn, detect_source_type, format_time_precise
from pipeline_version import current_version
from probe import probe
from record import get_version, write_record
from transcript_cache import archive_path, load_raw_archive, save_raw_archive
from transcription.whisperx_transcribe import transcribe, WHISPER_MODEL
from validator import validate
from verification import build_sidecar, needs_sidecar, write_sidecar

import yaml


def _read_existing_source_audio(record_path: Path) -> list[dict]:
    """Read the existing processing.source.audio list from a record file.

    Returns an empty list if the record doesn't exist or has no source.audio.
    """
    if not record_path.exists():
        return []

    content = record_path.read_text()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return []

    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return []

    if not isinstance(frontmatter, dict):
        return []

    processing = frontmatter.get("processing", {})
    if not isinstance(processing, dict):
        return []

    source = processing.get("source", {})
    if not isinstance(source, dict):
        return []

    audio_list = source.get("audio", [])
    if not isinstance(audio_list, list):
        return []

    return audio_list


def _merge_audio_entry(existing: list[dict], new_entry: dict) -> list[dict]:
    """Append the new audio entry only if its sha256 isn't already present."""
    new_sha = new_entry.get("sha256")
    for entry in existing:
        if entry.get("sha256") == new_sha:
            return existing
    return existing + [new_entry]


def _get_tool_versions() -> dict[str, str]:
    """Get installed versions of audio processing tools via importlib.metadata."""
    from importlib.metadata import PackageNotFoundError, version

    versions = {}
    for pkg_name, key in [("whisperx", "whisperx"), ("pyannote.audio", "pyannote")]:
        try:
            versions[key] = version(pkg_name)
        except PackageNotFoundError:
            versions[key] = "unknown"
    return versions


def _extract_known_speakers(
    title: str, description: str | None, publisher: str | None
) -> list[str]:
    """Extract likely speaker names from title and description.

    Returns a list of personal names (first name, optional middle name, last name)
    for the reviewer to work with. No titles, qualifications, or organisation names.
    """
    names = set()

    NOT_NAMES = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "at",
        "to",
        "from",
        "by",
    }

    def _looks_like_name(candidate: str) -> bool:
        words = candidate.split()
        if not (2 <= len(words) <= 3):
            return False
        if not all(w[0].isupper() for w in words):
            return False
        if any(w.lower() in NOT_NAMES for w in words):
            return False
        return True

    # Title often has "Guest Name: Topic | Show" or "Guest Name | Topic"
    for sep in [":", "|"]:
        if sep in title:
            candidate = title.split(sep)[0].strip().strip('"')
            if _looks_like_name(candidate):
                names.add(candidate)
            break

    # First line of description often introduces the guest
    if description:
        first_line = description.strip().split("\n")[0]
        for pattern in [
            " is a ",
            " is an ",
            " reports on ",
            " interviews ",
            " speaks with ",
        ]:
            if pattern in first_line:
                candidate = first_line.split(pattern)[0].strip()
                if _looks_like_name(candidate):
                    names.add(candidate)
                break

    # Publisher might be a person (e.g. "Lex Fridman") not an organisation
    if publisher and _looks_like_name(publisher):
        names.add(publisher)

    return sorted(names)


def _resolve_title(
    manifest: dict, source_url: str | None, source_id: str | None, source_type: str
) -> str:
    """Resolve a record title, never falling back to the asset filename.

    acquire always names the staged asset ``asset.{ext}``, so the old fallback
    to the asset stem produced the literal "asset" whenever yt-dlp metadata was
    missing. Prefer the real title; otherwise use an identifying value (source
    URL, then source id) rather than a meaningless filename.
    """
    return manifest.get("title") or source_url or source_id or f"Untitled {source_type}"


def _build_frontmatter(
    title: str,
    date_published: str,
    source_type: str,
    source_url: str | None,
    source_id: str | None,
    publisher: str | None,
    known_speakers: list[str],
    duration: float,
    hex_hash: str,
    date_accessed: str | None,
    language: str,
    source_audio: list[dict],
    word_timestamps: bool = False,
) -> str:
    """Assemble YAML frontmatter for an audio/video record."""
    escaped_title = title.replace('"', '\\"')
    schema = "anomalica/record/2" if word_timestamps else "anomalica/record/1"
    lines = [
        "---",
        f"schema: {schema}",
        f'title: "{escaped_title}"',
        f"date_published: {date_published}",
        f"source_type: {source_type}",
    ]
    if word_timestamps:
        lines.append("word_timestamps: true")
    if publisher:
        escaped_pub = publisher.replace('"', '\\"')
        lines.append(f'publisher: "{escaped_pub}"')
    if known_speakers:
        lines.append("speakers:")
        for name in known_speakers:
            escaped_name = name.replace('"', '\\"')
            lines.append(f'  - "{escaped_name}"')
    if source_url:
        lines.append(f"source_url: {source_url}")
    if source_id:
        lines.append(f"source_id: {source_id}")
    lines.append(f"duration: {int(duration)}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    if date_accessed:
        lines.append(f"date_accessed: {date_accessed}")
    lines.append(f"date_extracted: {datetime.now(timezone.utc).isoformat()}")
    lines.append("copyright:")
    lines.append("  status: publicly_accessible")
    tool_versions = _get_tool_versions()
    lines.append("processing:")
    lines.append("  handler: audio")
    lines.append(f"  version: {get_version()}")
    lines.append(f"  pipeline_version: {current_version(source_type)}")
    lines.append("  tools:")
    lines.append("    - name: whisperx")
    lines.append(f'      version: "{tool_versions["whisperx"]}"')
    lines.append(f"      model: {WHISPER_MODEL}")
    lines.append("      role: transcription")
    lines.append("      provider: local")
    lines.append("    - name: pyannote")
    lines.append(f'      version: "{tool_versions["pyannote"]}"')
    lines.append(f"      model: {DIARISATION_MODEL}")
    lines.append("      role: diarisation")
    lines.append("      provider: local")
    lines.append(f"  language: {language}")
    lines.append("  source:")
    lines.append("    audio:")
    for entry in source_audio:
        lines.append(f"      - codec: {entry.get('codec', 'unknown')}")
        if entry.get("container") is not None:
            lines.append(f"        container: {entry['container']}")
        if entry.get("bitrate") is not None:
            lines.append(f"        bitrate: {entry['bitrate']}")
        if entry.get("sample_rate") is not None:
            lines.append(f"        sample_rate: {entry['sample_rate']}")
        if entry.get("channels") is not None:
            lines.append(f"        channels: {entry['channels']}")
        lines.append(f"        size_bytes: {entry.get('size_bytes', 0)}")
        lines.append(f"        sha256: {entry.get('sha256', '')}")
        lines.append(f"        fetched_at: {entry.get('fetched_at', '')}")
    lines.append("---")
    return "\n".join(lines)


def _sentence_text_with_word_markers(sentence: TimedSentence) -> str:
    """Render a sentence with an inline ``{{t:SECONDS}}`` marker before each
    word (word-level/v2 output). Falls back to plain text when no per-word
    timings are present."""
    if not sentence.words:
        return sentence.text
    return " ".join(f"{{{{t:{w.start:.2f}}}}}{w.text}" for w in sentence.words)


def _build_content(turns: list[Turn], word_timestamps: bool = False) -> str:
    """Build the record body with speaker turn annotations and sentence-level timestamps.

    Each speaker change is marked with an HTML comment. An empty line
    indicates a paragraph break. For sentence-level (record/1) output each
    sentence starts on its own line prefixed with an HH:MM:SS.D timestamp.
    When ``word_timestamps`` is set, each word is instead prefixed with an
    inline ``{{t:SECONDS}}`` marker (the first of which is the line start),
    and the redundant HH:MM:SS.D line-start prefix is dropped - except for a
    segment the aligner left without word-level timing, which keeps the
    line-start stamp as its only timing. Consumers that only want prose strip
    the markers like any other annotation.
    """
    blocks = []
    for turn in turns:
        lines = [f"<!-- speaker: {turn.speaker} -->"]
        for sentence in turn.sentences:
            if word_timestamps and sentence.words:
                # Per-word {{t:}} markers carry the timing and the first marker
                # is the line start, so the redundant HH:MM:SS.D line-start
                # prefix is omitted for word-level records.
                lines.append(_sentence_text_with_word_markers(sentence))
            else:
                # record/1 sentences - and record/2 segments the aligner left
                # without word-level timing - keep the HH:MM:SS.D line-start
                # stamp as their only timing.
                ts = format_time_precise(sentence.time)
                lines.append(f"{ts} {sentence.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _unique_speakers(turns: list[Turn]) -> dict[str, float]:
    """Extract unique speaker IDs in order of first appearance.

    Returns a dict mapping speaker ID to the timestamp of their first sentence.
    """
    speakers: dict[str, float] = {}
    for turn in turns:
        if turn.speaker not in speakers and turn.sentences:
            speakers[turn.speaker] = turn.sentences[0].time
    return speakers


def run(
    staging_dir: Path,
    output_dir: Path,
    force: bool,
    word_timestamps: bool = False,
    use_cache: bool = True,
) -> int:
    """Run the audio ingestion pipeline. Returns 0 on success, 1 on failure.

    When ``word_timestamps`` is set, per-word timings are retained and emitted
    as inline markers, and the record is written to the parallel ``.v2`` store
    path (schema anomalica/record/2) so it never overwrites an existing v1
    record. This is the opt-in word-level/v2 output.

    When ``use_cache`` is set (default), the transcription + diarisation output
    is cached beside the record (``{hash}.transcript.json``) and reused on a
    later run, so re-rendering into a different format skips the GPU entirely.
    """
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    variant = ".v2" if word_timestamps else ""

    # The raw transcript archive lives beside the source audio (durable, off
    # git), not in the ingests store. /mnt/sources when containerised; falls
    # back to the sibling sources/ dir for non-container runs and tests.
    sources_dir = Path("/mnt/sources")
    if not sources_dir.exists():
        sources_dir = output_dir.parent / "sources"

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    source = manifest["source"]
    asset_name = manifest["asset"]
    detected_type = manifest.get("detected_type", "audio/mpeg")
    original_type = manifest.get("original_type")
    source_id = manifest.get("source_id")

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1

    hex_hash = hash_file(asset_path)

    # In word/v2 mode, dedup against the parallel .v2 record only, so a source
    # already ingested as v1 is still (re)processed into v2 without touching v1.
    already = (
        store_path(store_dir, hex_hash, f"{variant}.md").exists()
        if word_timestamps
        else store_exists(store_dir, hex_hash)
    )
    if not force and already:
        kind = "v2 record" if word_timestamps else "record"
        print(
            f"Skipping: {kind} already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    # Probe the audio file to capture characteristics for the source.audio block
    audio_info = probe(asset_path)
    new_audio_entry = {
        "codec": audio_info["codec"],
        "container": audio_info["container"],
        "bitrate": audio_info["bitrate"],
        "sample_rate": audio_info["sample_rate"],
        "channels": audio_info["channels"],
        "size_bytes": audio_info["size_bytes"],
        "sha256": hex_hash,
        "fetched_at": manifest.get(
            "fetched_at", datetime.now(timezone.utc).isoformat()
        ),
    }

    # Read existing source.audio list and merge (append only if sha256 is new)
    existing_record_path = store_dir / f"{hex_hash}{variant}.md"
    existing_audio_list = _read_existing_source_audio(existing_record_path)
    source_audio_list = _merge_audio_entry(existing_audio_list, new_audio_entry)

    apath = archive_path(sources_dir, hex_hash)
    if use_cache and apath.exists():
        print(
            f"Using raw archive ({apath.name}); skipping transcription/diarisation",
            file=sys.stderr,
        )
        segments, speaker_segments = load_raw_archive(apath)
    else:
        print(f"Transcribing: {asset_path.name}", file=sys.stderr)
        segments, whisperx_raw = transcribe(asset_path)
        if not segments:
            print("Error: transcription produced no segments", file=sys.stderr)
            return 1
        print(f"Diarising: {asset_path.name}", file=sys.stderr)
        speaker_segments, pyannote_raw = diarise(asset_path)
        if use_cache:
            save_raw_archive(
                apath,
                whisperx_raw,
                pyannote_raw,
                meta={
                    "whisper_model": WHISPER_MODEL,
                    "diarisation_model": DIARISATION_MODEL,
                    "hex_hash": hex_hash,
                },
            )
            print(f"Archived raw transcript: {apath}", file=sys.stderr)

    if not segments:
        print("Error: transcription produced no segments", file=sys.stderr)
        return 1

    print("Aligning transcription to speakers", file=sys.stderr)
    turns = align(segments, speaker_segments, keep_words=word_timestamps)

    if not turns:
        print("Error: alignment produced no speaker turns", file=sys.stderr)
        return 1

    # Prefer original source type from fetcher metadata (e.g. yt-dlp reports
    # "video" for YouTube even though only the audio track was downloaded).
    # Falls back to MIME type detection for direct audio/video file inputs.
    if original_type in ("video", "audio"):
        source_type = original_type
    else:
        source_type = detect_source_type(detected_type)
    speakers_unordered = _unique_speakers(turns)

    # Renumber speaker IDs by order of first appearance
    remap = {}
    for i, old_id in enumerate(speakers_unordered):
        remap[old_id] = f"Speaker {i + 1}"
    turns = [Turn(speaker=remap[t.speaker], sentences=t.sentences) for t in turns]

    duration = segments[-1].end
    language = "en"

    is_url = source.startswith("http://") or source.startswith("https://")
    source_url = source if is_url else None
    title = _resolve_title(manifest, source_url, source_id, source_type)
    publisher = manifest.get("publisher")
    description = manifest.get("description")
    known_speakers = _extract_known_speakers(title, description, publisher)
    date_published = manifest.get("date", manifest.get("fetched_at", "")[:10])
    if not date_published:
        date_published = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_accessed = manifest.get("fetched_at")

    frontmatter = _build_frontmatter(
        title=title,
        date_published=date_published,
        source_type=source_type,
        source_url=source_url,
        source_id=source_id,
        publisher=publisher,
        known_speakers=known_speakers,
        duration=duration,
        hex_hash=hex_hash,
        date_accessed=date_accessed,
        language=language,
        source_audio=source_audio_list,
        word_timestamps=word_timestamps,
    )
    body = _build_content(turns, word_timestamps=word_timestamps)
    content = frontmatter + "\n\n" + body

    expected_schema = "anomalica/record/2" if word_timestamps else "anomalica/record/1"
    result = validate(
        content, extra_required=["duration"], expected_schema=expected_schema
    )
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    record_path, link_path = write_record(
        store_dir,
        records_dir,
        hex_hash,
        content,
        date_published,
        source_type,
        title,
        variant=variant,
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)

    # The verification sidecar gates copyright access on the v1 record. The
    # word/v2 record is an experimental parallel artefact, so it does not get
    # its own sidecar (which would otherwise clobber the v1 one).
    if not word_timestamps and needs_sidecar(content):
        sidecar = build_sidecar(
            content, source_path=asset_path, duration_seconds=duration
        )
        sidecar_path = write_sidecar(store_dir, hex_hash, sidecar)
        print(
            f"Verification: {sidecar_path.name} ({len(sidecar.get('challenges', []))} challenges)",
            file=sys.stderr,
        )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe and diarise audio/video into Anomalica record format."
    )
    parser.add_argument("staging_dir", type=Path, help="Path to staging directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if output exists"
    )
    parser.add_argument(
        "--words",
        action="store_true",
        help="Retain per-word timestamps and write the parallel .v2 record "
        "(schema anomalica/record/2); never overwrites the v1 record",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force fresh transcription/diarisation instead of reusing the "
        "cached {hash}.transcript.json (and do not write the cache)",
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = (
            Path(__file__).resolve().parent.parent.parent.parent.parent / "ingests"
        )

    sys.exit(
        run(
            args.staging_dir,
            output_dir,
            args.force,
            word_timestamps=args.words,
            use_cache=not args.no_cache,
        )
    )


if __name__ == "__main__":
    main()
