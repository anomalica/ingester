test-pdf-extract FILE:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p output/pdf
    cd pdf
    cm run ingest input="$(realpath ../{{FILE}})" output="$(realpath ../output/pdf/)" -- --force

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
