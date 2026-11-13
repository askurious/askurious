"""Interviews kodieren: the downloads of an analysis are found where its category system is (feedback 2026-11-10:
"Kategoriensysteme lassen sich nicht herunterladen"). One menu "Herunterladen" in the views Kategoriensystem (list
and diagram) and Übersicht (transcript_coding/_parts.html, downloads), the category system as CSV
(app/transcript_coding_export.py, build_csv), and one row per analysis on the guide's export page."""
import csv
import io

from tests.interview.test_transcript_coding_review import _setup, _sub


def test_category_system_as_csv(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    _sub(client, base, main, "Müdigkeit", definition="erschöpft; am Abend")
    r = client.get(f"{base}/categories.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert f"kategoriensystem-{code}-{pid}.csv" in r.headers["Content-Disposition"]
    text = r.data.decode("utf-8")
    assert text.startswith("﻿")                                    # Excel reads the umlauts
    rows = list(csv.reader(io.StringIO(text[1:]), delimiter=";"))
    assert rows[0][:4] == ["Nummer", "Ebene", "Kategorie", "Definition"]
    assert rows[1][:3] == ["1", "1", "Belastung"]
    assert rows[2][:4] == ["1.1", "2", "Müdigkeit", "erschöpft; am Abend"]  # the semicolon stays inside its cell
    assert bea.get(f"{base}/categories.csv").status_code == 404           # coders see no exports


def test_download_menu_in_category_system_and_overview(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    for url in (f"{base}?view=categories", f"{base}?view=categories&show=diagram", f"{base}/overview"):
        page = client.get(url).data.decode()
        assert "tc-dl-menu" in page, url
        for f in ("kodierleitfaden.docx", "categories.xlsx", "categories.csv", "categories.qdc", "analysis.qdpx"):
            assert f"{base}/{f}" in page, (url, f)
    # a coder gets the coding guide only
    page = bea.get(f"{base}?view=categories").data.decode()
    assert "tc-dl-menu" in page and f"{base}/kodierleitfaden.docx" in page
    assert "categories.csv" not in page and "categories.qdc" not in page and "analysis.qdpx" not in page
    assert bea.get(f"{base}/kodierleitfaden.docx").status_code == 200


def test_export_page_lists_each_analysis(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    page = client.get(f"/guide/{code}/export").data.decode()
    assert "Kategoriensystem aus dem Leitfaden" in page                  # the guide's own, told apart
    for f in ("kodierleitfaden.docx", "categories.xlsx", "categories.csv", "categories.qdc", "analysis.qdpx"):
        assert f"{base}/{f}" in page, f
