# Refresh .env API secrets (ANTHROPIC_API_KEY, HF_TOKEN) from the Safe so they
# can't silently drift to a wrong account. Run on setup / after Safe changes.
env:
    #!/usr/bin/env bash
    set -euo pipefail
    ./scripts/gen-env.sh

test-acquire:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest acquire/workspace/tests/ -v

test-webpage:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest formats/webpage/workspace/tests/ -v

test-audio:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest formats/audio/workspace/tests/ -v

test-pdf:
    #!/usr/bin/env bash
    set -euo pipefail
    cd formats/pdf
    cm run pytest workspace/tests/ -v

test-shared:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest shared/tests/ -v

test-all:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== shared ==="
    python3 -m pytest shared/tests/ -v
    echo ""
    echo "=== acquire ==="
    python3 -m pytest acquire/workspace/tests/ -v
    echo ""
    echo "=== webpage ==="
    python3 -m pytest formats/webpage/workspace/tests/ -v
    echo ""
    echo "=== audio ==="
    python3 -m pytest formats/audio/workspace/tests/ -v
    echo ""
    echo "=== pdf ==="
    cd formats/pdf && cm run pytest workspace/tests/ -v

download-test-corpus: download-test-corpus-pdf

download-test-corpus-pdf:
    #!/usr/bin/env bash
    set -euo pipefail
    cd test-corpus
    python3 -c "
    import yaml, subprocess, sys
    from pathlib import Path

    with open('sources.yaml') as f:
        sources = yaml.safe_load(f)

    skipped = 0
    downloaded = 0
    manual = 0

    for entry in sources.get('pdf', []):
        path = Path(entry['path'])
        if path.exists():
            print(f'Skipping: {path} (already exists)')
            skipped += 1
            continue
        if entry.get('manual'):
            print(f'Manual:   {path} - {entry.get(\"note\", \"download manually\")}')
            manual += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f'Downloading: {path}')
        subprocess.run(['curl', '-fsSL', '-o', str(path), entry['url']], check=True)
        downloaded += 1

    print(f'Done. {downloaded} downloaded, {skipped} skipped, {manual} manual.')
    "
