"""The iOS app of the diary studies (askurious-diary-ios): its own oldest build ("min_app_version_ios"), and its App
Store entry (DIARY_IOS_APP_URL), which alone makes the join page, the privacy page and the privacy concept name it."""
import contextlib

IOS = "https://apps.apple.com/app/id0000000000"


@contextlib.contextmanager
def _config(app, **values):
    old = {k: app.config.get(k) for k in values}
    app.config.update(values)
    try:
        yield
    finally:
        app.config.update(old)


def test_the_server_sends_the_oldest_ios_build_it_accepts(app, client):
    from tests.diary.test_diary_app_contract import _sent_study

    sent = _sent_study(app, client)
    assert sent["min_app_version_ios"] == 0 and sent["min_app_version"] == 0
    with _config(app, DIARY_MIN_IOS_APP_VERSION=9):
        from app import diary
        from app.extensions import db
        from app.models import Enrolment, Survey
        with app.app_context():
            person = db.session.scalar(db.select(Enrolment))
            out = diary.study_json(db.session.get(Survey, 1), person, "de")
            assert out["min_app_version_ios"] == 9 and out["min_app_version"] == 0  # Android keeps its own


def test_the_join_page_names_the_iphone_only_with_an_app_store_entry(app, client):
    from tests.diary.test_diary import _new

    code = _new(app, client)
    page = app.test_client().get(f"/j/{code}").get_data(as_text=True)
    assert "iPhone" not in page and "apps.apple.com" not in page
    with _config(app, DIARY_IOS_APP_URL=IOS):
        page = app.test_client().get(f"/j/{code}").get_data(as_text=True)
    assert "für Android und iPhone" in page and f'href="{IOS}"' in page
    assert f'href="{app.config["DIARY_APP_URL"]}"' in page


def test_privacy_page_and_concept_name_the_ios_app_only_with_an_app_store_entry(app, client):
    from app import privacy_concept

    assert "App Store" not in client.get("/imprint").get_data(as_text=True)
    with app.test_request_context():
        row = next(r for r in privacy_concept.services() if "App Store" in r["words"])
        assert row["state"][0] is False
    with _config(app, DIARY_IOS_APP_URL=IOS):
        page = client.get("/imprint").get_data(as_text=True)
        assert "App Store" in page and "keinen Push-Dienst" in page
        client.post("/account/language", data={"lang": "en"})
        page = client.get("/imprint").get_data(as_text=True)
        assert "App Store" in page and "uses no push service" in page
        with app.test_request_context():
            row = next(r for r in privacy_concept.services() if "App Store" in r["words"])
            assert row["state"][0] is True
