# Web Ingester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web article ingester that fetches HTML, extracts article content mechanically via trafilatura, and writes Anomalica record format files to the shared output store.

**Architecture:** Two-layer pipeline (fetch then extract) writing to the shared `output/store/` + `output/records/` structure. A new `shared/` library at the repo root holds format-agnostic code (hashing, validation, record writing) used by both web and PDF ingesters. No AI model needed.

**Tech Stack:** Python 3.12, trafilatura (article extraction), requests (HTTP), pyyaml, pytest. Container-magic for containerisation.

---

## File Structure

### New files

```
shared/
  __init__.py                              # strip_code_fences utility
  hashing.py                               # SHA-256 hashing, idempotency checks
  record.py                                # Slugify, write to store, create symlinks
  validator.py                             # Format-agnostic record validation
  tests/
    conftest.py                            # Path setup
    test_hashing.py
    test_record.py
    test_validator.py

web/
  cm.yaml                                  # Container-magic project config
  workspace/
    ingest_web.py                          # CLI entry point, orchestration
    fetch/
      __init__.py                          # Fetch dispatcher
      http.py                              # Simple HTTP fetcher
      wayback.py                           # Wayback Machine fetcher
    extraction/
      __init__.py
      trafilatura_ext.py                   # trafilatura wrapper
    tests/
      conftest.py                          # Path setup for shared/ + workspace
      test_http_fetch.py
      test_wayback_fetch.py
      test_fetch_dispatcher.py
      test_trafilatura_ext.py
      test_ingest_web.py
```

### Modified files

```
test-corpus/sources.yaml                   # Add web entries
justfile                                   # Add web download + test recipes
```

---

## Task 1: shared/ - hashing module

**Files:**
- Create: `shared/__init__.py`
- Create: `shared/hashing.py`
- Create: `shared/tests/conftest.py`
- Create: `shared/tests/test_hashing.py`

- [ ] **Step 1: Create shared/ directory and test infrastructure**

```bash
mkdir -p shared/tests
```

```python
# shared/__init__.py
"""Shared utilities for Anomalica ingesters."""
```

```python
# shared/tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write failing tests for hashing module**

```python
# shared/tests/test_hashing.py
from hashing import hash_bytes, hash_string, hash_file, content_hash_label, store_path, store_exists


def test_hash_bytes_deterministic():
    assert hash_bytes(b"hello") == hash_bytes(b"hello")


def test_hash_bytes_differs_for_different_input():
    assert hash_bytes(b"hello") != hash_bytes(b"world")


def test_hash_string_uses_utf8():
    assert hash_string("hello") == hash_bytes(b"hello")


def test_hash_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello")
    assert hash_file(f) == hash_bytes(b"hello")


def test_content_hash_label():
    assert content_hash_label("abc123") == "sha256:abc123"


def test_store_path(tmp_path):
    p = store_path(tmp_path, "abc123")
    assert p == tmp_path / "abc123.md"


def test_store_path_custom_suffix(tmp_path):
    p = store_path(tmp_path, "abc123", ".meta.json")
    assert p == tmp_path / "abc123.meta.json"


def test_store_exists_false(tmp_path):
    assert store_exists(tmp_path, "abc123") is False


def test_store_exists_true(tmp_path):
    (tmp_path / "abc123.md").write_text("content")
    assert store_exists(tmp_path, "abc123") is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd shared && python -m pytest tests/test_hashing.py -v`
Expected: ImportError - hashing module not found.

- [ ] **Step 4: Write hashing module**

```python
# shared/hashing.py
"""SHA-256 hashing and output store path utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def hash_string(text: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hash_bytes(text.encode("utf-8"))


def hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash_label(hex_hash: str) -> str:
    """Format a hex hash as a content_hash value: sha256:HEXVALUE."""
    return f"sha256:{hex_hash}"


def store_path(store_dir: Path, hex_hash: str, suffix: str = ".md") -> Path:
    """Path to a file in the output store."""
    return store_dir / f"{hex_hash}{suffix}"


def store_exists(store_dir: Path, hex_hash: str) -> bool:
    """Check whether a record already exists in the store."""
    return store_path(store_dir, hex_hash).exists()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd shared && python -m pytest tests/test_hashing.py -v`
Expected: All 9 tests pass.

- [ ] **Step 6: Commit**

```bash
git add shared/__init__.py shared/hashing.py shared/tests/conftest.py shared/tests/test_hashing.py
git commit -m "feat(shared): add hashing module with SHA-256 utilities"
```

---

## Task 2: shared/ - validator module

Refactored from `pdf/workspace/validator.py`. Keeps all format-agnostic checks. Drops page-specific logic (file_page sequence, page count, truncation detection). Adds `extra_required` parameter for source-type-specific required fields.

**Files:**
- Create: `shared/validator.py`
- Create: `shared/tests/test_validator.py`

- [ ] **Step 1: Write failing tests for shared validator**

```python
# shared/tests/test_validator.py
from validator import validate, ValidationResult


VALID_RECORD = """---
schema: anomalica/record/1
title: Test Document
date: 2023-07-26
source_type: web
source_url: https://example.com
---

