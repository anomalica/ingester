from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from extraction.models import ExtractionResult
from extraction.prompt import build_extraction_prompt
from extraction.provider import ExtractionProvider


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


class ClaudeCodeProvider(ExtractionProvider):
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def _call_claude(
        self, prompt: str, pdf_path: Path
    ) -> tuple[ExtractionResult, dict]:
        schema = json.dumps(ExtractionResult.model_json_schema())
        full_prompt = f"{prompt}\n\nThe PDF file to extract is: {pdf_path}"

        result = subprocess.run(
            [
                "claude",
                "--print",
                "--model",
                self.model,
                "--json-schema",
                schema,
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
            timeout=300,
        )
        if result.returncode != 0:
            print(f"Claude Code stderr: {result.stderr.strip()}", file=sys.stderr)
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr}"
            )
        if not result.stdout.strip():
            raise RuntimeError("Claude Code returned empty response")

        # --output-format json wraps the response in Claude Code's envelope.
        # The structured output from --json-schema is in "structured_output".
        envelope = json.loads(result.stdout)
        if "structured_output" not in envelope:
            raise RuntimeError(
                f"No structured_output in Claude Code response: {list(envelope.keys())}"
            )

        extraction = ExtractionResult.model_validate(envelope["structured_output"])
        meta = _extract_metadata(envelope)
        return extraction, meta

    def extract(self, pdf_path: Path) -> tuple[ExtractionResult, dict]:
        prompt = build_extraction_prompt()
        return self._call_claude(prompt, pdf_path)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> tuple[ExtractionResult, dict]:
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
