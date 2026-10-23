"""Build app/data/plz_de.tsv from the GeoNames postal code dump for Germany.

    python tools/build_plz_de.py DE.zip      (DE.zip from https://download.geonames.org/export/zip/)

One row per postal code: plz, ort, lat, lon. GeoNames lists several places per code; the place is the
most frequent name, the position the median of those places. Codes that only large customers use
(Großkunden-PLZ, entries named after companies) get the district name and the median of all entries.
Source: GeoNames (www.geonames.org), licence CC BY 4.0."""
import collections
import re
import statistics
import sys
import zipfile

COMPANY = re.compile(r"(GmbH|mbH|\bAG\b|\bKG|\bOHG\b|\bSE\b|e\.\s?V\.|GbR|\bCo\.|gesellschaft|Verlag|Bank|Versicherung|"
                     r"Insurance|Service|Management|Corporate|Holding|Postfach|Vertrieb|Werke?\b)|&", re.I)
DISTRICT = re.compile(r"^(Stadtkreis|Landkreis|Kreisfreie Stadt|Kreis|Regionalverband) |, Stadt$")


def build(zip_path, out_path="app/data/plz_de.tsv"):
    by = collections.defaultdict(list)
    for line in zipfile.ZipFile(zip_path).read("DE.txt").decode("utf-8").splitlines():
        f = line.split("\t")
        if re.fullmatch(r"\d{5}", f[1]):
            by[f[1]].append(f)
    out = ["plz\tort\tlat\tlon"]
    for plz in sorted(by):
        entries = by[plz]
        places = [f for f in entries if not COMPANY.search(f[2])]
        if places:
            ort = collections.Counter(f[2] for f in places).most_common(1)[0][0]
        else:
            district = collections.Counter(f[7] or f[5] for f in entries).most_common(1)[0][0]
            ort = DISTRICT.sub("", district) + " (Großkunde)"
        use = places or entries
        lat = statistics.median(float(f[9]) for f in use)
        lon = statistics.median(float(f[10]) for f in use)
        out.append(f"{plz}\t{ort}\t{lat:.4f}\t{lon:.4f}")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    return len(out) - 1


if __name__ == "__main__":
    print(build(sys.argv[1]), "postal codes")
