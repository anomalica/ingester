"""Extraction provider using an OpenRouter vision model (default gpt-5.6-luna).

OpenRouter (and the OpenAI models behind it) will not accept a native PDF the way
the Anthropic API does, so each page is rendered to an image and the pages are
sent together in one chat call. Metered (real money) - the caller must clear the
pre-flight cost gate in ingest_pdf.py before constructing this provider.

The provider contract matches ClaudeCodeProvider / AnthropicProvider:
`extract(pdf_path)` and `extract_chunk(pdf_data, page_offset, page_count)` each
return `(markdown, meta)`.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pymupdf
import requests

from shared.validator import strip_code_fences
from extraction.prompt import build_extraction_prompt

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Government scans carry small print; render high enough to keep it legible. The
# research put the floor at ~200 DPI, one page per image, under the 272K image-token
# boundary - a normal page at 180-200 DPI is well inside it.
RENDER_DPI = int(os.environ.get("INGEST_RENDER_DPI", "190"))
CALL_TIMEOUT_S = float(os.environ.get("PDF_CALL_TIMEOUT_S", "600"))


class OpenRouterVisionProvider:
    def __init__(self, model: str = "openai/gpt-5.6-luna"):
        self.model = model
        self.key = os.environ.get("OPENROUTER_API_KEY")
        if not self.key:
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
        content = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
        body = {
            "model": self.model,
            "temperature": 0,
            # Reasoning tokens are drawn from this cap BEFORE any visible text, so a
            # low cap yields a blank body you still pay for. Kept generous, and
            # reasoning effort low because transcription is perception-bound not
            # reasoning-bound (both per the porting research).
            "max_tokens": 32000,
            "reasoning": {"effort": "low"},
            "usage": {"include": True},  # OpenRouter returns the actual $ cost
            "messages": [{"role": "user", "content": content}],
        }
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {self.key}",
                "HTTP-Referer": "https://anomalica.is",
                "X-Title": "Anomalica ingester",
            },
            json=body,
            timeout=CALL_TIMEOUT_S,
        )
        if r.status_code != 200:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:400]}")
        j = r.json()
        if "choices" not in j:
            raise RuntimeError(
                f"OpenRouter response had no choices: {json.dumps(j)[:400]}"
            )
        choice = j["choices"][0]
        text = (choice.get("message", {}).get("content") or "").strip()
        if choice.get("finish_reason") == "length":
            raise RuntimeError("OpenRouter output truncated (hit max_tokens)")
        if not text:
            raise RuntimeError("OpenRouter returned empty content")
        usage = j.get("usage", {})
        meta = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cost_usd": usage.get("cost"),
            "model": self.model,
            "provider": j.get("provider"),
        }
        return strip_code_fences(text), meta

    def extract(self, pdf_path: Path) -> tuple[str, dict]:
        urls = self._render(pdf_path.read_bytes())
        return self._call(build_extraction_prompt(), urls)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> tuple[str, dict]:
        urls = self._render(pdf_data)
        prompt = build_extraction_prompt(page_offset=page_offset, page_count=page_count)
        return self._call(prompt, urls)
