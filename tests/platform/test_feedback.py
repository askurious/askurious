from app.extensions import db
from app.models import Feedback


def test_feedback_dialog_submit_and_admin_list(app, client):
    page = client.get("/dashboard/").data.decode()
    assert "data-feedback-open" in page and "Störend" in page and "annoying problem" not in page
    r = client.post("/feedback/new", data={"message": "Speichern springt nach oben.", "severity": "3",
                                            "page": "/surveys/1/editor/"})
    assert r.status_code == 302 and r.headers["Location"] == "/surveys/1/editor/"
    client.post("/feedback/new", data={"message": "", "severity": "1"})                 # empty: not stored
    client.post("/feedback/new", data={"message": "x", "severity": "1", "page": "//evil.example"})
    with app.app_context():
        items = list(db.session.scalars(db.select(Feedback).order_by(Feedback.id)))
        assert [(f.severity, f.page) for f in items] == [(3, "/surveys/1/editor/"), (1, "")]
    admin = client.get("/feedback/").data.decode()                                     # test user is admin
    assert "Speichern springt nach oben." in admin and "Fehler" in admin
    assert '<ul class="feedback-list">' in admin    # not ul.plain: its row rule put the text next to the card head
    assert 'class="badge badge-error feedback-count"' in client.get("/dashboard/").data.decode()
    client.post("/feedback/1/status", data={"status": "done"})
    assert "Speichern springt" not in client.get("/feedback/").data.decode()
    assert "Speichern springt" in client.get("/feedback/?show=all").data.decode()
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/feedback/").status_code == 403
    assert other.post("/feedback/new", data={"message": "Idee", "severity": "1"}).status_code == 302


def test_scale_suggestion_is_filed_with_tag(app, client):
    from app.models import Feedback

    assert "Konstrukt vorschlagen" in client.get("/constructs/").data.decode()
    base = {"title": "NFP-S", "description": "Need for Privacy, 12 Items", "justification": "Privacy-Studien",
            "reference": "Muster et al. (2020)"}
    client.post("/feedback/scale", data={**base, "doi": "keine"})                 # not a DOI: not stored
    r = client.post("/feedback/scale", data={**base, "doi": "https://doi.org/10.1000/xyz123"})
    assert r.status_code == 302
    with app.app_context():
        items = list(db.session.scalars(db.select(Feedback)))
        assert len(items) == 1 and items[0].tag == "Scale suggestion"
        assert "Begründung: Privacy-Studien" in items[0].message and "https://doi.org/10.1000/xyz123" in items[0].message


def test_status_page_names_the_last_deploy(app, client, tmp_path, monkeypatch):
    import app as package
    from app import _deployed_at, status_node

    # the file lies next to the package; the tests run in parallel and share the real folder, so look in a folder
    # of this test's own (writing app/deployed_at.txt gave every other test's footer a deploy time)
    monkeypatch.setattr(package, "__file__", str(tmp_path / "__init__.py"))
    assert _deployed_at() is None                                            # outside a deploy: nothing
    (tmp_path / "deployed_at.txt").write_text("2026-10-02T09:23:00Z\n0bdef88f\n")
    stamp = _deployed_at()
    assert stamp["at"].strftime("%Y-%m-%d %H:%M") == "2026-10-02 11:23" and stamp["id"] == "0bdef88f"   # Berlin, summer time
    (tmp_path / "deployed_at.txt").write_text("2026-10-02T09:23:00Z\n")
    assert _deployed_at()["id"] == ""                                        # older deploys: no commit
    (tmp_path / "deployed_at.txt").write_text("2026-10-02T09:23:00Z\n0bdef88f\n")
    # the deploy stands on the status page now, no longer in the admins' footer (Ralf, 2026-10-11)
    app.config["STATUS_URL"] = "https://status.example.org/"
    page = client.get("/dashboard/").data.decode()
    legal = page[page.index('class="footer-legal"'):page.index("</footer>")]
    assert "deployt am" not in page and "0bdef88f" not in page
    assert '<a class="footer-status" href="https://status.example.org/">' in legal and "Status</a>" in legal
    monkeypatch.setenv("STATUS_CHECKS", "")
    monkeypatch.setenv("STATUS_DB", str(tmp_path / "status.db"))
    status = status_node.create().test_client()
    html = status.get("/", headers={"Accept-Language": "de"}).data.decode()
    # public, without sign-in: date and time only, the commit stands under Administration > Server (audit 2026-10-11,
    # BT3, decision 5a)
    assert "Stand der Software: deployt am 02.10.2026 11:23</p>" in html and "0bdef88f" not in html
    html = status.get("/", headers={"Accept-Language": "en"}).data.decode()
    assert "Software version: deployed on" in html and "0bdef88f" not in html
    server = client.get("/admin/database").data.decode()
    assert "Stand der Software: deployt am 02.10.2026 11:23, Commit <code>0bdef88f</code>" in server
    (tmp_path / "deployed_at.txt").unlink()
    assert "Stand der Software" not in status.get("/", headers={"Accept-Language": "de"}).data.decode()  # outside a deploy
