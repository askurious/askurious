"""The Kurzfragebogen of the interviews evaluated on the results page as the results of a questionnaire
(interview_results.short_results, surveys/_result_item.html): frequencies most frequent first with "keine Angabe"
last, "share (count)", numbers with M and median (feedback 2026-11-05)."""
from app.extensions import db
from app.models import Survey
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def _form(app, sid):
    from app import interview

    with app.app_context():
        short = interview.shown_form(db.session.get(Survey, sid), "short")
        age = next(i for i in short.items if i.type == "text_input")
        gender = next(i for i in short.items if i.type == "single_choice")
        codes = {label: o["code"] for o, label in zip(gender.config["options"], ["weiblich", "männlich", "divers", "keine Angabe"])}
        return age.id, gender.id, codes, gender.code


def test_short_form_counted_like_a_questionnaire(app, client):
    code, sid = make(app, client)
    age, gender, codes, gender_code = _form(app, sid)
    answers = [("57", "keine Angabe"), ("31", "weiblich"), ("44", "weiblich"), ("29", "männlich")]
    for years, label in answers:
        rid = start(app, client, code)
        assert client.post(f"/guide/{code}/interviews/{rid}/form",
                           data={f"i{age}": years, f"i{gender}": codes[label]}).json["ok"]
    trial = start(app, client, code, trial="1")
    client.post(f"/guide/{code}/interviews/{trial}/form", data={f"i{age}": "80", f"i{gender}": codes["divers"]})
    page = client.get(f"/guide/{code}/interviews/analysis").data.decode()
    assert 'id="short-form"' in page and f'id="res-{gender_code}"' in page
    part = page[page.index(f'id="res-{gender_code}"'):]
    part = part[:part.index("</section>")]
    # most frequent first, "keine Angabe" last, share with its count
    assert part.index("weiblich") < part.index("männlich") < part.index("keine Angabe")
    assert "50,0 % (2)" in part and "0,0 % (0)" in part  # "divers" only in the trial interview
    assert "n = 4" in part
    # the age as a number: mean 40.25, median 37.5
    assert "40,25" in page and "37,5" in page
    # the trial interview only with "mit Probeinterviews"
    with_trial = client.get(f"/guide/{code}/interviews/analysis?probe=1").data.decode()
    assert "20,0 % (1)" in with_trial and "n = 5" in with_trial
    # the case overview keeps its column of the interview form (iv_modes)
    assert "<td>Persönliches Interview</td>" in page


def test_no_short_section_without_interviews(app, client):
    code, sid = make(app, client)
    assert 'id="short-form"' not in client.get(f"/guide/{code}/interviews/analysis").data.decode()
