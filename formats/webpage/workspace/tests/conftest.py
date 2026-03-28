import sys
from pathlib import Path

workspace = Path(__file__).resolve().parent.parent
shared = workspace.parent.parent.parent / "shared"
container_shared = Path("/mnt/shared")
if container_shared.exists():
    sys.path.insert(0, str(container_shared))
else:
    sys.path.insert(0, str(shared))
sys.path.insert(0, str(workspace))