Article content here.
"""

VALID_RECORD_CODE_FENCED = """```markdown
---
schema: anomalica/record/1
title: Test Document
date: 2023-07-26
source_type: web
---

Article content here.
```"""

RECORD_WITH_COLON_IN_TITLE = """---
schema: anomalica/record/1
title: Document: A Subtitle
date: 2023-07-26
source_type: web
---

Content here.
"""


def test_valid_record_no_errors():
    result = validate(VALID_RECORD)
    assert result.errors == []


def test_missing_frontmatter():
    result = validate("No frontmatter here")
    assert any("No YAML frontmatter" in e for e in result.errors)


def test_incomplete_frontmatter():
    result = validate("---\ntitle: Test\n")
    assert any("Incomplete" in e or "missing" in e.lower() for e in result.errors)


def test_missing_required_field():
    record = """---
schema: anomalica/record/1
title: Test
date: 2023-07-26
---

Content.
"""
    result = validate(record)
    assert any("source_type" in e for e in result.errors)


def test_wrong_schema_version():
    record = """---
schema: anomalica/record/99
title: Test
date: 2023-07-26
source_type: web
---

Content.
"""
    result = validate(record)
    assert any("schema version" in e.lower() or "Wrong schema" in e for e in result.errors)


def test_code_fence_stripped():
    result = validate(VALID_RECORD_CODE_FENCED)
    assert result.fixed is not None
    assert not result.fixed.strip().startswith("```")


def test_yaml_colon_auto_fix():
    result = validate(RECORD_WITH_COLON_IN_TITLE)
    # Should either parse OK or auto-fix
    assert not any("invalid" in e.lower() for e in result.errors)


def test_html_tags_warned():
    record = """---
schema: anomalica/record/1
title: Test
date: 2023-07-26
source_type: web
---

Text with <sup>1</sup> superscript.
"""
    result = validate(record)
    assert any("HTML" in w for w in result.warnings)


def test_empty_body_warned():
    record = """---
schema: anomalica/record/1
title: Test
date: 2023-07-26
source_type: web
---
"""
    result = validate(record)
    assert any("empty" in w.lower() for w in result.warnings)


def test_extra_required_field_missing():
    record = """---
schema: anomalica/record/1
title: Test
date: 2023-07-26
source_type: web
---

Content.
"""
    result = validate(record, extra_required=["source_url"])
    assert any("source_url" in e for e in result.errors)


def test_extra_required_field_present():
    result = validate(VALID_RECORD, extra_required=["source_url"])
    assert result.errors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd shared && python -m pytest tests/test_validator.py -v`
Expected: ImportError - validator module not found.

- [ ] **Step 3: Write shared validator**

```python
# shared/validator.py
"""Format-agnostic validation for Anomalica record format files.

Checks structural correctness: frontmatter, schema version, YAML syntax,
no HTML tags. Source-type-specific checks (page completeness, required URL)
are handled by callers via the extra_required parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: str | None = None


REQUIRED_FRONTMATTER = ["schema", "title", "date", "source_type"]
CURRENT_SCHEMA = "anomalica/record/1"


