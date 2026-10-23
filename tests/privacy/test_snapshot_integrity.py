"""Storage and mathematical integrity regressions from the fresh audit of 14 October 2026."""


def test_backup_detects_same_size_edits_in_same_second(tmp_path):
    import os
    import runpy
    from pathlib import Path
    mod = runpy.run_path(str(Path.cwd() / "deploy/docker/snapshot_uploads.py"))
    src, snapshots = tmp_path / "source", tmp_path / "snapshots"
    src.mkdir(); snapshots.mkdir()
    file = src / "answer.txt"
    file.write_text("AAAA")
    os.utime(file, (1800000000.1, 1800000000.1))
    mod["take"](str(src), str(snapshots), "20261014-100000")
    file.write_text("BBBB")
    os.utime(file, (1800000000.8, 1800000000.8))
    _, copied, _ = mod["take"](str(src), str(snapshots), "20261014-100001")
    assert copied == 1
    assert file.read_text() == "BBBB"
    assert (snapshots / "20261014-100001/answer.txt").read_text() == "BBBB"
