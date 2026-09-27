import hashlib
import base64
import json
import struct
import wave

from anomalica_common.peaks import bins_for
from anomalica_common.publishing import GATED_ZONE, OPEN_ZONE

from waveform_sidecars import bindings, generate, valid_sidecar
import publish_waveform_sidecars


def test_backfill_uses_asset_hash_and_walks_legacy_records(tmp_path):
    store = tmp_path / "store"
    archive = tmp_path / "records"
    (store / "v1").mkdir(parents=True)
    archive.mkdir()
    legacy_hash = "a" * 64
    asset_hash = "b" * 64
    (archive / f"{legacy_hash}.ogg").write_bytes(b"old")
    (archive / f"{asset_hash}.opus").write_bytes(b"new")
    (store / "v1" / "old.md").write_text(
        f"---\nschema: anomalica/record/2\nsource_type: video\ncontent_hash: sha256:{legacy_hash}\n---\n"
    )
    (store / "new.md").write_text(
        f"---\nschema: anomalica/record/3\ncontent_hash: sha256:{'c' * 64}\n"
        f"assets:\n  - asset_hash: sha256:{asset_hash}\n    archived_ext: opus\n"
        "    source_type: video\n---\n"
    )
    (store / "v1" / "retired.md").write_text(
        f"---\nschema: anomalica/record/2\nsource_type: video\ncontent_hash: sha256:{'d' * 64}\nsuperseded_by: {'e' * 64}\n---\n"
    )
    result = bindings(store, archive)
    assert set(result) == {legacy_hash, asset_hash}
    assert result[legacy_hash][0] == archive / f"{legacy_hash}.ogg"
    assert result[asset_hash][0] == archive / f"{asset_hash}.opus"


def test_sidecar_matches_decoded_audio_and_detects_bad_payload(tmp_path):
    source = tmp_path / "record.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(struct.pack("<8000h", *([0] * 4000 + [32767] * 4000)))
    hash_ = hashlib.sha256(source.read_bytes()).hexdigest()
    sidecar = tmp_path / f"{hash_}.peaks.json"
    generate(source, sidecar, hash_)
    payload = json.loads(sidecar.read_text())
    assert valid_sidecar(sidecar, hash_)
    assert payload["duration"] == 1
    amplitudes = base64.b64decode(payload["peaks"])
    assert len(amplitudes) == bins_for(1)
    assert amplitudes[:50] == bytes(50)
    assert min(amplitudes[50:]) > 250
    payload["hex_hash"] = "wrong"
    sidecar.write_text(json.dumps(payload))
    assert not valid_sidecar(sidecar, hash_)


def test_peaks_route_by_own_rights_not_audio_zone_or_record_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(publish_waveform_sidecars, "RECORDS", tmp_path)
    hash_ = "a" * 64
    (tmp_path / f"{hash_}.peaks.json").write_text("{}")
    record = tmp_path / "record.md"
    record.write_text(
        f"---\nschema: anomalica/record/3\ncontent_hash: sha256:{'b' * 64}\n"
        f"assets:\n  - asset_hash: sha256:{hash_}\n    source_type: video\n"
        "    archived_ext: opus\n    copyright:\n      status: publicly_accessible\n---\n"
    )
    assert publish_waveform_sidecars.planned([record]) == {hash_: OPEN_ZONE}
    record.write_text(record.read_text().replace("publicly_accessible", "restricted"))
    assert publish_waveform_sidecars.planned([record]) == {hash_: GATED_ZONE}
