"""The data export says its charset once (audit 2026-10-18, INJ-6): "text/csv; charset=utf-8; charset=utf-8"."""


def test_the_data_csv_names_its_charset_once(app, client, survey):
    r = client.get("/surveys/1/export/data.csv")
    assert r.status_code == 200 and r.headers["Content-Type"] == "text/csv; charset=utf-8"
