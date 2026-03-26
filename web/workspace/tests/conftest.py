import sys
from pathlib import Path

# Add shared library to path
shared = Path(__file__).resolve().parent.parent.parent.parent / "shared"
sys.path.insert(0, str(shared))

# Add workspace to path
workspace = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace))
