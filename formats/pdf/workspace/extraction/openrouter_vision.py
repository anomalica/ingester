"""Extraction provider using an OpenRouter vision model (default gpt-5.6-luna).

OpenRouter (and the OpenAI models behind it) will not accept a native PDF the way
the Anthropic API does, so each page is rendered to an image and the pages are
sent together in one chat call.

Transport goes through the shared gateway (anomalica_common.llm.call_with_pages):
the same usage ledger and the same metered-spend backstop as the digester and
assimilator, so the ingester is no longer a fourth, separately-implemented path to
a paid model. Only the page RENDERING lives here, because pymupdf is an ingester
dependency, not a gateway one.

Metered (real money). This provider does NOT authorise spend - the caller
(ingest_pdf.py) must clear the pre-flight gate (spend_confirmed: a printed
estimate plus authorisation) BEFORE constructing it. Authorising here would be
wrong twice over: the flag is process-wide (it would unlock the Anthropic paths
too), and a constructor is not where a spend is approved.

The provider contract matches ClaudeCodeProvider / AnthropicProvider:
`extract(pdf_path)` and `extract_chunk(pdf_data, page_offset, page_count)` each
return `(markdown, meta)`.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pymupdf

from anomalica_common.llm import call_with_pages

from shared.validator import strip_code_fences
from extraction.images import data_uri, is_image
from extraction.prompt import build_extraction_prompt

# Government scans carry small print; render high enough to keep it legible. The
# research put the floor at ~200 DPI, one page per image, under the 272K image-token
# boundary - a normal page at 180-200 DPI is well inside it.
RENDER_DPI = int(os.environ.get("INGEST_RENDER_DPI", "190"))


class OpenRouterVisionProvider:
    def __init__(self, model: str = "openai/gpt-5.6-luna"):
        self.model = model
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError(
                "OPENROUTER_API_KEY not set - export it from the Safe before a "
                "metered OpenRouter run."
            )

    def _render(self, pdf_bytes: bytes) -> list[str]:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        urls = []
        for page in doc:
            png = page.get_pixmap(dpi=RENDER_DPI).tobytes("png")
            urls.append("data:image/png;base64," + base64.b64encode(png).decode())
        return urls

    def _call(self, prompt: str, image_urls: list[str]) -> tuple[str, dict]:
        text, meta = call_with_pages(prompt, "", self.model, image_urls)
        return strip_code_fences(text), meta

    def extract(self, pdf_path: Path) -> tuple[str, dict]:
        if is_image(pdf_path):
            prompt = build_extraction_prompt(source_type="image")
            return self._call(prompt, [data_uri(pdf_path)])
        urls = self._render(pdf_path.read_bytes())
        return self._call(build_extraction_prompt(), urls)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> tuple[str, dict]:
        urls = self._render(pdf_data)
        prompt = build_extraction_prompt(page_offset=page_offset, page_count=page_count)
        return self._call(prompt, urls)
