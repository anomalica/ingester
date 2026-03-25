from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from extraction.prompt import build_extraction_prompt


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

    def _call_claude(self, prompt: str, pdf_path: Path) -> tuple[str, dict]:
        full_prompt = (
            f"{prompt}\n\n"
            f"The PDF file to extract is: {pdf_path}\n\n"
            f"IMPORTANT: Read the PDF file directly using the Read tool. "
            f"Do not use Bash, do not use pdftotext or any other tool. "
            f"Just read the file with the Read tool - it handles PDFs natively. "
            f"Return ONLY the markdown output. No commentary, no summary, no preamble."
        )

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
            )
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

        # Strip markdown code fences if Claude wrapped the output
        content = content.strip()
        if content.startswith("```"):
            newline_pos = content.find("\n")
            if newline_pos >= 0:
                content = content[newline_pos + 1 :]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        meta = _extract_metadata(envelope)
        return content, meta

    def extract(self, pdf_path: Path) -> tuple[str, dict]:
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
