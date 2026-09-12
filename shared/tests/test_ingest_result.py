import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[2]
PREFIX = "ANOMALICA_INGEST_RESULT "


def _run(*args, cwd: Path, env=None):
    return subprocess.run(
        [*args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )


def _write(path: Path, text: str, executable: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


def _git(repo: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check
    )


def _record(content_hash: str) -> str:
    return (
        "---\n"
        "schema: anomalica/record/1\n"
        'title: "Fixture"\n'
        "date_published: 2026-01-01\n"
        "source_type: pdf\n"
        f"content_hash: sha256:{content_hash}\n"
        "---\n\nFixture body.\n"
    )


def _workspace(tmp_path: Path, asset: bytes, existing: bool = False):
    root = tmp_path / "anomalica"
    ingester = root / "ingester"
    ingests = root / "ingests"
    records = root / "records"
    binary = root / "bin"
    for path in (ingester, ingests, records, binary):
        path.mkdir(parents=True)

    shutil.copy2(PROJECT / "ingest", ingester / "ingest")
    (ingester / "shared").mkdir()
    shutil.copy2(
        PROJECT / "shared/ingest_result.py", ingester / "shared/ingest_result.py"
    )
    (ingester / "acquire/workspace").mkdir(parents=True)
    shutil.copy2(
        PROJECT / "acquire/workspace/detect.py",
        ingester / "acquire/workspace/detect.py",
    )
    shutil.copy2(
        PROJECT / "acquire/workspace/manifest_meta.py",
        ingester / "acquire/workspace/manifest_meta.py",
    )
    _write(ingester / "shared/quality.py", "")
    _write(ingester / "shared/bunny_storage.py", 'print("skipped: test")\n')
    _write(
        ingester / "formats/pdf/format.yaml",
        "name: pdf\nhandles:\n  - application/pdf\n",
    )

    content_hash = hashlib.sha256(asset).hexdigest()
    _write(
        binary / "cm",
        """#!/usr/bin/env python3
import fcntl
import os
import sys
from pathlib import Path

store = Path(os.environ["TEST_INGESTS_DIR"]) / "store"
content_hash = os.environ["TEST_CONTENT_HASH"]
lock_path = Path(os.environ["TEST_INGESTS_DIR"]) / ".git/anomalica-write.lock"
with lock_path.open("a+b") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise SystemExit("ingests writer lock is not held")
variant = ".v2" if os.environ.get("TEST_VARIANT") == "v2" else ""
schema = "anomalica/record/2" if variant else "anomalica/record/1"
path = store / f"{content_hash}{variant}.md"
path.write_text(
    "---\\nschema: " + schema + "\\ntitle: \\\"Fixture\\\"\\n"
    "date_published: 2026-01-01\\nsource_type: pdf\\ncontent_hash: sha256:" + content_hash
    + "\\n---\\n\\nFixture body.\\n"
)
with (Path(os.environ["TEST_INGESTER_DIR"]) / "cm-calls").open("a") as calls:
    calls.write("called\\n")
print(f"Written: {path}", file=sys.stderr)
if os.environ.get("TEST_DELETE_ASSET") == "1":
    for arg in sys.argv:
        if arg.startswith("/mnt/staging/"):
            run_uuid = arg.rsplit("/", 1)[-1]
            staging = Path(os.environ["TEST_INGESTER_DIR"]) / "staging" / run_uuid
            next(staging.glob("asset.*")).unlink()
""",
        executable=True,
    )

    for repo in (ingester, ingests):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "config", "user.email", "test@example.invalid")
        _git(repo, "config", "core.hooksPath", ".git/test-hooks")
        (repo / ".git/test-hooks").mkdir()
    for path in (ingests / "store", ingests / "by-name", ingests / "media"):
        _write(path / ".gitkeep", "")
    if existing:
        _write(ingests / "store" / f"{content_hash}.md", _record(content_hash))
    _git(ingests, "add", ".")
    _git(ingests, "commit", "-qm", "initial")
    _git(ingester, "add", ".")
    _git(ingester, "commit", "-qm", "initial")

    source = root / "fixture.pdf"
    source.write_bytes(asset)
    run_uuid = str(uuid.uuid4())
    result_path = root / "results" / f"{run_uuid}.json"
    env = os.environ.copy()
    env["PATH"] = f"{binary}:{env['PATH']}"
    env["TEST_INGESTS_DIR"] = str(ingests)
    env["TEST_INGESTER_DIR"] = str(ingester)
    env["TEST_CONTENT_HASH"] = content_hash
    return ingester, ingests, source, run_uuid, result_path, env, content_hash


def _invoke(ingester, source, run_uuid, result_path, env, *extra):
    return subprocess.run(
        [
            str(ingester / "ingest"),
            "--run-uuid",
            run_uuid,
            "--result-path",
            str(result_path),
            *extra,
            str(source),
        ],
        cwd=ingester,
        env=env,
        capture_output=True,
        text=True,
    )


def _result(proc, result_path):
    lines = proc.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(PREFIX)
    stdout_bytes = lines[0][len(PREFIX) :].encode()
    assert result_path.read_bytes() == stdout_bytes + b"\n"
    return json.loads(stdout_bytes)


def test_success_emits_result_only_after_record_commit(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nfixture\n")
    ingester, ingests, source, run_uuid, result_path, env, content_hash = setup

    proc = _invoke(ingester, source, run_uuid, result_path, env)

    assert proc.returncode == 0, proc.stderr
    result = _result(proc, result_path)
    assert result == {
        "schema": "anomalica/ingest-result/1",
        "run_uuid": run_uuid,
        "outcome": "committed",
        "record_path": f"store/{content_hash}.md",
        "content_hash": f"sha256:{content_hash}",
        "commit_sha": _git(ingests, "rev-parse", "HEAD").stdout.strip(),
        "completed_at": result["completed_at"],
    }
    committed = _git(
        ingests, "show", f"{result['commit_sha']}:{result['record_path']}"
    ).stdout
    assert f"content_hash: sha256:{content_hash}" in committed


def test_duplicate_emits_verified_no_op_result(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nexisting\n", existing=True)
    ingester, ingests, source, run_uuid, result_path, env, content_hash = setup
    head_before = _git(ingests, "rev-parse", "HEAD").stdout.strip()

    proc = _invoke(ingester, source, run_uuid, result_path, env)

    assert proc.returncode == 0, proc.stderr
    result = _result(proc, result_path)
    assert result["outcome"] == "no-op"
    assert result["record_path"] == f"store/{content_hash}.md"
    assert result["content_hash"] == f"sha256:{content_hash}"
    assert result["commit_sha"] == head_before
    assert _git(ingests, "rev-parse", "HEAD").stdout.strip() == head_before


def test_commit_failure_emits_no_result(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\ncommit failure\n")
    ingester, ingests, source, run_uuid, result_path, env, _ = setup
    _write(
        ingests / ".git/test-hooks/pre-commit",
        "#!/bin/sh\nexit 1\n",
        executable=True,
    )

    proc = _invoke(ingester, source, run_uuid, result_path, env)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert not result_path.exists()


def test_existing_result_path_is_not_replaced(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nexisting result\n", existing=True)
    ingester, _, source, run_uuid, result_path, env, _ = setup
    _write(result_path, "existing bytes\n")

    proc = _invoke(ingester, source, run_uuid, result_path, env)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert result_path.read_text() == "existing bytes\n"


def test_ambiguous_duplicate_emits_no_result(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nambiguous\n", existing=True)
    ingester, ingests, _, run_uuid, result_path, env, content_hash = setup
    source_url = "https://example.test/same-source"
    first = ingests / "store" / f"{content_hash}.md"
    first.write_text(
        _record(content_hash).replace(
            "source_type: pdf\n", f"source_type: pdf\nsource_url: {source_url}\n"
        )
    )
    other_hash = "b" * 64
    _write(
        ingests / "store" / f"{other_hash}.md",
        _record(other_hash).replace(
            "source_type: pdf\n", f"source_type: pdf\nsource_url: {source_url}\n"
        ),
    )
    _git(ingests, "add", ".")
    _git(ingests, "commit", "-qm", "ambiguous duplicate")

    proc = _invoke(ingester, source_url, run_uuid, result_path, env)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert not result_path.exists()


def test_forced_existing_record_update_is_committed(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nforced update\n", existing=True)
    ingester, _, source, run_uuid, result_path, env, _ = setup

    proc = _invoke(ingester, source, run_uuid, result_path, env, "--force")

    assert proc.returncode == 0, proc.stderr
    assert _result(proc, result_path)["outcome"] == "committed"


def test_missing_archived_asset_emits_no_result(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nmissing asset\n")
    ingester, _, source, run_uuid, result_path, env, _ = setup
    env["TEST_DELETE_ASSET"] = "1"

    proc = _invoke(ingester, source, run_uuid, result_path, env)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert not result_path.exists()


def test_unchanged_forced_refresh_emits_no_op(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nunchanged refresh\n", existing=True)
    ingester, ingests, source, run_uuid, result_path, env, content_hash = setup
    record_path = ingests / "store" / f"{content_hash}.md"
    record_path.write_text(
        _record(content_hash).replace(
            f"content_hash: sha256:{content_hash}\n",
            f"content_hash: sha256:{content_hash}\narchived_ext: pdf\n",
        )
    )
    _git(ingests, "add", ".")
    _git(ingests, "commit", "-qm", "archive source extension")
    head_before = _git(ingests, "rev-parse", "HEAD").stdout.strip()

    proc = _invoke(ingester, source, run_uuid, result_path, env, "--force")

    assert proc.returncode == 0, proc.stderr
    result = _result(proc, result_path)
    assert result["outcome"] == "no-op"
    assert result["commit_sha"] == head_before


def test_reprocess_receipt_retry_resolves_committed_v2_without_extraction(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nreprocess retry\n", existing=True)
    ingester, ingests, source, first_uuid, first_path, env, content_hash = setup
    env["TEST_VARIANT"] = "v2"

    first = _invoke(ingester, source, first_uuid, first_path, env, "--force")
    assert first.returncode == 0, first.stderr
    assert _result(first, first_path)["outcome"] == "committed"
    head_after_commit = _git(ingests, "rev-parse", "HEAD").stdout.strip()
    assert (ingester / "cm-calls").read_text().splitlines() == ["called"]

    # Simulate receipt loss after the record commit. Scheduler retries the same
    # reprocess job with a fresh UUID and result path, still using --force.
    first_path.unlink()
    retry_uuid = str(uuid.uuid4())
    retry_path = first_path.with_name(f"{retry_uuid}.json")
    retry = _invoke(ingester, source, retry_uuid, retry_path, env, "--force")

    assert retry.returncode == 0, retry.stderr
    result = _result(retry, retry_path)
    assert result["outcome"] == "no-op"
    assert result["record_path"] == f"store/{content_hash}.v2.md"
    assert result["commit_sha"] == head_after_commit
    assert _git(ingests, "rev-parse", "HEAD").stdout.strip() == head_after_commit
    assert (ingester / "cm-calls").read_text().splitlines() == ["called"]


def test_no_op_verification_waits_for_the_shared_ingests_writer_lock(tmp_path):
    setup = _workspace(tmp_path, b"%PDF-1.4\nlocked no-op\n", existing=True)
    ingester, ingests, source, run_uuid, result_path, env, _ = setup
    command = [
        str(ingester / "ingest"),
        "--run-uuid",
        run_uuid,
        "--result-path",
        str(result_path),
        str(source),
    ]
    lock_path = ingests / ".git/anomalica-write.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        proc = subprocess.Popen(
            command,
            cwd=ingester,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=0.3)
        assert not result_path.exists()
        fcntl.flock(lock, fcntl.LOCK_UN)
    stdout, stderr = proc.communicate(timeout=5)

    assert proc.returncode == 0, stderr
    assert (
        _result(
            subprocess.CompletedProcess(command, proc.returncode, stdout, stderr),
            result_path,
        )["outcome"]
        == "no-op"
    )
