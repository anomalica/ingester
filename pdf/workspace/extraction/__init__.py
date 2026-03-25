"""Shared extraction utilities."""

from __future__ import annotations


def strip_code_fences(content: str) -> str:
    """Strip markdown code fences if the content is wrapped in them."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content
    newline_pos = stripped.find("\n")
    if newline_pos >= 0:
        stripped = stripped[newline_pos + 1 :]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.strip()
