"""Demographic standard questions of the RatSWD for the construct toolbox (concept "Demografie-Standards", 2026-10-15).

The items are the minimal versions and basic items of the "Standardfragenkatalog zur Erhebung soziodemographischer
und krisenbezogener Variablen" (Rat für Sozial- und Wirtschaftsdaten, 2023; CC BY 4.0), developed with the
KonsortSWD sub-project on harmonised variables and pretested. Wording, answer codes and filters as in the catalogue:
"Weiß nicht" is -1 and "Keine Angabe" -2 there, both count as missing. Household net income has no minimal version;
the shortest form is the closed question with unfolding brackets HNE_3 to HNE_6 (after NEPS).

Each item is a construct of its own, so it can be left out or moved like any other question; app/constructs.py adds
them to SEEDS. They start with status "review" until the wording is checked against the PDF (as gdms). The English
texts are working translations for Askurious, not part of the catalogue.

They are standard questions, not scales: they stay out of the pooled analysis of the constructs (licence.md section
4; concept decision 7a). NOT_POOLED names them; app/constructs.pooled() asks it."""

CITATION = ("Rat für Sozial- und Wirtschaftsdaten. (2023). Standardfragenkatalog zur Erhebung soziodemographischer und "
            "krisenbezogener Variablen (2. Aufl.; RatSWD Output Series, 7. Berufungsperiode Nr. 4b). RatSWD.")
DOI = "10.17620/02671.76"
LICENCE = ("Lizenz: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), frei nutzbar mit Quellenangabe; Änderungen "
           "kennzeichnen (das Codebuch nennt Abweichungen vom Wortlaut). „Weiß nicht“ (−1) und „Keine Angabe“ (−2) "
           "zählen als fehlend. Standardfrage: nicht Teil der gemeinsamen Auswertung der Konstrukte (Pool). Englische "
           "Fassung: inoffizielle Arbeitsübersetzung.")
LICENCE_EN = ("Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), free to use with attribution; mark "
              "changes (the codebook lists deviations from the wording). “Don't know” (−1) and “No answer” (−2) count "
              "as missing. Standard question: not part of the pooled analysis of the constructs (pool). English "
              "version: unofficial working translation.")
# a note on the card where an item can identify people in small samples, or touches special categories (Art. 9 GDPR)
REIDENT = (" Geburtsmonat und -jahr machen Personen zusammen mit Bundesland, Haushaltsgröße oder Beruf in kleinen "
           "Stichproben erkennbar; veröffentlichen Sie dann nur das Geburtsjahr oder Altersgruppen.")
REIDENT_EN = (" Month and year of birth, together with federal state, household size or occupation, can make people "
              "recognisable in small samples; then publish only the year of birth or age groups.")
ART9 = (" Das Geburtsland der Eltern kann auf die ethnische Herkunft hindeuten (besondere Kategorie nach Art. 9 DSGVO): "
        "Ihre Einwilligungserklärung muss das abdecken.")
ART9_EN = (" The parents' country of birth can point to ethnic origin (special category under Art. 9 GDPR): your "
           "consent must cover it.")

DK, NA = "-1", "-2"  # the catalogue's codes for "Weiß nicht" and "Keine Angabe"
_DK = (DK, "Weiß nicht", "Don't know")
_NA = (NA, "Keine Angabe", "No answer")
_YES_NO = [("1", "Ja", "Yes"), ("2", "Nein", "No"), _DK, _NA]


def _hint(question, hint):
    """The question with the catalogue's note to the respondent below it, smaller."""
    return f"{question}\n<small>{hint}</small>" if hint else question


def choice(code, question, options, text_options=()):
    """A single choice template. question: (German, English) or (German, English, hint de, hint en); options:
    [(code, German, English)]; codes below zero count as missing; text_options: codes with a text field."""
    de, en, *hints = question
    hint_de, hint_en = (hints + ["", ""])[:2]
    opts = [{"id": f"o{n}", "code": c, **({"missing": True} if c.startswith("-") else {}),
             **({"text": True} if c in text_options else {})}
            for n, (c, _de, _en) in enumerate(options, 1)]
    texts = {"de": {"question": _hint(de, hint_de), "options": {f"o{n}": d for n, (_c, d, _e) in enumerate(options, 1)}},
             "en": {"question": _hint(en, hint_en), "options": {f"o{n}": e for n, (_c, _d, e) in enumerate(options, 1)}}}
    return {"type": "single_choice", "code": code, "config": {"options": opts, "layout": "vertical"}, "texts": texts}


def _alt1():
    texts = {"de": {"question": "Die Ergebnisse dieser Befragung werden auch für unterschiedliche Altersgruppen "
                                "ausgewertet. Bitte nennen Sie dazu den Monat und das Jahr, in dem Sie geboren wurden."},
             "en": {"question": "The results of this survey will also be analysed for different age groups. For this, "
                                "please state the month and the year in which you were born."}}
    return {"type": "date_time", "code": "ALT_1", "texts": texts,
            "config": {"parts": "month", "span": "past", "year_min": None, "year_max": None, "minute_step": 5,
                       "dont_know": False}}


def _ges1():
    return choice("GES_1", ("Welches Geschlecht haben Sie?", "What is your sex?"),
                  [("1", "männlich", "male"), ("2", "weiblich", "female"), ("3", "divers", "diverse")])


