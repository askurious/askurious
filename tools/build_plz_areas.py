"""Build app/data/plz_areas/<region>.json.gz from the OpenStreetMap postal code areas.

    python tools/build_plz_areas.py postleitzahlen.topojson

Input: postleitzahlen.topojson from https://github.com/yetzt/postleitzahlen/releases (brotli-compressed;
unpack first, e.g. `brotli -d` or `node -e "..zlib.brotliDecompressSync.."`). Licence ODbL 1.0,
© OpenStreetMap contributors.

One file per postal region (first two digits), a small topology like TopoJSON: shared borders are
stored once. Coordinates are rounded to 5 decimals (about 1 m) and delta-encoded as integers:
  {"arcs": [[[x0, y0], [dx, dy], ...], ...],      x, y in 1e-5 degrees (lon, lat)
   "areas": {"52062": {"type": "Polygon", "arcs": [[0, ~1, ...], ...]}, ...}}
An arc index i < 0 means arc ~i (= -i - 1) reversed, as in TopoJSON. Reading: app/items/postal_code.py."""
import gzip
import json
import os
import sys
from collections import defaultdict

SCALE = 100000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "plz_areas")


def encode(arc):
    """Absolute coordinates -> first point absolute, then deltas; consecutive duplicates after rounding dropped."""
    out, last = [], None
    for lon, lat in arc:
        p = (round(lon * SCALE), round(lat * SCALE))
        if p == last:
            continue
        out.append([p[0] - last[0], p[1] - last[1]] if last else list(p))
        last = p
    if len(out) == 1:  # an arc shorter than the rounding: keep it as two identical points
        out.append([0, 0])
    return out


def walk(arcs):
    """All arc indices in a nested TopoJSON arcs list."""
    for a in arcs:
        if isinstance(a, list):
            yield from walk(a)
        else:
            yield a


def remap(arcs, index):
    return [remap(a, index) if isinstance(a, list) else (index[a] if a >= 0 else ~index[~a]) for a in arcs]


def build(path):
    topo = json.load(open(path, encoding="utf-8"))
    assert "transform" not in topo, "expects unquantized TopoJSON"
    regions = defaultdict(dict)
    for g in topo["objects"]["postleitzahlen"]["geometries"]:
        plz = g["properties"]["postcode"]
        if g["type"] in ("Polygon", "MultiPolygon"):
            regions[plz[:2]][plz] = g
    os.makedirs(OUT, exist_ok=True)
    total = 0
    for region, areas in sorted(regions.items()):
        used = sorted({a if a >= 0 else ~a for g in areas.values() for a in walk(g["arcs"])})
        index = {a: n for n, a in enumerate(used)}
        data = {"arcs": [encode(topo["arcs"][a]) for a in used],
                "areas": {plz: {"type": g["type"], "arcs": remap(g["arcs"], index)} for plz, g in sorted(areas.items())}}
        target = os.path.join(OUT, f"{region}.json.gz")
        with gzip.GzipFile(target, "wb", mtime=0) as fh:  # mtime 0: same input, same bytes
            fh.write(json.dumps(data, separators=(",", ":")).encode())
        total += os.path.getsize(target)
    # every code with an area, one per line: lets the app answer "has an area?" without reading the regions
    with open(os.path.join(OUT, "codes.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(f"{plz}\n" for plz in sorted(p for areas in regions.values() for p in areas)))
    return sum(len(a) for a in regions.values()), len(regions), total


if __name__ == "__main__":
    n, files, size = build(sys.argv[1])
    print(f"{n} postal codes in {files} files, {size / 1e6:.1f} MB")
