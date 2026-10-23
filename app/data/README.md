# Data files

`plz_de.tsv`: German postal codes with place name and position (WGS84), one row per code.
Source: [GeoNames](https://www.geonames.org/) postal code dump `DE.zip`, licence [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Built with `tools/build_plz_de.py` (downloaded 2026-09-30). Positions are approximate (centre of the places GeoNames lists for a code), not postal-area centroids.

`plz_areas/<region>.json.gz`: boundaries of the German postal code areas, one file per region (first two digits), as a small topology with shared borders stored once and coordinates rounded to 5 decimals (about 1 m).
Source: [OpenStreetMap](https://www.openstreetmap.org/copyright) (`boundary=postal_code`), export `postleitzahlen.topojson.br` of [yetzt/postleitzahlen](https://github.com/yetzt/postleitzahlen/releases) release 2026.02.
Licence: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), © OpenStreetMap contributors; these derived files are under ODbL as well.
Built with `tools/build_plz_areas.py` (downloaded 2026-09-30). Codes used only by large customers have no area of their own. The tool also writes `plz_areas/codes.txt`, every code with an area, one per line: the app answers "has an area?" from it and sends the region files to the browser unread (`app/geo.py`).

`plz_states_de.tsv`: the federal state of every German postal code (state key of the AGS without the leading zero), for conditions and quotas on "Bundesland (aus der Postleitzahl)".
Derived from the three files around it: a code with an area gets the state that holds most of its area (`plz_areas`, laid over `states_de.json.gz`), a code without an area the state of its position (`plz_de.tsv`). Licences as of the sources: ODbL 1.0 (© OpenStreetMap contributors), CC BY 4.0 (GeoNames), © BKG (2026) dl-de/by-2-0; this derived table is under ODbL as well.
Built with `tools/build_plz_states_de.py` (no download, needs numpy). The outlines of the states are generalised, so right at a state border a code can be assigned to the neighbour; codes that cross a border get the state with the larger part.

`states_de.json.gz`: outlines of the 16 German federal states, keyed by the state key of the AGS without the leading zero, in the format of the postal code areas (one arc per ring, coordinates rounded to 5 decimals).
Source: Verwaltungsgebiete 1:2 500 000 (VG2500, as of 31.12.2025) of the [Bundesamt für Kartographie und Geodäsie](https://www.bkg.bund.de/) ([description of the data set](https://sg.geodatenzentrum.de/web_public/gdz/dokumentation/deu/vg2500.pdf)), file `vg2500_12-31.utm32s.gpkg.zip`, layer `vg2500_lan`, land areas only (GF = 9): the coastal waters of the North Sea and the Baltic Sea and Lake Constance, which the layer assigns to the states as well, are left out.
Licence: [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0); attribution as the BKG prescribes it: © [BKG](https://www.bkg.bund.de) (2026) [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0), Datenquellen: https://sgx.geodatenzentrum.de/web_public/gdz/datenquellen/datenquellen_vg_nuts.pdf; the coordinates were converted from UTM zone 32N to longitude and latitude.
Built with `tools/build_states_de.py` (downloaded 2026-10-02). Generalised for small scales: fine for a map of Germany, not for street level.

`population_de.json.gz`: persons per federal state, sex and year of age (0 to 89, 90 and more) in Germany on 31.12.2025, keyed by the state key of the AGS without the leading zero; the shares of the quota assistant "Bevölkerung Deutschland" (`app/census.py`).
Source: Statistisches Bundesamt (Destatis), [Genesis-Online](https://genesis.destatis.de/datenbank/online/statistic/12411/table/12411-0013), table 12411-0013 "Bevölkerung: Bundesländer, Stichtag, Geschlecht, Altersjahre" (population update based on the 2022 census).
Licence: [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0), © Statistisches Bundesamt (Destatis), 2026.
Built with `tools/build_population_de.py` (downloaded 2026-10-02). New figures appear every June for 31.12. of the year before: load the table's JSON again and run the tool; quotas that exist keep their targets.

`paper_digits.npz`: 5,620 handwritten digits as 8 x 8 counts (x, uint8) with their value (y), for the local digit recognition of paper questionnaires (`app/paper_digits.py`).
Source: "Optical Recognition of Handwritten Digits" (E. Alpaydin, C. Kaynak, 1998), UCI Machine Learning Repository, https://doi.org/10.24432/C50P49, the original 32 x 32 bitmaps (`optdigits-orig.*`, all four parts).
Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); modified: every digit cut to its ink, fitted into 32 x 32 keeping its proportions and counted in 4 x 4 blocks, as the app does with a scanned digit.
Built with `tools/build_paper_digits.py <folder with the unpacked optdigits-orig.* files>` (downloaded 2026-10-11). 98 % of the writer-independent test digits are read right with the training parts as reference.

`geo_ip.bin.gz`: country and, within Germany, federal state of every IP address range (IPv4, and IPv6 by its first 64 bits), for the survey setting "Land und Bundesland aus der IP-Adresse schätzen" (`app/geoip.py`).
Source: [DB-IP](https://db-ip.com) "IP to City Lite", issue 2026-10 (IP Geolocation by DB-IP). Licence: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); modified: reduced to country and German state, neighbouring ranges with the same result merged, cities and coordinates dropped.
Built with `tools/build_geo_ip.py <dbip-city-lite-YYYY-MM.csv.gz>` from a file downloaded by hand (2026-10-02; about 90 MB, 7.8 million rows become 860,000 ranges in 1.8 MB). A new issue appears every month; rebuilding about once a quarter is enough. The server never downloads anything.
The Lite database is less accurate than the paid one: the country is reliable, the state is an estimate (mobile networks, company and university networks, VPNs).

`common_passwords.txt`: the 10,000 most frequent passwords as stems (lower case, digits and characters at the end cut off), the block list of `app/passwords.py`.
Source: the frequency list `passwords.txt` of [zxcvbn](https://github.com/dropbox/zxcvbn), © 2012–2016 Dan Wheeler and Dropbox, Inc.; the list itself goes back to Mark Burnett's ten million passwords, a freely published list (2015).
Licence: MIT (zxcvbn); the full licence text is in the head of the file. Modified: the first 10,000 entries, each normalised as the app compares passwords, without duplicates.
Built with `tools/passwort_sperrliste.py <path to passwords.txt>`.

`THIRD_PARTY_LICENSES.txt`: the Python packages of the lock files with version and licence; written by `tools/third_party_licences.py`, not by hand. The page `/sources` lists every source of data, texts, fonts, icons and libraries.