def _bil1():
    return choice("BIL_1", (
        "Welchen höchsten allgemeinbildenden Schulabschluss haben Sie?",
        "What is your highest general school-leaving qualification?",
        "Hinweis: Personen mit ausländischen Bildungsabschlüssen sollen sich den Kategorien 2, 3 oder 4 zuordnen.",
        "Note: people with qualifications from abroad should choose category 2, 3 or 4."), [
        ("1", "(Noch) kein Schulabschluss", "No school-leaving qualification (yet)"),
        ("2", "Hauptschulabschluss (Volksschulabschluss) oder gleichwertiger Abschluss, Polytechnische Oberschule der "
              "DDR mit Abschluss der 8. oder 9. Klasse",
         "Lower secondary school certificate (Hauptschulabschluss, Volksschulabschluss) or equivalent, GDR "
         "polytechnic secondary school completed after grade 8 or 9"),
        ("3", "Realschulabschluss (Mittlere Reife) oder gleichwertiger Abschluss, Polytechnische Oberschule der DDR mit "
              "Abschluss der 10. Klasse",
         "Intermediate school certificate (Realschulabschluss, Mittlere Reife) or equivalent, GDR polytechnic "
         "secondary school completed after grade 10"),
        ("4", "(Fach-)Hochschulreife/Abitur (auch EOS oder EOS mit Lehre)",
         "Entrance qualification for universities of applied sciences or universities (Fachhochschulreife, Abitur; "
         "also EOS or EOS with apprenticeship)"),
        _DK])


def _bil4():
    return choice("BIL_4", (
        "Welchen höchsten Ausbildungsabschluss bzw. Hochschulabschluss haben Sie?",
        "What is your highest vocational or university qualification?",
        "Hinweis: Personen mit ausländischen Bildungsabschlüssen sollen sich den Kategorien 2 bis 8 zuordnen.",
        "Note: people with qualifications from abroad should choose one of the categories 2 to 8."), [
        ("1", "(Noch) kein beruflicher Abschluss", "No vocational qualification (yet)"),
        ("2", "Abschluss einer einjährigen Ausbildung an einer Schule des Gesundheitswesens, Vorbereitungsdienst für den "
              "mittleren Dienst in der öffentlichen Verwaltung",
         "One-year training at a school for health care professions, preparatory service for the intermediate "
         "civil service"),
        ("3", "Abschluss einer zwei- bis dreijährigen Ausbildung an einer Schule des Gesundheitswesens",
         "Two- to three-year training at a school for health care professions"),
        ("4", "Abgeschlossene Berufsausbildung (Lehre) oder berufsqualifizierender Abschluss einer Berufsfachschule oder "
              "Kollegschule",
         "Completed vocational training (apprenticeship) or vocational qualification from a full-time vocational "
         "school (Berufsfachschule, Kollegschule)"),
        ("5", "Abschluss einer Ausbildung zum Erzieher/zur Erzieherin, einer Fach-, Meister- oder Technikerschule, "
              "Verwaltungs- und Wirtschaftsakademie oder Fachakademie, auch Fachschule der DDR",
         "Qualification as a nursery teacher, from a technical college, master craftsman or technician school, "
         "academy of administration and economics or Fachakademie, also a GDR Fachschule"),
        ("6", "Diplom einer Berufsakademie oder FH, Bachelor",
         "Diploma from a university of cooperative education or university of applied sciences, bachelor's degree"),
        ("7", "Diplom einer Universität, Master, Magister, Staatsexamen",
         "University diploma, master's degree, Magister, state examination"),
        ("8", "Promotion", "Doctorate"),
        _DK])


ERW_EMPLOYED = ("1", "2")                           # main job, full or part time
ERW_NOT_EMPLOYED = ("3", "4", "5", "6", "7", "8", "9")  # filter of ERW_2
# filter of BER_1 and STE_1: their wording also asks those no longer employed about their former main job ("Wenn Sie
# nicht mehr erwerbstätig sind …", "haben oder hatten"), the catalogue filters "heute oder früher erwerbstätig". The
# bundle has no question on earlier employment, so all but pupils/students (3) and volunteers (8) get them: retired
# (4), permanently unable to work (5), housework and care (6), unemployed (7) and other reasons (9); pre-test 2026-10-20
ERW_EVER_EMPLOYED = ("1", "2", "4", "5", "6", "7", "9")


def _erw1():
    return choice("ERW_1", (
        "Bitte geben Sie an, was am ehesten auf Ihre jetzige Situation zutrifft.",
        "Please state what best describes your current situation.",
        "Hinweis: Nur eine Nennung möglich.", "Note: only one answer possible."), [
        ("1", "Hauptberuflich erwerbstätig, Vollzeit", "Employed in main job, full time"),
        ("2", "Hauptberuflich erwerbstätig, Teilzeit", "Employed in main job, part time"),
        ("3", "Schüler/-in, Student/-in", "Pupil, student"),
        ("4", "Altersrente, Pensionär/-in", "Old-age pension, retired civil servant"),
        ("5", "Dauerhaft erwerbsunfähig", "Permanently unable to work"),
        ("6", "Hausarbeit, Betreuung von Kindern oder anderen Personen (Hausfrau / Hausmann)",
         "Housework, caring for children or other people (housewife / househusband)"),
        ("7", "Zurzeit arbeitslos / auf Arbeitssuche", "Currently unemployed / looking for work"),
        ("8", "Freiwilliger Wehrdienst, Bundesfreiwilligendienst, FSJ, FÖJ",
         "Voluntary military service, Federal Volunteer Service, voluntary social or ecological year (FSJ, FÖJ)"),
        ("9", "Aus anderen Gründen aktuell nicht (hauptberuflich) erwerbstätig, und zwar:",
         "Currently not employed (in a main job) for other reasons, namely:"),
        _DK], text_options=("9",))