def strip_code_fences(content: str) -> str:
    """Strip markdown code fences if the content is wrapped in them."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content
    newline_pos = stripped.find("\n")
    if newline_pos >= 0:
        stripped = stripped[newline_pos + 1 :]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _fix_yaml_quoting(frontmatter: str) -> str:
    """Fix unquoted YAML values that contain colons."""
    lines = frontmatter.split("\n")
    fixed = []
    for line in lines:
        match = re.match(r"^([a-z_]+): (.+)$", line)
        if match:
            key, value = match.group(1), match.group(2)
            if ":" in value and not value.startswith('"') and not value.startswith("'"):
                value = '"' + value.replace('"', '\\"') + '"'
                line = f"{key}: {value}"
        fixed.append(line)
    return "\n".join(fixed)


def validate(
    content: str, extra_required: list[str] | None = None
) -> ValidationResult:
    """Validate a record against the Anomalica record format.

    Args:
        content: The full record file content.
        extra_required: Additional frontmatter fields required beyond the
            base set (schema, title, date, source_type).

    Returns:
        ValidationResult with errors, warnings, and optionally fixed content.
    """
    result = ValidationResult()
    fixed_content = content

    # Check for code fences wrapping the entire output
    stripped = content.strip()
    if stripped.startswith("```"):
        result.errors.append("Content wrapped in code fence - should be stripped")
        fixed_content = strip_code_fences(content)
        result.fixed = fixed_content
        stripped = fixed_content

    # Parse frontmatter
    if not stripped.startswith("---"):
        result.errors.append("No YAML frontmatter found (must start with ---)")
        return result

    parts = stripped.split("---", 2)
    if len(parts) < 3:
        result.errors.append("Incomplete YAML frontmatter (missing closing ---)")
        return result

    # Try to parse frontmatter, auto-fixing unquoted colons if needed
    frontmatter_text = parts[1].strip()
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        fixed_fm = _fix_yaml_quoting(parts[1])
        try:
            frontmatter = yaml.safe_load(fixed_fm)
            parts[1] = fixed_fm
            fixed_content = "---".join(parts)
            result.fixed = fixed_content
            result.warnings.append("Auto-fixed: quoted YAML values containing colons")
        except yaml.YAMLError:
            result.errors.append("Frontmatter YAML is invalid - could not parse")
            return result

    if not isinstance(frontmatter, dict):
        result.errors.append("Frontmatter YAML is not a mapping")
        return result

    # Check required fields
    all_required = REQUIRED_FRONTMATTER + (extra_required or [])
    for field_name in all_required:
        if field_name not in frontmatter:
            result.errors.append(f"Missing required frontmatter field: {field_name}")

    # Check schema version
    if frontmatter.get("schema") and frontmatter["schema"] != CURRENT_SCHEMA:
        result.errors.append(
            f"Wrong schema version: {frontmatter['schema']} (expected {CURRENT_SCHEMA})"
        )

    # Check body content
    body = parts[2].strip()
    if not body:
        result.warnings.append("No content after frontmatter (empty body)")
        return result

    # Check for HTML tags
    html_tags = re.findall(r"<(sup|sub|br|div|span|p|b|i|em|strong)[>\s/]", body)
    if html_tags:
        unique_tags = sorted(set(html_tags))
        result.warnings.append(
            f"HTML tags found (should use markdown instead): "
            f"{', '.join('<' + t + '>' for t in unique_tags)}"
        )

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd shared && python -m pytest tests/test_validator.py -v`
Expected: All 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add shared/validator.py shared/tests/test_validator.py
git commit -m "feat(shared): add format-agnostic record validator"
```

---

## Task 3: shared/ - record writing module

**Files:**
- Create: `shared/record.py`
- Create: `shared/tests/test_record.py`

- [ ] **Step 1: Write failing tests for record module**

```python
# shared/tests/test_record.py
import json

from record import slugify, symlink_name, write_record


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    assert slugify("Glowing Auras and 'Black Money'") == "glowing-auras-and-black-money"


def test_slugify_truncates():
    long_title = "A" * 100
    result = slugify(long_title, max_length=60)
    assert len(result) <= 60


def test_slugify_strips_trailing_hyphens():
    assert not slugify("Hello---World---").endswith("-")


def test_symlink_name():
    name = symlink_name("2023-06-05", "web", "Some Article Title")
    assert name == "2023-06-05-web-some-article-title.md"


def test_write_record_creates_files(tmp_path):
    store = tmp_path / "store"
    records = tmp_path / "records"
    metadata = {"input_url": "https://example.com"}

    record_path, link_path = write_record(
        store_dir=store,
        records_dir=records,
        hex_hash="abc123",
        content="---\ntitle: Test\n---\nBody",
        metadata=metadata,
        date="2023-06-05",
        source_type="web",
        title="Test Article",
    )

    assert record_path.exists()
    assert record_path.read_text() == "---\ntitle: Test\n---\nBody"
    assert (store / "abc123.meta.json").exists()
    assert json.loads((store / "abc123.meta.json").read_text()) == metadata
    assert link_path.is_symlink()
    assert link_path.resolve() == record_path.resolve()
    assert link_path.name == "2023-06-05-web-test-article.md"


def test_write_record_overwrites_existing_symlink(tmp_path):
    store = tmp_path / "store"
    records = tmp_path / "records"
    records.mkdir(parents=True)

    # Create a stale symlink
    stale = records / "2023-06-05-web-test-article.md"
    stale.symlink_to("/nonexistent")

    write_record(store, records, "abc123", "content", {}, "2023-06-05", "web", "Test Article")
    assert stale.resolve() == (store / "abc123.md").resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd shared && python -m pytest tests/test_record.py -v`
Expected: ImportError - record module not found.

- [ ] **Step 3: Write record module**

```python
# shared/record.py
"""Record writing utilities - store files and human-readable symlinks."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text


def symlink_name(date: str, source_type: str, title: str) -> str:
    """Generate the human-readable symlink filename."""
    slug = slugify(title)
    return f"{date}-{source_type}-{slug}.md"


