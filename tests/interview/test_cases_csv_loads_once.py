"""The case overview (cases.csv, app/interview_results.py) asks once per file whether the interviewee's own screen was
used (interview_screen.used loads every interview with its transcript), not once more per interview: K + 1 loads of all
K interviews before (performance audit 2026-11-11: 11 s for 40 long interviews); the file stays the same."""
from app import interview_screen
from tests.interview.test_interview_data import make, start


def test_the_screen_use_is_asked_once_whatever_the_number_of_interviews(app, client, monkeypatch):
    code, sid = make(app, client)
    for n in range(4):
        start(app, client, code, label=f"B{n + 1:02d}")
    before = client.get(f"/guide/{code}/interviews/cases.csv?probe=1").data
    calls = []
    real = interview_screen.used

    def counted(survey):
        calls.append(1)
        return real(survey)

    monkeypatch.setattr(interview_screen, "used", counted)
    after = client.get(f"/guide/{code}/interviews/cases.csv?probe=1").data
    assert len(calls) == 1
    assert after == before and after.decode("utf-8-sig").count("\n") >= 5