def _erw2():
    return choice("ERW_2", ("Gehen Sie nebenher einer bezahlten Erwerbstätigkeit (Minijob, Aushilfstätigkeit) nach?",
                            "Do you have a paid side job (mini-job, casual work)?"),
                  [("1", "Ja", "Yes"), ("2", "Nein", "No"), _DK])


def _ber1():
    hint_de = ("Hinweis: Bitte geben Sie die genaue Tätigkeitsbezeichnung an, also z. B. nicht „kaufmännische "
               "Angestellte“, sondern: „Speditionskauffrau“, nicht „Arbeiter“, sondern: „Maschinenschlosser“. Wenn Sie "
               "Beamter sind, geben Sie bitte Ihre Amtsbezeichnung an, z. B. „Polizeimeister“, oder „Studienrat“. Wenn "
               "Sie Auszubildender sind, geben Sie bitte Ihren Ausbildungsberuf an.")
    hint_en = ("Note: please give the exact job title, e.g. not “clerk” but “freight forwarding clerk”, not “worker” but "
               "“machine fitter”. If you are a civil servant, please give your official title, e.g. “police sergeant” "
               "or “secondary school teacher”. If you are an apprentice, please give the occupation you are training for.")
    texts = {"de": {"question": _hint("Welche berufliche Tätigkeit üben Sie derzeit hauptsächlich aus? Wenn Sie nicht "
                                      "mehr erwerbstätig sind, welche Tätigkeit haben Sie bei Ihrer früheren "
                                      "hauptsächlichen Erwerbstätigkeit zuletzt ausgeübt?", hint_de),
                    "prefix": "", "suffix": "", "validation_message": ""},
             "en": {"question": _hint("What is your main occupation at present? If you are no longer employed, what "
                                      "occupation did you last have in your former main employment?", hint_en),
                    "prefix": "", "suffix": "", "validation_message": ""}}
    return {"type": "text_input", "code": "BER_1", "texts": texts,
            "config": {"multiline": False, "max_length": 300, "validation": "none", "min": None, "max": None, "pattern": ""}}


def _ste1():
    return choice("STE_1", (
        "Welche berufliche Stellung haben oder hatten Sie in ihrer hauptsächlich ausgeübten Erwerbstätigkeit?",
        "What is or was your occupational status in your main employment?"), [
        ("1", "Selbstständige/-r Landwirt/-in", "Self-employed farmer"),
        ("2", "Genossenschaftsbauer/-bäuerin (ehemals LPG) in der ehem. DDR",
         "Member of an agricultural cooperative (formerly LPG) in the former GDR"),
        ("3", "Akademiker/-in in freiem Beruf (Arzt/Ärztin, Rechtsanwalt/ -anwältin, Steuerberater/-in u. Ä.)",
         "Self-employed professional (doctor, lawyer, tax adviser and the like)"),
        ("4", "Selbstständige/-r im Handel, im Gastgewerbe, in der Industrie, in der Dienstleistung, auch in einer Ich-AG",
         "Self-employed in trade, hospitality, industry or services, also as a sole trader (Ich-AG)"),
        ("5", "Mitglied einer PGH in der ehem. DDR", "Member of a craftsmen's cooperative (PGH) in the former GDR"),
        ("6", "Beamter/Beamtin, Richter/-in, Berufssoldat/-in", "Civil servant, judge, professional soldier"),
        ("7", "Angestellte/-r", "Salaried employee"),
        ("8", "Arbeiter/-in", "Manual worker"),
        ("9", "In einer beruflichen Ausbildung/Lehre", "In vocational training/apprenticeship"),
        ("10", "Mithelfende/-r Familienangehörige/-r", "Family member helping in the family business"),
        _DK])


def _hne3():
    return choice("HNE_3", (
        "Es würde uns schon helfen, wenn Sie sich zumindest grob einer der folgenden Kategorien zuordnen könnten. "
        "Beträgt Ihr monatliches Haushaltsnettoeinkommen weniger als 1.500 Euro, 1.500 bis unter 3.000 Euro oder "
        "3.000 Euro und mehr im Monat?",
        "It would already help us if you could place yourself at least roughly in one of the following categories. "
        "Is your monthly net household income less than 1,500 euros, 1,500 to under 3,000 euros or 3,000 euros or more "
        "per month?"), [
        ("1", "weniger als 1.500 Euro", "less than 1,500 euros"),
        ("2", "1.500 bis unter 3.000 Euro", "1,500 to under 3,000 euros"),
        ("3", "3.000 Euro und mehr", "3,000 euros or more"), _DK, _NA])


def _bracket(code, upper_de, upper_en, options):
    return choice(code, (
        f"Können Sie mir jetzt noch sagen, ob es {upper_de} im Monat beträgt?",
        f"Can you now also tell me whether it is {upper_en} per month?"), options + [_DK, _NA])


