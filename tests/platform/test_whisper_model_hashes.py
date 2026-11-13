"""The Whisper service starts only on a model volume that matches the hashes of its manifest.json (whisper_service
.integrity, called from __main__): the accepted torch and transformers advisories (osv-accepted.txt) rest on "only the
hashed, read-only models are loaded" (operations audit 2026-11-11, Q6)."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/docker/whisper"))
from whisper_service.integrity import problems  # noqa: E402


def _volume(tmp_path):
    (tmp_path / "small").mkdir()
    (tmp_path / "small" / "model.bin").write_bytes(b"weights")
    (tmp_path / "pyannote").mkdir()
    (tmp_path / "pyannote" / "config-runtime.yaml").write_text("pipeline: x\n")
    hashes = {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob("*") if p.is_file()}
    manifest = {"sha256": hashes}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_a_matching_volume_passes(tmp_path):
    assert problems(tmp_path, _volume(tmp_path)) == []


def test_a_changed_extra_or_missing_file_stops_the_start(tmp_path):
    manifest = _volume(tmp_path)
    (tmp_path / "small" / "model.bin").write_bytes(b"other weights")
    (tmp_path / "small" / "extra.bin").write_bytes(b"x")
    (tmp_path / "pyannote" / "config-runtime.yaml").unlink()
    found = problems(tmp_path, manifest)
    assert "hash differs: small/model.bin" in found and "not in the manifest: small/extra.bin" in found
    assert "missing: pyannote/config-runtime.yaml" in found


def test_a_manifest_without_hashes_stops_it(tmp_path):
    assert problems(tmp_path, {"models": {}}) and "no hashes" in problems(tmp_path, {})[0]


def test_the_service_checks_before_it_serves():
    main = (Path(__file__).resolve().parents[2] / "deploy/docker/whisper/whisper_service/__main__.py").read_text()
    assert main.index("problems(settings.models, data)") < main.index("serve(")
