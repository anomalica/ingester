ingest URL_OR_PATH *FLAGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ./ingest {{FLAGS}} "{{URL_OR_PATH}}"

ingest-pdf FILE:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p output/store output/records
    cd formats/pdf
    cm run ingest input="$(realpath ../../{{FILE}})" output="$(realpath ../../output/)" -- --force

test-web-extract URL:
    #!/usr/bin/env bash
    set -euo pipefail
    ./ingest --force "{{URL}}"

test-web-corpus:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -c "
    import yaml
    with open('test-corpus/sources.yaml') as f:
        sources = yaml.safe_load(f)
    for entry in sources.get('web', []):
        print(entry['url'])
    " | while read -r url; do
        echo "Extracting: $url"
        ./ingest --force "$url" || echo "FAILED: $url"
    done

test-acquire:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest acquire/workspace/tests/ -v

test-webpage:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest formats/webpage/workspace/tests/ -v

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
