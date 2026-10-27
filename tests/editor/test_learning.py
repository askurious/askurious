"""Lernhilfen (app/learning.py): reasons at decisions, reading aids at statistics, the switch on "Konto" and the
glossary page (concept "Askurious für Studierende und Berufseinsteiger", package B)."""
from app import learning
from app.extensions import db
from app.models import User
from tests.conftest import add


def test_every_text_has_sources_and_is_short():
    for key in learning.REASONS:
        r = learning.reason(key)
        assert r["sources"] and all(isinstance(s, str) and "(" in s for s in r["sources"]), key
        assert r["text"].count(". ") <= 3, key
    for key in learning.TERMS:
        t = learning.term(key)
        assert t["sources"] and t["example"], key


def test_reasons_and_terms_follow_the_switch(app, client, survey):
    add(client, "likert")                                                       # 2
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Warum? Freiwillig, Nachfragen oder Pflicht?" in page and "Warum? Wie viele Stufen hat eine Skala?" in page
    assert "Revilla, M. A., Saris, W. E., &amp; Krosnick, J. A. (2014)" in page
    r = client.post("/account/learning-aids", data={"shown": "1"}, follow_redirects=True).data.decode()
    assert "Lernhilfen ausgeschaltet." in r
    with app.app_context():
        assert db.session.scalar(db.select(User).limit(1)).learning_aids is False
    page = client.get("/surveys/1/editor/items/2").data.decode()
    # the beginners' folds go; a fold that weighs a design choice (the grid) stays for everybody (V28)
    assert "Wie viele Stufen hat eine Skala?" not in page and "Freiwillig, Nachfragen oder Pflicht?" not in page
    assert "Eine Matrix oder einzelne Fragen?" in page
    client.post("/account/learning-aids", data={"shown": "1", "on": "1"})
    assert "learn-why" in client.get("/surveys/1/quality").data.decode()


def test_glossary_page_lists_every_term_with_its_anchor(client):
    page = client.get("/learn/glossary").data.decode()
    # the section of the experimental feature "sem" only for whom it is on (tests/analysis/test_sem_survey.py)
    sem = next(keys for title, _note, keys in learning.SECTIONS if title == learning.SEM_SECTION)
    for key, (symbol, name, *_rest) in learning.TERMS.items():
        assert (f'id="{key}"' in page and name in page) or (key in sem and f'id="{key}"' not in page)
    assert "Leiner, D. J. (2019)" in page


def test_start_list_asks_for_a_pretest_and_an_attention_check(app, client, survey):
    """Package D: before the start, a pretest with five people and, in longer Likert questions, an attention check;
    neither stops the start."""
    from app.models import Item, Survey

    add(client, "likert")                                                       # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "statements": [{"id": f"s{i}"} for i in range(10)]}
        db.session.commit()
        checks = learning.method_checks(db.session.get(Survey, 1), 0, 0)
    assert [c[0] for c in checks] == [False, False, False]   # pretest, attention check, data quality (UI/UX audit 2026-10-16, 3)
    page = client.get("/surveys/1/field").data.decode()
    assert "Pretest: 0 von 5 Testdurchläufen, 0 offene Kommentare" in page and "Aufmerksamkeitsprüfung in den Likert-Fragen" in page
    # one line for the pretest and how far the list is (UI/UX audit 2026-10-07, W8)
    assert "Noch nicht getestet" not in page and "von 4 Punkten erledigt" in page
    assert "Warum? Aufmerksamkeitsprüfung einbauen?" in page


def test_every_key_in_the_templates_exists():
    """why("…") and term("…") in a template name an entry of REASONS or TERMS (a missing one fails only on the page)."""
    import re
    from pathlib import Path

    used = {"why": set(), "term": set()}
    for path in (Path(__file__).resolve().parents[2] / "app" / "templates").rglob("*.html"):
        for kind, key in re.findall(r'\b(why|term)\("([a-z_]+)"', path.read_text(encoding="utf-8")):
            used[kind].add(key)
    assert used["why"] - set(learning.REASONS) == set() and used["term"] - set(learning.TERMS) == set()
    assert {"conjoint_tasks", "none_option", "conjoint_kind", "hb_or_logit", "holdout", "segments_k",
            "segments_covariates", "segments_describe", "geo_level", "geo_fallback"} <= used["why"]