def _hne4():
    return _bracket("HNE_4", "weniger als 500 Euro, 500 bis unter 1.000 Euro oder 1.000 Euro und mehr",
                    "less than 500 euros, 500 to under 1,000 euros or 1,000 euros or more", [
                        ("1", "weniger als 500 Euro", "less than 500 euros"),
                        ("2", "500 bis unter 1.000 Euro", "500 to under 1,000 euros"),
                        ("3", "1.000 Euro bis unter 1.500 Euro", "1,000 euros to under 1,500 euros")])


def _hne5():
    return _bracket("HNE_5", "weniger als 2.000 Euro, 2.000 bis unter 2.500 Euro oder 2.500 Euro und mehr",
                    "less than 2,000 euros, 2,000 to under 2,500 euros or 2,500 euros or more", [
                        ("1", "1.500 bis unter 2.000 Euro", "1,500 to under 2,000 euros"),
                        ("2", "2.000 bis unter 2.500 Euro", "2,000 to under 2,500 euros"),
                        ("3", "2.500 Euro bis unter 3.000 Euro", "2,500 euros to under 3,000 euros")])


def _hne6():
    return _bracket("HNE_6", "weniger als 4.000 Euro, 4.000 bis unter 5.000 Euro oder 5.000 Euro und mehr",
                    "less than 4,000 euros, 4,000 to under 5,000 euros or 5,000 euros or more", [
                        ("1", "3.000 bis unter 4.000 Euro", "3,000 to under 4,000 euros"),
                        ("2", "4.000 bis unter 5.000 Euro", "4,000 to under 5,000 euros"),
                        ("3", "5.000 Euro und mehr", "5,000 euros or more")])


def _sta1():
    return choice("STA_1", ("Haben Sie die deutsche Staatsangehörigkeit?", "Do you have German citizenship?"), _YES_NO)


def _geb(code, de, en):
    return lambda: choice(code, (de, en), _YES_NO)


def _hhg1():
    return choice("HHG_1", (
        "Wie viele Personen leben ständig in Ihrem Haushalt, Sie selbst eingeschlossen? Zu diesem Haushalt zählen alle "
        "Personen, die hier gemeinsam wohnen und wirtschaften.",
        "How many people live permanently in your household, including yourself? The household includes everyone who "
        "lives and keeps house together here.",
        "Hinweis: Denken Sie dabei bitte auch an alle im Haushalt lebenden Kinder.",
        "Note: please also think of all children living in the household."), [
        ("1", "Eine Person", "One person"), ("2", "Mehrere Personen, und zwar:", "Several people, namely:"), _DK, _NA],
        text_options=("2",))


def _fam1():
    return choice("FAM_1", (
        "Welchen Familienstand haben Sie?", "What is your marital status?",
        "Hinweis: Bitte behandeln Sie eine eingetragene Lebenspartnerschaft in dieser Frage wie eine Ehe.",
        "Note: please treat a registered civil partnership like a marriage in this question."), [
        ("1", "Ledig, noch nie verheiratet gewesen", "Single, never married"), ("2", "Verheiratet", "Married"),
        ("3", "Geschieden", "Divorced"), ("4", "Verwitwet", "Widowed"), _DK, _NA])


def _seed(key, name, short, description, scoring, spec, topic, notes="", reference="Person: veränderlich"):
    return {"key": key, "seed_version": 1, "name": name, "status": "review", "short": short,
            "description": description, "authors": "RatSWD", "year": 2023, "citation": CITATION, "doi": DOI,
            "scoring": scoring, "notes": LICENCE + notes, "spec": spec,
            "tags": ["status:Standardfrage", "quelle:RatSWD/KonsortSWD", "art:Demografie", f"thema:{topic}"],
            "reference": reference}


_MIN = "Minimalversion des RatSWD-Standardfragenkatalogs (2023), mit KonsortSWD entwickelt und vorgetestet. "
_BASIC = "Basis-Merkmal des RatSWD-Standardfragenkatalogs (2023). "
_DKNA = " −1 = weiß nicht, −2 = keine Angabe (fehlend)."
_DK_ONLY = " −1 = weiß nicht (fehlend)."

