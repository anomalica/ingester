import sys
from pathlib import Path

# Put the ebook workspace on the path so tests can `from extraction... import`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
