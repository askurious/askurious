"""Interviews kodieren, double coding (app/transcript_coding_double.py): Cohen's kappa against hand calculations, the
word table and the matching of passages, the assignment, blind coding until everyone is done, the comparison with its
decisions and the consensual coding, the agreement page and its CSV."""
import pytest

from app import transcript_coding_double as double
from app.extensions import db
from app.models import CodingProject, Response
from tests.interview.test_transcript_coding_review import BEA, TEXT, _code, _setup, _sub
from tests.interview.test_transcript_coding import _person


# --- the arithmetic, by hand --------------------------------------------------------------------------------------


def test_kappa_by_hand():
    # Wikipedia's (and many textbooks') example: 20 both, 5 only A, 10 only B, 15 neither
    # p_o = 35/50 = 0.7; p_e = (25*30 + 25*20) / 50² = 0.5; kappa = (0.7 - 0.5) / 0.5 = 0.4
    p_o, kappa = double.table_kappa(20, 5, 10, 15)
    assert p_o == pytest.approx(0.7) and kappa == pytest.approx(0.4)
    assert double.table_kappa(5, 0, 0, 5) == (1.0, 1.0)                              # full agreement
    assert double.table_kappa(2, 2, 2, 2)[1] == pytest.approx(0.0)                   # as by chance
    assert double.table_kappa(0, 5, 5, 0) == (0.0, -1.0)                             # complete disagreement
    assert double.table_kappa(0, 0, 0, 10) == (1.0, None)                            # nobody coded: no kappa
    assert double.table_kappa(0, 0, 0, 0) == (None, None)
    # Landis & Koch (1977): < 0 poor, 0.00-0.20 slight, 0.21-0.40 fair, 0.41-0.60 moderate, 0.61-0.80 substantial,
    # 0.81-1.00 almost perfect
    assert [double.reading(k) for k in (-0.1, 0.0, 0.2, 0.4, 0.41, 0.6, 0.8, 0.85)] == [
        "schlechter als Zufall", "sehr gering", "sehr gering", "gering", "mittelmäßig", "mittelmäßig", "beträchtlich",
        "fast vollkommen"]


def test_word_table_and_matching():
    text = "eins zwei drei vier fünf"
    a = [{"id": "a1", "cat": 1, "start": 0, "end": 9}]                               # eins zwei
    b = [{"id": "b1", "cat": 1, "start": 5, "end": 14}, {"id": "b2", "cat": 2, "start": 15, "end": 19}]  # zwei drei; vier
    assert double.word_table(text, a, b, 1) == [1, 1, 1, 2]                          # zwei both, eins A, drei B
    assert double.word_table(text, a, b, 2) == [0, 0, 1, 4]
    diffs = double.differences(a, b, threshold=0.9)
    assert [d["kind"] for d in diffs] == ["other", "only_b"]                         # overlap 4/14 < 0.9 → not matched
    assert [d["kind"] for d in double.differences(a, b, threshold=0.2)] == ["match", "only_b"]
    assert double.passage_agreement(a, b, 1, 0.2) == (2, 2)


# --- the pages ------------------------------------------------------------------------------------------------------


def _assign(app, client, base, rid, keys):
    client.post(f"{base}/i/{rid}/double", data={"coder": keys})


def test_blind_until_done_then_compare_and_agreement(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    cat = _sub(client, base, main, "Erschöpfung")
    with app.app_context():
        from app.models import User

        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")).id
        bea_id = db.session.scalar(db.select(User).filter_by(orcid=BEA)).id
    carl, carl_id = _person(app, "0000-0001-5109-3700", "Carl")
    client.post(f"{base}/members", data={"kind": "person", "person": "0000-0001-5109-3700"})
    _assign(app, client, base, rid, [f"u:{bea_id}", f"u:{carl_id}"])
    with app.app_context():
        entry = double.assignment(db.session.get(CodingProject, pid), rid)
        assert entry["coders"] == [f"u:{bea_id}", f"u:{carl_id}"] and entry["done"] == []
    assert "Doppelkodierung" in client.get(base).data.decode()
    _code(bea, base, rid, "Abends", cat, 29)
    _code(carl, base, rid, "Abends", cat, 29)
    _code(carl, base, rid, "Manchmal", cat)
    # blind: each sees only their own passage, the team sees all
    import json
    import re

    def shown(who):
        page = who.get(f"{base}/i/{rid}").data.decode()
        return json.loads(re.search(r'id="tc-data">(.*?)</script>', page, re.S).group(1))["codings"]
    assert len(shown(bea)) == 1 and len(shown(carl)) == 2 and len(shown(client)) == 3
    assert bea.get(f"{base}/i/{rid}/compare").status_code == 404                    # not before everyone is done
    assert "Keine Stellen mehr zum Prüfen" in bea.get(f"{base}/check").data.decode()  # no check of a blind interview
    bea.post(f"{base}/i/{rid}/done")
    assert len(shown(bea)) == 1                                                       # Carl is not done yet
    carl.post(f"{base}/i/{rid}/done")
    assert len(shown(bea)) == 3
    page = bea.get(f"{base}/i/{rid}/compare").data.decode()
    assert "übereinstimmend" in page and "nur B" in page
    # a decision for the passage only Carl coded: the consensual coding takes it, the single codings stay
    with app.app_context():
        row = db.session.get(Response, rid)
        project = db.session.get(CodingProject, pid)
        a_items = double.coder_codings(row, project, f"u:{bea_id}")
        b_items = double.coder_codings(row, project, f"u:{carl_id}")
        only_b = next(d for d in double.differences(a_items, b_items) if d["kind"] == "only_b")
    bea.post(f"{base}/i/{rid}/compare", data={"diff": only_b["id"], "choice": "b", "comment": "passt"})
    with app.app_context():
        row = db.session.get(Response, rid)
        project = db.session.get(CodingProject, pid)
        diffs = double.differences(double.coder_codings(row, project, f"u:{bea_id}"), double.coder_codings(row, project, f"u:{carl_id}"))
        assert len(double.consensus(row, project, diffs)) == 2
        assert len([c for c in row.interview["codings"] if c["project"] == pid]) == 3       # the single codings stay
    # the agreement: 3 passages, so a figure; CSV with the words of the hand table
    page = client.get(f"{base}/agreement").data.decode()
    assert "1 abgeschlossene Doppelkodierungen" in page or "abgeschlossene Doppelkodierung" in page
    csv_text = client.get(f"{base}/agreement?format=csv").data.decode("utf-8-sig")
    words = len(double.WORD.findall(TEXT))
    line = next(line for line in csv_text.splitlines() if line.startswith("Erschöpfung;"))
    both, only_a, only_b_words, none = map(int, line.split(";")[1:5])
    assert (both, only_a) == (5, 0) and only_b_words == 1 and both + only_a + only_b_words + none == words
    assert me


def test_assignment_needs_two_and_only_the_team(app, client):
    code, pid, rid, base, main, bea = _setup(app, client)
    with app.app_context():
        from app.models import User

        bea_id = db.session.scalar(db.select(User).filter_by(orcid=BEA)).id
    _assign(app, client, base, rid, [f"u:{bea_id}"])
    with app.app_context():
        assert double.assignment(db.session.get(CodingProject, pid), rid) is None
    assert bea.post(f"{base}/i/{rid}/double", data={"coder": [f"u:{bea_id}", "u:1"]}).status_code == 404
    assert bea.post(f"{base}/i/{rid}/done").status_code == 404                      # not assigned
