"""The test run of a whole diary study (pre-test 2026-10-16: the preview asked the questions in the order of the flow,
and the repeated short survey only once): one run per part, in the order of a person's study, the short surveys
three times, each end page leading on to the next part."""
import re

from app import diary
from app.extensions import db
from app.models import Survey
from tests.diary.test_diary import _full


def _through(client, url):
    """Go through one test run with checks off; the end page."""
    page = client.get(url, follow_redirects=True).get_data(as_text=True)
    for _ in range(30):
        if "p-end" in page:
            return page
        form = page[page.index('<form method="post"'):]
        hidden = dict(re.findall(r'<input type="hidden" name="(pos|page|page_lang)" value="([^"]*)"', form[:form.index("</form>")]))
        page = client.post("/surveys/1/editor/preview?lang=de", data={"nav": "next", **hidden},
                           follow_redirects=True).get_data(as_text=True)
    raise AssertionError("the run did not end")


def test_the_parts_in_the_order_of_a_study(app, client):
    _full(app, client)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert diary.study_run(survey) == ["entry", "m1", "m1", "m1", "exit"]
        assert diary.study_run(survey, 1) == ["entry", "m1", "exit"]


def test_the_plain_preview_runs_the_whole_study(app, client):
    _full(app, client)
    client.get("/surveys/1/editor/preview?validate=0")
    r = client.get("/surveys/1/editor/preview?lang=de")
    assert r.status_code == 302 and "part=entry" in r.headers["Location"] and "study=0" in r.headers["Location"]
    page = _through(client, r.headers["Location"])
    assert "Teil 1 von 5" in page and "Weiter mit: Kurzbefragung: Moment" in page
    seen = []
    for k in range(1, 5):
        url = re.search(r'href="([^"]*study=%d[^"]*)"' % k, page).group(1).replace("&amp;", "&")
        page = _through(client, url)
        seen.append(page)
        assert f"Teil {k + 1} von 5" in page
        if k < 4:
            assert "Wie alt sind Sie?" not in page
    assert "Alle Teile sind durchlaufen." in seen[-1]


def test_one_part_alone_has_no_study_steps(app, client):
    _full(app, client)
    client.get("/surveys/1/editor/preview?validate=0")
    page = _through(client, "/surveys/1/editor/preview?part=m1")
    assert "Testdurchlauf der ganzen Tagebuchstudie" not in page
