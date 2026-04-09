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
from hashing import content_hash_label, hash_file, store_exists
from models import Turn, detect_source_type, format_time
from record import get_version, write_record
from transcription.whisperx_transcribe import transcribe, WHISPER_MODEL
from validator import validate


def _get_tool_versions() -> dict[str, str]:
    """Get installed versions of audio processing tools."""
    versions = {}
    try:
        import whisperx

        versions["whisperx"] = whisperx.__version__
    except (ImportError, AttributeError):
        versions["whisperx"] = "unknown"
    try:
        import pyannote.audio

        versions["pyannote"] = pyannote.audio.__version__
    except (ImportError, AttributeError):
        versions["pyannote"] = "unknown"
    return versions


def _build_frontmatter(
    title: str,
    date: str,
    source_type: str,
    source_url: str | None,
    duration: float,
    hex_hash: str,
    speakers: dict[str, float],
    language: str,
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
    lines.append(f"duration: {int(duration)}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    lines.append(f"extracted_at: {datetime.now(timezone.utc).isoformat()}")
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
    lines.append("---")
    return "\n".join(lines)


def _build_content(turns: list[Turn]) -> str:
    """Build the record body with speaker turn annotations."""
    blocks = []
    for turn in turns:
        timestamp = format_time(turn.time)
        block = f"---\nspeaker: {turn.speaker}\ntime: {timestamp}\n---\n{turn.text}"
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

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1

    hex_hash = hash_file(asset_path)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

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
        duration=duration,
        hex_hash=hex_hash,
        speakers=speakers,
        language=language,
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
        store_dir, records_dir, hex_hash, content, date, source_type, title
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
        output_dir = Path(__file__).resolve().parent.parent.parent.parent / "output"

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