SEEDS = [
    _seed("ratswd_alt1", "Geburtsmonat und -jahr (RatSWD ALT_1)", "Monat und Jahr der Geburt, aus Listen gewählt",
          _BASIC + "Aus den Demographischen Standards (2016) mit leicht geändertem Fragetext; als Element „Datum / "
                   "Uhrzeit“ im Modus „Monat und Jahr“ (Listen statt Tippen, wie der Katalog für computergestützte "
                   "Befragungen empfiehlt).",
          "JJJJ-MM (ISO 8601). Daraus KonsortSWD x_doby (Jahr) und x_age (Alter in abgeschlossenen Jahren).",
          _alt1, "Alter", REIDENT, "Person: stabil"),
    _seed("ratswd_ges1", "Geschlecht (RatSWD GES_1)", "Männlich, weiblich, divers",
          _BASIC + "Aus den Demographischen Standards (2016), mit „divers“ als dritter Antwort wie im "
                   "Personenstandsgesetz. Für Geschlechtsidentität mit Selbstbeschreibung gibt es das Konstrukt "
                   "„Geschlecht“ nach Spiel et al. (2019).",
          "1 = männlich, 2 = weiblich, 3 = divers; gleiche Codes wie KonsortSWD x_sex.", _ges1, "Gender",
          reference="Person: stabil"),
    _seed("ratswd_bil1", "Höchster Schulabschluss (RatSWD BIL_1)", "Vier Stufen von „kein Abschluss“ bis Abitur",
          _MIN + "Höchster allgemeinbildender Schulabschluss in vier Stufen.",
          "1 bis 4 aufsteigend." + _DK_ONLY, _bil1, "Bildung"),
    _seed("ratswd_bil4", "Höchster Ausbildungsabschluss (RatSWD BIL_4)", "Acht Stufen von „kein Abschluss“ bis Promotion",
          _MIN + "Höchster beruflicher oder Hochschulabschluss in acht Stufen. Im Katalog gefiltert: nur wer nicht "
                 "mehr zur Schule geht.",
          "1 bis 8 aufsteigend." + _DK_ONLY, _bil4, "Bildung"),
    _seed("ratswd_erw1", "Erwerbssituation (RatSWD ERW_1)", "Hauptstatus in neun Kategorien",
          _MIN + "Derzeitige Situation (Hauptstatus): erwerbstätig in Vollzeit oder Teilzeit oder sieben Gründe, "
                 "hauptberuflich nicht erwerbstätig zu sein, mit Textfeld für „aus anderen Gründen“.",
          "1 bis 9 wie im Katalog; Text zu 9 in einer eigenen Spalte. Daraus KonsortSWD x_mainstatus." + _DK_ONLY,
          _erw1, "Erwerbstätigkeit"),
    _seed("ratswd_erw2", "Nebenerwerbstätigkeit (RatSWD ERW_2)", "Bezahlter Nebenjob: ja oder nein",
          _MIN + "Folgefrage zu ERW_1: im Katalog nur für alle, die hauptberuflich nicht erwerbstätig sind "
                 "(ERW_1 = 3 bis 9).",
          "1 = ja, 2 = nein." + _DK_ONLY, _erw2, "Erwerbstätigkeit"),
    _seed("ratswd_ber1", "Berufliche Tätigkeit (RatSWD BER_1)", "Offene Angabe der Tätigkeit",
          _MIN + "Aus den Demographischen Standards (2016) mit dem Hinweis zur genauen Tätigkeitsbezeichnung aus dem "
                 "SOEP. Im Katalog gefiltert: nur wer heute oder früher erwerbstätig ist oder in Ausbildung.",
          "Freitext; für ISCO-08 oder KldB 2010 nachträglich zu codieren. Die Codes −1 (weiß nicht) und −3 (trifft "
          "nicht zu) des Katalogs entfallen: Ein leeres Feld zählt als fehlend.", _ber1, "Beruf"),
    _seed("ratswd_ste1", "Stellung im Beruf (RatSWD STE_1)", "Zehn Oberkategorien der beruflichen Stellung",
          _MIN + "Nur die Oberkategorien der Demographischen Standards (2016). Im Katalog gefiltert wie BER_1.",
          "1 bis 10 wie im Katalog." + _DK_ONLY, _ste1, "Beruf"),
    _seed("ratswd_hne3", "Haushaltsnettoeinkommen grob (RatSWD HNE_3)", "Drei Klassen: unter 1.500, bis 3.000, ab 3.000 Euro",
          _BASIC + "Geschlossene Frage mit „unfolding brackets“ nach NEPS (erste Frage); die Folgefragen HNE_4 bis "
                   "HNE_6 teilen jede Klasse in drei. Der Katalog stellt sie nach der offenen Frage HNE_1; der "
                   "Fragetext beginnt deshalb mit „Es würde uns schon helfen …“.",
          "1 = unter 1.500 Euro, 2 = 1.500 bis unter 3.000 Euro, 3 = 3.000 Euro und mehr." + _DKNA, _hne3, "Einkommen"),
    _seed("ratswd_hne4", "Haushaltsnettoeinkommen unter 1.500 Euro (RatSWD HNE_4)", "Folgefrage: unter 500, bis 1.000, bis 1.500 Euro",
          _BASIC + "Folgefrage zu HNE_3 = 1.",
          "1 = unter 500 Euro, 2 = 500 bis unter 1.000 Euro, 3 = 1.000 bis unter 1.500 Euro." + _DKNA, _hne4, "Einkommen"),
    _seed("ratswd_hne5", "Haushaltsnettoeinkommen 1.500 bis 3.000 Euro (RatSWD HNE_5)", "Folgefrage: bis 2.000, bis 2.500, bis 3.000 Euro",
          _BASIC + "Folgefrage zu HNE_3 = 2.",
          "1 = 1.500 bis unter 2.000 Euro, 2 = 2.000 bis unter 2.500 Euro, 3 = 2.500 bis unter 3.000 Euro." + _DKNA,
          _hne5, "Einkommen"),
    _seed("ratswd_hne6", "Haushaltsnettoeinkommen ab 3.000 Euro (RatSWD HNE_6)", "Folgefrage: bis 4.000, bis 5.000, ab 5.000 Euro",
          _BASIC + "Folgefrage zu HNE_3 = 3.",
          "1 = 3.000 bis unter 4.000 Euro, 2 = 4.000 bis unter 5.000 Euro, 3 = 5.000 Euro und mehr." + _DKNA,
          _hne6, "Einkommen"),
    _seed("ratswd_sta1", "Deutsche Staatsangehörigkeit (RatSWD STA_1)", "Ja oder nein",
          _BASIC + "Aus den Demographischen Standards (2016).",
          "1 = ja, 2 = nein. Daraus KonsortSWD x_citsc." + _DKNA, _sta1, "Staatsangehörigkeit"),
    _seed("ratswd_geb1", "In Deutschland geboren (RatSWD GEB_1)", "Ja oder nein",
          _BASIC + "Aus den Demographischen Standards (2016).",
          "1 = ja, 2 = nein. Daraus KonsortSWD x_bornsc." + _DKNA,
          _geb("GEB_1", "Sind Sie in Deutschland geboren?", "Were you born in Germany?"), "Migration",
          reference="Person: stabil"),
    _seed("ratswd_geb3", "Mutter in Deutschland geboren (RatSWD GEB_3)", "Ja oder nein",
          _BASIC + "Aus den Demographischen Standards (2016).",
          "1 = ja, 2 = nein. Daraus KonsortSWD x_mbornsc." + _DKNA,
          _geb("GEB_3", "Ist Ihre Mutter in Deutschland geboren?", "Was your mother born in Germany?"), "Migration",
          ART9, "Person: stabil"),
    _seed("ratswd_geb5", "Vater in Deutschland geboren (RatSWD GEB_5)", "Ja oder nein",
          _BASIC + "Aus den Demographischen Standards (2016).",
          "1 = ja, 2 = nein. Daraus KonsortSWD x_fbornsc." + _DKNA,
          _geb("GEB_5", "Ist Ihr Vater in Deutschland geboren?", "Was your father born in Germany?"), "Migration",
          ART9, "Person: stabil"),
    _seed("ratswd_hhg1", "Haushaltsgröße (RatSWD HHG_1)", "Eine Person oder mehrere, mit Anzahl",
          _BASIC + "Aus den Demographischen Standards (2016); die Anzahl bei „mehrere Personen“ steht im Textfeld.",
          "1 = eine Person, 2 = mehrere Personen (Anzahl in der Textspalte). Daraus KonsortSWD x_hhsize." + _DKNA,
          _hhg1, "Haushalt"),
    _seed("ratswd_fam1", "Familienstand (RatSWD FAM_1)", "Ledig, verheiratet, geschieden, verwitwet",
          _BASIC + "Aus den Demographischen Standards nach zwei Pretests gekürzt; eine eingetragene "
                   "Lebenspartnerschaft zählt wie eine Ehe.",
          "1 = ledig, 2 = verheiratet, 3 = geschieden, 4 = verwitwet. Daraus KonsortSWD x_marital." + _DKNA,
          _fam1, "Familienstand"),
]
KEYS = [s["key"] for s in SEEDS]
NOT_POOLED = set(KEYS)

