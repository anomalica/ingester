import sys
from pathlib import Path

# Put the ebook workspace on the path so tests can `from extraction... import`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The handler imports shared modules (dedup, hashing, ...) bare, resolved at
# runtime from PYTHONPATH=/mnt/shared in the container. Mirror that here so a test
# can import ingest_ebook itself, not only the extraction module.
_SHARED = Path("/mnt/shared")
if _SHARED.is_dir():
    sys.path.insert(0, str(_SHARED))
