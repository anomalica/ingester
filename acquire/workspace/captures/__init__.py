"""Page-capture utilities for the patchright fetcher.

Each function takes an already-loaded patchright Page and produces a
self-contained snapshot artefact (PDF, HTML). Adblock and modal-strip
CSS injection happens before any snapshot is captured so all artefacts
reflect the same cleaned DOM state.
"""

from captures.adblock import apply_cosmetic_filters
from captures.pdf import capture_pdf

__all__ = ["apply_cosmetic_filters", "capture_pdf"]