_MIN_EN = "Minimal version of the RatSWD catalogue of standard questions (2023), developed with KonsortSWD and pretested. "
_BASIC_EN = "Basic characteristic of the RatSWD catalogue of standard questions (2023). "
_DKNA_EN = " −1 = don't know, −2 = no answer (missing)."
_DK_ONLY_EN = " −1 = don't know (missing)."

EN = {
    "ratswd_alt1": {
        "name": "Month and year of birth (RatSWD ALT_1)", "short": "Month and year of birth, chosen from lists",
        "description": _BASIC_EN + "From the Demographische Standards (2016) with a slightly changed question; as the "
                                   "element “Date / time” in the mode “Month and year” (lists instead of typing, as the "
                                   "catalogue recommends for computer-assisted surveys).",
        "scoring": "YYYY-MM (ISO 8601). Gives KonsortSWD x_doby (year) and x_age (age in completed years).",
        "notes": LICENCE_EN + REIDENT_EN},
    "ratswd_ges1": {
        "name": "Sex (RatSWD GES_1)", "short": "Male, female, diverse",
        "description": _BASIC_EN + "From the Demographische Standards (2016), with “divers” as a third answer as in the "
                                   "German civil status law. For gender identity with self-description there is the "
                                   "construct “Gender” after Spiel et al. (2019).",
        "scoring": "1 = male, 2 = female, 3 = diverse; the same codes as KonsortSWD x_sex."},
    "ratswd_bil1": {
        "name": "Highest school-leaving qualification (RatSWD BIL_1)", "short": "Four levels from “none” to Abitur",
        "description": _MIN_EN + "Highest general school-leaving qualification in four levels.",
        "scoring": "1 to 4 ascending." + _DK_ONLY_EN},
    "ratswd_bil4": {
        "name": "Highest vocational qualification (RatSWD BIL_4)", "short": "Eight levels from “none” to doctorate",
        "description": _MIN_EN + "Highest vocational or university qualification in eight levels. Filtered in the "
                                 "catalogue: only for people no longer at school.",
        "scoring": "1 to 8 ascending." + _DK_ONLY_EN},
    "ratswd_erw1": {
        "name": "Employment situation (RatSWD ERW_1)", "short": "Main status in nine categories",
        "description": _MIN_EN + "Current situation (main status): employed full or part time, or seven reasons for "
                                 "not being employed in a main job, with a text field for “other reasons”.",
        "scoring": "1 to 9 as in the catalogue; the text for 9 in a column of its own. Gives KonsortSWD x_mainstatus."
                   + _DK_ONLY_EN},
    "ratswd_erw2": {
        "name": "Side job (RatSWD ERW_2)", "short": "Paid side job: yes or no",
        "description": _MIN_EN + "Follow-up to ERW_1: in the catalogue only for those not employed in a main job "
                                 "(ERW_1 = 3 to 9).",
        "scoring": "1 = yes, 2 = no." + _DK_ONLY_EN},
    "ratswd_ber1": {
        "name": "Occupation (RatSWD BER_1)", "short": "Occupation as free text",
        "description": _MIN_EN + "From the Demographische Standards (2016) with the SOEP note on the exact job title. "
                                 "Filtered in the catalogue: only people employed now or before, or in training.",
        "scoring": "Free text; to be coded into ISCO-08 or KldB 2010 afterwards. The catalogue's codes −1 (don't know) "
                   "and −3 (not applicable) are left out: an empty field counts as missing."},
    "ratswd_ste1": {
        "name": "Occupational status (RatSWD STE_1)", "short": "Ten main categories of occupational status",
        "description": _MIN_EN + "Only the main categories of the Demographische Standards (2016). Filtered in the "
                                 "catalogue as BER_1.",
        "scoring": "1 to 10 as in the catalogue." + _DK_ONLY_EN},
    "ratswd_hne3": {
        "name": "Household net income, rough (RatSWD HNE_3)", "short": "Three classes: under 1,500, to 3,000, from 3,000 euros",
        "description": _BASIC_EN + "Closed question with unfolding brackets after NEPS (first question); the follow-ups "
                                   "HNE_4 to HNE_6 split each class into three. The catalogue asks it after the open "
                                   "question HNE_1, so the question starts with “It would already help us …”.",
        "scoring": "1 = under 1,500 euros, 2 = 1,500 to under 3,000 euros, 3 = 3,000 euros or more." + _DKNA_EN},
    "ratswd_hne4": {
        "name": "Household net income under 1,500 euros (RatSWD HNE_4)",
        "short": "Follow-up: under 500, to 1,000, to 1,500 euros", "description": _BASIC_EN + "Follow-up to HNE_3 = 1.",
        "scoring": "1 = under 500 euros, 2 = 500 to under 1,000 euros, 3 = 1,000 to under 1,500 euros." + _DKNA_EN},
    "ratswd_hne5": {
        "name": "Household net income 1,500 to 3,000 euros (RatSWD HNE_5)",
        "short": "Follow-up: to 2,000, to 2,500, to 3,000 euros", "description": _BASIC_EN + "Follow-up to HNE_3 = 2.",
        "scoring": "1 = 1,500 to under 2,000 euros, 2 = 2,000 to under 2,500 euros, 3 = 2,500 to under 3,000 euros."
                   + _DKNA_EN},
    "ratswd_hne6": {
        "name": "Household net income from 3,000 euros (RatSWD HNE_6)",
        "short": "Follow-up: to 4,000, to 5,000, from 5,000 euros", "description": _BASIC_EN + "Follow-up to HNE_3 = 3.",
        "scoring": "1 = 3,000 to under 4,000 euros, 2 = 4,000 to under 5,000 euros, 3 = 5,000 euros or more." + _DKNA_EN},
    "ratswd_sta1": {
        "name": "German citizenship (RatSWD STA_1)", "short": "Yes or no",
        "description": _BASIC_EN + "From the Demographische Standards (2016).",
        "scoring": "1 = yes, 2 = no. Gives KonsortSWD x_citsc." + _DKNA_EN},
    "ratswd_geb1": {
        "name": "Born in Germany (RatSWD GEB_1)", "short": "Yes or no",
        "description": _BASIC_EN + "From the Demographische Standards (2016).",
        "scoring": "1 = yes, 2 = no. Gives KonsortSWD x_bornsc." + _DKNA_EN},
    "ratswd_geb3": {
        "name": "Mother born in Germany (RatSWD GEB_3)", "short": "Yes or no",
        "description": _BASIC_EN + "From the Demographische Standards (2016).",
        "scoring": "1 = yes, 2 = no. Gives KonsortSWD x_mbornsc." + _DKNA_EN, "notes": LICENCE_EN + ART9_EN},
    "ratswd_geb5": {
        "name": "Father born in Germany (RatSWD GEB_5)", "short": "Yes or no",
        "description": _BASIC_EN + "From the Demographische Standards (2016).",
        "scoring": "1 = yes, 2 = no. Gives KonsortSWD x_fbornsc." + _DKNA_EN, "notes": LICENCE_EN + ART9_EN},
    "ratswd_hhg1": {
        "name": "Household size (RatSWD HHG_1)", "short": "One person or several, with the number",
        "description": _BASIC_EN + "From the Demographische Standards (2016); the number for “several people” is typed "
                                   "into the text field.",
        "scoring": "1 = one person, 2 = several people (number in the text column). Gives KonsortSWD x_hhsize."
                   + _DKNA_EN},
    "ratswd_fam1": {
        "name": "Marital status (RatSWD FAM_1)", "short": "Single, married, divorced, widowed",
        "description": _BASIC_EN + "From the Demographische Standards, shortened after two pretests; a registered civil "
                                   "partnership counts as a marriage.",
        "scoring": "1 = single, 2 = married, 3 = divorced, 4 = widowed. Gives KonsortSWD x_marital." + _DKNA_EN},
}
for _key, _en in EN.items():
    _en.setdefault("notes", LICENCE_EN)


