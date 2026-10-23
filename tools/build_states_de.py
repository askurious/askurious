"""Build app/data/states_de.json.gz from the administrative areas of the BKG (VG2500).

    python tools/build_states_de.py vg2500_12-31.utm32s.gpkg.zip

Input: the GeoPackage of "Verwaltungsgebiete 1:2 500 000, Stand 31.12." (VG2500) from
https://gdz.bkg.bund.de/ (zip or the unpacked .gpkg). Licence: Datenlizenz Deutschland Namensnennung 2.0,
© BKG (year of the last download) dl-de/by-2-0. Needs only the standard library: a GeoPackage is an SQLite file, the geometries are
well-known binary in UTM zone 32N (ETRS89), converted here to longitude and latitude.

Only the land areas are taken (GF = 9). The layer also holds the waters that belong to a state (GF = 8: the
coastal waters of the North Sea and the Baltic Sea, Lake Constance); on a map coloured by answers they would
paint the sea.

Output in the format of the postal code areas (tools/build_plz_areas.py), one "arc" per ring:
  {"arcs": [[[x0, y0], [dx, dy], ...], ...],      x, y in 1e-5 degrees (lon, lat)
   "areas": {"9": {"type": "MultiPolygon", "arcs": [[[0], [1]], ...]}, ...}}
The keys are the state keys of the AGS without the leading zero. Reading: app/items/federal_state.py."""
import gzip
import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
import zipfile

SCALE = 100000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "states_de.json.gz")

# GRS80 ellipsoid, UTM zone 32N
A, F = 6378137.0, 1 / 298.257222101
K0, E0, LON0 = 0.9996, 500000.0, math.radians(9.0)


def to_lonlat(easting, northing):
    """UTM 32N -> (lon, lat) in degrees (Krüger series, well below a millimetre inside the zone)."""
    n = F / (2 - F)
    a_ = A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    xi, eta = northing / (K0 * a_), (easting - E0) / (K0 * a_)
    beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96, n ** 2 / 48 + n ** 3 / 15, 17 * n ** 3 / 480)
    xi_ = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(beta, 1))
    eta_ = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(beta, 1))
    chi = math.asin(math.sin(xi_) / math.cosh(eta_))
    delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3, 7 * n ** 2 / 3 - 8 * n ** 3 / 5, 56 * n ** 3 / 15)
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    lon = LON0 + math.atan2(math.sinh(eta_), math.cos(xi_))
    return math.degrees(lon), math.degrees(lat)


def polygons(blob):
    """[[ring, ...], ...] of a GeoPackage geometry (Polygon or MultiPolygon); a ring is [(x, y), ...]."""
    assert blob[:2] == b"GP", "not a GeoPackage geometry"
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
    pos = 8 + envelope

    def read(fmt):
        nonlocal pos
        values = struct.unpack_from(fmt, blob, pos)
        pos += struct.calcsize(fmt)
        return values

    def geometry():
        order = "<" if read("B")[0] else ">"
        kind = read(order + "I")[0]
        dims = 2 + (kind // 1000 in (1, 2)) + (kind // 1000 == 3) * 2  # Z, M or ZM coordinates are skipped
        if kind % 1000 == 3:
            rings = []
            for _ in range(read(order + "I")[0]):
                pts = [read(order + "d" * dims)[:2] for _ in range(read(order + "I")[0])]
                rings.append(pts)
            return [rings]
        assert kind % 1000 == 6, f"geometry type {kind}"
        return [poly for _ in range(read(order + "I")[0]) for poly in geometry()]

    return geometry()


def encode(ring):
    """Absolute coordinates -> first point absolute, then deltas; consecutive duplicates after rounding dropped."""
    out, last = [], None
    for lon, lat in ring:
        p = (round(lon * SCALE), round(lat * SCALE))
        if p == last:
            continue
        out.append([p[0] - last[0], p[1] - last[1]] if last else list(p))
        last = p
    return out


LAND = 9  # GF ("Geofaktor") of the VG2500: 9 = land, 8 = water


def _table(con):
    """(table, geometry column, key column) of the layer with the 16 states."""
    for table, column in con.execute("SELECT table_name, column_name FROM gpkg_geometry_columns"):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        key = next((c for c in cols if c.upper() == "AGS"), None)
        if key and "GF" in cols and con.execute(
                f'SELECT COUNT(DISTINCT "{key}") FROM "{table}" WHERE GF = ?', (LAND,)).fetchone()[0] == 16:
            return table, column, key
    raise SystemExit("no layer with 16 states (columns AGS and GF) found")


def build(path):
    with tempfile.TemporaryDirectory() as tmp:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                name = next(n for n in z.namelist() if n.lower().endswith(".gpkg"))
                path = z.extract(name, tmp)
        con = sqlite3.connect(path)
        table, column, key = _table(con)
        states = {}
        for ags, blob in con.execute(f'SELECT "{key}", "{column}" FROM "{table}" WHERE GF = ?', (LAND,)):
            states.setdefault(str(int(ags)), []).extend(polygons(blob))
        con.close()
    arcs, areas = [], {}
    for code in sorted(states, key=int):
        polys = []
        for rings in states[code]:
            ids = []
            for ring in rings:
                arc = encode([to_lonlat(x, y) for x, y in ring])
                if len(arc) >= 4:  # a ring that survives the rounding
                    ids.append([len(arcs)])
                    arcs.append(arc)
            if ids:
                polys.append(ids)
        areas[code] = {"type": "MultiPolygon", "arcs": polys}
    with gzip.GzipFile(OUT, "wb", mtime=0) as fh:  # mtime 0: same input, same bytes
        fh.write(json.dumps({"arcs": arcs, "areas": areas}, separators=(",", ":")).encode())
    return table, len(areas), sum(len(a) for a in arcs), os.path.getsize(OUT)


if __name__ == "__main__":
    table, n, points, size = build(sys.argv[1])
    print(f"{n} states from layer {table}, {points} points, {size / 1e3:.0f} kB")
