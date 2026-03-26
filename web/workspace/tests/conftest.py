import sys
from pathlib import Path

# Add shared library to path - works on host; in container it is at /mnt/shared
shared = Path(__file__).resolve().parent.parent.parent.parent / "shared"
container_shared = Path("/mnt/shared")
if container_shared.exists():
    sys.path.insert(0, str(container_shared))
else:
    sys.path.insert(0, str(shared))

# Add workspace to path
workspace = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace))