def write_record(
    store_dir: Path,
    records_dir: Path,
    hex_hash: str,
    content: str,
    metadata: dict,
    date: str,
    source_type: str,
    title: str,
) -> tuple[Path, Path]:
    """Write a record to the store and create a symlink in records/.

    Returns:
        Tuple of (record_path, symlink_path).
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    record_path = store_dir / f"{hex_hash}.md"
    meta_path = store_dir / f"{hex_hash}.meta.json"

    record_path.write_text(content)
    meta_path.write_text(json.dumps(metadata, indent=2))

    link_name = symlink_name(date, source_type, title)
    link_path = records_dir / link_name

    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()

    rel_target = os.path.relpath(record_path, records_dir)
    link_path.symlink_to(rel_target)

    return record_path, link_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd shared && python -m pytest tests/test_record.py -v`
Expected: All 6 tests pass.

- [ ] **Step 5: Run all shared/ tests**

Run: `cd shared && python -m pytest tests/ -v`
Expected: All 26 tests pass (hashing + validator + record).

- [ ] **Step 6: Commit**

```bash
git add shared/record.py shared/tests/test_record.py
git commit -m "feat(shared): add record writing module with store and symlinks"
```

---

## Task 4: web/ directory skeleton and container-magic config

**Files:**
- Create: `web/cm.yaml`
- Create: `web/workspace/fetch/__init__.py`
- Create: `web/workspace/extraction/__init__.py`
- Create: `web/workspace/tests/conftest.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p web/workspace/fetch web/workspace/extraction web/workspace/tests
```

- [ ] **Step 2: Create cm.yaml**

```yaml
# web/cm.yaml
names:
  image: anomalica-ingester-web
  workspace: workspace
  user: nonroot

runtime:
  features: []
  volumes:
    - ../output:/mnt/output:rw
    - ../shared:/mnt/shared:ro

stages:
  base:
    from: python:3.12-slim
    steps:
      - pip:
          install:
            - trafilatura
            - requests
            - pyyaml

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  ingest:
    command: python workspace/ingest_web.py
    description: Extract structured content from a web article
    env:
      PYTHONUNBUFFERED: "1"
      PYTHONPATH: "/mnt/shared"
```

- [ ] **Step 3: Create package init files and test conftest**

```python
# web/workspace/fetch/__init__.py
"""Web page fetch layer."""
```

```python
# web/workspace/extraction/__init__.py
"""Web content extraction layer."""
```

```python
# web/workspace/tests/conftest.py
import sys
from pathlib import Path

# Add shared library to path
shared = Path(__file__).resolve().parent.parent.parent.parent / "shared"
sys.path.insert(0, str(shared))

# Add workspace to path
workspace = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(workspace))
```

- [ ] **Step 4: Build the container image**

Run: `cd web && cm build`
Expected: Image builds successfully with trafilatura, requests, pyyaml, pytest installed.

- [ ] **Step 5: Verify shared/ is accessible inside the container**

Run: `cd web && cm run python -c "import sys; sys.path.insert(0, '/mnt/shared'); from hashing import hash_string; print(hash_string('test'))"`
Expected: Prints the SHA-256 hash of "test".

- [ ] **Step 6: Commit**

```bash
git add web/cm.yaml web/workspace/fetch/__init__.py web/workspace/extraction/__init__.py web/workspace/tests/conftest.py
git commit -m "feat(web): add directory skeleton and container-magic config"
```

---

## Task 5: HTTP fetcher

**Files:**
- Create: `web/workspace/fetch/http.py`
- Create: `web/workspace/tests/test_http_fetch.py`

- [ ] **Step 1: Write failing tests**

```python
# web/workspace/tests/test_http_fetch.py
from unittest.mock import patch, Mock

from fetch.http import fetch


@patch("fetch.http.requests.get")
def test_fetch_returns_html_on_success(mock_get):
    mock_response = Mock()
    mock_response.text = "<html><body>Article</body></html>"
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = fetch("https://example.com/article")
    assert result == "<html><body>Article</body></html>"
    mock_get.assert_called_once()


@patch("fetch.http.requests.get")
def test_fetch_sends_browser_user_agent(mock_get):
    mock_response = Mock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    fetch("https://example.com/article")
    call_kwargs = mock_get.call_args
    assert "User-Agent" in call_kwargs.kwargs.get("headers", {}) or \
           "User-Agent" in call_kwargs[1].get("headers", {})


@patch("fetch.http.requests.get")
def test_fetch_returns_none_on_http_error(mock_get):
    import requests as req
    mock_get.side_effect = req.RequestException("Connection refused")

    result = fetch("https://example.com/article")
    assert result is None


@patch("fetch.http.requests.get")
def test_fetch_returns_none_on_non_2xx(mock_get):
    import requests as req
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = req.HTTPError("403 Forbidden")
    mock_get.return_value = mock_response

    result = fetch("https://example.com/article")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && cm run pytest workspace/tests/test_http_fetch.py -v`
Expected: ImportError - fetch.http module has no fetch function.

- [ ] **Step 3: Write HTTP fetcher**

```python
# web/workspace/fetch/http.py
"""Simple HTTP fetcher with browser-like headers."""

from __future__ import annotations

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
TIMEOUT = 30


def fetch(url: str) -> str | None:
    """Fetch a URL via HTTP GET. Returns HTML string or None on failure."""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && cm run pytest workspace/tests/test_http_fetch.py -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/workspace/fetch/http.py web/workspace/tests/test_http_fetch.py
git commit -m "feat(web): add HTTP fetcher"
```

---

## Task 6: Wayback Machine fetcher

**Files:**
- Create: `web/workspace/fetch/wayback.py`
- Create: `web/workspace/tests/test_wayback_fetch.py`

- [ ] **Step 1: Write failing tests**

```python
# web/workspace/tests/test_wayback_fetch.py
from unittest.mock import patch, Mock

from fetch.wayback import fetch


def _mock_availability_response(snapshot_url, status="200"):
    """Build a mock Wayback Machine availability API response."""
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "archived_snapshots": {
            "closest": {
                "url": snapshot_url,
                "status": status,
            }
        }
    }
    return resp


@patch("fetch.wayback.requests.get")
def test_fetch_returns_archived_html(mock_get):
    archive_url = "https://web.archive.org/web/20171216/https://example.com"
    availability_resp = _mock_availability_response(archive_url)
    page_resp = Mock()
    page_resp.text = "<html><body>Archived article</body></html>"
    page_resp.raise_for_status = Mock()

    mock_get.side_effect = [availability_resp, page_resp]

    result = fetch("https://example.com")
    assert result == "<html><body>Archived article</body></html>"
    assert mock_get.call_count == 2


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_when_no_snapshot(mock_get):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"archived_snapshots": {}}
    mock_get.return_value = resp

    result = fetch("https://example.com")
    assert result is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_non_200_snapshot(mock_get):
    resp = _mock_availability_response("https://web.archive.org/...", status="404")
    mock_get.return_value = resp

    result = fetch("https://example.com")
    assert result is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_network_error(mock_get):
    import requests as req
    mock_get.side_effect = req.RequestException("Timeout")

    result = fetch("https://example.com")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && cm run pytest workspace/tests/test_wayback_fetch.py -v`
Expected: ImportError - fetch.wayback has no fetch function.

- [ ] **Step 3: Write Wayback Machine fetcher**

```python
# web/workspace/fetch/wayback.py
"""Wayback Machine fetcher - retrieves archived snapshots of web pages."""

from __future__ import annotations

import requests

AVAILABILITY_API = "https://archive.org/wayback/available"
TIMEOUT = 30


def fetch(url: str) -> str | None:
    """Fetch the closest Wayback Machine snapshot. Returns HTML or None."""
    try:
        resp = requests.get(
            AVAILABILITY_API, params={"url": url}, timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

        snapshots = data.get("archived_snapshots", {})
        closest = snapshots.get("closest")
        if not closest or closest.get("status") != "200":
            return None

        archive_url = closest["url"]
        page = requests.get(archive_url, timeout=TIMEOUT)
        page.raise_for_status()
        return page.text
    except requests.RequestException:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && cm run pytest workspace/tests/test_wayback_fetch.py -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/workspace/fetch/wayback.py web/workspace/tests/test_wayback_fetch.py
git commit -m "feat(web): add Wayback Machine fetcher"
```

---

## Task 7: Fetch layer - export fetcher list

The `fetch/__init__.py` exports the ordered list of fetchers. The orchestrator (ingest_web.py) handles the fetch+extract retry loop, because it needs to try extraction after each fetch to decide whether the HTML is usable.

**Files:**
- Modify: `web/workspace/fetch/__init__.py`

- [ ] **Step 1: Update fetch __init__ to export FETCHERS list**

```python
# web/workspace/fetch/__init__.py
"""Web page fetch layer.

