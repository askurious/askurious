"""Deterministic static assets for both app/nginx images (SP13 PF-13/PF-17e)."""
import gzip
import hashlib
import sys
from pathlib import Path
from rjsmin import jsmin
from rcssmin import cssmin


def build(root, prefix="/static", minify=True):
    hashes = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in (".gz", ".br") or path.name == ".keep":
            continue
        relative = path.relative_to(root).as_posix()
        if minify and path.suffix in (".js", ".css") and not relative.startswith("vendor/"):
            source = path.read_text(encoding="utf-8")
            value = jsmin(source, keep_bang_comments=True) if path.suffix == ".js" else cssmin(source, keep_bang_comments=True)
            path.write_text(value + "\n", encoding="utf-8", newline="\n")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:12]
        hashes.append(f'"{prefix}/{relative}" "{digest}";')
        if path.suffix in (".js", ".css", ".svg", ".json", ".txt"):
            path.with_name(path.name + ".gz").write_bytes(gzip.compress(data, compresslevel=6, mtime=0))
    (root.parent / "static-hashes.conf").write_text("\n".join(hashes) + "\n", encoding="utf-8")


def with_previous(root, previous):
    """Keep last release's exact bytes: old HTML still names its hashes during a sequential deployment."""
    build(previous, prefix="/previous/static", minify=False)
    build(root)
    current_map = root.parent / "static-hashes.conf"
    current_map.write_text(current_map.read_text(encoding="utf-8") +
                           (previous.parent / "static-hashes.conf").read_text(encoding="utf-8"), encoding="utf-8")
    versions = {}
    for folder, prefix in ((previous, "/previous/static"), (root, "/static")):
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.suffix in (".gz", ".br") or path.name == ".keep":
                continue
            relative = path.relative_to(folder).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            versions[f"/static/{relative}:{digest}"] = f"{prefix}/{relative}"
    (root.parent / "static-versions.conf").write_text(
        "\n".join(f'"{key}" "{value}";' for key, value in sorted(versions.items())) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[2] == "--previous":
        with_previous(Path(sys.argv[1]), Path(sys.argv[3]))
    else:
        build(Path(sys.argv[1]))
