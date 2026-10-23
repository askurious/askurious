"""Build app/data/geo_ip.bin.gz: country and German federal state of every IP address range.

    python tools/build_geo_ip.py dbip-city-lite-2026-10.csv.gz

The source is the free database "IP to City Lite" of DB-IP (https://db-ip.com/db/download/ip-to-city-lite,
CC BY 4.0, a new issue every month, no account): download the CSV file by hand, e.g.
https://download.db-ip.com/free/dbip-city-lite-2026-10.csv.gz (about 90 MB), and pass its path. The server
never downloads anything; the small table built here travels with the code.

Of each row (first address, last address, continent, country, state, city, latitude, longitude) only the
country stays, and for Germany the federal state. Rows that follow each other with the same result become one
range, IPv6 ranges are cut to their first 64 bits (the network part). The names of the German states are
matched by the list STATES below; a name that is not in it stops the tool, so a new spelling in a later issue
cannot silently lose a state.

Output (gzip): a line "QGEO1", a line of JSON {"month", "codes", "v4", "v6"}, then per family the first
addresses of the ranges (IPv4: 4 bytes, IPv6: the upper 8 bytes; little-endian) and for each range the index
of its code in "codes" (2 bytes). A code is "" (unknown), a country ("FR") or a country with a state
("DE-5": the state key of the AGS without the leading zero, as in app/items/federal_state.py).
Reading: app/geoip.py."""
import csv
import gzip
import ipaddress
import json
import os
import re
import sys
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "data", "geo_ip.bin.gz")

# the spellings of the German states in the database -> state key of the AGS without the leading zero
STATES = {
    "Schleswig-Holstein": "1",
    "Hamburg": "2", "Free and Hanseatic City of Hamburg": "2",
    "Lower Saxony": "3", "Niedersachsen": "3",
    "Bremen": "4", "City state Bremen": "4", "Free Hanseatic City of Bremen": "4",
    "North Rhine-Westphalia": "5", "Nordrhein-Westfalen": "5",
    "Hesse": "6", "Hessen": "6",
    "Rheinland-Pfalz": "7", "Rhineland-Palatinate": "7",
    "Baden-Wurttemberg": "8", "Baden-Württemberg": "8",
    "Bavaria": "9", "Bayern": "9",
    "Saarland": "10",
    "Berlin": "11", "State of Berlin": "11",
    "Brandenburg": "12",
    "Mecklenburg-Vorpommern": "13", "Mecklenburg-Western Pomerania": "13",
    "Saxony": "14", "Sachsen": "14",
    "Saxony-Anhalt": "15", "Sachsen-Anhalt": "15",
    "Thuringia": "16", "Thüringen": "16",
}
UNKNOWN = ("ZZ", "")  # the database's country for ranges nobody uses (reserved, private)


def code_of(country, state):
    if country in UNKNOWN:
        return ""
    if country != "DE" or not state:
        return country
    if state not in STATES:
        sys.exit(f"Unknown German state in the database: {state!r} (add its spelling to STATES)")
    return f"DE-{STATES[state]}"


def build(path):
    codes, index = [""], {"": 0}
    starts = {4: array("I"), 6: array("Q")}
    values = {4: array("H"), 6: array("H")}
    after = {4: 0, 6: 0}  # the first address behind the last row read, per family
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for first, last, _continent, country, state, *_rest in csv.reader(fh):
            rows += 1
            a, b = ipaddress.ip_address(first), ipaddress.ip_address(last)
            family = a.version
            lo, hi = (int(a), int(b) + 1) if family == 4 else (int(a) >> 64, (int(b) >> 64) + 1)
            if lo < after[family]:
                if hi <= after[family]:
                    continue  # an IPv6 range inside a network part that is taken already
                lo = after[family]
            code = code_of(country, state)
            if code not in index:
                index[code] = len(codes)
                codes.append(code)

            def add(start, value):
                if not values[family] or values[family][-1] != value:
                    starts[family].append(start)
                    values[family].append(value)

            if lo > after[family]:
                add(after[family], 0)  # a gap between two rows: unknown
            add(lo, index[code])
            after[family] = hi
    for family, top in ((4, 1 << 32), (6, 1 << 64)):
        if after[family] < top and values[family][-1] != 0:
            starts[family].append(after[family])
            values[family].append(0)
    return rows, codes, starts, values


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    month = re.search(r"(\d{4}-\d{2})", os.path.basename(path))
    if not month:
        sys.exit("The file name must carry the month of the issue, e.g. dbip-city-lite-2026-10.csv.gz")
    rows, codes, starts, values = build(path)
    head = {"month": month.group(1), "codes": codes, "v4": len(starts[4]), "v6": len(starts[6])}
    for family in (4, 6):
        if sys.byteorder == "big":
            starts[family].byteswap()
            values[family].byteswap()
    with gzip.GzipFile(OUT, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(b"QGEO1\n" + json.dumps(head, separators=(",", ":")).encode() + b"\n")
        for family in (4, 6):
            fh.write(starts[family].tobytes())
            fh.write(values[family].tobytes())
    states = sum(1 for c in codes if c.startswith("DE-"))
    print(f"{rows} rows -> {head['v4']} IPv4 and {head['v6']} IPv6 ranges, {len(codes) - 1} codes "
          f"({states} German states), {os.path.getsize(OUT) / 1e6:.1f} MB: {OUT}")


if __name__ == "__main__":
    main()
