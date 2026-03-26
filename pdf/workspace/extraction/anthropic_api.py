"""Extraction provider using the Anthropic API directly.

Sends PDFs as document attachments in a single API call.
No tool use, no multi-turn conversation. Uses streaming to avoid SDK timeout.
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from extraction import strip_code_fences
from extraction.prompt import build_extraction_prompt


class ContentFilteredError(RuntimeError):
    """Raised when the API blocks output due to content filtering."""


class AnthropicProvider:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self.client = anthropic.Anthropic()

    def _call_api(self, prompt: str, pdf_data: bytes) -> tuple[str, dict]:
        encoded = base64.standard_b64encode(pdf_data).decode("utf-8")

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=64000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": "application/pdf",
                                    "data": encoded,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            ) as stream:
                message = stream.get_final_message()
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            if "content filtering" in str(e).lower():
                raise ContentFilteredError(str(e)) from e
            raise RuntimeError(str(e)) from e

        content = message.content[0].text

        content = strip_code_fences(content)

        meta = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "stop_reason": message.stop_reason,
        }
        if hasattr(message.usage, "cache_creation_input_tokens"):
            meta["cache_creation_input_tokens"] = (
                message.usage.cache_creation_input_tokens
            )
        if hasattr(message.usage, "cache_read_input_tokens"):
            meta["cache_read_input_tokens"] = message.usage.cache_read_input_tokens

        # If output was truncated, raise so retry logic can handle it
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Output truncated at {message.usage.output_tokens} tokens"
            )

        return content, meta

    def extract(self, pdf_path: Path) -> tuple[str, dict]:
        prompt = build_extraction_prompt()
        pdf_data = pdf_path.read_bytes()
        return self._call_api(prompt, pdf_data)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> tuple[str, dict]:
        prompt = build_extraction_prompt(page_offset=page_offset, page_count=page_count)
        return self._call_api(prompt, pdf_data)
