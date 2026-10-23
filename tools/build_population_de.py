"""Build app/data/population_de.json.gz from the population update of the Statistisches Bundesamt.

    python tools/build_population_de.py 12411-0013-data.json [2026-10-02]

Input: table 12411-0013 "Bevölkerung: Bundesländer, Stichtag, Geschlecht, Altersjahre" of GENESIS-Online as
the JSON the table page itself loads (https://genesis.destatis.de/genesis/api/rest/tables/12411-0013/data,
JSON-stat: the dimensions GES, DLAND, STAG, ALT103 and one flat list of values). The second argument is the
day of the download (default: today). Licence: Datenlizenz Deutschland Namensnennung 2.0, © Statistisches
Bundesamt (Destatis). Needs only the standard library.

The newest reference date in the table is taken. The totals the table carries (per sex, per state, all ages)
are checked against the sums of the single years, so a changed layout fails here and not in a quota.

Output:
  {"table": "12411-0013", "date": "2025-12-31", "retrieved": "2026-10-02", "top_age": 90,
   "states": {"8": {"m": [n0, n1, ..., n90], "f": [...]}, ...}}
The keys are the state keys of the AGS without the leading zero (the answer codes of the element Bundesland);
the lists hold the persons per year of age, the last entry is "90 years and more"."""
import datetime
import gzip
import json
import os
import sys

TABLE = "12411-0013"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "population_de.json.gz")
SEXES = {"m": "GESM", "f": "GESW"}
TOTAL = "%TOTAL%"
TOP_AGE = 90


def read(path):
    """{(sex code, state code, age code): persons} at the newest reference date, and that date."""
    with open(path, encoding="utf-8") as fh:
        cube = json.load(fh)["data"][0]
    ids, sizes = cube["id"], cube["size"]
    index = {name: cube["dimension"][name]["category"]["index"] for name in ids}
    date = max(index["STAG"])
    strides, step = {}, 1
    for name, size in zip(reversed(ids), reversed(sizes)):
        strides[name], step = step, step * size
    if step != len(cube["value"]):
        raise SystemExit(f"{step} values expected from the sizes, {len(cube['value'])} found")
    base = strides["STAG"] * index["STAG"][date]
    out = {}
    for sex, s in index["GES"].items():
        for state, d in index["DLAND"].items():
            for age, a in index["ALT103"].items():
                value = cube["value"][base + strides["GES"] * s + strides["DLAND"] * d + strides["ALT103"] * a]
                if value is None or value != int(value):
                    raise SystemExit(f"no whole number for {sex}, {state}, {age}: {value!r}")
                out[(sex, state, age)] = int(value)
    return out, date


def build(path, retrieved):
    values, date = read(path)
    ages = [f"ALT{n:03d}" for n in range(TOP_AGE)] + [f"ALT{TOP_AGE:03d}UM"]
    codes = sorted({state for _, state, _ in values})
    if len(codes) != 16 or {age for _, _, age in values} != set(ages) | {TOTAL}:
        raise SystemExit("the table does not hold 16 states and the ages 0 to 89, 90 and more")
    states = {}
    for code in codes:
        rows = {key: [values[(sex, code, age)] for age in ages] for key, sex in SEXES.items()}
        for key, sex in SEXES.items():
            if sum(rows[key]) != values[(sex, code, TOTAL)]:
                raise SystemExit(f"state {code}, {sex}: the years add up to {sum(rows[key])}, the table says "
                                 f"{values[(sex, code, TOTAL)]}")
        for n, age in enumerate(ages):
            if rows["m"][n] + rows["f"][n] != values[(TOTAL, code, age)]:
                raise SystemExit(f"state {code}, {age}: male and female do not add up to the total")
        states[str(int(code))] = rows
    return {"table": TABLE, "date": date, "retrieved": retrieved, "top_age": TOP_AGE, "states": states}


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    data = build(argv[1], argv[2] if len(argv) > 2 else datetime.date.today().isoformat())
    with gzip.GzipFile(OUT, "wb", mtime=0) as fh:
        fh.write(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    total = sum(sum(rows["m"]) + sum(rows["f"]) for rows in data["states"].values())
    print(f"{OUT}: {len(data['states'])} states, reference date {data['date']}, {total} persons, "
          f"{os.path.getsize(OUT)} bytes")


if __name__ == "__main__":
    main(sys.argv)
