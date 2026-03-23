import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_missing_input_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_cli_nonexistent_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", "/nonexistent.pdf"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_cli_skips_existing_output(tmp_path):
    output_file = tmp_path / "simple.json"
    output_file.write_text("{}")
    result = subprocess.run(
        [
            "python",
            "workspace/ingest_pdf.py",
            str(FIXTURES / "simple.pdf"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skip" in result.stderr.lower()
    assert output_file.read_text() == "{}"
