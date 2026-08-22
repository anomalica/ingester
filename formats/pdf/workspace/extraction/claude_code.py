from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from shared.validator import strip_code_fences
from extraction.images import is_image, model_image
from extraction.prompt import build_extraction_prompt

_IMAGE_SUFFIX = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


# How long one chunk extraction may take before it is treated as failed. Chunks
# normally finish in 8-16 minutes; this is generous enough not to cut a slow one
# and short enough that a hang costs a chunk rather than the night. Tunable,
# because the thing it guards (chunk size, model, page density) is tunable.
CALL_TIMEOUT_S = float(os.environ.get("PDF_CALL_TIMEOUT_S", "1800"))


def _extract_metadata(envelope: dict) -> dict:
    """Extract useful metadata from the Claude Code response envelope."""
    meta = {}
    if "duration_ms" in envelope:
        meta["duration_ms"] = envelope["duration_ms"]
    if "total_cost_usd" in envelope:
        meta["cost_usd"] = envelope["total_cost_usd"]
    if "modelUsage" in envelope:
        meta["model_usage"] = envelope["modelUsage"]
    if "usage" in envelope:
        usage = envelope["usage"]
        meta["tokens"] = {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
            if k in usage
        }
    if "num_turns" in envelope:
        meta["num_turns"] = envelope["num_turns"]
    return meta


class ClaudeCodeProvider:
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def _call_claude(
        self, prompt: str, pdf_path: Path, is_image_input: bool = False
    ) -> tuple[str, dict]:
        noun = "image" if is_image_input else "PDF"
        full_prompt = (
            f"{prompt}\n\n"
            f"The {noun} file to extract is: {pdf_path}\n\n"
            f"IMPORTANT: Read the {noun} file directly using the Read tool. "
            f"Do not use Bash, do not use pdftotext or any other tool. "
            f"Just read the file with the Read tool - it handles {noun}s natively. "
            f"Return ONLY the markdown output. No commentary, no summary, no preamble."
        )

        # ANTHROPIC_API_KEY must not be visible to Claude Code. Its presence takes
        # precedence over the claude.ai login, which disables the connectors and
        # makes the CLI exit 1 with a warning rather than extracting - intermittent
        # chunk failures on a 416-page document, each one costing a retry. This is
        # the subscription path by definition (the API path does not come through
        # here), so the key is not merely unnecessary, it is actively wrong.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        try:
            result = subprocess.run(
                [
                    "claude",
                    "--print",
                    "--model",
                    self.model,
                    "--allowedTools",
                    "Read",
                    "--no-session-persistence",
                    "--add-dir",
                    str(pdf_path.parent),
                    "--output-format",
                    "json",
                ],
                input=full_prompt,
                capture_output=True,
                text=True,
                env=env,
                # A WALL. Without one this call could hang forever, and one did: a
                # single 20-page chunk sat 30 minutes with no output, no CPU and no
                # way to recover, because nothing above this has a timeout either -
                # the batch script's own timeout only covers the whole document, and
                # once it exits, nothing is watching at all. A hung call has to become
                # a failed chunk so the retry and re-split logic can do their job.
                timeout=CALL_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Claude Code timed out after {CALL_TIMEOUT_S:.0f}s"
            ) from exc
        if result.returncode != 0:
            print(f"Claude Code stderr: {result.stderr.strip()}", file=sys.stderr)
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr}"
            )
        if not result.stdout.strip():
            raise RuntimeError("Claude Code returned empty response")

        envelope = json.loads(result.stdout)
        content = envelope.get("result", "")
        if not content.strip():
            raise RuntimeError("Claude Code returned empty result")

        content = strip_code_fences(content)

        meta = _extract_metadata(envelope)
        return content, meta

    def extract(self, pdf_path: Path) -> tuple[str, dict]:
        if is_image(pdf_path):
            # The Read tool reads common images, but hand it a normalised copy so a
            # future oversized or non-native format still works, and so the model
            # sees the same bounded image every provider does.
            data, media_type = model_image(pdf_path)
            suffix = _IMAGE_SUFFIX.get(media_type, ".png")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(data)
                tmp_path = Path(f.name)
            try:
                prompt = build_extraction_prompt(source_type="image")
                return self._call_claude(prompt, tmp_path, is_image_input=True)
            finally:
                tmp_path.unlink(missing_ok=True)
        prompt = build_extraction_prompt()
        return self._call_claude(prompt, pdf_path)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> tuple[str, dict]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            tmp_path = Path(f.name)
        try:
            prompt = build_extraction_prompt(
                page_offset=page_offset, page_count=page_count
            )
            return self._call_claude(prompt, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
