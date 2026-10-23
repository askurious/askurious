"""Build app/data/plz_states_de.tsv: the federal state of every German postal code.

    python tools/build_plz_states_de.py

No download: everything comes from files of this repository (see app/data/README.md).
  - app/data/plz_areas/*.json.gz   the postal code areas (OpenStreetMap, ODbL 1.0)
  - app/data/plz_de.tsv            positions of all codes (GeoNames, CC BY 4.0), for codes without an area
  - app/data/states_de.json.gz     outlines of the 16 states (BKG VG2500, dl-de/by-2-0)

A code with an area gets the state that holds most of the area: a grid of points over the area is laid over
the outlines of the states. A code without an area (large customers, post office boxes) gets the state of its
position. Points outside all outlines (the outlines are generalised and leave out the sea) go to the nearest
state. Postal codes do not stop at state borders: a few areas lie in two states and get the one with the
larger part. The outlines of the states are generalised (1:2 500 000), so close to a border the result can be
off by some hundred metres; the tool prints how many areas have less than 95 % of their grid in one state.

Output, one row per code: plz <tab> state key of the AGS without the leading zero.
Reading: app/items/postal_code.py (state)."""
import gzip
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "app", "data")
OUT = os.path.join(DATA, "plz_states_de.tsv")
GRID = 14  # points per side of the grid over the bounding box of an area


def decode(arcs):
    """Delta-encoded arcs -> lists of (lon, lat) in 1e-5 degrees, as integers."""
    out = []
    for arc in arcs:
        x = y = 0
        points = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            points.append((x, y))
        out.append(points)
    return out


def rings(area, arcs):
    """The rings of an area, each a closed list of points; outer rings and holes alike (even-odd rule)."""
    polygons = [area["arcs"]] if area["type"] == "Polygon" else area["arcs"]
    out = []
    for polygon in polygons:
        for ring in polygon:
            points = []
            for i in ring:
                arc = arcs[i] if i >= 0 else arcs[~i][::-1]
                points += arc if not points else arc[1:]
            out.append(points)
    return out


class Shape:
    """Rings as arrays of edges, for the even-odd test of many points at once."""

    def __init__(self, ring_list):
        x1, y1, x2, y2 = [], [], [], []
        for ring in ring_list:
            for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
                if (ax, ay) != (bx, by):
                    x1.append(ax), y1.append(ay), x2.append(bx), y2.append(by)
        self.x1, self.y1, self.x2, self.y2 = (np.array(v, dtype=np.float64) for v in (x1, y1, x2, y2))
        self.box = (min(self.x1.min(), self.x2.min()), min(self.y1.min(), self.y2.min()),
                    max(self.x1.max(), self.x2.max()), max(self.y1.max(), self.y2.max()))

    def inside(self, px, py):
        """Boolean array: which of the points lie inside."""
        px, py = px[:, None], py[:, None]
        crosses = (self.y1 > py) != (self.y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = self.x1 + (py - self.y1) * (self.x2 - self.x1) / (self.y2 - self.y1)
        return (np.count_nonzero(crosses & (px < at), axis=1) % 2).astype(bool)

    def distance(self, px, py):
        """The smallest distance of one point to a corner of the outline (enough to pick the nearest state)."""
        return float(np.min((self.x1 - px) ** 2 + (self.y1 - py) ** 2))


def main():
    with gzip.open(os.path.join(DATA, "states_de.json.gz"), "rt", encoding="utf-8") as fh:
        topology = json.load(fh)
    arcs = decode(topology["arcs"])
    states = {code: Shape(rings(area, arcs)) for code, area in topology["areas"].items()}

    def votes(px, py):
        """{state: number of the points that lie in it}"""
        out = {}
        for code, shape in states.items():
            x0, y0, x1, y1 = shape.box
            near = (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1)
            if near.any():
                hits = int(np.count_nonzero(shape.inside(px[near], py[near])))
                if hits:
                    out[code] = hits
        return out

    def nearest(x, y):
        return min(states, key=lambda code: states[code].distance(x, y))

    result = {}
    folder = os.path.join(DATA, "plz_areas")
    for name in sorted(os.listdir(folder)):
        with gzip.open(os.path.join(folder, name), "rt", encoding="utf-8") as fh:
            region = json.load(fh)
        region_arcs = decode(region["arcs"])
        for plz, area in region["areas"].items():
            shape = Shape(rings(area, region_arcs))
            x0, y0, x1, y1 = shape.box
            gx, gy = np.meshgrid(np.linspace(x0, x1, GRID + 2)[1:-1], np.linspace(y0, y1, GRID + 2)[1:-1])
            gx, gy = gx.ravel(), gy.ravel()
            own = shape.inside(gx, gy)
            if own.any():
                gx, gy = gx[own], gy[own]
            else:  # a sliver the grid misses: its corners
                gx, gy = shape.x1, shape.y1
            found = votes(gx, gy)
            if not found:
                result[plz] = (nearest(float(gx.mean()), float(gy.mean())), False)
                continue
            best = max(sorted(found, key=int), key=lambda code: found[code])
            result[plz] = (best, found[best] < 0.95 * sum(found.values()))

    with open(os.path.join(DATA, "plz_de.tsv"), encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            plz, _, lat, lon = line.rstrip("\n").split("\t")
            if plz in result:
                continue
            x, y = float(lon) * 100000, float(lat) * 100000
            found = votes(np.array([x]), np.array([y]))
            result[plz] = (next(iter(found)) if found else nearest(x, y), False)

    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("plz\tstate\n")
        for plz in sorted(result):
            fh.write(f"{plz}\t{result[plz][0]}\n")
    split = sum(1 for _, mixed in result.values() if mixed)
    print(f"{len(result)} codes, {split} of them with less than 95 % of the area in their state -> {OUT}")


if __name__ == "__main__":
    main()