def test_choice_tasks_and_location_explain_their_decisions(app, client, survey):
    """Concept "Lernhilfen für die neuen Funktionen" (L1, L3): the editors of Conjoint and Standort."""
    add(client, "conjoint")                                                     # 2
    add(client, "geolocation")                                                  # 3
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Warum? Wie viele Aufgaben und Alternativen?" in page and "Warum? „Keine davon“ anbieten?" in page
    assert "Haaijer, R., Kamakura, W. A., &amp; Wedel, M. (2001)" in page
    page = client.get("/surveys/1/editor/items/3").data.decode()
    assert "Warum? Wie genau speichern?" in page and "Warum? Ersatz aus der IP-Adresse?" in page


def test_start_list_asks_for_a_holdout_in_choice_tasks(app, client, survey):
    """L11: a Conjoint without a holdout task gets a hint before the start, which does not stop it."""
    from app.models import Item, Survey

    add(client, "conjoint")                                                     # 2
    with app.app_context():
        checks = learning.method_checks(db.session.get(Survey, 1), 0, 0)
        code = db.session.get(Item, 2).code
    assert (False, f"Holdout-Aufgabe in „{code}“") in [(c[0], c[1]) for c in checks]
    assert [c[4] for c in checks if c[1].startswith("Holdout")] == ["holdout"]


def test_glossary_is_sorted_into_sections_and_covers_every_term():
    """L8: every term in exactly one section; the page shows the sections as headings."""
    keys = [k for _title, _note, ks in learning.SECTIONS for k in ks]
    # no fixed count: sorted(keys) == sorted(TERMS) already says every term is in a section (a count only conflicted
    # between parallel branches that each add terms)
    assert sorted(keys) == sorted(learning.TERMS) and len(keys) == len(set(keys))
    titles = [s[0] for s in learning.glossary()]
    assert titles.index("Wahlmodelle") < titles.index("Segmente") and "IAT und Belohnungsaufschub" in titles
    assert set(learning.TERMS_EN) == set(learning.TERMS) and set(learning.REASONS_EN) == set(learning.REASONS)
    assert len(learning.SECTIONS_EN) == len(learning.SECTIONS) and set(learning.REASON_PLACES) == set(learning.REASONS)


def test_glossary_page_shows_the_sections(client):
    page = client.get("/learn/glossary").data.decode()
    assert "<h2>Wahlmodelle</h2>" in page and "<h2>Segmente</h2>" in page and 'id="rhat"' in page
    assert "Vehtari, A., Gelman, A." in page and "R̂ und ESS prüfen die Rechnung" in page


def test_decisions_are_explained_to_everybody_and_terms_open_in_place(app, client, survey):
    """UI/UX audit 2026-10-05, V27 and V28: a fold that weighs a methodological choice is there without the learning
    aids; the "?" of a term is a button with its explanation (no new tab), n and M only once per page."""
    from app import learning

    client.post("/account/learning-aids", data={"shown": "1"})                     # learning aids off
    client.post("/surveys/1/flow/quotas/add")
    page = client.get("/surveys/1/flow/quotas/").data.decode()
    assert "Warum? Harte oder weiche Quote?" in page
    client.post("/account/learning-aids", data={"shown": "1", "on": "1"})
    with app.test_request_context():
        from flask import render_template_string
        from flask_login import login_user

        login_user(db.session.scalar(db.select(User).limit(1)))
        html = render_template_string('{% from "_learn.html" import term %}{{ term("n") }} {{ term("n") }} {{ term("sd") }} {{ term("d") }}')
    assert html.count("data-term ") == 3 and 'target="_blank"' not in html and 'data-term-href="/' in html
    assert learning.show_reason("quota_hard")


