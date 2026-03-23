from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from extraction.models import ExtractionResult
from extraction.prompt import build_extraction_prompt
from extraction.provider import ExtractionProvider


class ClaudeCodeProvider(ExtractionProvider):
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def _call_claude(self, prompt: str, pdf_path: Path) -> ExtractionResult:
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
                full_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr}"
            )

        return ExtractionResult.model_validate_json(result.stdout)

    def extract(self, pdf_data: bytes) -> ExtractionResult:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            pdf_path = Path(f.name)
        try:
            prompt = build_extraction_prompt()
            return self._call_claude(prompt, pdf_path)
        finally:
            pdf_path.unlink(missing_ok=True)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> ExtractionResult:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            pdf_path = Path(f.name)
        try:
            prompt = build_extraction_prompt(
                page_offset=page_offset, page_count=page_count
            )
            return self._call_claude(prompt, pdf_path)
        finally:
            pdf_path.unlink(missing_ok=True)