# ---- the bundle "Demografie kurz (RatSWD)" ----
# One click inserts a block with the basic items; each part can be left out (concept decision 5b). Offered in the
# editor (constructs), by the setup wizard and by AInstein (wizard.add_demographics).
BUNDLE_NAME = ("Demografie kurz (RatSWD)", "Short demographics (RatSWD)")
BUNDLE = [  # (part, German, English, construct keys): in the order of the block
    ("alt", "Geburtsmonat und -jahr (ALT_1)", "Month and year of birth (ALT_1)", ("ratswd_alt1",)),
    ("ges", "Geschlecht (GES_1)", "Sex (GES_1)", ("ratswd_ges1",)),
    ("bil1", "Schulabschluss (BIL_1)", "School-leaving qualification (BIL_1)", ("ratswd_bil1",)),
    ("bil4", "Ausbildungsabschluss (BIL_4)", "Vocational qualification (BIL_4)", ("ratswd_bil4",)),
    ("erw1", "Erwerbssituation (ERW_1)", "Employment situation (ERW_1)", ("ratswd_erw1",)),
    ("erw2", "Nebenerwerbstätigkeit (ERW_2)", "Side job (ERW_2)", ("ratswd_erw2",)),
    ("ber1", "Berufliche Tätigkeit (BER_1)", "Occupation (BER_1)", ("ratswd_ber1",)),
    ("ste1", "Stellung im Beruf (STE_1)", "Occupational status (STE_1)", ("ratswd_ste1",)),
    ("sta1", "Deutsche Staatsangehörigkeit (STA_1)", "German citizenship (STA_1)", ("ratswd_sta1",)),
    ("geb1", "In Deutschland geboren (GEB_1)", "Born in Germany (GEB_1)", ("ratswd_geb1",)),
    ("geb35", "Geburtsland der Eltern (GEB_3, GEB_5)", "Parents' country of birth (GEB_3, GEB_5)",
     ("ratswd_geb3", "ratswd_geb5")),
    ("hhg1", "Haushaltsgröße (HHG_1)", "Household size (HHG_1)", ("ratswd_hhg1",)),
    ("fam1", "Familienstand (FAM_1)", "Marital status (FAM_1)", ("ratswd_fam1",)),
    ("hne", "Haushaltsnettoeinkommen (HNE_3 bis HNE_6)", "Household net income (HNE_3 to HNE_6)",
     ("ratswd_hne3", "ratswd_hne4", "ratswd_hne5", "ratswd_hne6")),
]
PARTS = [part for part, *_ in BUNDLE]
# the catalogue's filters: construct key -> (the construct whose answer decides, its answer codes); such a question
# goes on a page after that one (a condition reads only answers of earlier pages, app/item_condition.py)
FILTERS = {"ratswd_erw2": ("ratswd_erw1", ERW_NOT_EMPLOYED), "ratswd_ber1": ("ratswd_erw1", ERW_EVER_EMPLOYED),
           "ratswd_ste1": ("ratswd_erw1", ERW_EVER_EMPLOYED), "ratswd_hne4": ("ratswd_hne3", ("1",)),
           "ratswd_hne5": ("ratswd_hne3", ("2",)), "ratswd_hne6": ("ratswd_hne3", ("3",))}