def test_questionnaire_flow_and_comparisons_explain_more_decisions(app, client, survey):
    """Concept "Lernhilfen: Erweiterungen und Verbesserungen" (E1, E2): slider, grid, drop-down list; the test of the
    group comparisons only once per page."""
    add(client, "slider")                                                       # 2
    add(client, "likert")                                                       # 3
    add(client, "single_choice")                                                # 4
    assert "Warum? Schieberegler oder Stufen?" in client.get("/surveys/1/editor/items/2").data.decode()
    assert "Warum? Eine Matrix oder einzelne Fragen?" in client.get("/surveys/1/editor/items/3").data.decode()
    page = client.get("/surveys/1/editor/items/4").data.decode()
    assert "Warum? Dropdown-Liste oder Auswahlknöpfe?" in page and "Couper, M. P., Tourangeau, R." in page
    flow = client.get("/surveys/1/flow/").data.decode()
    assert "Warum? Wie lang darf der Fragebogen sein?" in flow and "Warum? Warum zufällig zuteilen?" in flow
    with app.test_request_context():
        assert learning.show_reason("which_test") and not learning.show_reason("which_test")


def test_start_list_names_many_required_questions_and_a_long_questionnaire(app, client, survey, monkeypatch):
    """E5: more than half of the questions required, an estimate over 20 minutes; neither stops the start."""
    from app import duration
    from app.models import Item, Survey

    for _ in range(4):
        add(client, "text_input")                                               # 2 to 5
    with app.app_context():
        for i in (2, 3, 4):
            db.session.get(Item, i).answer_mode = "required"
        db.session.commit()
        monkeypatch.setattr(duration, "estimate", lambda s, lang=None: {"typical": 25 * 60})
        checks = learning.method_checks(db.session.get(Survey, 1), 5, 0)
    found = {c[4]: c for c in checks if not c[0]}
    assert "3 von 4 Fragen sind Pflicht" in found["answer_mode"][2] and found["length"][2] == "geschätzt etwa 25 Minuten"


def test_glossary_lists_the_decisions_with_their_place(client):
    """E8: every fold "Warum?" on the glossary page, with where it stands and its sources in the list at the end."""
    page = client.get("/learn/glossary").data.decode()
    assert "<h2>Entscheidungen</h2>" in page and 'id="warum-which_test"' in page and "Ablauf, Kennzahlen" in page
    # the folds of an experimental feature only for whom it is on (learning.REASON_FEATURES)
    assert page.count('id="warum-') == len(set(learning.REASONS) - set(learning.REASON_FEATURES)) and "Delacre, M., Lakens, D." in page
    assert 'id="warum-interview_screen"' in page  # the participant screen is regular since 2026-10-22


def test_every_statistic_in_a_results_header_has_its_question_mark():
    """E8: a table header of the researchers' results that is a statistic of the glossary goes through term(), so a
    new table cannot drop the explanation (n, M, SD, Md get theirs once per page anyway; dashboards have none)."""
    import re
    from pathlib import Path

    symbols = {v[0] for v in learning.TERMS.values()} | {v[0] for v in learning.TERMS_EN.values()}
    symbols -= {learning.TERMS[k][0] for k in learning.SIMPLE_TERMS} | {learning.TERMS_EN[k][0] for k in learning.SIMPLE_TERMS}
    raw = []
    for path in (Path(__file__).resolve().parents[2] / "app" / "templates" / "surveys").rglob("*.html"):
        for m in re.finditer(r"<th(?:\s[^>]*)?>([^<{]+)</th>", path.read_text(encoding="utf-8")):
            if m.group(1).strip() in symbols:
                raw.append(f"{path.name}: {m.group(1).strip()}")
    assert not raw, raw


