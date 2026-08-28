"""Load the Whisper initial_prompt (custom vocabulary) from a reviewable text file.

The prompt biases transcription toward the corpus's names, acronyms and places so
they come out correctly spelled and cased at source - before the post-hoc caser or
the name-fix housekeeping run. Lines starting with '#' in the file are comments (for
the human reviewer) and are stripped; the rest is joined into the prompt.

Whisper's initial_prompt is capped at ~224 tokens, so a file over budget is silently
truncated by the model - the loader warns (it does not truncate itself, so what is
sent stays exactly what a reviewer reads).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DEFAULT_FILE = Path(__file__).with_name("whisper_prompt.txt")
TOKEN_BUDGET = 224


def load_prompt(path: str | Path | None = None) -> str | None:
    """The initial_prompt text (comments stripped), or None if disabled/absent/empty.

    Disabled with INGEST_WHISPER_PROMPT=0; the file is overridable with
    INGEST_WHISPER_PROMPT_FILE."""
    if os.environ.get("INGEST_WHISPER_PROMPT") == "0":
        return None
    chosen = path or os.environ.get("INGEST_WHISPER_PROMPT_FILE") or _DEFAULT_FILE
    file = Path(chosen).expanduser()
    if not file.exists():
        return None
    terms = [
        line.strip()
        for line in file.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    prompt = " ".join(terms).strip()
    if not prompt:
        return None
    # Rough guard: ~1.3 Whisper tokens per whitespace word. Warn, never truncate -
    # trimming here would send the reviewer something different from what they read.
    approx_tokens = int(len(prompt.split()) * 1.3)
    if approx_tokens > TOKEN_BUDGET:
        print(
            f"[WARNING] whisper initial_prompt is ~{approx_tokens} tokens "
            f"(~{TOKEN_BUDGET} budget); Whisper will truncate the tail - trim {file}",
            file=sys.stderr,
        )
    return prompt