Provides an ordered list of fetchers. Each fetcher has the same interface:
fetch(url: str) -> str | None (returns HTML or None).

The orchestrator iterates FETCHERS in order, trying extraction after each
successful fetch to determine whether the HTML contains usable content.
"""

from fetch import http, wayback

FETCHERS = [
    ("http", http.fetch),
    ("wayback", wayback.fetch),
]
```

- [ ] **Step 2: Commit**

```bash
git add web/workspace/fetch/__init__.py
git commit -m "feat(web): export ordered fetcher list"
```

---

## Task 8: trafilatura extraction wrapper

**Files:**
- Create: `web/workspace/extraction/trafilatura_ext.py`
- Create: `web/workspace/tests/test_trafilatura_ext.py`

- [ ] **Step 1: Write failing tests**

```python
# web/workspace/tests/test_trafilatura_ext.py
from unittest.mock import patch, Mock

from extraction.trafilatura_ext import extract_article, Article


SAMPLE_HTML = """
<html>
<head>
    <meta property="og:title" content="Test Article Title">
    <meta property="article:author" content="Jane Smith">
    <meta property="article:published_time" content="2023-06-05">
    <meta property="og:site_name" content="Test News">
</head>
<body>
<article>
<h1>Test Article Title</h1>
<p>This is the first paragraph of the article with enough text to be extracted.</p>
<p>This is the second paragraph with more content for extraction.</p>
</article>
</body>
</html>
"""


def test_extract_article_returns_article():
    result = extract_article(SAMPLE_HTML, "https://example.com")
    assert result is not None
    assert isinstance(result, Article)
    assert len(result.text) > 0


def test_extract_article_returns_none_for_empty_html():
    result = extract_article("", "https://example.com")
    assert result is None


def test_extract_article_returns_none_for_non_article():
    result = extract_article("<html><body><nav>Menu</nav></body></html>", "https://example.com")
    assert result is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_maps_metadata(mock_extract):
    doc = Mock()
    doc.text = "Article body text"
    doc.title = "Test Title"
    doc.author = "Alice; Bob"
    doc.date = "2023-06-05"
    doc.sitename = "Test Site"
    doc.description = "A test article"
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result.title == "Test Title"
    assert result.authors == ["Alice", "Bob"]
    assert result.date == "2023-06-05"
    assert result.sitename == "Test Site"


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_handles_none_author(mock_extract):
    doc = Mock()
    doc.text = "Article body text"
    doc.title = "Test"
    doc.author = None
    doc.date = "2023-01-01"
    doc.sitename = None
    doc.description = None
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result.authors is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_returns_none_when_no_text(mock_extract):
    doc = Mock()
    doc.text = ""
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_returns_none_when_bare_extraction_returns_none(mock_extract):
    mock_extract.return_value = None

    result = extract_article("<html></html>", "https://example.com")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && cm run pytest workspace/tests/test_trafilatura_ext.py -v`
Expected: ImportError - trafilatura_ext not found.

- [ ] **Step 3: Write trafilatura extraction wrapper**

```python
# web/workspace/extraction/trafilatura_ext.py
"""Article extraction via trafilatura."""

from __future__ import annotations

from dataclasses import dataclass

from trafilatura import bare_extraction


@dataclass
class Article:
    text: str
    title: str | None
    authors: list[str] | None
    date: str | None
    sitename: str | None
    description: str | None


def extract_article(html: str, url: str | None = None) -> Article | None:
    """Extract article content and metadata from HTML.

    Args:
        html: The HTML string to extract from.
        url: Original URL (used by trafilatura for metadata context, not fetched).

    Returns:
        Article with text and metadata, or None if extraction fails.
    """
    doc = bare_extraction(
        html,
        url=url,
        with_metadata=True,
        include_formatting=True,
        include_links=True,
        include_tables=True,
        include_images=True,
        favor_precision=True,
    )
    if doc is None or not doc.text:
        return None

    authors = None
    if doc.author:
        authors = [a.strip() for a in doc.author.split(";")]

    return Article(
        text=doc.text,
        title=doc.title,
        authors=authors,
        date=doc.date,
        sitename=doc.sitename,
        description=doc.description,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && cm run pytest workspace/tests/test_trafilatura_ext.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/workspace/extraction/trafilatura_ext.py web/workspace/tests/test_trafilatura_ext.py
git commit -m "feat(web): add trafilatura extraction wrapper"
```

---

## Task 9: CLI orchestration - ingest_web.py

**Files:**
- Create: `web/workspace/ingest_web.py`
- Create: `web/workspace/tests/test_ingest_web.py`

- [ ] **Step 1: Write failing tests**

```python
# web/workspace/tests/test_ingest_web.py
import json
from pathlib import Path
from unittest.mock import patch, Mock

from extraction.trafilatura_ext import Article

import ingest_web


SAMPLE_ARTICLE = Article(
    text="# Test Article\n\nFirst paragraph of article content.\n\nSecond paragraph.",
    title="Test Article",
    authors=["Jane Smith"],
    date="2023-06-05",
    sitename="Example News",
    description="A test article",
)


def _patch_fetchers(http_html=None, wayback_html=None):
    """Patch the FETCHERS list with controlled return values."""
    def _make_fetcher(html):
        def fetcher(url):
            return html
        return fetcher

    return patch(
        "ingest_web.FETCHERS",
        [
            ("http", _make_fetcher(http_html)),
            ("wayback", _make_fetcher(wayback_html)),
        ],
    )


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_record_to_store(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    md_files = list((tmp_path / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "schema: anomalica/record/1" in content
    assert "source_type: web" in content
    assert "source_url: https://example.com/article" in content
    assert "Test Article" in content


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_metadata(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    meta_files = list((tmp_path / "store").glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert meta["input_url"] == "https://example.com/article"
    assert meta["fetch_method"] == "http"
    assert "duration_ms" in meta
    assert "trafilatura_metadata" in meta


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_creates_symlink(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    links = list((tmp_path / "records").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "2023-06-05-web-test-article" in links[0].name


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_skips_when_exists(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(url="https://example.com/article", output_dir=tmp_path, force=False)
        ingest_web.run(url="https://example.com/article", output_dir=tmp_path, force=False)

    md_files = list((tmp_path / "store").glob("*.md"))
    assert len(md_files) == 1


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_re_extracts_with_force(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(url="https://example.com/article", output_dir=tmp_path, force=False)
        ingest_web.run(url="https://example.com/article", output_dir=tmp_path, force=True)

    assert mock_extract.call_count == 2


def test_ingest_exits_when_all_fetchers_fail(tmp_path):
    with _patch_fetchers(http_html=None, wayback_html=None):
        exit_code = ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )
    assert exit_code != 0


@patch("ingest_web.extract_article", return_value=None)
def test_ingest_exits_when_extraction_fails_all_fetchers(mock_extract, tmp_path):
    """HTTP returns HTML but extraction fails, then wayback also returns HTML but extraction fails."""
    with _patch_fetchers(http_html="<html>paywall</html>", wayback_html="<html>also bad</html>"):
        exit_code = ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )
    assert exit_code != 0
    # Both fetchers were tried
    assert mock_extract.call_count == 2


def test_ingest_falls_back_to_wayback_when_http_extraction_fails(tmp_path):
    """HTTP returns HTML but extraction fails. Wayback returns HTML and extraction succeeds."""
    call_count = {"n": 0}

    def _extract_side_effect(html, url):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # HTTP HTML is a paywall
        return SAMPLE_ARTICLE  # Wayback HTML works

    with _patch_fetchers(http_html="<html>paywall</html>", wayback_html="<html>article</html>"):
        with patch("ingest_web.extract_article", side_effect=_extract_side_effect):
            exit_code = ingest_web.run(
                url="https://example.com/article",
                output_dir=tmp_path,
                force=False,
            )

    assert exit_code == 0
    meta_files = list((tmp_path / "store").glob("*.meta.json"))
    meta = json.loads(meta_files[0].read_text())
    assert meta["fetch_method"] == "wayback"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && cm run pytest workspace/tests/test_ingest_web.py -v`
Expected: ImportError or AttributeError - ingest_web.run not found.

- [ ] **Step 3: Write ingest_web.py**

```python
#!/usr/bin/env python3
"""Web article ingester - extracts structured content into Anomalica record format."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hashing import hash_string, content_hash_label, store_exists
from record import write_record
from validator import validate