def test_the_box_has_the_example_and_a_fold_can_be_hidden(app, client, survey):
    """E6: the "?" carries the example of the glossary. E7: „Nicht mehr zeigen“ hides one fold for this person,
    „Wieder zeigen“ on Konto brings all back; other folds stay."""
    add(client, "likert")                                                       # 2
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'data-learn-hide="likert_grid"' in page and "Nicht mehr zeigen" in page
    assert client.post("/learn/why/likert_grid/hide").get_json() == {"ok": True}
    assert client.post("/learn/why/unbekannt/hide").status_code == 404
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Eine Matrix oder einzelne Fragen?" not in page and "Wie viele Stufen hat eine Skala?" in page
    konto = client.get("/account/appearance").data.decode()
    assert "1 Erklärungen „Warum?“ ausgeblendet." in konto or "Erklärungen „Warum?“ ausgeblendet" in konto
    client.post("/learn/why/again")
    assert "Eine Matrix oder einzelne Fragen?" in client.get("/surveys/1/editor/items/2").data.decode()
    with app.test_request_context():
        from flask import render_template_string
        from flask_login import login_user

        login_user(db.session.scalar(db.select(User).limit(1)))
        html = render_template_string('{% from "_learn.html" import term %}{{ term("g") }}')
    assert 'data-term-example="δ* = 0,41 [0,12; 0,70]: ein kleiner bis mittlerer Unterschied, aber unsicher."' in html


def test_every_term_and_reason_stands_somewhere():
    """Concept "Lernhilfen: dritte Runde", F7: no entry of the glossary is left without its "?", no fold "Warum?"
    without a place (in a template or in the start list of method_checks)."""
    import inspect
    import re
    from pathlib import Path

    used = {"why": set(), "term": set()}
    for path in (Path(__file__).resolve().parents[2] / "app" / "templates").rglob("*.html"):
        for kind, key in re.findall(r'\b(why|term)\("([a-z_0-9]+)"', path.read_text(encoding="utf-8")):
            used[kind].add(key)
    checks = set(re.findall(r'"([a-z_]+)"\)\)', inspect.getsource(learning.method_checks)))
    assert set(learning.TERMS) - used["term"] == set()
    assert set(learning.REASONS) - used["why"] - checks == set()


def test_third_round_folds_terms_and_glossary_search(app, client, survey):
    """F1 to F6: the new folds in the editors, the terms in the glossary sections, the search field."""
    add(client, "card_sort")                                                    # 2
    add(client, "q_sort")                                                       # 3
    add(client, "personal_code")                                                # 4
    assert "Warum? Offen, geschlossen oder gemischt?" in client.get("/surveys/1/editor/items/2").data.decode()
    assert "Warum? Warum eine erzwungene Verteilung?" in client.get("/surveys/1/editor/items/3").data.decode()
    page = client.get("/surveys/1/editor/items/4").data.decode()
    assert "Warum? Warum ein Code statt Name?" in page and "Yurek, L. A." in page
    page = client.get("/lernen/glossar", follow_redirects=True).data.decode()
    assert "data-learn-search" in page and "<h2>Planung und Power</h2>" in page and 'id="kripp"' in page
    assert "Nosek, B. A., Ebersole" in page and "Dillman, D. A." in page


def test_fourth_round_folds_terms_start_list_and_method_sentence(app, client, survey):
    """Concept "Lernhilfen: vierte Runde": the folds at the voice message, the seal chain, paper sheets and the
    construct box (G1, G2), the diary section of the glossary only with the diary study (G4), the start list on a
    required voice message (G6) and the sentence on voice messages in the method section (G7)."""
    from app import methods
    from app.models import Item, Survey
    from tests.conftest import experiments_on

    experiments_on(client, "voice")
    add(client, "voice")                                                        # 2
    page = client.get("/surveys/1/editor/items/2").data.decode()
    assert "Warum? Sprechen statt tippen?" in page and "Lenzner, T., &amp; Höhne, J. K. (2022)" in page
    assert "Warum? Was belegt die Siegelkette?" in client.get("/surveys/1").data.decode()
    assert "Warum? Warum zwei Personen prüfen lassen?" in client.get("/surveys/1/field").data.decode()
    assert "Warum? Warum die Skala unverändert lassen?" in client.get("/constructs/box?survey=1").data.decode()
    with app.app_context():
        item = db.session.get(Item, 2)
        item.code, item.answer_mode = "stimme", "required"
        item.config = {**item.config, "text_fallback": False}
        db.session.commit()
        survey_ = db.session.get(Survey, 1)
        texts = [c[1] for c in learning.method_checks(survey_, 5, 0) if c[4] == "voice"]
        assert texts == ["Sprachnachricht „stimme“ nicht als Pflicht"]
        item.config = {**item.config, "text_fallback": True}
        db.session.commit()
        assert not [c for c in learning.method_checks(survey_, 5, 0) if c[4] == "voice"]
    v = {"code": "stimme", "n": 10, "spoke": 7, "typing": True}
    assert methods.voice_de(v) == ("Die Frage „stimme“ wurde als Sprachnachricht im Browser beantwortet, wahlweise auch "
                                   "getippt; 7 von 10 Personen sprachen ihre Antwort ein, 3 tippten sie.")
    assert methods.voice_en({**v, "typing": False}).endswith("7 of 10 spoke their answer")
    page = client.get("/learn/glossary").data.decode()
    assert 'id="zeta"' in page and 'id="bt_worth"' in page and 'id="icc"' in page  # diary for everyone since 2026-10-27
    assert "<h2>Tagebuchstudie</h2>" in page and 'id="compliance"' in page