def bundle_parts():
    """[(part, label)] of the bundle in the language of the page."""
    from app.lang import english

    return [(part, en if english() else de) for part, de, en, _keys in BUNDLE]


def bundle_keys(parts):
    """The construct keys of the chosen parts, in the order of the block: first the questions everyone gets, then
    those that follow from an answer on the first page."""
    keys = [k for part, _de, _en, ks in BUNDLE if part in parts for k in ks]
    first = [k for k in keys if FILTERS.get(k, (None,))[0] not in keys]
    return first + [k for k in keys if k not in first]


def add_bundle(survey, parts, block):
    """Fill `block` of `survey` with the questions of the chosen parts (app.ratswd.PARTS), each built from its
    construct like a question added in the editor, a page break before those with a filter, and their conditions.
    The block must belong to the survey already (its questions get their ids on the flush here). Returns the
    questions."""
    from app import constructs, duplicate, item_texts, items
    from app.models import Item

    keys = bundle_keys(parts)
    from app import edit_guard

    used = edit_guard.taken_codes(survey)  # read under the survey's row

    def free(base, first=""):
        """base if free, else base_2, base_3 … (a page break: _pb1, _pb2 …, as the editor names them)."""
        code, n = base + first, 1
        while code in used:
            n += 1
            code = f"{base}{n}" if first else f"{base}_{n}"
        used.add(code)
        return code

    rows, made, broke = list(block.items), {}, False
    for key in keys:
        if not broke and FILTERS.get(key, (None,))[0] in made:  # the first question that follows from an answer
            kind = items.get("page_break")
            rows.append(Item(type="page_break", answer_mode="optional", code=free("_pb", "1"),
                             config=kind.default_config(), texts=item_texts.new(kind, survey.languages,
                                                                                survey.default_language)))
            broke = True
        kind_key, base, config, texts = constructs.build(key, survey.languages)
        code = free(base)
        if code != base:
            duplicate.number_rows(kind_key, config, code)
        item = made[key] = Item(type=kind_key, answer_mode="nudge", code=code, config=config, texts=texts)
        rows.append(item)
    block.items = rows
    for position, item in enumerate(rows):
        item.position = position
    from app.extensions import db

    db.session.flush()
    for key, item in made.items():
        source, codes = FILTERS.get(key, (None, ()))
        if source in made:
            item.config = {**item.config, "when": {"item": made[source].id, "op": "in", "value": ", ".join(codes)}}
    return list(made.values())
