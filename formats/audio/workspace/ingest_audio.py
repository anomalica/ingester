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
from hashing import content_hash_label, hash_file, source_id_to_filename
from models import Turn, detect_source_type, format_time
from probe import probe
from record import get_version, write_record
from transcription.whisperx_transcribe import transcribe, WHISPER_MODEL
from validator import validate

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


def _build_frontmatter(
    title: str,
    date: str,
    source_type: str,
    source_url: str | None,
    source_id: str | None,
    duration: float,
    hex_hash: str,
    speakers: dict[str, float],
    language: str,
    source_audio: list[dict],
) -> str:
    """Assemble YAML frontmatter for an audio/video record."""
    escaped_title = title.replace('"', '\\"')
    lines = [
        "---",
        "schema: anomalica/record/1",
        f'title: "{escaped_title}"',
        f"date: {date}",
        f"source_type: {source_type}",
    ]
    if source_url:
        lines.append(f"source_url: {source_url}")
    if source_id:
        lines.append(f"source_id: {source_id}")
    lines.append(f"duration: {int(duration)}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    lines.append(f"extracted_at: {datetime.now(timezone.utc).isoformat()}")
    lines.append("copyright:")
    lines.append("  status: publicly_accessible")
    lines.append("speakers:")
    for speaker_id, first_time in speakers.items():
        lines.append(f"  - id: {speaker_id}")
        lines.append("    name: Unknown")
        lines.append(f"    first_appearance: {format_time(first_time)}")
        lines.append("    relevant: true")
    tool_versions = _get_tool_versions()
    lines.append("processing:")
    lines.append("  handler: audio")
    lines.append(f"  version: {get_version()}")
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


def _build_content(turns: list[Turn]) -> str:
    """Build the record body with speaker turn annotations."""
    blocks = []
    for turn in turns:
        timestamp = format_time(turn.time)
        block = f"<!-- anomalica\nspeaker: {turn.speaker}\ntime: {timestamp}\n-->\n{turn.text}"
        blocks.append(block)
    return "\n\n".join(blocks) + "\n"


def _unique_speakers(turns: list[Turn]) -> dict[str, float]:
    """Extract unique speaker IDs in order of first appearance.

    Returns a dict mapping speaker ID to the timestamp of their first turn.
    """
    speakers: dict[str, float] = {}
    for turn in turns:
        if turn.speaker not in speakers:
            speakers[turn.speaker] = turn.time
    return speakers


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    """Run the audio ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"

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

    # For URL-based sources, use source_id as the store key (stable across
    # re-encodings). For local files (no source_id from acquire), fall back
    # to the file content hash.
    store_key = source_id_to_filename(source_id) if source_id else hex_hash

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

    # Read existing source.audio list (if record exists) and merge.
    # Skip processing entirely if this exact audio (same sha256) is already
    # represented in the existing record.
    existing_record_path = store_dir / f"{store_key}.md"
    existing_audio_list = _read_existing_source_audio(existing_record_path)
    sha_already_present = any(
        entry.get("sha256") == hex_hash for entry in existing_audio_list
    )

    if sha_already_present and not force:
        print(
            f"Skipping: this audio version is already in the record ({store_key})",
            file=sys.stderr,
        )
        return 0

    source_audio_list = _merge_audio_entry(existing_audio_list, new_audio_entry)

    print(f"Transcribing: {asset_path.name}", file=sys.stderr)
    segments = transcribe(asset_path)

    if not segments:
        print("Error: transcription produced no segments", file=sys.stderr)
        return 1

    print(f"Diarising: {asset_path.name}", file=sys.stderr)
    speaker_segments = diarise(asset_path)

    print("Aligning transcription to speakers", file=sys.stderr)
    turns = align(segments, speaker_segments)

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
    turns = [Turn(speaker=remap[t.speaker], time=t.time, text=t.text) for t in turns]
    speakers = {remap[k]: v for k, v in speakers_unordered.items()}

    duration = segments[-1].end
    language = "en"

    is_url = source.startswith("http://") or source.startswith("https://")
    source_url = source if is_url else None
    title = manifest.get("title", Path(asset_name).stem)
    date = manifest.get("date", manifest.get("fetched_at", "")[:10])
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    frontmatter = _build_frontmatter(
        title=title,
        date=date,
        source_type=source_type,
        source_url=source_url,
        source_id=source_id,
        duration=duration,
        hex_hash=hex_hash,
        speakers=speakers,
        language=language,
        source_audio=source_audio_list,
    )
    body = _build_content(turns)
    content = frontmatter + "\n\n" + body

    result = validate(content, extra_required=["duration", "speakers"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    record_path, link_path = write_record(
        store_dir, records_dir, store_key, content, date, source_type, title
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe and diarise audio/video into Anomalica record format."
    )
    parser.add_argument("staging_dir", type=Path, help="Path to staging directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = (
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "anomalica-ingests"
        )

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