def test_fifth_round_how_folds_sections_decision_groups_and_transcript(app, client, survey):
    """Concept "Lernhilfen: fünfte Runde": "So funktioniert es" in six editors (H1), the split glossary sections (H2),
    the decisions in the groups of the tabs (H3) and the fold at the transcript (H4)."""
    from tests.conftest import experiments_on

    experiments_on(client, "iat", "discounting")
    expected = {"drill_down": "So funktioniert es: Tabelle und Listen", "cloze": "So funktioniert es: Lücken im Text",
                "iat": "So funktioniert es: Kategorien, Begriffe und Runden",
                "discounting": "So funktioniert es: Jetzt oder später",
                "association": "So funktioniert es: Begriffe nennen und bewerten",
                "image_mark": "So funktioniert es: Markieren oder Bereiche"}
    for n, (type_, title) in enumerate(expected.items(), start=2):
        add(client, type_)
        page = client.get(f"/surveys/1/editor/items/{n}").data.decode()
        assert title in page, type_
    # open while still empty: the drill-down without entries, the cloze without text
    assert '<details class="learn-why learn-how" open>' in client.get("/surveys/1/editor/items/2").data.decode()
    assert set(learning.REASON_PHASES) <= set(learning.REASONS)
    titles = [title for title, _note, _keys in learning.SECTIONS]
    assert titles.index("Paarvergleich und Conjoint-Arten") < titles.index("Wahlmodelle")
    assert {"SEM: Schätzung und Passung", "PLS", "Längsschnitt und Gruppenvergleich"} <= set(titles)
    page = client.get("/learn/glossary").data.decode()
    assert "<h2>PLS</h2>" in page and "<h2>Paarvergleich und Conjoint-Arten</h2>" in page
    decisions = page.split('id="entscheidungen"')[1]
    assert decisions.index("Planen</h3>") < decisions.index("Erheben</h3>") < decisions.index("Analyse</h3>")
    assert decisions.index("Warum das Transkript gegenlesen?") > decisions.index("Analyse</h3>")
    assert learning.reason("transcript_check")["sources"]



def test_sem_two_steps_versions_and_new_statistics_have_their_aids():
    """SEM 2026-10-26: the two steps, leaving a statement out and the model versions explain their decision (results,
    phase Analyse); rho_A, R², total effect, weight w and BIC weight are in the glossary, in German and English."""
    for key in ("sem_two_steps", "sem_drop", "sem_versions"):
        assert key in learning.REASONS_EN and learning.REASON_PHASES[key] == 2 and key in learning.REASON_PLACES
    assert "sem_drop" in learning.ONCE_REASONS  # one fold under many statements of a table
    listed = {k for _title, _note, keys in learning.SECTIONS for k in keys}
    for key in ("rho_a", "r2", "total_effect", "outer_weight", "bic_weight"):
        assert key in listed and key in learning.TERMS_EN
    assert "Anderson" in learning.reason("sem_two_steps")["sources"][0]