from fetch import FETCHERS
from extraction.trafilatura_ext import extract_article


def _build_frontmatter(
    title: str, date: str, url: str, authors: list[str] | None, hex_hash: str
) -> str:
    """Assemble YAML frontmatter for a web record."""
    lines = [
        "---",
        "schema: anomalica/record/1",
    ]
    if ":" in title:
        lines.append(f'title: "{title}"')
    else:
        lines.append(f"title: {title}")
    lines.extend(
        [
            f"date: {date}",
            "source_type: web",
            f"source_url: {url}",
        ]
    )
    if authors:
        lines.append("authors:")
        for author in authors:
            lines.append(f"  - {author}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    lines.append("---")
    return "\n".join(lines)


def run(url: str, output_dir: Path, force: bool) -> int:
    """Run the web ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    start_time = time.monotonic()

    # Try each fetcher in order. After each successful fetch, attempt
    # extraction. If extraction fails (paywall, cookie wall), try the
    # next fetcher.
    article = None
    fetch_method = None

    for method_name, fetcher in FETCHERS:
        print(f"Trying {method_name} fetch...", file=sys.stderr)
        html = fetcher(url)
        if html is None:
            print(f"  {method_name}: no response", file=sys.stderr)
            continue
        print(f"  {method_name}: got HTML, extracting...", file=sys.stderr)
        article = extract_article(html, url)
        if article is None:
            print(f"  {method_name}: no article content extracted", file=sys.stderr)
            continue
        fetch_method = method_name
        print(f"  {method_name}: success", file=sys.stderr)
        break

    if article is None:
        print(
            "All fetch methods exhausted - no content extracted", file=sys.stderr
        )
        return 1

    print(f"Extracted via {fetch_method}: {article.title}", file=sys.stderr)

    # Hash and check idempotency
    hex_hash = hash_string(article.text)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    # Build record content
    date = article.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = article.title or "Untitled"
    frontmatter = _build_frontmatter(title, date, url, article.authors, hex_hash)
    content = frontmatter + "\n\n" + article.text + "\n"

    # Validate
    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    # Write output
    duration_ms = int((time.monotonic() - start_time) * 1000)
    metadata = {
        "input_url": url,
        "input_hash": content_hash_label(hex_hash),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": fetch_method,
        "duration_ms": duration_ms,
        "trafilatura_metadata": {
            "title": article.title,
            "authors": article.authors,
            "date": article.date,
            "sitename": article.sitename,
            "description": article.description,
        },
    }

    record_path, link_path = write_record(
        store_dir, records_dir, hex_hash, content, metadata, date, "web", title
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from a web article into Anomalica record format."
    )
    parser.add_argument("url", help="URL of the web article to extract")
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = Path("output")

    sys.exit(run(args.url, output_dir, args.force))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && cm run pytest workspace/tests/test_ingest_web.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Run all web tests**

Run: `cd web && cm run pytest workspace/tests/ -v`
Expected: All tests pass (http + wayback + dispatcher + trafilatura + ingest).

- [ ] **Step 6: Commit**

```bash
git add web/workspace/ingest_web.py web/workspace/tests/test_ingest_web.py
git commit -m "feat(web): add CLI orchestration and record writing"
```

---

## Task 10: Test corpus and justfile integration

**Files:**
- Modify: `test-corpus/sources.yaml`
- Modify: `justfile`

- [ ] **Step 1: Add web entries to sources.yaml**

Add the following `web:` section to `test-corpus/sources.yaml`:

```yaml
web:
  - url: https://www.nytimes.com/2017/12/16/us/politics/pentagon-program-ufo-harry-reid.html
    title: "Glowing Auras and 'Black Money': The Pentagon's Mysterious U.F.O. Program"
    date: 2017-12-16
    note: paywalled - will need Wayback Machine

  - url: https://theintercept.com/2019/06/01/ufo-unidentified-history-channel-luis-elizondo-pentagon/
    title: The Media Loves the UFO Expert Who Says He Worked for an Obscure Pentagon Program. Did He?
    date: 2019-06-01

  - url: https://www.theblackvault.com/documentarchive/pentagon-reinforces-mr-luis-elizondo-had-no-responsibilities-on-aatip-senator-harry-reids-2009-memo-changes-nothing/
    title: Pentagon Reinforces Mr. Luis Elizondo Had No Responsibilities on AATIP
    date: 2019-01-01

  - url: https://thehill.com/opinion/4038159-stunning-ufo-crash-retrieval-allegations-deemed-credible-urgent/
    title: Stunning UFO Crash Retrieval Allegations Deemed Credible, Urgent
    date: 2022-07-01

  - url: https://thedebrief.org/intelligence-officials-say-u-s-has-retrieved-non-human-craft/
    title: Intelligence Officials Say U.S. Has Retrieved Craft of Non-Human Origin
    date: 2023-06-05

  - url: https://burlison.house.gov/media/press-releases/rep-burlison-welcomes-former-us-air-force-officer-david-grusch-special-advisor
    title: Rep. Burlison Welcomes David Grusch as Special Advisor
    date: 2025-03-01
```

- [ ] **Step 2: Add web recipes to justfile**

Append to `justfile`:

```just
test-web-extract URL:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p output
    cd web
    cm run ingest "{{URL}}" -- --force

test-web-corpus:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p output
    cd web
    python3 -c "
    import yaml
    with open('../test-corpus/sources.yaml') as f:
        sources = yaml.safe_load(f)
    for entry in sources.get('web', []):
        print(entry['url'])
    " | while read -r url; do
        echo "Extracting: $url"
        cm run ingest "$url" || echo "FAILED: $url"
    done
```

- [ ] **Step 3: Commit**

```bash
git add test-corpus/sources.yaml justfile
git commit -m "feat(web): add test corpus entries and justfile recipes"
```

- [ ] **Step 4: Run a single integration test**

Pick a straightforward article (The Debrief, no paywall):

Run: `just test-web-extract https://thedebrief.org/intelligence-officials-say-u-s-has-retrieved-non-human-craft/`

Expected: Record written to `output/store/{hash}.md` with valid frontmatter and extracted article text. Symlink created in `output/records/`.

- [ ] **Step 5: Verify the output record**

Check the generated record:

```bash
# Check frontmatter
head -15 output/store/*.md

# Check symlink
ls -la output/records/

# Check metadata
cat output/store/*.meta.json | python3 -m json.tool
```

Expected: Frontmatter has schema, title, date, source_type: web, source_url, authors, content_hash. Symlink exists and points to the store file. Metadata has fetch_method, trafilatura_metadata.

- [ ] **Step 6: Run the full test corpus**

Run: `just test-web-corpus`

Expected: Most articles extract successfully. The NYT article may need Wayback Machine fallback. Note any failures for investigation.

- [ ] **Step 7: Commit any fixes from integration testing**

If integration testing revealed issues, fix them, run unit tests to confirm nothing regressed, then commit:

```bash
cd web && cm run pytest workspace/tests/ -v
cd shared && python -m pytest tests/ -v
git add -u
git commit -m "fix(web): adjustments from integration testing"
```
