"""Deterministic builds retain exact previous bytes for rolling participants (SP13 PF-13/PF-17e).

Install deploy/docker/requirements-static.lock to run these build-only dependency checks.
"""
import hashlib
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def builder():
    pytest.importorskip("rjsmin")
    pytest.importorskip("rcssmin")
    path = Path(__file__).resolve().parents[2] / "tools" / "build_static.py"
    spec = importlib.util.spec_from_file_location("static_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_old_urls_keep_exact_old_bytes_and_current_urls_use_minified_bytes(tmp_path, builder):
    current = tmp_path / "static"
    old = tmp_path / "previous" / "static"
    current.mkdir()
    old.mkdir(parents=True)
    old_bytes = b"/* old public style */ .live { color: red; }\n"
    (old / "participant.css").write_bytes(old_bytes)
    (current / "participant.css").write_text("/* new style */ .live { color: blue; }\n", encoding="utf-8")
    builder.with_previous(current, old)
    assert (old / "participant.css").read_bytes() == old_bytes
    old_hash = hashlib.sha256(old_bytes).hexdigest()[:12]
    current_hash = hashlib.sha256((current / "participant.css").read_bytes()).hexdigest()[:12]
    versions = (tmp_path / "static-versions.conf").read_text(encoding="utf-8")
    assert f'"/static/participant.css:{old_hash}" "/previous/static/participant.css";' in versions
    assert f'"/static/participant.css:{current_hash}" "/static/participant.css";' in versions
    assert old_hash != current_hash
    first = (current / "participant.css.gz").read_bytes()
    builder.with_previous(current, old)
    assert (current / "participant.css.gz").read_bytes() == first


def test_unchanged_assets_prefer_the_current_file_without_duplicate_map_keys(tmp_path, builder):
    current = tmp_path / "static"
    old = tmp_path / "previous" / "static"
    current.mkdir()
    old.mkdir(parents=True)
    for folder in (current, old):
        (folder / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    builder.with_previous(current, old)
    versions = (tmp_path / "static-versions.conf").read_text(encoding="utf-8").splitlines()
    assert len(versions) == 1 and versions[0].endswith('"/static/icon.svg";')
