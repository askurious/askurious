"""Lernhilfen: reasons at the decisions of a study and reading aids at the statistics, for researchers new to
empirical work (concept "Askurious für Studierende und Berufseinsteiger", package B).

A switch on the page "Anzeige und Sprache" (User.learning_aids) shows or hides them; new accounts start with it on, accounts that
existed before with it off (migration 0004). Each text is short (two sentences at most), says what speaks for and
against the options, and names its sources, in full as everywhere in Askurious (templates/_sources.html). The
glossary page (/learn/glossary) lists the reading aids with an example each.

REASONS: decisions in the editor and the settings, rendered by `why(key)` in templates/_learn.html.
TERMS: statistics in tables and texts, rendered by `term(key)`: the symbol with a link to its entry in the glossary.
Sources are named as (module, constant) and read when shown, so this module imports none of the statistics modules.
"""
import importlib

from flask_login import current_user

from app.lang import de_en, english

DOERING = ("Döring, N. (2023). Forschungsmethoden und Evaluation in den Sozial- und Humanwissenschaften (6. Aufl.). "
           "Springer. https://doi.org/10.1007/978-3-662-64762-2")
KROSNICK_PRESSER = ("Krosnick, J. A., & Presser, S. (2010). Question and questionnaire design. In P. V. Marsden & "
                    "J. D. Wright (Eds.), Handbook of survey research (2nd ed., pp. 263–313). Emerald.")
REVILLA = ("Revilla, M. A., Saris, W. E., & Krosnick, J. A. (2014). Choosing the number of categories in agree–disagree "
           "scales. Sociological Methods & Research, 43(1), 73–97. https://doi.org/10.1177/0049124113509605")

BRAZELL = ("Brazell, J. D., Diener, C. G., Karniouchina, E., Moore, W. L., Séverin, V., & Uldry, P.-F. (2006). The "
           "no-choice option and dual response choice designs. Marketing Letters, 17(4), 255–268. "
           "https://doi.org/10.1007/s11002-006-7943-8")
HAAIJER = ("Haaijer, R., Kamakura, W. A., & Wedel, M. (2001). The “no-choice” alternative in conjoint choice "
           "experiments. International Journal of Market Research, 43(1), 93–106.")
HOEKSTRA = ("Hoekstra, R., Morey, R. D., Rouder, J. N., & Wagenmakers, E.-J. (2014). Robust misinterpretation of "
            "confidence intervals. Psychonomic Bulletin & Review, 21(5), 1157–1164. https://doi.org/10.3758/s13423-013-0572-3")
KEUSCH = ("Keusch, F., Struminskaya, B., Antoun, C., Couper, M. P., & Kreuter, F. (2019). Willingness to participate in "
          "passive mobile data collection. Public Opinion Quarterly, 83(S1), 210–235. https://doi.org/10.1093/poq/nfz007")
COUPER_2004 = ("Couper, M. P., Tourangeau, R., Conrad, F. G., & Crawford, S. D. (2004). What they see is what we get: "
               "Response options for web surveys. Social Science Computer Review, 22(1), 111–127.")
COUPER_2013 = ("Couper, M. P., Tourangeau, R., Conrad, F. G., & Zhang, C. (2013). The design of grids in web surveys. Social "
               "Science Computer Review, 31(3), 322–345. https://doi.org/10.1177/0894439312469865")
DELACRE = ("Delacre, M., Lakens, D., & Leys, C. (2017). Why psychologists should by default use Welch’s t-test instead of "
           "Student’s t-test. International Review of Social Psychology, 30(1), 92–101. https://doi.org/10.5334/irsp.82")
FUNKE = ("Funke, F., Reips, U.-D., & Thomas, R. K. (2011). Sliders for the smart: Type of rating scale on the Web "
         "interacts with educational level. Social Science Computer Review, 29(2), 221–231.")
GALESIC = ("Galesic, M., & Bosnjak, M. (2009). Effects of questionnaire length on participation and indicators of response "
           "quality in a web survey. Public Opinion Quarterly, 73(2), 349–360. https://doi.org/10.1093/poq/nfp031")
LIU_CERNAT = ("Liu, M., & Cernat, A. (2018). Item-by-item versus matrix questions: A web survey experiment. Social "
              "Science Computer Review, 36(6), 690–706.")
MAVLETOVA = ("Mavletova, A., Couper, M. P., & Lebedev, D. (2018). Grid and item-by-item formats in PC and mobile web "
             "surveys. Social Science Computer Review, 36(6), 647–668.")
REVILLA_COUPER = ("Revilla, M., & Couper, M. P. (2018). Comparing grids with vertical and horizontal item-by-item formats "
                  "for PCs and smartphones. Social Science Computer Review, 36(3), 349–368.")
VILLAR = ("Villar, A., Callegaro, M., & Yang, Y. (2013). Where am I? A meta-analysis of experiments on the effects of "
          "progress indicators for web surveys. Social Science Computer Review, 31(6), 744–762. "
          "https://doi.org/10.1177/0894439313497468")
DILLMAN = ("Dillman, D. A., Smyth, J. D., & Christian, L. M. (2014). Internet, phone, mail, and mixed-mode surveys: "
           "The tailored design method (4th ed.). Wiley.")
NOSEK_2018 = ("Nosek, B. A., Ebersole, C. R., DeHaven, A. C., & Mellor, D. T. (2018). The preregistration revolution. "
              "Proceedings of the National Academy of Sciences, 115(11), 2600–2606. https://doi.org/10.1073/pnas.1708274114")
# duplicating questions and blocks 2026-10-21: copies or a loop
AUSPURG_HINZ = ("Auspurg, K., & Hinz, T. (2015). Factorial survey experiments. SAGE. "
                "https://doi.org/10.4135/9781483398075")
# Coscine-Ausbau 2026-10-20: where data are kept
BAKER_2013 = ("Baker, R., Brick, J. M., Bates, N. A., Battaglia, M., Couper, M. P., Dever, J. A., Gile, K. J., & "
              "Tourangeau, R. (2013). Summary report of the AAPOR task force on non-probability sampling. Journal of Survey "
              "Statistics and Methodology, 1(2), 90–143. https://doi.org/10.1093/jssam/smt008")
GLASER_STRAUSS = "Glaser, B. G., & Strauss, A. L. (1967). The discovery of grounded theory. Aldine."
DDI_CV = ("DDI Alliance. (2026). DDI controlled vocabularies [Kontrollierte Vokabulare: ModeOfCollection 5.0.1, "
          "TimeMethod 1.2.3, SamplingProcedure 2.0.2]. CESSDA Vocabulary Service. https://vocabularies.cessda.eu/")
DDI_L4 = ("DDI Alliance. (2026). DDI Lifecycle 4.0 public review (Version v4.0-pr). "
          "https://github.com/ddialliance/ddimodel/releases/tag/v4.0-pr")
LOHR = "Lohr, S. L. (2022). Sampling: Design and analysis (3rd ed.). CRC Press. https://doi.org/10.1201/9780429298899"
DFG_KODEX = ("Deutsche Forschungsgemeinschaft. (2019). Leitlinien zur Sicherung guter wissenschaftlicher Praxis: Kodex. "
             "https://doi.org/10.5281/zenodo.3923602")
SIMMONS_2011 = ("Simmons, J. P., Nelson, L. D., & Simonsohn, U. (2011). False-positive psychology: Undisclosed "
                "flexibility in data collection and analysis allows presenting anything as significant. Psychological "
                "Science, 22(11), 1359–1366. https://doi.org/10.1177/0956797611417632")

# concept "Lernhilfen: vierte Runde" (G1, G2)
REVILLA_VOICE = ("Revilla, M., Couper, M. P., Bosch, O. J., & Asensio, M. (2020). Testing the use of voice input in a "
                 "smartphone web survey. Social Science Computer Review, 38(2), 207–224. "
                 "https://doi.org/10.1177/0894439318810715")
LENZNER_HOEHNE = ("Lenzner, T., & Höhne, J. K. (2022). Who is willing to use audio and voice inputs in smartphone surveys? "
                  "Evidence from a split-ballot experiment. Social Science Computer Review, 40(4), 1011–1026. "
                  "https://doi.org/10.1177/0894439321994000")
STGB_201 = ("Strafgesetzbuch (StGB) § 201: Verletzung der Vertraulichkeit des Wortes. "
            "https://www.gesetze-im-internet.de/stgb/__201.html")
BARCHARD_PACE = ("Barchard, K. A., & Pace, L. A. (2011). Preventing human error: The impact of data entry methods on data "
                 "accuracy and statistical results. Computers in Human Behavior, 27(5), 1834–1839. "
                 "https://doi.org/10.1016/j.chb.2011.04.004")
HAYES_2022 = ("Hayes, A. F. (2022). Introduction to mediation, moderation, and conditional process analysis: A "
              "regression-based approach (3rd ed.). Guilford Press.")
RFC_3161 = ("Adams, C., Cain, P., Pinkas, D., & Zuccherato, R. (2001). Internet X.509 public key infrastructure time-stamp "
            "protocol (TSP) (RFC 3161). Internet Engineering Task Force. https://doi.org/10.17487/RFC3161")
# pen notes on presentation slides 2026-10-23 (app/live/ink.py)
SCHNEIDER_2018 = ("Schneider, S., Beege, M., Nebel, S., & Rey, G. D. (2018). A meta-analysis of how signaling affects "
                  "learning with media. Educational Research Review, 23, 1–24. https://doi.org/10.1016/j.edurev.2017.11.001")
FIORELLA_MAYER_2016 = ("Fiorella, L., & Mayer, R. E. (2016). Effects of observing the instructor draw diagrams on learning "
                       "from multimedia messages. Journal of Educational Psychology, 108(4), 528–546. "
                       "https://doi.org/10.1037/edu0000065")

# SEM evaluation in two steps, model versions (2026-10-26)
ANDERSON_GERBING = ("Anderson, J. C., & Gerbing, D. W. (1988). Structural equation modeling in practice: A review and "
                    "recommended two-step approach. Psychological Bulletin, 103(3), 411–423. "
                    "https://doi.org/10.1037/0033-2909.103.3.411")

# key: (title of the fold, text, sources as constants of this module or (module, NAME))
REASONS = {
    "answer_mode": (
        "Freiwillig, Nachfragen oder Pflicht?",
        "Pflichtfragen verhindern Lücken, verleiten aber zu Zufallsantworten oder zum Abbruch, wenn jemand nicht "
        "antworten kann oder will. „Nachfragen“ markiert die Frage einmal und lässt dann weiter: meist der beste Mittelweg; "
        "Pflicht vor allem für Fragen, ohne die der Rest nicht ausgewertet werden kann (z. B. die Versuchsbedingung).",
        (DOERING,)),
    "scale_points": (
        "Wie viele Stufen hat eine Skala?",
        "Fünf bis sieben benannte Stufen messen bei Zustimmungsskalen am zuverlässigsten; mehr Stufen bringen kaum "
        "Genauigkeit, weniger verlieren Information. Eine Mitte erlaubt eine echte neutrale Haltung, wird aber auch "
        "als Ausweg genutzt.",
        (REVILLA, KROSNICK_PRESSER)),
    "missing_option": (
        "„Weiß nicht“ als fehlend?",
        "Eine Ausweichoption verhindert geratene Antworten bei Personen ohne Meinung; als fehlend markiert zählt sie "
        "in Mittelwerten und Anteilen nicht mit. Sie wird aber auch aus Bequemlichkeit gewählt; darum nur dort "
        "anbieten, wo Nicht-Wissen wirklich vorkommt.",
        (KROSNICK_PRESSER,)),
    "randomize": (
        "Reihenfolge zufällig?",
        "Wer die ersten Optionen oder Aussagen eher wählt, verzerrt die Antworten (Reihenfolgeeffekt); eine zufällige "
        "Reihenfolge je Person verteilt diesen Effekt gleichmäßig. Nicht zufällig bei Skalen mit natürlicher Ordnung "
        "(z. B. Häufigkeiten) und bei „Anderes“ oder „Weiß nicht“ am Ende.",
        (KROSNICK_PRESSER,)),
    "attention_check": (
        "Aufmerksamkeitsprüfung einbauen?",
        "Eine Frage mit vorgegebener Antwort („Bitte wählen Sie ‚stimme eher zu‘“) erkennt Personen, die nicht lesen. "
        "Eine oder zwei reichen; mehr verärgern aufmerksame Teilnehmende, und der Ausschluss sollte vor der "
        "Erhebung festgelegt sein.",
        (("app.quality", "GUMMER"), ("app.quality", "MEADE"))),
    "speeder": (
        "Gesamtdauer oder Relative Speed Index?",
        "Die Gesamtdauer unter einem Anteil des Medians ist leicht zu erklären, benachteiligt aber Fragebögen mit "
        "verschieden langen Wegen. Der Relative Speed Index vergleicht jede Seite mit ihrem eigenen Median und ist "
        "dann genauer; er braucht Seitenzeiten.",
        (("app.quality", "LEINER"),)),
    "data_pool": (
        "Welche Teilnahmen zählen?",
        "Ergebnisse gelten nur für echte, abgeschlossene Teilnahmen; Testdurchläufe und Dummy-Daten helfen beim "
        "Prüfen des Fragebogens und der Auswertung, gehören aber nicht in einen Bericht. Abgebrochene Teilnahmen "
        "einzubeziehen erhöht die Fallzahl, verzerrt aber, wenn bestimmte Personen eher abbrechen.",
        (DOERING,)),
    "quota_hard": (
        "Harte oder weiche Quote?",
        "Eine harte Quote lässt nie mehr Personen in eine Teilquote als vorgesehen und hält dafür Plätze frei; eine "
        "weiche lässt alle fertig machen, die schon begonnen haben, und überschreitet das Soll um die Toleranz. "
        "Hart, wenn ein Panel jede Teilnahme bezahlt; weich, wenn niemand abgewiesen werden soll.",
        (("app.weights", "AAPOR"),)),
    "weighting": (
        "Gewichten?",
        "Gewichtung gleicht aus, dass manche Gruppen in der Stichprobe zu selten sind, aber nur für die Merkmale, nach "
        "denen gewichtet wird. Große Gewichte verringern die effektive Fallzahl und machen Ergebnisse unsicherer.",
        (("app.weights", "DEMING_STEPHAN"), ("app.weights", "KISH"))),
    "sample_size": (
        "Wie viele Teilnehmende brauche ich?",
        "Die Zahl hängt von der kleinsten Wirkung ab, die man entdecken will, und von der gewünschten Sicherheit "
        "(Power, meist 80 %). Kleine Wirkungen brauchen viele Personen; wer zu wenige befragt, findet einen echten "
        "Unterschied oft nicht.",
        (("app.samplesize", "COHEN"), ("app.samplesize", "FAUL_2009"))),
    # choice models, segments and the element "Standort" (concept "Lernhilfen für die neuen Funktionen", L1 to L3)
    "conjoint_tasks": (
        "Wie viele Aufgaben und Alternativen?",
        "Mehr Aufgaben je Person machen die Schätzung genauer, bis Ermüdung einsetzt; üblich sind 8 bis 15 Aufgaben "
        "mit drei oder vier Alternativen, unter 6 warnt Askurious. Mehrere Versionen des Versuchsplans verteilen die Kombinationen "
        "auf die Personen und machen den Versuchsplan robuster.",
        (("app.conjoint_design", "JOHNSON_ORME"), ("app.conjoint_design", "ORME"))),
    "none_option": (
        "„Keine davon“ anbieten?",
        "Ohne diese Option muss jede Person wählen, auch wenn ihr nichts gefällt; mit ihr lässt sich abschätzen, ob "
        "überhaupt gekauft würde. Sie liefert aber weniger Information über die Merkmale, weil manche Personen sie oft "
        "wählen. Die Nachfrage (Dual Response) verbindet beides: Alle wählen erst unter den Optionen und sagen dann, ob "
        "sie die gewählte wirklich nehmen würden; so gibt es Präferenzen von allen und trotzdem eine Schätzung, ob "
        "gekauft würde.",
        (HAAIJER, BRAZELL)),
    "conjoint_kind": (
        "Welche Conjoint-Art?",
        "CBC lässt wählen und passt für Preise und Marktanteile mit bis etwa sechs Merkmalen; ACBC baut die Varianten um "
        "die Wunschvariante jeder Person und lohnt sich bei vielen Merkmalen und kleinen Stichproben, dauert aber länger. "
        "Die Bewertungs-Conjoint (CVA) lässt Angebote bewerten statt wählen und liefert Werte je Person ohne HB, aber nur "
        "mit wenigen Merkmalen; Vignetten (eine Schleife im Ablauf) passen, wenn zu jedem Fall beliebige Fragen kommen.",
        (("app.holdouts", "ORME"), ("app.cva_design", "GREEN_SRINIVASAN"))),
    "hb_or_logit": (
        "Aggregiertes Logit oder HB?",
        "Das aggregierte Logit beschreibt die Stichprobe als Ganzes und reicht für die Rangfolge und die Wichtigkeit im "
        "Mittel der Teilnutzen; unterscheiden sich die Vorlieben in der Richtung, unterschätzt es die Wichtigkeit. HB "
        "schätzt Werte je Person; erst damit zeigen Verteilungen, Simulator und Export, wie verschieden die Vorlieben sind. "
        "Beide rechnen unabhängig voneinander aus denselben Wahlen: das Logit in Sekunden und deshalb sofort, HB mit vielen "
        "tausend Ziehungen per Knopf. Kleine "
        "Abweichungen sind normal, weil das Logit verschiedene Vorlieben vorab wegmittelt und meist flacher ausfällt: "
        "Bei den Teilnutzen sollten Richtung und meist auch Reihenfolge übereinstimmen; Wichtigkeiten können deutlich "
        "abweichen, vor allem bei Merkmalen, die Personen gegensätzlich bewerten (HB mittelt die Spannweiten der "
        "einzelnen Personen).",
        (("app.hb", "ALLENBY"), ("app.hb", "ROSSI"))),
    "holdout": (
        "Holdout-Aufgabe einbauen?",
        "Eine Aufgabe oder ein Set, das nicht in die Schätzung eingeht, prüft, ob das Modell echte Antworten vorhersagt; "
        "ohne sie bleibt nur die Anpassung an die eigenen Daten. Eine oder zwei reichen und verlängern die Befragung "
        "kaum.",
        (("app.holdouts", "ORME"),)),
    "segments_k": (
        "Wie viele Segmente?",
        "Das BIC schlägt die Zahl vor, die Passung und Sparsamkeit am besten abwägt; Askurious überspringt dabei sehr "
        "kleine und instabile Lösungen. Wählen Sie auch nach Inhalt: Segmente, die sich deutlich unterscheiden, "
        "benennen lassen und Holdouts besser vorhersagen.",
        (("app.latent_class", "NYLUND"), ("app.latent_class", "ANDREWS_CURRIM"))),
    "segments_covariates": (
        "Kovariaten ins Modell?",
        "Kovariaten lassen die Segmentanteile je Person von Fragen wie dem Alter abhängen; das schätzt diese "
        "Zusammenhänge genauer, verschiebt aber die Segmente selbst. Wer Segmente nur aus den Wahlen bilden will, lässt "
        "sie weg und nutzt „Segmente beschreiben“.",
        (("app.latent_class", "DAYTON"), ("app.segments", "VERMUNT"))),
    "segments_describe": (
        "Warum BCH-Gewichte?",
        "Niemand gehört sicher zu einem Segment. Zählt man jede Person nur in ihrem wahrscheinlichsten Segment oder "
        "anteilig nach ihrer Wahrscheinlichkeit, verwischen die Unterschiede zwischen den Segmenten. BCH-Gewichte "
        "rechnen diese Fehlzuordnung im Mittel heraus; Tabelle und Tests nutzen sie, die Tests sind explorativ.",
        (("app.segments", "BOLCK"), ("app.segments", "VERMUNT"))),
    "geo_level": (
        "Wie genau speichern?",
        "Genaue Koordinaten können auf eine Wohnung zeigen; speichern Sie nur so genau, wie die Fragestellung es "
        "braucht, für Regionen reichen Postleitzahl oder Bundesland. Sorgen um die Privatsphäre senken die "
        "Bereitschaft, Daten vom Smartphone zu teilen.",
        (KEUSCH, ("app.retention", "GDPR"))),
    "geo_fallback": (
        "Ersatz aus der IP-Adresse?",
        "Der Ersatz füllt Lücken beim Bundesland, ist aber ungenauer: IP-Datenbanken treffen das Land meist, die "
        "Region oft nicht, im Mobilfunk besonders selten. Leer lassen, wenn ein falsches Bundesland mehr schadet als "
        "ein fehlendes.",
        (("app.geoip", "POESE"), ("app.geoip", "KOMOSNY"))),
    # questionnaire, flow and group comparisons (concept "Lernhilfen: Erweiterungen und Verbesserungen", E1, E2)
    "dropdown": (
        "Dropdown-Liste oder Auswahlknöpfe?",
        "Auswahlknöpfe zeigen alle Antworten auf einmal; in einer Dropdown-Liste wählen Personen häufiger die ersten "
        "sichtbaren Einträge. Eine Liste passt für lange, bekannte Listen wie Bundesländer oder Geburtsjahre.",
        (COUPER_2004,)),
    "slider": (
        "Schieberegler oder Stufen?",
        "Schieberegler wirken modern, kosten aber mehr Zeit und führen häufiger zu Abbrüchen und fehlenden Antworten, "
        "besonders bei Personen mit niedrigerer Bildung. Benannte Stufen sind meist die sicherere Wahl.",
        (FUNKE,)),
    "likert_grid": (
        "Eine Matrix oder einzelne Fragen?",
        "Eine Matrix spart Platz und Zeit, verleitet aber zu Abbrüchen und dazu, alle Zeilen gleich zu beantworten. "
        "Lange Matrizen besser auf mehrere Seiten aufteilen.",
        (COUPER_2013,)),
    "side_by_side": (
        "Side-by-Side oder einzelne Fragen?",
        "Mehrere Fragen nebeneinander sparen Platz und zeigen, was zusammengehört. Je mehr Spalten und Antwortfelder "
        "eine Matrix hat, desto mehr wird ausgelassen und durchgeklickt; am Smartphone liefert die Abfrage Zeile für "
        "Zeile bessere Daten, deshalb erscheint dort jede Zeile als Karte. Höchstens drei Spalten, wenige Stufen und "
        "eine Bedingung („nur wenn genutzt“) halten die Matrix kurz; wer eine Skala mit Skalenwert und α braucht, "
        "nimmt die Likert-Matrix.",
        (COUPER_2013, REVILLA_COUPER, MAVLETOVA, LIU_CERNAT)),
    "progress": (
        "Fortschrittsanzeige zeigen?",
        "Eine gleichmäßige Anzeige verringert Abbrüche kaum; eine, die am Anfang langsam vorankommt, erhöht sie. Bei "
        "langen Fragebögen mit vielen Verzweigungen eher weglassen.",
        (VILLAR,)),
    "length": (
        "Wie lang darf der Fragebogen sein?",
        "Je länger die angekündigte Dauer, desto weniger Personen beginnen; gegen Ende werden die Antworten kürzer, "
        "schneller und gleichförmiger. Eine ehrliche Angabe in der Einladung ist trotzdem besser als eine zu kurze.",
        (GALESIC,)),
    "random_assignment": (
        "Warum zufällig zuteilen?",
        "Nur eine zufällige Zuteilung sorgt dafür, dass sich die Gruppen allein in der Bedingung unterscheiden; dann "
        "lässt sich ein Unterschied als Wirkung lesen. Gleich große Gruppen geben die meiste Power.",
        (DOERING,)),
    "which_test": (
        "Warum dieser Test?",
        "Der Test folgt aus dem Messniveau beider Größen: zwei metrische Größen die Korrelation nach Pearson, eine nur "
        "geordnete die Rangkorrelation nach Spearman; Gruppen und eine metrische Zielgröße Welchs t-Test oder bei mehr "
        "als zwei Gruppen die Varianzanalyse nach Welch, die keine gleichen Streuungen voraussetzen. Bei geordneten "
        "Stufen ohne gleiche Abstände vergleicht ein Rangtest (Mann-Whitney, Kruskal-Wallis) die Lage der Gruppen ohne "
        "Mittelwerte. Zwei nominale Größen prüft der χ²-Test der Kreuztabelle.",
        (DELACRE, ("app.compare", "MANN_WHITNEY"))),
    "weighting_scope": (
        "Was wird gewichtet?",
        "Gewichtet sind Anteile, Mittelwerte, Mediane und Standardabweichungen; ihre Konfidenzintervalle rechnen mit "
        "dem Linearisierungs-Standardfehler, der auch unterschiedliche Streuung in den Gewichtungsgruppen "
        "berücksichtigt. Die Anzahlen (n) sind die ungewichteten. Ungewichtet bleiben Reliabilität (α, ω), "
        "Skalenwerte, Wahlmodelle und Preisbereitschaft; die Wahlmodelle (Zählwerte, aggregiertes Modell, HB, "
        "Segmente) rechnen mit allen Teilnahmen der ungewichteten Ansicht, also auf denselben Personen wie ohne "
        "Gewichtung. Im Gruppenvergleich („Nach Gruppen aufteilen“) sind Anteile, Mediane, Mittelwerte, "
        "Standardabweichungen und Intervalle ebenso gewichtet; Tests gibt es dort nur in der ungewichteten Ansicht.",
        (("app.weights", "LUMLEY"), ("app.weights", "KISH"))),
    # concept "Lernhilfen: dritte Runde" (F1, F2)
    "card_sort_mode": (
        "Offen, geschlossen oder gemischt?",
        "Offen bilden die Personen eigene Stapel und zeigen, wie sie selbst ordnen; das lohnt sich am Anfang. "
        "Geschlossen prüft, ob vorgegebene Stapel passen; gemischt erlaubt beides, ist aber schwerer auszuwerten.",
        (("app.items.card_sort", "RUGG"),)),
    "q_sort_grid": (
        "Warum eine erzwungene Verteilung?",
        "Die feste Form zwingt dazu, Aussagen gegeneinander abzuwägen, statt allem zuzustimmen; dadurch werden die "
        "Sortierungen vergleichbar. Sie zeigt Rangordnungen innerhalb einer Person, keine absolute Zustimmung.",
        (("app.items.q_sort", "WATTS"),)),
    "personal_code": (
        "Warum ein Code statt Name?",
        "Ein selbst erzeugter Code verbindet Befragungen derselben Person, ohne dass Namen gespeichert werden. Ein "
        "Teil der Codes passt später nicht mehr zusammen; einfache, stabile Bestandteile verringern das.",
        (("app.items.personal_code", "YUREK"), ("app.items.personal_code", "SCHNELL"))),
    "holm": (
        "Mehrere Hypothesen korrigieren?",
        "Wer viele Hypothesen prüft, findet allein durch Zufall eher eine „signifikante“. Holm hält die Fehlerrate für "
        "alle zusammen und verliert weniger Power als Bonferroni.",
        (("app.study_plan", "HOLM"),)),
    "preregistration": (
        "Warum vorab festlegen?",
        "Wer Hypothesen und Auswertung vor den Daten festhält, trennt Prüfen von Erkunden; nachträgliche "
        "Entscheidungen lassen sonst zu viele Befunde zufällig gut aussehen. Abweichungen bleiben erlaubt, wenn sie "
        "genannt werden.",
        (NOSEK_2018,)),
    "study_material": (
        "Warum das Befragungsmaterial mit Anmerkungen weitergeben?",
        "Erst das vollständige Material zeigt, was Teilnehmende gesehen haben und unter welchen Bedingungen; so lassen "
        "sich eine Befragung prüfen, nachbauen und mit der Präregistrierung vergleichen. Bedingungen, Zufallsreihenfolgen "
        "und Pflichtfragen bestimmen mit, wer welche Fragen beantwortet hat, und gehören deshalb dazu.",
        (SIMMONS_2011, NOSEK_2018)),
    "second_coding": (
        "Warum eine zweite Person kodieren lassen?",
        "Erst eine unabhängige Zweitkodierung zeigt, ob die Kategorien so klar sind, dass andere sie gleich anwenden. "
        "Krippendorffs α ab etwa 0,80 gilt als verlässlich, ab 0,67 für vorläufige Schlüsse.",
        (("app.coding_check", "KRIPPENDORFF"),)),
    "reminders": (
        "Erinnern, und wie oft?",
        "Eine oder zwei Erinnerungen erhöhen die Teilnahme deutlich; jede weitere bringt weniger und verärgert eher. "
        "Ein paar Tage Abstand und ein anderer Wortlaut wirken besser als dieselbe Nachricht noch einmal.",
        (DILLMAN,)),
    # concept "Lernhilfen: vierte Runde" (G1: the collection)
    "voice": (
        "Sprechen statt tippen?",
        "Gesprochene Antworten sind meist länger und reicher als getippte, aber viele mögen in einer Umfrage nicht laut "
        "sprechen. Bieten Sie Tippen als Ausweg an und machen Sie die Sprachnachricht nicht zur Pflicht.",
        (REVILLA_VOICE, LENZNER_HOEHNE)),
    # KI-Chat for participants (ki-assistent-tagebuch-konzept.md, 2026-10-23)
    "ai_chat": (
        "Eigener Modellzugang und feste Anweisungen?",
        "Wer untersucht, wie Menschen einen KI-Assistenten nutzen, muss festhalten, welches Modell mit welchen "
        "Anweisungen antwortete; sonst lässt sich die Studie nicht wiederholen. Ein eigener Zugang der Studie hält Modell "
        "und Herkunft fest, und die Teilnehmenden müssen erkennen können, dass eine KI antwortet.",
        (("app.items.ai_chat", "EU_AI_ACT"),)),
    # concept unterfragen (Ralf 2026-10-18)
    "guide_sub": (
        "Unterfrage oder eigene Leitfrage?",
        "Eine Unterfrage ist eine Nachfrage, die nur manchmal passt, etwa „falls zutreffend“ oder nur bei bestimmten "
        "Personen. Was in jedem Interview vorkommen soll, bleibt eine eigene Leitfrage. Stellen Sie Nachfragen erst, "
        "wenn die Erzählung zur Leitfrage zu Ende ist, damit sie die Erzählung nicht lenken.",
        (("app.items.guide_question", "HELFFERICH"),)),
    # concept bedingungen-leitfaden (Ralf 2026-10-19)
    "guide_probe_if": (
        "Nachfrage, Bedingung oder eigene Frage?",
        "Eine konkrete Frage fragt nach, was nicht von selbst kam; knüpfen Sie sie deshalb an ihren Aspekt. Eine "
        "Nachfrage darunter passt nur, wenn die konkrete Frage zutrifft. Hängt eine Frage an einer früheren, die nicht "
        "direkt darüber steht, wählen Sie bei „nur wenn“ „K1 trifft zu“ oder „trifft nicht zu“. Die Interviewansicht "
        "macht das alles nur blass, entscheiden tun Sie im Gespräch.",
        (("app.items.guide_question", "HELFFERICH"),)),
    "conference": (
        "Warum alle fragen?",
        "Aufnehmen dürfen Sie nur, wenn alle, die im Gespräch sprechen, vorher eingewilligt haben; heimliches Aufnehmen "
        "ist strafbar. Fragen Sie deshalb zu Beginn alle, in einer Videokonferenz auch die Mithörenden.",
        (STGB_201,)),
    # tasks in an interview (concept konzept-interview-formate, Ralf 2026-10-20)
    "interview_task": (
        "Warum eine Aufgabe im Interview?",
        "Eine kleine Aufgabe bringt Menschen ins Erzählen, wo eine Frage allein wenig Antwort findet: Sie sortieren oder "
        "ordnen erst und begründen dann. Fragen Sie deshalb nach dem Ergebnis nach, etwa „Warum steht das ganz oben?“. "
        "Das Ergebnis ist Gesprächsanlass, kein Messwert; die Begründung steht im Transkript.",
        (("app.interview_tasks", "BARTON"), ("app.items.card_sort", "RUGG"))),
    # the interviewee's own screen for tasks (app/interview_screen.py, Ralf 2026-10-20; regular since 2026-10-22)
    "interview_screen": (
        "Warum der Teilnehmerbildschirm?",
        "Im Videogespräch kann die Person eine Aufgabe sonst nicht selbst legen; Sie müssten eintragen, was sie sagt. "
        "Auf ihrem eigenen Gerät ordnet sie selbst, und Sie sehen das Ergebnis sofort als Gesprächsanlass. Geben Sie den "
        "Link nur ihr, und fragen Sie danach nach, etwa „Warum steht das ganz oben?“. Korrigiert sie sich mündlich, "
        "ändern Sie das Ergebnis; die Fallübersicht vermerkt es dann als von Ihnen geändert.",
        (("app.interview_tasks", "BARTON"),)),
    # transcript export (Ralf 2026-10-18)
    "transcript_export": (
        "Warum Zeilennummern und Platzhalter?",
        "Mit Zeilennummern belegen Sie Zitate nachprüfbar, etwa „I03, Z. 12–15“. Ersetzen Sie vor dem Export Namen, Orte "
        "und Arbeitgeber durch Platzhalter, denn die Datei verlässt die Plattform.",
        (("app.interview_results", "DRESING_PEHL"),)),
    # concept "Lernhilfen: fünfte Runde", H4
    "transcript_check": (
        "Warum das Transkript gegenlesen?",
        "Die automatische Verschriftung irrt vor allem bei Namen, Fachwörtern, Dialekt und wenn zwei zugleich sprechen. "
        "Lesen Sie das Transkript vor der Auswertung einmal mit der Aufnahme gegen, Stellen, die Sie zitieren, immer.",
        (("app.interview_results", "DRESING_PEHL"),)),
    "diary_trigger": (
        "Feste Zeiten, zufällig oder bei einem Ereignis?",
        "Feste Zeiten zeigen Verläufe über den Tag und lassen sich gut einplanen. Zufällige Abfragen treffen den "
        "typischen Alltag, ohne dass sich die Personen darauf einstellen; ein Knopf für ein Ereignis passt zu seltenen "
        "Situationen, die sonst keine Abfrage trifft.",
        (("app.diary", "BOLGER"), ("app.diary", "VAN_BERKEL"))),
    "paper_check": (
        "Warum zwei Personen prüfen lassen?",
        "Beim Abtippen oder Prüfen von Bögen übersieht eine Person einzelne Fehler, und schon wenige verschieben "
        "Mittelwerte und Tests. Eine zweite, unabhängige Eingabe oder Prüfung findet deutlich mehr davon als bloßes "
        "Durchsehen.",
        (BARCHARD_PACE,)),
    # G2: planning and analysis
    "sem_controls": (
        "Welche Kontrollvariablen?",
        "Nur Größen, die vor den Einflussgrößen feststehen und sie wie die Zielgrößen beeinflussen können (etwa Alter "
        "oder Bildung), und vorab festgelegt. Eine Größe, auf die die Einflussgröße selbst wirkt (ein Mittler), "
        "verfälscht als Kontrolle den Pfad; viele Kontrollen „auf Verdacht“ kosten Power und machen Ergebnisse "
        "beliebig.",
        (("app.sem", "KLINE"), HAYES_2022)),
    "sem_estimation": (
        "Welche Schätzung?",
        "Fehlende Werte: Full Information ML nutzt alle Teilnahmen und ist bei Lücken fast immer besser als das Weglassen "
        "unvollständiger Fälle, das Power kostet und verzerrt, sobald Lücken nicht rein zufällig sind. Antworten: Bei "
        "fünf und mehr Stufen und nicht zu schiefen Antworten ist ML mit robusten Fehlern üblich; bei weniger Stufen "
        "unterschätzt ML die Ladungen, dann geordnete Kategorien (WLSMV). Beides zusammen geht nicht: WLSMV nutzt "
        "paarweise vorhandene Antworten.",
        (("app.sem_means", "ENDERS"), ("app.sem", "RHEMTULLA"))),
    "sem_compare": (
        "Warum ein Konkurrenzmodell vorab?",
        "Ein Vergleich, den man erst nach dem Blick in die Daten wählt, passt das Modell an den Zufall an. Vorab "
        "festgelegt prüft er eine Frage der Theorie, etwa ob eine Vermittlung vollständig ist. Nur Modelle, die man "
        "begründen kann; ein schlechter passendes Konkurrenzmodell bestätigt das eigene nicht automatisch.",
        (("app.sem", "MACCALLUM_1992"), ("app.sem", "BURNHAM"))),
    # longitudinal and multilevel concept 2026-10-17 (E14)
    "clpm_or_riclpm": (
        "CLPM oder RI-CLPM?",
        "Das klassische Cross-Lagged-Panel-Modell mischt stabile Unterschiede zwischen Personen mit Wirkungen innerhalb "
        "der Personen: Wer immer viel X und viel Y hat, lässt X auf Y wirken, ohne dass sich bei einer Person etwas "
        "bewegt. Das RI-CLPM nimmt diese stabilen Unterschiede heraus; es braucht drei Wellen und mehr Personen.",
        (("app.panel_models", "HAMAKER"), ("app.panel_models", "MULDER_HAMAKER"))),
    "centring": (
        "Warum am Personenmittel zentrieren?",
        "Ein Zusammenhang über alle Einträge mischt zwei Fragen: ob Personen mit mehr X auch mehr Y haben (zwischen) "
        "und ob eine Person in Momenten mit mehr X auch mehr Y hat (innerhalb). Beide können verschieden ausfallen; "
        "die Zerlegung in Personenmittel und Abweichung trennt sie.",
        (("app.mixed", "ENDERS_TOFIGHI"),)),
    "sem_invariance": (
        "Warum Messinvarianz prüfen?",
        "Wer Gruppen vergleicht (Sprachfassungen, Bedingungen, Geschlechter), nimmt an, dass die Skala in allen dasselbe "
        "misst. Hält das nicht, kann ein Unterschied der Mittelwerte von der Übersetzung oder vom Verständnis einer "
        "Aussage kommen statt vom Konstrukt. Metrisch erlaubt den Vergleich von Zusammenhängen, skalar den von "
        "Mittelwerten.",
        (("app.sem_means", "VANDENBERG"), ("app.sem_means", "CHEN"))),
    "sem_higher": (
        "Wann ein Konstrukt höherer Ordnung?",
        "Wenn die Theorie ein Ganzes aus mehreren Teilen kennt und die Hypothesen über das Ganze sprechen. Es macht das "
        "Modell sparsamer und vermeidet Kollinearität zwischen eng verwandten Teilen. Die Teile werden in Schritt 1 wie "
        "jedes Konstrukt geprüft, das Ganze in Schritt 2 über die Werte der Teile: reflektiv mit Ladungen, CR und AVE, "
        "formativ mit VIF und Gewichten.",
        (("app.sem", "SARSTEDT_2019"), ("app.sem", "BECKER_2012"))),
    "sem_formative": (
        "Reflektiv oder formativ?",
        "Reflektiv, wenn das Konstrukt die Antworten verursacht: Die Aussagen sind austauschbar und hängen eng zusammen "
        "(fast alle Skalen). Formativ, wenn die Aussagen das Konstrukt erst bilden und je etwas Eigenes beitragen (ein "
        "Index); dann fehlt mit einer Aussage ein Teil der Bedeutung. Vier Fragen helfen: Verläuft die Ursache vom "
        "Konstrukt zu den Aussagen (reflektiv) oder umgekehrt (formativ)? Sind die Aussagen austauschbar? Ändern sie "
        "sich gemeinsam? Haben sie dieselben Ursachen und Folgen? Im Zweifel reflektiv: Ein validiertes Instrument ist "
        "fast immer so gebaut. Für ein formatives Konstrukt eine Frage einplanen, die es im Ganzen misst; sie dient der "
        "Redundanzanalyse.",
        (("app.sem", "JARVIS"), ("app.sem", "HAIR"), ("app.sem", "CHEAH"))),
    "moderation_mediation": (
        "Vermittlung oder Moderation?",
        "Vermittlung (Mediation) beschreibt, wie eine Wirkung zustande kommt: über eine Größe dazwischen. Moderation "
        "beschreibt, für wen oder wann sie stärker oder schwächer ist; die dritte Größe verändert dann die Stärke "
        "eines Pfads.",
        (HAYES_2022,)),
    "org_design": (
        "Warum bekommt jede Befragung eine Kopie?",
        "Das Erscheinungsbild gehört zu den Erhebungsbedingungen: Farben und Gestaltung können beeinflussen, wie "
        "geantwortet wird. Ändert sich das Organisationsdesign, soll keine laufende Befragung mitten in der Erhebung "
        "anders aussehen. Deshalb übernimmt jede Befragung eine Kopie, und Änderungen der Mitglieder betreffen nur ihre "
        "Kopie.",
        (COUPER_2004,)),
    "seal": (
        "Was belegt die Siegelkette?",
        "Sie zeigt später, dass seit dem Zeitstempel kein Fall unbemerkt geändert, entfernt oder hinzugefügt wurde. "
        "Ob die Antworten ehrlich oder sorgfältig gegeben wurden, belegt sie nicht; dafür gibt es die "
        "Qualitätskontrolle.",
        (RFC_3161,)),
    # the words of the DDI controlled vocabularies (DDI export, app/ddi.py; Ralf 2026-10-27)
    "sampling_procedure": (
        "Wahrscheinlichkeitsauswahl oder nicht?",
        "Nur bei einer Wahrscheinlichkeitsauswahl (Zufallsauswahl, Zufallspanel) hat jede Person der Grundgesamtheit "
        "eine bekannte Chance, ausgewählt zu werden; dann gelten Konfidenzintervalle und p-Werte für die Grundgesamtheit. "
        "Willkürliche Auswahl, Quotenstichprobe, respondentengesteuerte Auswahl (Schneeball) und Access-Panel sind "
        "Nicht-Wahrscheinlichkeitsauswahlen, eine bewusste Auswahl oder theoretische Stichprobe ist das Mittel "
        "qualitativer Studien.",
        (BAKER_2013, GLASER_STRAUSS, DDI_CV)),
    "random_kind": (
        "Welche Art von Zufallsauswahl?",
        "Die einfache Zufallsauswahl braucht eine Liste aller und ist am leichtesten auszuwerten; die systematische "
        "zieht jede k-te Person und ist ebenso gut, solange die Liste keine Regelmäßigkeit hat. Geschichtet wird, damit "
        "kleine Gruppen sicher vorkommen (genauer, aber ungleich gewichtet, wenn nicht proportional), Klumpen und Stufen "
        "sparen Wege und Listen, machen die Schätzung aber ungenauer, weil sich Personen einer Gruppe ähneln (Designeffekt).",
        (LOHR, DDI_CV)),
    "population": (
        "Wie genau die Grundgesamtheit beschreiben?",
        "So genau, dass man sagen kann, wer dazugehört und wer nicht: wer (z. B. Studierende), wo (RWTH Aachen), wann "
        "(Wintersemester 2026/27) und mit welchen Grenzen (ab 18 Jahren). Nur für diese Gruppe lassen sich Ergebnisse "
        "verallgemeinern, und Archive ordnen die Studie danach ein.",
        (DOERING, DDI_CV)),
    "time_method": (
        "Querschnitt, Panel oder Trend?",
        "Ein Querschnitt befragt einmal; ein Längsschnitt als Panel befragt dieselben Personen mehrmals und zeigt "
        "Veränderung in den Personen. Ein Trend (wiederholter Querschnitt) befragt jedes Mal neue Personen und zeigt "
        "Veränderung in der Grundgesamtheit, nicht in einzelnen Personen.",
        (DOERING, DDI_CV)),
    "ddi_export": (
        "Wozu DDI?",
        "DDI ist der Standard, mit dem Datenarchive wie GESIS und der CESSDA-Katalog Studien beschreiben. Die Datei "
        "nennt Erhebungsart, Zeitbezug und Auswahlverfahren mit den festen Begriffen der DDI-Vokabulare, etwa "
        "„Selbstausgefüllter Fragebogen: Webbasiert (CAWI)“, und bringt Fragebogen und Codebuch in eine Form, die "
        "ein Archiv ohne Abtippen übernehmen kann.",
        (DDI_L4, DDI_CV)),
    "archive_where": (
        "Zenodo oder Coscine?",
        "Coscine bewahrt Daten in der Hochschule auf, auch nicht öffentliche, wie die DFG es für Forschungsdaten "
        "mindestens zehn Jahre verlangt; Zenodo macht sie mit DOI für alle auffindbar. Wer veröffentlichen darf, nimmt "
        "beides: Coscine als Archiv, Zenodo oder die Veröffentlichungsanfrage für die DOI.",
        (DFG_KODEX,)),
    "validated_scale": (
        "Warum die Skala unverändert lassen?",
        "Reliabilität, Gültigkeit und Vergleichswerte einer geprüften Skala gelten nur für ihren Wortlaut und alle "
        "ihre Aussagen. Wer Aussagen umformuliert oder weglässt, misst etwas anderes und muss die Güte selbst neu "
        "prüfen.",
        (DOERING,)),
    "copy_or_loop": (
        "Kopien oder Schleife?",
        "Sollen dieselben Fragen zu mehreren Szenarien oder Produkten gestellt werden, ist eine Schleife meist besser: "
        "Sie zeigt den Block je Tabellenzeile, auf Wunsch in zufälliger Reihenfolge gegen Reihenfolgeeffekte, und die "
        "Antworten aller Durchgänge lassen sich direkt vergleichen. Kopien lohnen sich, wenn sich die Fragen je Kontext "
        "unterscheiden; jede Kopie bekommt eigene Variablennamen.",
        (AUSPURG_HINZ,)),
    "ink_slides": (
        "Auf Folien schreiben?",
        "Anstreichen und Zeigen lenkt den Blick auf das, worum es gerade geht; eine Meta-Analyse findet dafür einen "
        "kleinen bis mittleren Lerneffekt. Vor dem Publikum zu zeichnen kann das Verstehen zusätzlich fördern, zu viele "
        "Striche lenken dagegen ab: Folie leeren, wenn die Notiz ihren Zweck erfüllt hat.",
        (SCHNEIDER_2018, FIORELLA_MAYER_2016)),
    # SEM evaluation in two steps, statements left out, model versions (2026-10-26)
    "sem_two_steps": (
        "Erst das Messmodell, dann das Strukturmodell?",
        "Ein Pfad zwischen zwei Konstrukten ist nur so gut wie ihre Messung: Bei schwachen Ladungen, geringer "
        "Reliabilität oder zu ähnlichen Konstrukten misst er etwas anderes als gemeint. Darum zuerst Reliabilität und "
        "Validität jedes Konstrukts prüfen und erst dann Pfade, R² und Effekte deuten.",
        (ANDERSON_GERBING, ("app.sem", "HAIR_2019"))),
    "sem_drop": (
        "Eine Aussage weglassen?",
        "Reflektiv: unter einer Ladung von 0,40 weglassen, zwischen 0,40 und 0,70 nur, wenn CR und AVE dadurch steigen "
        "und der Inhalt des Konstrukts erhalten bleibt. Formativ erst, wenn das Gewicht nicht signifikant und die "
        "Ladung unter 0,50 ist, und nur, wenn die Aussage inhaltlich verzichtbar ist; jede Aussage trägt einen Teil der "
        "Bedeutung. Der Grund wird festgehalten, denn eine Änderung nach Sicht der Daten macht die Version explorativ.",
        (("app.sem", "HAIR"), ("app.sem", "CENFETELLI"), ("app.sem", "DIAMANTOPOULOS_W"))),
    "sem_versions": (
        "Hauptmodell oder explorative Version?",
        "Das vorab festgelegte Modell prüft die Hypothesen; jede Änderung nach Sicht der Ergebnisse (eine Aussage "
        "weglassen, einen Pfad ergänzen) passt das Modell an diese Stichprobe an und macht Zufallsbefunde "
        "wahrscheinlicher. Darum bleibt das Hauptmodell das Ergebnis, Varianten werden als explorativ berichtet, alle "
        "genannt und am besten mit neuen Daten geprüft.",
        (("app.sem", "MACCALLUM_1992"), SIMMONS_2011, NOSEK_2018)),
}

# key: (symbol as shown, name, what it means, example, sources)
TERMS = {
    "n": ("n", "Fallzahl", "Wie viele Teilnahmen in die Zahl eingehen. Je kleiner n, desto unsicherer der Wert.",
          "n = 192: 192 Personen haben die Frage beantwortet.", (DOERING,)),
    "m": ("M", "Mittelwert", "Der Durchschnitt der Antworten. Bei schiefen Verteilungen oder Ausreißern sagt der Median "
          "oft mehr.", "M = 3,4 auf einer Skala von 1 bis 5: im Schnitt leicht über der Mitte.", (DOERING,)),
    "sd": ("SD", "Standardabweichung", "Wie weit die Antworten im Mittel um den Mittelwert streuen; in derselben "
           "Einheit wie die Antworten.", "SD = 1,2 bei M = 3,4: Die meisten Antworten liegen zwischen etwa 2 und 5.",
           (DOERING,)),
    "md": ("Md", "Median", "Der mittlere Wert: Die Hälfte der Antworten liegt darunter, die Hälfte darüber. "
           "Unempfindlich gegen Ausreißer.", "Md = 46 Jahre: Die Hälfte ist jünger als 46.", (DOERING,)),
    # 95 % belong to the procedure, not to the one interval (audit 2026-10-05, I-8; Hoekstra et al., 2014)
    "ci": ("KI", "Konfidenzintervall", "Ein Bereich aus einem Verfahren, das bei Zufallsstichproben in 95 % der Fälle "
           "den wahren Wert der Grundgesamtheit einschließt. Schmal = genau, breit = unsicher.",
           "31 % [25 %; 38 %]: Werte zwischen 25 und 38 % sind mit den Daten gut vereinbar.",
           (("app.intervals", "WILSON"), HOEKSTRA)),
    "alpha": ("α", "Cronbachs Alpha", "Wie gut die Aussagen einer Skala dasselbe messen (Reliabilität). Werte ab "
              "etwa 0,70 gelten oft als ausreichend; Alpha hängt aber auch von der Zahl der Aussagen ab.",
              "α = 0,82 für vier Aussagen: Die Skala misst recht einheitlich.", (("app.scales", "CRONBACH"),)),
    "omega": ("ω", "McDonalds Omega", "Wie Alpha ein Maß der Reliabilität, aber ohne die Annahme, dass alle Aussagen "
              "gleich stark zum Wert beitragen; darum meist vorzuziehen.",
              "ω = 0,84: etwas höher als α, weil die Aussagen unterschiedlich gut messen.", (("app.scales", "HAYES"),)),
    "d": ("d", "Cohens d", "Wie groß ein Unterschied zwischen zwei Gruppen ist, in Standardabweichungen. Etwa 0,2 "
          "klein, 0,5 mittel, 0,8 groß, je nach Fach.", "d = 0,47: ein mittlerer Unterschied.",
          (("app.samplesize", "COHEN"),)),
    "r": ("r", "Korrelation", "Wie eng zwei Größen zusammenhängen, von −1 bis 1. Etwa 0,1 schwach, 0,3 mittel, 0,5 "
          "stark. Ein Zusammenhang ist noch keine Ursache.", "r = −0,32 zwischen Handyzeit und Schlaf: Wer länger am "
          "Handy ist, schläft eher kürzer.", (("app.samplesize", "COHEN"),)),
    "p": ("p", "p-Wert", "Wie wahrscheinlich ein mindestens so großer Unterschied wäre, wenn es in Wahrheit keinen "
          "gibt. Unter ,05 gilt er meist als „signifikant“; über seine Größe sagt p nichts, dafür gibt es d oder r.",
          "p = ,002: Ein solcher Unterschied wäre ohne echten Unterschied sehr selten.",
          (("app.compare", "TOMCZAK"),)),
    "rsi": ("RSI", "Relative Speed Index", "Wie viel schneller eine Person als üblich war, über alle Seiten gemittelt "
            "und je Seite am Median gemessen. Über 2 heißt: doppelt so schnell wie typisch.",
            "RSI = 2,6: Die Person war deutlich zu schnell, um die Fragen zu lesen.", (("app.quality", "LEINER"),)),
    "speeder": ("Speeder", "Zu schnell Antwortende (Speeder)", "Teilnahmen, die deutlich schneller als üblich waren, "
                "gemessen an der Gesamtdauer oder am Relative Speed Index. So schnell lassen sich die Fragen kaum lesen; "
                "die Qualitätssicherung markiert solche Teilnahmen oder nimmt sie heraus.",
                "Median 12 Minuten, diese Person 3 Minuten: als Speeder markiert.", (("app.quality", "LEINER"),)),
    "screen_out": ("Screen-out", "Screen-out", "Ein Ende für Personen, die nicht zur Zielgruppe gehören, meist nach einer "
                   "Filterfrage am Anfang. Ein Screen-out ist weder Abschluss noch Abbruch und geht nicht in die "
                   "Ergebnisse ein.", "Wer jünger als 18 ist, endet nach der ersten Seite mit einem Screen-out.",
                   (DOERING,)),
    # choice models and segments (concept "Lernhilfen für die neuen Funktionen", L4 to L6)
    "partworth": ("Teilnutzen", "Teilnutzen", "Wie stark eine Stufe die Wahl gegenüber den anderen Stufen desselben "
                  "Merkmals fördert, je Merkmal um 0 zentriert. Nur innerhalb eines Merkmals vergleichbar.",
                  "9 €: +0,8, 15 €: −0,9: Der niedrigere Preis wird deutlich vorgezogen.",
                  (("app.conjoint_design", "ORME"),)),
    "cva": ("CVA", "Bewertungs-Conjoint (CVA)", "Conjoint, in der Personen ganze Angebote bewerten statt wählen, einzeln "
            "auf einer Skala, paarweise abgestuft oder alle in eine Reihenfolge gebracht. Eine Regression je Person ergibt Teilnutzen ohne HB; dafür braucht "
            "jede Person mehr Aufgaben als Teilnutzen, also wenige Merkmale.",
            "14 Wohnungsangebote je Person auf 0 bis 10: Miete −2,1 bis +2,4 Punkte.",
            (("app.cva_design", "GREEN_SRINIVASAN"),)),
    "aca": ("ACA", "Adaptive Conjoint (ACA)", "Conjoint in zwei Schritten: Personen bewerten zuerst Stufen und Wichtigkeit "
            "selbst (Selbstauskunft), dann Paare aus wenigen Merkmalen, die das System nach jeder Antwort so wählt, dass "
            "beide Seiten ähnlich gut sind. Verträgt viele Merkmale, unterschätzt aber den Preis.",
            "12 Merkmale, 20 Paare: Wichtigkeit Akku 24 %, Kamera 18 %.",
            (("app.aca_design", "JOHNSON"), ("app.aca_design", "PINNELL"))),
    "self_explicated": ("Selbstauskunft", "Selbstauskunft (Self-Explicated)", "Teilnutzen, die direkt aus den Angaben der "
                        "Person stammen: wie sehr ihr jede Stufe gefällt, mal wie wichtig ihr das Merkmal ist. Schnell, "
                        "aber ohne Abwägung; ACA korrigiert sie mit den Paaren.",
                        "Selbstauskunft 30 %, nach den Paaren 22 %: Die Person hat das Merkmal überschätzt.",
                        (("app.aca_design", "JOHNSON"),)),
    "calibration": ("Kalibrierung", "Kalibrierung (ACA)", "Einige Angebote mit „Wie wahrscheinlich würden Sie es wählen (0 "
                    "bis 100)?“ am Ende; die Teilnutzen werden so gestreckt, dass ihre Summen diese Wahrscheinlichkeiten "
                    "vorhersagen. Nur für Kaufwahrscheinlichkeiten nötig.",
                    "R² = 0,8: Die Teilnutzen sagen die genannten Wahrscheinlichkeiten gut vorher.",
                    (("app.aca_design", "JOHNSON"),)),
    "r2_person": ("R² je Person", "R² je Person", "Wie gut die Teilnutzen einer Person ihre eigenen Bewertungen nachbilden, von 0 bis "
                  "1. Niedrige Werte heißen: widersprüchlich oder zufällig geantwortet; Askurious markiert Werte unter 0,3.",
                  "R² = 0,85: Die Bewertungen folgen fast ganz den Merkmalen.", (("app.cva_design", "GREEN_SRINIVASAN"),)),
    "importance": ("Wichtigkeit", "Wichtigkeit der Merkmale", "Die Spannweite der Teilnutzen eines Merkmals als Anteil "
                   "an der Summe aller Spannweiten. Hängt von den gewählten Stufen ab: Weit auseinanderliegende Stufen "
                   "machen ein Merkmal wichtiger.", "Preis 42 %: Der Preis bewegt die Wahl am stärksten.",
                   (("app.conjoint_design", "ORME"),)),
    "wtp": ("Zahlungsbereitschaft", "Zahlungsbereitschaft", "Was eine Stufe in Geld wert ist, über den Preiskoeffizienten "
            "umgerechnet. Nur für alle oder je Segment; je Person wäre sie zu unsicher.",
            "+2,40 € für „Bio“ gegenüber dem Mittel der Stufen.", (("app.conjoint_design", "ORME"),)),
    "score": ("Score", "Wahrscheinlichkeits-Score", "Wie wahrscheinlich eine Aussage in einem Set durchschnittlicher "
              "Aussagen als wichtigste gewählt wird, auf Summe 100 skaliert. Verhältnisse nach dieser "
              "Konvention hängen von der Set-Größe ab.",
              "20 gegen 10: nach dieser Konvention doppelt so wichtig.", (("app.maxdiff_analysis", "LOUVIERE"),)),
    "rho2": ("ρ²", "McFadden-ρ²", "Wie viel besser das Modell die Wahlen erklärt als Raten (alle Alternativen gleich "
             "wahrscheinlich). Die Werte liegen deutlich unter einem R² der Regression.",
             "ρ² = 0,28: Das Modell erklärt die Wahlen gut.", (("app.choice_model", "MCFADDEN"),)),
    "rlh": ("RLH", "Root Likelihood", "Das geometrische Mittel der Wahrscheinlichkeiten, die das Modell den Wahlen einer "
            "Person gibt. Zufall wäre 1 durch die Zahl der Alternativen.",
            "RLH = 0,62 bei drei Alternativen (Zufall 0,33): klare Vorlieben.", (("app.conjoint_design", "ORME"),)),
    "sop": ("Share of Preference", "Präferenzanteil (Share of Preference)", "Der Anteil der Wahlen, den ein Produkt im "
            "Simulator bekäme, als Wahrscheinlichkeit je Person gemittelt. Kein Marktanteil: Bekanntheit und Vertrieb "
            "fehlen.", "34 % gegen zwei andere Produkte.", (("app.simulator", "ORME"),)),
    "first_choice": ("First Choice", "First Choice", "Wie oft das Produkt für eine Person den höchsten Nutzen hat, über "
                     "die Personen gemittelt. Reagiert stärker auf kleine Änderungen als der Präferenzanteil.",
                     "41 %: für gut vier von zehn Personen das beste Produkt.", (("app.simulator", "ORME"),)),
    "hit_rate": ("Trefferquote", "Trefferquote", "Anteil der Personen, deren Wahl in der Holdout-Aufgabe das Modell "
                 "richtig vorhersagt; mit dem Zufall vergleichen.", "62 % bei drei Optionen (Zufall 33 %).",
                 (("app.holdouts", "ORME"),)),
    "mae": ("MAE", "Mittlere absolute Abweichung", "Wie weit die vorhergesagten Wahlanteile im Mittel von den "
            "beobachteten abweichen, in Prozentpunkten. Kleiner ist besser.",
            "MAE = 3,1 PP: Die Anteile liegen im Mittel um 3 Punkte daneben.", (("app.holdouts", "ORME"),)),
    "rhat": ("R̂", "R-hat", "Ob die HB-Kette sich eingependelt hat: Vergleicht die erste mit der zweiten Hälfte der "
             "Ziehungen. Unter 1,01 gilt sie als konvergiert.", "R̂ = 1,004: konvergiert.",
             (("app.hb_convergence", "VEHTARI"),)),
    "ess": ("ESS", "Effektive Stichprobengröße", "Wie vielen unabhängigen Ziehungen die gespeicherten entsprechen; "
            "aufeinanderfolgende Ziehungen ähneln sich. Ab 400 ist der Mittelwert verlässlich; für Intervalle zählen "
            "die Ränder der Verteilung, die dieser Wert nicht prüft.",
            "ESS = 850 von 1 000 Ziehungen.", (("app.hb_convergence", "VEHTARI"), ("app.hb_convergence", "GEYER"))),
    "hb": ("HB", "Hierarchical Bayes", "Schätzt für jede Person eigene Nutzenwerte: Sie streuen um einen gemeinsamen "
           "Mittelwert, und wer wenige Aufgaben beantwortet hat, wird zu diesem Mittel hin gezogen. Grundlage für "
           "Marktsimulator, Segmente und Werte je Person.", "Teilnutzen von „Bio“ je Person zwischen −0,4 und +2,1, im "
           "Mittel +0,8.", (("app.hb", "ROSSI"), ("app.hb", "ALLENBY"))),
    "mnl": ("MNL", "Multinomiales Logit-Modell", "Ein gemeinsames Modell für die ganze Stichprobe: die "
            "Wahrscheinlichkeit jeder Alternative aus der Summe ihrer Teilnutzen. Unterschiede zwischen Personen "
            "bleiben unberücksichtigt; dafür ist es schnell und braucht weniger Daten als Hierarchical Bayes.",
            "Preis 12 € statt 9 €: im Mittel −0,6 Nutzen für alle.", (("app.choice_model", "MCFADDEN"),
                                                                       ("app.choice_model", "TRAIN"))),
    "holdout": ("Holdout", "Holdout-Aufgabe", "Eine feste Wahl- oder Bewertungsaufgabe, die nicht in die Schätzung "
                "eingeht. Das Modell sagt ihre Antworten vorher; Trefferquote und MAE zeigen, wie gut das gelingt.",
                "Trefferquote 62 % bei drei Optionen (Zufall 33 %).", (("app.holdouts", "ORME"),)),
    "bic": ("BIC", "BIC, AIC3 und CAIC", "Passung gegen Zahl der Parameter, kleiner ist besser. AIC3 bestraft weniger "
            "und schlägt eher mehr Segmente vor, CAIC eher weniger.",
            "BIC 4 812 bei drei, 4 830 bei vier Segmenten: Drei passen besser.",
            (("app.latent_class", "SCHWARZ"), ("app.latent_class", "BOZDOGAN"), ("app.latent_class", "ANDREWS_CURRIM"))),
    "entropy": ("Entropie", "Entropie R²", "Wie eindeutig die Personen einem Segment zugeordnet sind, von 0 bis 1. "
                "Niedrige Werte: Segmente nur anteilig lesen.",
                "0,86: Die meisten Personen gehören klar zu einem Segment.", (("app.latent_class", "RAMASWAMY"),)),
    "membership": ("Zugehörigkeitswahrscheinlichkeit", "Zugehörigkeitswahrscheinlichkeit", "Wie wahrscheinlich eine "
                   "Person zu jedem Segment gehört; zusammen 100 %. Größen und Beschreibungen der Segmente rechnen damit.",
                   "70 % „Preisbewusst“, 30 % „Qualität zuerst“.", (("app.latent_class", "WEDEL"),)),
    # concept "Lernhilfen: Erweiterungen und Verbesserungen", E3
    # beside Welch's test the difference in the average of both variances (math audit 2026-10-06, L-2 and E-9)
    "g": ("δ*", "Standardisierter Unterschied δ*", "Wie Cohens d oder Hedges’ g der Unterschied zweier Mittelwerte in "
          "Standardabweichungen, hier im Mittel beider Varianzen, damit das Intervall wie der Welch-Test auch bei "
          "ungleicher Streuung hält; der Wert ist wie Hedges’ g für kleine Gruppen korrigiert. Etwa 0,2 klein, "
          "0,5 mittel, 0,8 groß.", "δ* = 0,41 [0,12; 0,70]: ein kleiner bis "
          "mittlerer Unterschied, aber unsicher.", (("app.compare", "BONETT"),)),
    "omega2": ("ω²", "Omega-Quadrat", "Welcher Anteil der Streuung auf die Gruppen zurückgeht, bei mehr als zwei "
               "Gruppen; weniger verzerrt als η². Etwa 0,01 klein, 0,06 mittel, 0,14 groß.",
               "ω² = 0,05: Die Gruppen erklären 5 % der Unterschiede.", (("app.compare", "OLEJNIK"),)),
    "chi2": ("χ²", "Chi-Quadrat", "Wie weit eine Kreuztabelle von dem abweicht, was ohne Zusammenhang zu erwarten wäre. "
             "Seine Größe hängt von der Fallzahl ab; die Stärke zeigt Cramérs V.",
             "χ²(2, N = 180) = 9,4: Die Verteilung unterscheidet sich zwischen den Gruppen.",
             (("app.compare", "AGRESTI"),)),
    "cramer_v": ("V", "Cramérs V", "Wie stark der Zusammenhang in einer Kreuztabelle ist, von 0 (keiner) bis 1. Etwa "
                 "0,1 schwach, 0,3 mittel, 0,5 stark (bei zwei Spalten).", "V = 0,23: ein schwacher bis mittlerer "
                 "Zusammenhang.", (("app.compare", "CRAMER"),)),
    "u": ("U", "Mann-Whitney U", "Ein Rangtest: wie oft Werte der einen Gruppe über denen der anderen liegen. Er "
          "braucht keine Mittelwerte; die Stärke steht als r daneben.", "U = 3 120, r = 0,21: Die erste Gruppe "
          "antwortet etwas höher.", (("app.compare", "MANN_WHITNEY"),)),
    "gini": ("Gini", "Gini-Koeffizient", "Wie ungleich ein Wert verteilt ist: 0 heißt alle gleich, Werte nahe 1 "
             "heißen, wenige haben fast alles. Nur für Werte mit echtem Nullpunkt.",
             "Gini = 0,48 bei Ausgaben pro Monat: deutlich ungleich verteilt.", (("app.results", "GINI"),)),
    "difficulty": ("P", "Schwierigkeit", "Wie hoch eine Aussage im Mittel beantwortet wird, von 0 (alle auf der "
                   "niedrigsten Stufe) bis 100. Sehr hohe oder niedrige Werte unterscheiden kaum zwischen Personen.",
                   "P = 82: Fast alle stimmen zu.", (("app.scales", "LIENERT"),)),
    "rit": ("r_it", "Trennschärfe", "Wie eng eine Aussage mit der Summe der übrigen zusammenhängt. Unter etwa 0,30 "
            "misst sie wahrscheinlich etwas anderes als die Skala.", "r_it = 0,18: diese Aussage prüfen.",
            (("app.scales", "LIENERT"),)),
    "loading": ("λ", "Ladung", "Wie stark eine Aussage das Gemeinsame der Skala misst, standardisiert im "
                "Ein-Faktor-Modell; aus den Ladungen entsteht ω.", "λ = 0,74: Die Aussage misst die Skala gut.",
                (("app.scales", "MCDONALD"),)),
    "odds_ratio": ("OR", "Odds Ratio", "Um welchen Faktor die Chance steigt, in einem Segment statt im Bezugssegment "
                   "zu sein: gegenüber der Bezugsantwort oder je Standardabweichung. 1 heißt kein Unterschied.",
                   "OR = 2,1 für Studierende: Ihre Chance ist etwa doppelt so hoch.", (("app.latent_class", "DAYTON"),)),
    "n_eff": ("n_eff", "Effektive Fallzahl", "Wie vielen ungewichteten Teilnahmen die gewichtete Stichprobe an "
              "Genauigkeit entspricht. Je ungleicher die Gewichte, desto kleiner.",
              "n = 400, n_eff = 310: Die Gewichtung kostet etwa ein Viertel der Genauigkeit.", (("app.weights", "KISH"),)),
    "d_eff": ("D-Effizienz", "D-Effizienz", "Wie gut ein Versuchsplan die Wirkungen der Faktoren trennt: 100 heißt "
              "Faktoren unkorreliert und Stufen gleich oft. Ab etwa 90 gilt ein Plan als gut.",
              "D-Effizienz 94: kaum schlechter als ein vollständiger Plan.", (("app.doe", "KUHFELD"),)),
    "psm_points": ("OPP · IPP · PMC · PME", "Preispunkte nach Van Westendorp", "Die Schnittpunkte der vier "
                   "Preiskurven: PMC und PME begrenzen den akzeptablen Bereich, OPP (zu billig × zu teuer) und IPP "
                   "(weder billig noch teuer) liegen darin. Sie beschreiben Wahrnehmungen, keine Kaufbereitschaft.",
                   "PMC 8 €, OPP 10 €, IPP 12 €, PME 15 €: Akzeptabel sind 8 bis 15 €.", (("app.items.psm", "CITATION"),)),
    # the structural equation model (the section only for whom it is on, glossary())
    "beta": ("β", "Standardisierter Pfad", "Wie stark eine Größe eine andere im Strukturgleichungsmodell vorhersagt, "
             "wenn die übrigen Pfade berücksichtigt sind, in Standardabweichungen. Meist zwischen −1 und 1; bei stark "
             "korrelierten Prädiktoren kann es darüber liegen. Bei einer 0/1-Größe (z. B. Bedingung) ist β kein Effekt "
             "je Standardabweichung: Dann zählt der Unterschied der Gruppen.",
             "β = 0,45: Wer eine Standardabweichung mehr Technikbereitschaft hat, hat 0,45 Standardabweichungen mehr "
             "Nutzungsabsicht.",
             (("app.sem", "BOLLEN"),)),
    "indirect": ("ab", "Indirekter Effekt", "Wie viel einer Wirkung über einen Mittler läuft: das Produkt der Pfade "
                 "x → m und m → y. Gestützt, wenn beide Pfade signifikant sind (gemeinsame Signifikanz); das "
                 "Bootstrap-Intervall zeigt, wie groß der Effekt ist.",
                 "ab = 0,30, 95-%-KI [0,19; 0,42]: Ein Teil der Wirkung läuft über den Mittler.",
                 (("app.sem", "MACKINNON"), ("app.sem", "PREACHER"))),
    "interaction": ("β(x × w)", "Moderation (Wechselwirkung)", "Ob die Stärke eines Pfads von einer dritten Größe "
                    "abhängt, dem Moderator. Geschätzt als Pfad eines Produkts aus Einflussgröße und Moderator; die "
                    "einfachen Steigungen zeigen den Pfad, wo der Moderator niedrig (−1 SD) und hoch (+1 SD) ist, die "
                    "SD aus dem geschätzten Modell; ist der Moderator eine 0/1-Größe, in jeder der beiden Gruppen. Eine "
                    "positive Wechselwirkung heißt: Der Pfad wird mit dem Moderator größer (ist er negativ, wird er dem "
                    "Betrag nach schwächer). Bei einem Moderator mit Gruppen außerhalb einer Vermittlung wird der Pfad "
                    "in jeder Gruppe geschätzt und verglichen.",
                    "β(x × w) = 0,20; einfache Steigungen 0,15 (−1 SD) und 0,55 (+1 SD): je höher w, desto größer der "
                    "Pfad von x auf y.",
                    (("app.sem", "MARSH"), ("app.sem", "LIN"), ("app.sem", "HAYES"))),
    "cfi": ("CFI · TLI", "Vergleichende Fitindizes", "Wie viel besser das Modell passt als ein Modell ohne jeden "
            "Zusammenhang; 1 ist perfekt, ab etwa 0,95 gilt der Fit als gut (Konvention, kein Test).",
            "CFI = 0,98: Das Modell passt fast so gut wie ein Modell ohne Einschränkungen.", (("app.sem", "BENTLER"), ("app.sem", "HU_BENTLER"))),
    "rmsea": ("RMSEA", "Root Mean Square Error of Approximation", "Der Fehler des Modells je Freiheitsgrad; bis "
              "etwa 0,06 gilt der Fit als gut, über 0,10 als schlecht. Mit 90-%-Intervall.",
              "RMSEA = 0,04 [0,02; 0,06]: guter Fit, auch die obere Grenze ist akzeptabel.",
              (("app.sem", "STEIGER"), ("app.sem", "HU_BENTLER"))),
    "srmr": ("SRMR", "Standardised Root Mean Square Residual", "Die mittlere Abweichung zwischen beobachteten und "
             "vom Modell erwarteten Korrelationen; bis etwa 0,08 gilt sie als klein.",
             "SRMR = 0,04: Die Korrelationen werden gut getroffen.", (("app.sem", "HU_BENTLER"),)),
    "ave": ("AVE · CR", "Erfasste Varianz und Composite Reliability", "AVE: wie viel der Streuung der Aussagen das "
            "Konstrukt im Mittel erklärt, ab 0,50 mehr als der Messfehler. CR: Reliabilität aus den Ladungen, ab 0,70.",
            "AVE = 0,61, CR = 0,86: Das Konstrukt ist gut gemessen.", (("app.sem", "FORNELL"),)),
    "htmt": ("HTMT", "Heterotrait-Monotrait-Verhältnis", "Wie ähnlich zwei Konstrukte gemessen werden: Korrelationen "
             "zwischen ihren Aussagen im Verhältnis zu denen innerhalb. Über 0,85 trennen sie sich kaum.",
             "HTMT = 0,92: Die beiden Skalen messen fast dasselbe.", (("app.sem", "HENSELER"),)),
    "robust_fit": ("CFI · TLI · RMSEA robust", "Robuste Fitindizes", "CFI, TLI und RMSEA, korrigiert für nicht "
                   "normalverteilte Antworten (schief oder mit vielen Extremwerten, wie oft bei Likert-Aussagen). Sie "
                   "nutzen dieselben Grenzen; bei normalverteilten Antworten sind sie fast gleich den üblichen Werten.",
                   "CFI = 0,94, robuster CFI = 0,96: Mit der Korrektur passt das Modell gut.",
                   (("app.sem", "BROSSEAU_LIARD"), ("app.sem", "BROSSEAU_SAVALEI"))),
    "residual_cor": ("r − r̂", "Residualkorrelation", "Wie weit die beobachtete Korrelation zweier Aussagen von der "
                     "abweicht, die das Modell erwartet. Über 0,10 erklärt das Modell diesen Zusammenhang schlecht. Ein "
                     "Hinweis zum Verstehen des Fits, kein Anlass, das Modell nachträglich anzupassen.",
                     "0,14 zwischen zwei Aussagen derselben Skala: Sie haben mehr gemeinsam als den Faktor, etwa eine "
                     "ähnliche Formulierung.", (("app.sem", "KLINE"), ("app.sem", "MACCALLUM_1992"))),
    "control": ("Kontrolle", "Kontrollvariable", "Eine Größe, deren Einfluss aus den Pfaden der Hypothesen "
                "herausgerechnet wird: Sie bekommt einen Pfad auf jede erklärte Größe. Ein Pfad gilt dann bei gleicher "
                "Kontrollvariable, etwa bei gleichem Alter.",
                "Kontrolliert für Alter: β(Technikbereitschaft → Nutzung) = 0,31 gilt für Personen gleichen Alters.",
                (("app.sem", "KLINE"),)),
    "single_item": ("rel", "Einzelfrage mit Reliabilität", "Eine einzelne Frage enthält Messfehler wie jede Aussage einer "
                    "Skala. Mit einer angenommenen Reliabilität (oft 0,70) rechnet das Modell diesen Fehler heraus; ohne "
                    "gilt die Frage als fehlerfrei, und ihre Pfade fallen zu klein aus.",
                    "Reliabilität 0,70: 30 % der Streuung der Antworten gelten als Messfehler.",
                    (("app.sem", "PETRESCU"),)),
    "f2": ("f²", "Effektstärke f²", "Wie viel erklärte Varianz (R²) der Zielgröße fehlt, wenn ein Pfad wegfällt, im "
           "Verhältnis zur unerklärten. Ab 0,02 klein, ab 0,15 mittel, ab 0,35 groß.",
           "f² = 0,18: Der Pfad trägt einen mittleren Teil zur Erklärung bei.", (("app.sem", "COHEN_1988"), ("app.sem", "HAIR"))),
    "vif": ("VIF", "Varianzinflationsfaktor", "Wie stark eine Größe mit den anderen Einflussgrößen desselben Ziels (oder "
            "einer formativen Aussage mit den anderen Aussagen) zusammenhängt. Ab 5 überschneiden sie sich zu stark, "
            "ihre Pfade oder Gewichte werden unsicher; unter 3 ist es gut.",
            "VIF = 6,2: Zwei Einflussgrößen messen fast dasselbe.", (("app.sem", "HAIR"),)),
    "formative": ("Modus B", "Formatives Konstrukt", "Die Aussagen bilden das Konstrukt, statt es zu messen: Fällt eine "
                  "weg, ändert sich seine Bedeutung (z. B. ein Index aus Ausstattungsmerkmalen). Geschätzt mit Gewichten "
                  "(PLS Modus B); AVE, CR und HTMT gelten nur für reflektive Konstrukte.",
                  "Gewicht 0,42, p = ,003: Die Aussage trägt deutlich zum Index bei.", (("app.sem", "HAIR"),)),
    "redundancy": ("Redundanzanalyse", "Redundanzanalyse", "Prüft die Konvergenzvalidität eines formativen Konstrukts: "
                   "Es sagt ein Globalmaß desselben Konstrukts vorher, meist eine einzelne Frage, die es im Ganzen misst. "
                   "Ab einem Pfad von 0,70 (R² ab 0,50) gilt es als ausreichend.",
                   "Pfad 0,74: Der Index erfasst, was die globale Frage meint.",
                   (("app.sem", "CHIN_1998"), ("app.sem", "CHEAH"))),
    "plspredict": ("Q²predict", "Vorhersagekraft (PLSpredict)", "Wie gut das Modell die Aussagen der erklärten "
                   "Konstrukte für neue Fälle vorhersagt, geprüft durch Kreuzvalidierung: Q²_predict über 0 ist besser "
                   "als der Mittelwert; verglichen wird der Fehler mit einem einfachen linearen Modell.",
                   "Vorhersagekraft mittel: Bei der Mehrheit der Aussagen sagt das Modell besser vorher als das lineare "
                   "Modell.", (("app.sem", "SHMUELI"),)),
    "construct_score": ("Konstruktwert", "Konstruktwert", "Der Wert jeder Teilnahme auf einem Konstrukt des PLS-Modells: "
                        "die gewichtete Summe ihrer Aussagen, standardisiert (Mittel 0, SD 1). Für weitere Rechnungen "
                        "außerhalb, etwa eine Clusteranalyse.",
                        "1,2: Diese Teilnahme liegt 1,2 Standardabweichungen über dem Mittel.", (("app.sem", "HAIR"),)),
    "fiml": ("FIML", "Full Information Maximum Likelihood", "Schätzt das Modell aus allen Teilnahmen, auch denen mit "
             "Lücken: Jede zählt mit den Antworten, die sie gegeben hat. Unverzerrt, wenn Lücken nur vom Beantworteten "
             "abhängen (MAR); listenweises Weglassen braucht die strengere Annahme, dass sie rein zufällig sind (MCAR).",
             "N = 412 mit FIML statt 287 vollständige: mehr Power, weniger Verzerrung.",
             (("app.sem_means", "ENDERS"), ("app.sem_means", "YUAN_BENTLER"))),
    "wlsmv": ("WLSMV", "Geordnete Kategorien (WLSMV)", "Behandelt die Antworten einer Aussage als geordnete Stufen "
              "statt als Zahlen: Hinter jeder Aussage steht eine stetige Neigung, die Schwellen teilen sie in Stufen. "
              "Das Modell rechnet mit polychorischen Korrelationen. Genauer als ML bei weniger als fünf Stufen oder "
              "sehr schiefen Antworten.",
              "4-stufige Aussagen: Ladungen mit WLSMV 0,78 statt 0,71 mit ML.",
              (("app.sem_ordinal", "MUTHEN"), ("app.sem", "RHEMTULLA"))),
    "weighted_sem": ("w", "Gewichtetes Modell", "Mit den Gewichten der Befragung (Seite „Quoten“) zählt jede Teilnahme "
                     "so viel, wie ihre Gruppe in der Zielpopulation ausmacht: Kovarianzen gewichtet, Standardfehler "
                     "robust mit den Gewichten. n_eff ist die Stichprobe, der die gewichtete gleichwertig ist.",
                     "n = 400, n_eff = 310: Die Gewichte kosten Genauigkeit wie 90 Teilnahmen weniger.",
                     (("app.sem", "ASPAROUHOV_2005"),)),
    "second_order": ("2. Ordnung", "Faktor zweiter Ordnung", "Ein Konstrukt über den Subskalen eines Instruments: Es "
                     "erklärt, was die Subskalen gemeinsam haben, und geht mit einem Pfad ins Modell statt mit einem je "
                     "Subskala. Passt, wenn das Manual einen Gesamtwert vorsieht.",
                     "Die Big-Five-Subskalen bleiben getrennt; die drei Facetten von Technikbereitschaft bilden einen "
                     "Faktor zweiter Ordnung.", (("app.sem", "KLINE"),)),
    "higher_order": ("HOC", "Konstrukt höherer Ordnung", "Ein Konstrukt über mehreren Konstrukten unterer Ordnung (den "
                     "Subskalen eines Instruments oder mehreren Skalen). Bei PLS zweistufig geschätzt: Stufe 1 liefert die "
                     "Werte der Teile, Stufe 2 nimmt sie als Indikatoren. Reflektiv, wenn das Ganze die Teile prägt; "
                     "formativ, wenn die Teile das Ganze bilden.",
                     "Bedienen, Informieren und Kommunizieren bilden zusammen „Digitale Kompetenz“ (formativ).",
                     (("app.sem", "SARSTEDT_2019"), ("app.sem", "BECKER_2012"))),
    "model_compare": ("ΔAIC", "Modellvergleich", "Zwei vorab festgelegte Modelle, das eine eine Einschränkung des anderen "
                      "(z. B. ohne direkten Effekt): Der Differenztest zeigt, ob das Weggelassene fehlt; AIC und BIC "
                      "wägen Fit gegen Sparsamkeit ab, kleiner ist besser.",
                      "Δχ²(1) = 0,8, p = ,37; ΔBIC = −5,2: Die vollständige Vermittlung genügt.",
                      (("app.sem_means", "SATORRA_BENTLER_2001"), ("app.sem", "BURNHAM"))),
    "efa": ("EFA", "Explorative Faktorenanalyse", "Sucht in den Antworten auf die Aussagen, wie viele gemeinsame "
            "Faktoren hinter ihnen stehen und welche Aussage zu welchem gehört, ohne Vorgabe. Für selbst gebaute Skalen "
            "und um Subskalen zu prüfen; danach braucht eine Bestätigung neue Daten.",
            "Zwei Faktoren: Die Aussagen 1 bis 4 laden auf F1, 5 bis 8 auf F2 – wie die beiden geplanten Subskalen.",
            (("app.efa", "FABRIGAR"), ("app.efa", "BERNAARDS"))),
    "parallel": ("PA", "Parallelanalyse", "Wie viele Faktoren eine Analyse behalten sollte: so viele, wie Eigenwerte der "
                 "Daten über denen zufälliger Daten gleicher Größe liegen (95. Perzentil). Verlässlicher als die "
                 "Faustregel „Eigenwert über 1“.", "Eigenwerte 2,9 und 1,6 gegen 1,2 und 1,1 zufällig: zwei Faktoren.",
                 (("app.efa", "HORN"), ("app.efa", "GLORFELD"))),
    "mc_power": ("Power (Simulation)", "Power per Simulation", "Wie oft ein Test in vielen künstlichen Datensätzen aus dem "
                 "geplanten Modell signifikant wird. Genauer als die Formeln, wo diese nur nähern: bei Vermittlung, "
                 "Moderation und kleinen Stichproben.",
                 "H3 74 % bei N = 250: In drei von vier Studien dieser Größe wäre die Vermittlung belegt.",
                 (("app.sem_power_job", "MUTHEN_MUTHEN"), ("app.sem_power_job", "WANG_RHEMTULLA"))),
    "riclpm": ("RI-CLPM", "Random-Intercept-Cross-Lagged-Panel-Modell", "Prüft über mehrere Wellen, ob X in einer "
               "Welle Y in der nächsten verändert, nachdem stabile Unterschiede zwischen den Personen (zufällige "
               "Achsenabschnitte) herausgenommen sind. Die Pfade beschreiben Schwankungen einer Person um ihr eigenes "
               "Niveau.", "β = 0,22 von X auf Y: Wer in einer Welle mehr X als gewöhnlich hat, hat in der nächsten mehr Y.",
               (("app.panel_models", "HAMAKER"), ("app.panel_models", "MULDER_HAMAKER"))),
    "growth": ("I · S", "Latente Wachstumskurve", "Beschreibt den Verlauf über die Wellen mit einem Ausgangsniveau (I) und "
               "einer Steigung (S) je Person; ihre Mittelwerte zeigen den durchschnittlichen Verlauf, ihre Varianzen, wie "
               "verschieden Personen beginnen und sich verändern.",
               "M(S) = 0,26 je Welle, Var(S) = 0,02: im Mittel ein Anstieg, mit wenig Unterschied zwischen Personen.",
               (("app.panel_models", "BOLLEN_CURRAN"),)),
    "invariance": ("Invarianz", "Messinvarianz", "Ob eine Skala in allen Gruppen dasselbe misst: dieselbe Struktur "
                   "(konfigural), dieselben Ladungen (metrisch), dazu dieselben Achsenabschnitte (skalar) und dieselben "
                   "Residuen (strikt). Erst mit metrischer Invarianz lassen sich Zusammenhänge, erst mit skalarer "
                   "Mittelwerte zwischen Gruppen vergleichen.",
                   "Skalar hält über die Sprachen: Mittelwerte der deutschen und englischen Fassung sind vergleichbar.",
                   (("app.sem_means", "VANDENBERG"), ("app.sem_means", "CHEN"))),
    "latent_mean": ("α", "Latenter Mittelwert", "Der Mittelwert eines Konstrukts in einer Gruppe, verglichen mit der "
                    "ersten Gruppe, ohne Messfehler. d drückt den Unterschied in Standardabweichungen aus.",
                    "d = 0,40: Die zweite Gruppe liegt 0,4 Standardabweichungen höher.", (("app.sem_means", "VANDENBERG"),)),
    "jn": ("Johnson-Neyman", "Johnson-Neyman-Bereich", "Für welche Werte des Moderators der Pfad signifikant ist: die "
           "Grenzen, an denen das 95-%-Intervall der Steigung die Null berührt. Ergänzt die einfachen Steigungen, die "
           "nur zwei Punkte (−1 SD und +1 SD) zeigen.",
           "Signifikant, wo w über −0,40 SD liegt: für alle außer den Personen mit sehr niedrigem w.",
           (("app.sem", "JOHNSON_NEYMAN"), HAYES_2022)),
    # concept "Lernhilfen: dritte Runde" (F3)
    "power": ("Power", "Power (1 − β)", "Wie wahrscheinlich ein Test eine Wirkung der angenommenen Größe findet, "
              "wenn es sie gibt. Meist werden 80 % angestrebt.", "Power 80 %: In acht von zehn Studien würde der "
              "Effekt gefunden.", (("app.samplesize", "COHEN"),)),
    "mde": ("Erreichbarer Effekt", "Erreichbarer Effekt", "Die kleinste Wirkung, die mit der gegebenen Fallzahl und "
            "Power noch gefunden würde. Ist sie größer als der kleinste relevante Effekt, reicht die Stichprobe "
            "nicht.", "d = 0,45 bei 160 Personen: Kleinere Unterschiede blieben wahrscheinlich unentdeckt.",
            (("app.samplesize", "LAKENS_2022"),)),
    "p_holm": ("p (Holm)", "Holm-korrigiertes p", "Der p-Wert nach der Korrektur für mehrere Hypothesen; er wird wie "
               "gewohnt mit 0,05 verglichen.", "p = 0,012, korrigiert für drei Hypothesen 0,036: weiter signifikant.",
               (("app.study_plan", "HOLM"),)),
    "kripp": ("α", "Krippendorffs Alpha", "Wie sehr zwei Kodierende über den Zufall hinaus übereinstimmen: 1 = völlig "
              "gleich, 0 = wie zufällig. Ab etwa 0,80 verlässlich.", "α = 0,74: brauchbar für vorläufige Schlüsse.",
              (("app.coding_check", "KRIPPENDORFF"),)),
    "similarity": ("Ähnlichkeit", "Ähnlichkeit zweier Karten", "Der Anteil der Personen, die zwei Karten auf denselben "
                   "Stapel legten, unter denen, die beide sortiert haben.", "62 %: Die meisten sehen die beiden "
                   "Karten zusammen.", (("app.items.card_sort", "RUGG"),)),
    "iat_d": ("D", "D-Wert des IAT", "Wie viel schneller eine Person bei der einen Zuordnung als bei der anderen ist, "
              "in Standardabweichungen ihrer Zeiten. Nach verbreiteter Konvention etwa 0,15 schwach, 0,35 mittel, "
              "0,65 stark.",
              "D = 0,42: eine mittlere Assoziation.", (("app.items.iat", "GREENWALD"),)),
    "k_discount": ("k (Aufschub)", "k (Belohnungsaufschub)", "Wie stark ein späterer Betrag an Wert verliert; größer heißt "
                   "ungeduldiger. Meist als ln(k) berichtet, weil k sehr schief verteilt ist.",
                   "k = 0,01: Ein Betrag in 100 Tagen ist etwa die Hälfte wert.", (("app.items.discounting", "MYERSON"),)),
    # concept "Lernhilfen: vierte Runde" (G3)
    "bt_worth": ("Wert", "Wert im Paarvergleich (Bradley-Terry)", "Wie stark eine Option vorgezogen wird, aus allen "
                 "Vergleichen geschätzt und auf die Summe 100 gebracht. Das Verhältnis zweier Werte ist die Chance, dass "
                 "die eine der anderen vorgezogen wird.", "Werte 40 und 20: A wird B in etwa zwei von drei Vergleichen "
                 "vorgezogen.", (("app.paired_analysis", "BRADLEY_TERRY"),)),
    "zeta": ("ζ", "Kendalls Konsistenzkoeffizient", "Wie widerspruchsfrei eine Person verglichen hat: 1 heißt keine "
             "zirkulären Triaden (A vor B, B vor C, C vor A), 0 so viele wie überhaupt möglich.",
             "ζ = 0,90: wenige Widersprüche, die Person hat überlegt gewählt.", (("app.paired_analysis", "KENDALL"),)),
    "icc": ("ICC(1)", "Intraklassenkorrelation ICC(1)", "Der Anteil der Streuung, der zwischen den Personen liegt; der "
            "Rest liegt innerhalb der Personen, von Eintrag zu Eintrag. Klein heißt: Die Antworten schwanken vor allem "
            "im Alltag einer Person.", "ICC(1) = 0,30: 30 % der Streuung trennt die Personen, 70 % schwankt im Alltag.",
            (("app.diary", "SHROUT"), ("app.diary", "BOLGER"))),
    "within_between": ("innerhalb · zwischen", "Effekt innerhalb und zwischen Personen", "Innerhalb: ob eine Person "
                       "in Momenten, in denen X über ihrem eigenen Mittel liegt, auch mehr Y berichtet. Zwischen: ob "
                       "Personen mit höherem mittleren X auch höheres Y haben. Das Mehrebenenmodell schätzt beide getrennt.",
                       "innerhalb b = 0,41: Ein Punkt mehr X als gewöhnlich geht mit 0,41 Punkten mehr Y einher.",
                       (("app.mixed", "ENDERS_TOFIGHI"), ("app.mixed", "SATTERTHWAITE"))),
    "random_slope": ("zufällige Steigung", "Zufällige Steigung", "Der Effekt innerhalb der Personen darf sich zwischen "
                     "ihnen unterscheiden; das Modell schätzt seinen Mittelwert und wie stark er streut. Sonst gilt "
                     "für alle derselbe Effekt.", "Varianz der Steigung 0,06: Der Effekt liegt bei den meisten zwischen "
                     "etwa 0,0 und 0,9.", (("app.mixed", "LAIRD_WARE"),)),
    "omega_levels": ("ω innerhalb · zwischen", "Reliabilität auf zwei Ebenen", "Wie verlässlich eine Skala Momente "
                     "derselben Person unterscheidet (innerhalb) und wie verlässlich sie Personen unterscheidet "
                     "(zwischen), aus der Zweiebenen-Faktorenanalyse.", "ω innerhalb 0,62, zwischen 0,91: gut für "
                     "Personen, schwächer für Schwankungen im Alltag.", (("app.sem_twolevel", "GELDHOF"),)),
    "compliance": ("Antwortquote", "Antwortquote (Compliance)", "Der Anteil der Abfragen, die eine Person beantwortet hat, "
                   "unter allen, die sie bekommen hat. Unter der Hälfte gilt sie meist als auffällig.",
                   "18 von 24 Abfragen: Antwortquote 75 %.", (("app.diary", "WRZUS"),)),
    # SEM evaluation in two steps and model versions (2026-10-26)
    "rho_a": ("ρA", "Reliabilität ρA", "Die Reliabilität eines Konstrukts bei PLS, aus den Gewichten geschätzt. Sie liegt "
              "meist zwischen Cronbachs Alpha (untere Grenze) und CR (obere Grenze); wie diese ab 0,70, über 0,95 sagen "
              "die Aussagen fast dasselbe.", "α = 0,78, ρA = 0,81, CR = 0,86: Das Konstrukt ist zuverlässig gemessen.",
              (("app.sem", "DIJKSTRA"), ("app.sem", "HAIR_2019"))),
    "r2": ("R²", "Erklärte Varianz (R²)", "Welcher Anteil der Unterschiede in einer Zielgröße durch die Größen erklärt "
           "wird, die auf sie zeigen, von 0 bis 1. Das korrigierte R² berücksichtigt die Zahl der Einflussgrößen; bei PLS "
           "gilt ab 0,25 schwach, ab 0,50 mittel, ab 0,75 substanziell, je nach Fach.",
           "R² = 0,42: Die Einflussgrößen erklären gut zwei Fünftel der Unterschiede in der Kaufabsicht.",
           (("app.sem", "HAIR_2011"), ("app.sem", "HAIR"))),
    "total_effect": ("Gesamteffekt", "Gesamteffekt", "Der direkte Effekt plus die Summe aller indirekten Wege von einer "
                     "Größe zu einer anderen. Er zeigt, wie stark eine Größe insgesamt wirkt, auch wenn der direkte Pfad "
                     "klein ist; ein Intervall ohne die Null spricht für einen Gesamteffekt.",
                     "direkt 0,08, indirekt 0,21, gesamt 0,29 [0,17; 0,40]: Die Wirkung läuft vor allem über die "
                     "vermittelnde Größe.", (("app.sem", "HAIR"),)),
    "outer_weight": ("w", "Gewicht (PLS)", "Wie stark eine Aussage in den Wert ihres Konstrukts eingeht. Bei einem "
                     "formativen Konstrukt ihr eigener Beitrag neben den anderen Aussagen, wie ein Regressionsgewicht; ein "
                     "kleines Gewicht heißt dann nicht unwichtig, es zählt auch die Ladung.",
                     "w = 0,12, nicht signifikant, Ladung 0,63: Die Aussage bleibt, sie überschneidet sich nur mit den "
                     "anderen.", (("app.sem", "CENFETELLI"), ("app.sem", "HAIR"))),
    "bic_weight": ("BIC-Gewicht", "BIC-Gewicht (Modellvergleich)", "Aus dem Unterschied der BIC zweier Modelle: die "
                   "Wahrscheinlichkeit, dass ein Modell unter den verglichenen das bessere ist, wenn vorher beide als gleich "
                   "wahrscheinlich galten. Kleineres BIC ist besser; es belohnt Erklärung und bestraft zusätzliche Pfade.",
                   "BIC-Gewicht 88,0 % für Version 3: Sie ist unter beiden sehr wahrscheinlich die bessere.",
                   (("app.sem", "BURNHAM"), ("app.sem_versions", "SHARMA"))),
}

# the sections of the glossary page, in this order; within a section alphabetical (headings only, no row of jump links)
SECTIONS = (
    ("Grundlagen", "", ("n", "m", "md", "sd", "ci", "gini")),
    ("Reliabilität", "", ("alpha", "omega")),
    ("Itemanalyse", "", ("difficulty", "rit", "loading", "efa", "parallel")),
    ("Unterschiede und Zusammenhänge", "", ("d", "g", "r", "p", "omega2", "chi2", "cramer_v", "u")),
    ("Datenqualität", "", ("rsi", "speeder", "screen_out")),
    ("Gewichtung und Versuchsplan", "", ("n_eff", "d_eff")),
    ("Preis", "", ("psm_points",)),
    # concept "Lernhilfen: fünfte Runde", H2: the choice models and SEM in smaller sections
    ("Paarvergleich und Conjoint-Arten", "", ("bt_worth", "zeta", "cva", "r2_person", "aca", "self_explicated",
                                              "calibration")),
    ("Wahlmodelle", "R̂ und ESS prüfen die Rechnung der HB-Schätzung, nicht ihr Ergebnis.",
     ("partworth", "importance", "wtp", "score", "rho2", "rlh", "sop", "first_choice", "hit_rate", "mae", "rhat", "ess",
      "hb", "mnl", "holdout")),
    ("Segmente", "", ("bic", "entropy", "membership", "odds_ratio")),
    ("Strukturgleichungsmodelle", "", ("beta", "indirect", "interaction", "jn", "control", "single_item",
                                       "second_order", "higher_order", "formative", "redundancy", "r2", "total_effect")),
    ("SEM: Schätzung und Passung", "", ("fiml", "wlsmv", "weighted_sem", "model_compare", "bic_weight", "cfi", "rmsea", "srmr",
                                        "robust_fit", "residual_cor", "ave", "htmt")),
    ("PLS", "", ("f2", "vif", "plspredict", "construct_score", "rho_a", "outer_weight")),
    ("Längsschnitt und Gruppenvergleich", "", ("invariance", "latent_mean", "riclpm", "growth")),
    ("Planung und Power", "", ("power", "mde", "p_holm", "mc_power")),
    ("Offene Antworten und Sortierungen", "", ("kripp", "similarity")),
    ("IAT und Belohnungsaufschub", "", ("iat_d", "k_discount")),
    ("Tagebuchstudie", "", ("icc", "compliance", "within_between", "random_slope", "omega_levels")),
)
DIARY_SECTION = "Tagebuchstudie"  # only with the experimental diary study (app/experimental.py), like SEM before
SEM_SECTION = "Strukturgleichungsmodelle"  # for everyone since 2026-10-11 (before only with the experimental feature)

# the same in English (concept "Mehrsprachige Oberfläche", E13): title and text of each fold, symbol, name, meaning
# and example of each term; the sources stay those of the German tables. Read at call time by reason() and term().
REASONS_EN = {
    "answer_mode": (
        "Optional, request or required?",
        "Required questions prevent gaps but invite random answers or break-offs when someone cannot or will not "
        "answer. “Request” marks the question once and then lets people go on: usually the best middle way; required mainly for "
        "questions without which the rest cannot be analysed (e.g. the experimental condition)."),
    "scale_points": (
        "How many points should a scale have?",
        "Five to seven labelled points measure agreement most reliably; more points add hardly any precision, fewer "
        "lose information. A midpoint allows a genuinely neutral position but is also used as a way out."),
    "missing_option": (
        "“Don’t know” as missing?",
        "An opt-out option prevents guessed answers from people without an opinion; marked as missing it does not "
        "count in means and shares. It is also chosen out of convenience, though; so only offer it where not knowing "
        "really occurs."),
    "randomize": (
        "Random order?",
        "If people tend to pick the first options or statements, the answers are biased (order effect); a random "
        "order per person spreads this effect evenly. Not random for scales with a natural order (e.g. frequencies) "
        "and for “Other” or “Don’t know” at the end."),
    "attention_check": (
        "Add an attention check?",
        "A question with a set answer (“Please select ‘somewhat agree’”) detects people who do not read. One or two "
        "are enough; more annoy attentive participants, and the exclusion should be fixed before data collection."),
    "speeder": (
        "Total duration or Relative Speed Index?",
        "A total duration below a share of the median is easy to explain but penalises questionnaires with paths of "
        "different length. The Relative Speed Index compares each page with its own median and is then more precise; "
        "it needs page times."),
    "data_pool": (
        "Which participations count?",
        "Results only apply to real, completed participations; test runs and dummy data help to check the "
        "questionnaire and the analysis but do not belong in a report. Including broken-off participations raises the "
        "number of cases but biases the results if certain people break off more often."),
    "quota_hard": (
        "Hard or soft quota?",
        "A hard quota never lets more people into a sub-quota than planned and keeps places free for this; a soft one "
        "lets everyone finish who has already started and exceeds the target by the tolerance. Hard if a panel pays "
        "for every participation; soft if nobody should be turned away."),
    "weighting": (
        "Weight the data?",
        "Weighting makes up for some groups being too rare in the sample, but only for the characteristics used for "
        "weighting. Large weights reduce the effective sample size and make results less certain."),
    "sample_size": (
        "How many participants do I need?",
        "The number depends on the smallest effect you want to detect and on the certainty you want (power, usually "
        "80 %). Small effects need many people; if you survey too few, you will often miss a real difference."),
    "conjoint_tasks": (
        "How many tasks and alternatives?",
        "More tasks per person make the estimate more precise until fatigue sets in; 8 to 15 tasks with three or four "
        "alternatives are common, below 6 Askurious warns. Several versions of the experimental design spread the combinations across the "
        "people and make the experimental design more robust."),
    "none_option": (
        "Offer “None of these”?",
        "Without this option everyone has to choose, even if they like nothing; with it you can estimate whether "
        "anything would be bought at all. It gives less information about the attributes, though, because some "
        "people choose it often. The follow-up question (dual response) combines both: everyone first chooses among the "
        "options and then says whether they would really take the chosen one; that gives preferences from everyone and "
        "still an estimate of whether anything would be bought."),
    "conjoint_kind": (
        "Which kind of conjoint?",
        "CBC asks for choices and suits prices and market shares with up to about six attributes; ACBC builds the "
        "variants around each person’s preferred variant and pays off with many attributes and small samples, but takes "
        "longer. The ratings-based conjoint (CVA) has offers rated instead of chosen and gives values per person without "
        "HB, but only with few attributes; vignettes (a loop in the flow) suit when any questions follow each case."),
    "hb_or_logit": (
        "Aggregate logit or HB?",
        "The aggregate logit describes the sample as a whole and is enough for the ranking and for the importance "
        "computed from the mean part-worths; if preferences differ in direction, it underestimates the importance. HB "
        "estimates values per person; only then do distributions, simulator and export show how different the "
        "preferences are. Both are estimated independently from the same choices: the logit in seconds and therefore at "
        "once, HB with many thousand draws at the press of a button. "
        "Small differences are normal, because the logit averages different preferences away beforehand and "
        "usually comes out flatter: for the part-worths direction and mostly also ranking should agree; importances "
        "can differ markedly, above all for attributes that people rate in opposite ways (HB averages the ranges of "
        "the individual people)."),
    "holdout": (
        "Add a holdout task?",
        "A task or set that is left out of the estimation checks whether the model predicts real answers; without it "
        "only the fit to your own data remains. One or two are enough and hardly lengthen the survey."),
    "segments_k": (
        "How many segments?",
        "The BIC suggests the number that best balances fit and parsimony; Askurious skips very small and unstable "
        "solutions. Also choose by content: segments that differ clearly, can be named and predict holdouts better."),
    "segments_covariates": (
        "Covariates in the model?",
        "Covariates let each person’s segment shares depend on questions such as age; this estimates these "
        "relationships more precisely but shifts the segments themselves. If you want segments formed from the "
        "choices alone, leave them out and use “Describe segments”."),
    "segments_describe": (
        "Why BCH weights?",
        "Nobody belongs to a segment for certain. Counting each person only in their most likely segment, or in "
        "proportion to their probabilities, blurs the differences between the segments. BCH weights remove this "
        "misclassification on average; the table and tests use them, the tests are exploratory."),
    "geo_level": (
        "How precisely to store?",
        "Exact coordinates can point to a home; store only as precisely as the research question needs, for regions "
        "a postcode or federal state is enough. Privacy concerns lower the willingness to share data from a "
        "smartphone."),
    "geo_fallback": (
        "Fallback from the IP address?",
        "The fallback fills gaps in the federal state but is less precise: IP databases usually get the country "
        "right, the region often not, on mobile networks especially rarely. Leave it off if a wrong federal state "
        "does more harm than a missing one."),
    "dropdown": (
        "Drop-down list or option buttons?",
        "Option buttons show all answers at once; in a drop-down list people more often pick the first visible "
        "entries. A list suits long, familiar lists such as federal states or years of birth."),
    "slider": (
        "Slider or labelled points?",
        "Sliders look modern but take more time and lead more often to break-offs and missing answers, especially "
        "among people with less education. Labelled points are usually the safer choice."),
    "likert_grid": (
        "A grid or single questions?",
        "A grid saves space and time but invites break-offs and answering every row the same. Better split long "
        "grids over several pages."),
    "side_by_side": (
        "Side-by-side or single questions?",
        "Several questions side by side save space and show what belongs together. The more columns and answer "
        "fields a grid has, the more is skipped and clicked through; on a smartphone asking row by row gives better "
        "data, which is why each row shows as a card there. At most three columns, few points and a condition (“only "
        "if used”) keep the grid short; anyone who needs a scale with a scale score and α takes the Likert matrix."),
    "progress": (
        "Show a progress bar?",
        "A steady progress bar hardly reduces break-offs; one that moves slowly at the start increases them. With "
        "long questionnaires with many branches, rather leave it out."),
    "length": (
        "How long may the questionnaire be?",
        "The longer the announced duration, the fewer people start; towards the end answers get shorter, faster and "
        "more uniform. An honest figure in the invitation is still better than one that is too short."),
    "random_assignment": (
        "Why assign at random?",
        "Only random assignment makes sure the groups differ in the condition alone; then a difference can be read as "
        "an effect. Groups of equal size give the most power."),
    "which_test": (
        "Why this test?",
        "The test follows from the level of measurement of both variables: two metric variables Pearson’s "
        "correlation, one only ordered Spearman’s rank correlation; groups and a metric outcome Welch’s t-test or, with "
        "more than two groups, Welch’s analysis of variance, neither of which assumes equal spreads. With ordered points "
        "without equal distances a rank test (Mann-Whitney, Kruskal-Wallis) compares the location of the groups "
        "without means. Two nominal variables are tested by the χ² test of the cross table."),
    "weighting_scope": (
        "What is weighted?",
        "Shares, means, medians and standard deviations are weighted; their confidence intervals use the "
        "linearisation standard error, which also takes different spread in the weighting groups into account. The "
        "counts (n) are the unweighted ones. Reliability (α, ω), scale scores, choice models and willingness to pay "
        "stay unweighted; the choice models (counts, aggregate model, HB, segments) use all participations of the "
        "unweighted view, so they rest on the same people as without weighting. In the group comparison (“Split "
        "into groups”) shares, medians, means, standard deviations and intervals are weighted as well; tests are "
        "there only in the unweighted view."),
    "card_sort_mode": (
        "Open, closed or hybrid?",
        "Open, people make their own piles and show how they themselves order things; worth it early on. Closed "
        "checks whether given piles fit; hybrid allows both but is harder to analyse."),
    "q_sort_grid": (
        "Why a forced distribution?",
        "The fixed shape makes people weigh statements against each other instead of agreeing with everything; that "
        "makes the sorts comparable. It shows rank orders within a person, not absolute agreement."),
    "personal_code": (
        "Why a code instead of a name?",
        "A self-generated code links surveys of the same person without storing names. Some codes no longer match "
        "later; simple, stable parts reduce that."),
    "holm": (
        "Correct for several hypotheses?",
        "Anyone who tests many hypotheses is more likely to find a “significant” one by chance alone. Holm keeps the "
        "error rate for all of them together and loses less power than Bonferroni."),
    "preregistration": (
        "Why decide in advance?",
        "Recording hypotheses and analysis before the data separates testing from exploring; decisions made "
        "afterwards otherwise let too many findings look good by chance. Deviations stay allowed if they are named."),
    "study_material": (
        "Why pass on the survey material with notes?",
        "Only the complete material shows what participants saw and under which conditions; then a survey can be "
        "checked, replicated and compared with its pre-registration. Conditions, random orders and required questions "
        "decide who answered which questions, so they belong to it."),
    "second_coding": (
        "Why have a second person code?",
        "Only an independent second coding shows whether the categories are clear enough for others to apply them "
        "the same way. Krippendorff’s α from about 0.80 counts as reliable, from 0.67 for tentative conclusions."),
    "reminders": (
        "Remind, and how often?",
        "One or two reminders raise participation clearly; each further one brings less and annoys more. A few days "
        "apart and different wording work better than the same message again."),
    "voice": (
        "Speak instead of type?",
        "Spoken answers are usually longer and richer than typed ones, but many people do not like to speak aloud in a "
        "survey. Offer typing as a way out and do not make the voice message required."),
    "ai_chat": (
        "An access of its own and fixed instructions?",
        "Whoever studies how people use an AI assistant has to record which model answered with which instructions; "
        "otherwise the study cannot be repeated. An access of the study's own fixes the model and its origin, and "
        "participants have to be able to see that an AI is answering."),
    "guide_sub": (
        "Sub-question or a guide question of its own?",
        "A sub-question is a follow-up that fits only sometimes, such as “if applicable” or only for certain people. "
        "What should come up in every interview stays a guide question of its own. Ask follow-ups only once the "
        "narrative on the guide question has ended, so that they do not steer it."),
    "guide_probe_if": (
        "Follow-up, condition or a question of its own?",
        "A specific question asks about what did not come up by itself, so tie it to its aspect. A follow-up below it "
        "fits only if the specific question applies. If a question depends on an earlier one that is not directly "
        "above it, choose “Q1 applies” or “does not apply” under “only if”. The interview view only pales all of "
        "this; you decide in the conversation."),
    "conference": (
        "Why ask everyone?",
        "You may record only if everyone who speaks in the conversation has consented beforehand; recording secretly "
        "is a criminal offence. So ask everyone at the start, in a video conference the listeners too."),
    "copy_or_loop": (
        "Copies or a loop?",
        "If the same questions are to be asked about several scenarios or products, a loop is usually better: it shows "
        "the block per table row, in random order if wished against order effects, and the answers of all passes can be "
        "compared directly. Copies pay off when the questions differ per context; each copy gets variable names of its own."),
    "interview_task": (
        "Why a task in an interview?",
        "A small task gets people talking where a question alone finds little answer: they sort or order first and then "
        "give their reasons. So ask about the result afterwards, such as “Why is that at the top?”. The result is a "
        "prompt for the conversation, not a measurement; the reasons are in the transcript."),
    "interview_screen": (
        "Why the participant screen?",
        "In a video interview the person cannot otherwise lay out a task themselves; you would have to enter what they "
        "say. On their own device they order it themselves, and you see the result at once as a prompt for the "
        "conversation. Give the link only to them, and ask about it afterwards, such as “Why is that at the top?”. If "
        "they correct themselves out loud, change the result; the case overview then notes it as changed by you."),
    "transcript_export": (
        "Why line numbers and placeholders?",
        "Line numbers let you cite quotes verifiably, such as “I03, l. 12–15”. Replace names, places and employers with "
        "placeholders before exporting, as the file leaves the platform."),
    "transcript_check": (
        "Why read the transcript against the recording?",
        "Automatic transcription errs above all with names, technical terms, dialect and when two people speak at once. "
        "Read the transcript against the recording once before the analysis, and always the passages you quote."),
    "diary_trigger": (
        "Fixed times, random or on an event?",
        "Fixed times show the course over the day and are easy to plan around. Random prompts catch everyday life "
        "without people preparing for them; a button for an event suits rare situations that no prompt would catch."),
    "paper_check": (
        "Why let two people check?",
        "When typing or checking sheets one person misses single errors, and even a few shift means and tests. A "
        "second, independent entry or check finds far more of them than merely looking over."),
    "sem_controls": (
        "Which control variables?",
        "Only variables that are fixed before the predictors and can influence them as well as the outcomes (such as age "
        "or education), and chosen in advance. A variable the predictor itself affects (a mediator) distorts the path "
        "as a control; many controls “just in case” cost power and make results arbitrary."),
    "sem_estimation": (
        "Which estimation?",
        "Missing values: full information ML uses all participations and with gaps is almost always better than leaving "
        "out incomplete cases, which costs power and biases as soon as gaps are not purely random. Answers: with five "
        "or more levels and answers that are not too skewed, ML with robust errors is common; with fewer levels ML "
        "underestimates the loadings, then ordered categories (WLSMV). Both together are not possible: WLSMV uses "
        "pairwise available answers."),
    "sem_compare": (
        "Why a competing model in advance?",
        "A comparison chosen only after looking at the data fits the model to chance. Set in advance it tests a question "
        "of theory, e.g. whether a mediation is full. Only models one can justify; a competing model that fits worse "
        "does not automatically confirm one's own."),
    "clpm_or_riclpm": (
        "CLPM or RI-CLPM?",
        "The classic cross-lagged panel model mixes stable differences between people with effects within people: "
        "whoever always has a lot of X and a lot of Y lets X act on Y without anything moving within a person. The "
        "RI-CLPM takes these stable differences out; it needs three waves and more people."),
    "centring": (
        "Why centre at the person's mean?",
        "A relationship over all entries mixes two questions: whether people with more X also have more Y (between) "
        "and whether a person has more Y in moments with more X (within). Both can differ; splitting into the "
        "person's mean and the deviation from it separates them."),
    "sem_invariance": (
        "Why test measurement invariance?",
        "Anyone who compares groups (language versions, conditions, genders) assumes that the scale measures the same "
        "in all of them. If it does not, a difference of the means can come from the translation or from how a "
        "statement is understood instead of from the construct. Metric allows comparing relations, scalar comparing "
        "means."),
    "sem_higher": (
        "When a higher-order construct?",
        "When the theory knows a whole of several parts and the hypotheses speak about the whole. It makes the model "
        "more parsimonious and avoids collinearity between closely related parts. The parts are checked in step 1 like "
        "any construct, the whole in step 2 through the scores of the parts: reflective with loadings, CR and AVE, "
        "formative with VIF and weights."),
    "sem_formative": (
        "Reflective or formative?",
        "Reflective when the construct causes the answers: the statements are interchangeable and closely related (almost "
        "all scales). Formative when the statements form the construct and each adds something of its own (an index); "
        "then leaving one out removes part of the meaning. Four questions help: does the cause run from the construct "
        "to the statements (reflective) or the other way round (formative)? Are the statements interchangeable? Do they "
        "change together? Do they share their causes and consequences? When in doubt reflective: a validated instrument "
        "is almost always built that way. For a formative construct, plan a question that measures it as a whole; it "
        "serves the redundancy analysis."),
    "moderation_mediation": (
        "Mediation or moderation?",
        "Mediation describes how an effect comes about: through a variable in between. Moderation describes for whom "
        "or when it is stronger or weaker; the third variable then changes the strength of a path."),
    "org_design": (
        "Why does every survey get a copy?",
        "The appearance is part of the conditions of data collection: colours and layout can influence how people "
        "answer. If the organisation design changes, no running survey should look different in the middle of data "
        "collection. That is why every survey takes a copy, and members' changes only affect their copy."),
    "seal": (
        "What does the seal chain prove?",
        "It shows later that since the timestamp no case was changed, removed or added unnoticed. It does not prove "
        "that the answers were honest or careful; that is what the quality control is for."),
    "sampling_procedure": (
        "Probability sample or not?",
        "Only in a probability sample (random sample, probability panel) does every person of the population have a "
        "known chance to be chosen; then confidence intervals and p values hold for the population. An availability "
        "sample, a quota sample, respondent-assisted sampling (snowball) and an access panel are non-probability "
        "samples, a purposive sample or theoretical sampling is the means of qualitative studies."),
    "random_kind": (
        "Which kind of random sampling?",
        "Simple random sampling needs a list of everyone and is the easiest to analyse; systematic sampling takes every "
        "k-th person and is just as good as long as the list has no regular pattern. Stratifying makes sure small groups "
        "appear (more precise, but unequally weighted unless proportional), clusters and stages save travel and lists "
        "but make the estimate less precise, because people of one group are alike (design effect)."),
    "population": (
        "How precisely to describe the population?",
        "Precisely enough to say who belongs and who does not: who (e.g. students), where (RWTH Aachen), when (winter "
        "term 2026/27) and with which limits (aged 18 and over). Results can be generalised only to this group, and "
        "archives classify the study by it."),
    "time_method": (
        "Cross-section, panel or trend?",
        "A cross-section asks once; a longitudinal panel asks the same people several times and shows change within "
        "people. A trend (repeated cross-section) asks new people each time and shows change in the population, not "
        "in single people."),
    "ddi_export": (
        "Why DDI?",
        "DDI is the standard data archives such as GESIS and the CESSDA catalogue describe studies with. The file "
        "names mode, time method and sampling procedure with the fixed terms of the DDI vocabularies, such as "
        "“Self-administered questionnaire: Web-based (CAWI)”, and brings questionnaire and codebook into a form an "
        "archive can take over without retyping."),
    "archive_where": (
        "Zenodo or Coscine?",
        "Coscine keeps data within the university, also non-public ones, as the DFG requires for research data for at "
        "least ten years; Zenodo makes them findable for everyone with a DOI. Whoever may publish takes both: Coscine as "
        "the archive, Zenodo or the publication request for the DOI."),
    "validated_scale": (
        "Why leave the scale unchanged?",
        "The reliability, validity and norms of a tested scale hold only for its wording and all its statements. "
        "Rewording or dropping statements measures something else, and its quality has to be checked anew."),
    "ink_slides": (
        "Why write on slides?",
        "Highlighting and pointing draws the eye to what matters right now; a meta-analysis finds a small to medium "
        "effect on learning. Drawing in front of the audience can help understanding further, while too many strokes "
        "distract: clear the slide once a note has done its job."),
    "sem_two_steps": (
        "Measurement model first, then the structural model?",
        "A path between two constructs is only as good as their measurement: with weak loadings, low reliability or "
        "constructs that are too similar it measures something other than intended. So check the reliability and "
        "validity of every construct first and only then interpret paths, R² and effects."),
    "sem_drop": (
        "Leave a statement out?",
        "Reflective: leave it out below a loading of 0.40, between 0.40 and 0.70 only if CR and AVE rise and the "
        "content of the construct is kept. Formative only when the weight is not significant and the loading is below "
        "0.50, and only if the statement can be spared for its content; every statement carries part of the meaning. "
        "The reason is recorded, because a change after seeing the data makes the version exploratory."),
    "sem_versions": (
        "Main model or exploratory version?",
        "The model set in advance tests the hypotheses; every change after seeing the results (leaving out a "
        "statement, adding a path) fits the model to this sample and makes chance findings more likely. So the main "
        "model stays the result, variants are reported as exploratory, all of them named and best tested on new data."),
}

TERMS_EN = {
    "n": ("n", "Number of cases", "How many participations go into the figure. The smaller n, the less certain the "
          "value.", "n = 192: 192 people answered the question."),
    "m": ("M", "Mean", "The average of the answers. With skewed distributions or outliers the median often says "
          "more.", "M = 3.4 on a scale from 1 to 5: on average slightly above the middle."),
    "sd": ("SD", "Standard deviation", "How far the answers spread around the mean on average; in the same unit as "
           "the answers.", "SD = 1.2 with M = 3.4: most answers lie between about 2 and 5."),
    "md": ("Md", "Median", "The middle value: half of the answers lie below it, half above. Not sensitive to "
           "outliers.", "Md = 46 years: half are younger than 46."),
    "ci": ("CI", "Confidence interval", "A range from a procedure that, with random samples, includes the true value "
           "of the population in 95 % of cases. Narrow = precise, wide = uncertain.",
           "31 % [25 %; 38 %]: values between 25 and 38 % are well compatible with the data."),
    "alpha": ("α", "Cronbach’s alpha", "How well the statements of a scale measure the same thing (reliability). "
              "Values from about 0.70 are often taken as sufficient; alpha also depends on the number of statements, "
              "though.", "α = 0.82 for four statements: the scale measures fairly consistently."),
    "omega": ("ω", "McDonald’s omega", "Like alpha a measure of reliability, but without assuming that all statements "
              "contribute equally to the score; therefore usually preferable.",
              "ω = 0.84: a little higher than α because the statements measure unequally well."),
    "d": ("d", "Cohen’s d", "How large a difference between two groups is, in standard deviations. About 0.2 small, "
          "0.5 medium, 0.8 large, depending on the field.", "d = 0.47: a medium difference."),
    "r": ("r", "Correlation", "How closely two variables are related, from −1 to 1. About 0.1 weak, 0.3 medium, 0.5 "
          "strong. A relationship is not yet a cause.", "r = −0.32 between phone time and sleep: those who spend "
          "longer on their phone tend to sleep less."),
    "p": ("p", "p-value", "How likely a difference at least this large would be if there were in truth none. Below "
          ".05 it is usually called “significant”; p says nothing about its size, d or r do that.",
          "p = .002: such a difference would be very rare without a real difference."),
    "rsi": ("RSI", "Relative Speed Index", "How much faster than usual a person was, averaged over all pages and "
            "measured against the median of each page. Above 2 means: twice as fast as typical.",
            "RSI = 2.6: the person was clearly too fast to read the questions."),
    "speeder": ("Speeder", "Speeders (answering too fast)", "Participations that were clearly faster than usual, "
                "measured by the total duration or by the Relative Speed Index. At that speed the questions can hardly "
                "be read; quality assurance marks such participations or takes them out.",
                "Median 12 minutes, this person 3 minutes: marked as a speeder."),
    "screen_out": ("Screen-out", "Screen-out", "An end for people outside the target group, usually after a filter "
                   "question at the start. A screen-out is neither a completion nor a break-off and does not go into the "
                   "results.", "Anyone under 18 ends with a screen-out after the first page."),
    "partworth": ("Part-worth", "Part-worth", "How strongly a level favours the choice compared with the other levels "
                  "of the same attribute, centred on 0 per attribute. Only comparable within one attribute.",
                  "€9: +0.8, €15: −0.9: the lower price is clearly preferred."),
    "cva": ("CVA", "Ratings-based conjoint (CVA)", "Conjoint in which people rate whole offers instead of choosing, one by "
            "one on a scale, in pairs on a graded scale or all put in order. A regression per person gives part-worths without HB; for that "
            "every person needs more tasks than part-worths, so few attributes.",
            "14 flat offers per person on 0 to 10: rent −2.1 to +2.4 points."),
    "aca": ("ACA", "Adaptive conjoint (ACA)", "Conjoint in two steps: people first rate levels and importance themselves "
            "(self-explication), then pairs of a few attributes that the system picks after every answer so that both "
            "sides are about equally good. Handles many attributes but underestimates the price.",
            "12 attributes, 20 pairs: importance battery 24%, camera 18%."),
    "self_explicated": ("Self-explicated", "Self-explicated part-worths", "Part-worths taken directly from what the person "
                        "says: how much they like each level, times how important the attribute is to them. Quick, but "
                        "without trade-offs; ACA corrects them with the pairs.",
                        "Self-explicated 30%, after the pairs 22%: the person overrated the attribute."),
    "calibration": ("Calibration", "Calibration (ACA)", "A few offers with “How likely would you be to choose it (0 to "
                    "100)?” at the end; the part-worths are stretched so that their sums predict these likelihoods. Only "
                    "needed for purchase likelihoods.",
                    "R² = 0.8: the part-worths predict the likelihoods given well."),
    "r2_person": ("R² per person", "R² per person", "How well a person’s part-worths reproduce their own ratings, from 0 to 1. Low "
                  "values mean contradictory or random answers; Askurious marks values under 0.3.",
                  "R² = 0.85: the ratings follow the attributes almost entirely."),
    "importance": ("Importance", "Importance of the attributes", "The range of an attribute’s part-worths as a share "
                   "of the sum of all ranges. Depends on the chosen levels: levels far apart make an attribute more "
                   "important.", "Price 42 %: the price moves the choice most."),
    "wtp": ("Willingness to pay", "Willingness to pay", "What a level is worth in money, converted via the price "
            "coefficient. Only for everyone or per segment; per person it would be too uncertain.",
            "+€2.40 for “organic” compared with the mean of the levels."),
    "score": ("Score", "Probability score", "How likely a statement is to be chosen as most important in a set of "
              "average statements, scaled to a sum of 100. Ratios by this convention depend on the set "
              "size.", "20 against 10: twice as important by this convention."),
    "rho2": ("ρ²", "McFadden’s ρ²", "How much better the model explains the choices than guessing (all alternatives "
             "equally likely). The values are clearly lower than a regression R².",
             "ρ² = 0.28: the model explains the choices well."),
    "rlh": ("RLH", "Root likelihood", "The geometric mean of the probabilities the model gives to a person’s choices. "
            "Chance would be 1 divided by the number of alternatives.",
            "RLH = 0.62 with three alternatives (chance 0.33): clear preferences."),
    "sop": ("Share of Preference", "Share of preference", "The share of choices a product would get in the simulator, "
            "averaged as a probability per person. Not a market share: awareness and distribution are missing.",
            "34 % against two other products."),
    "first_choice": ("First Choice", "First Choice", "How often the product has the highest utility for a person, "
                     "averaged over the people. Reacts more strongly to small changes than the share of preference.",
                     "41 %: the best product for just over four in ten people."),
    "hit_rate": ("Hit rate", "Hit rate", "Share of people whose choice in the holdout task the model predicts "
                 "correctly; compare it with chance.", "62 % with three options (chance 33 %)."),
    "mae": ("MAE", "Mean absolute error", "How far the predicted choice shares differ from the observed ones on "
            "average, in percentage points. Smaller is better.",
            "MAE = 3.1 pp: the shares are off by 3 points on average."),
    "rhat": ("R̂", "R-hat", "Whether the HB chain has settled: compares the first with the second half of the draws. "
             "Below 1.01 it counts as converged.", "R̂ = 1.004: converged."),
    "ess": ("ESS", "Effective sample size", "How many independent draws the stored ones correspond to; consecutive "
            "draws are similar. From 400 the mean is reliable; for intervals the tails of the distribution matter, "
            "which this value does not check.", "ESS = 850 of 1,000 draws."),
    "hb": ("HB", "Hierarchical Bayes", "Estimates utilities of their own for each person: they spread around a common "
           "mean, and people who answered few tasks are pulled towards that mean. The basis for the market simulator, "
           "segments and values per person.", "Part-worth of “organic” per person between −0.4 and +2.1, +0.8 on "
           "average."),
    "mnl": ("MNL", "Multinomial logit model", "One common model for the whole sample: the probability of each "
            "alternative from the sum of its part-worths. Differences between people are not taken into account; in "
            "return it is fast and needs less data than hierarchical Bayes.",
            "Price €12 instead of €9: −0.6 utility on average for everyone."),
    "holdout": ("Holdout", "Holdout task", "A fixed choice or rating task that is left out of the estimation. The "
                "model predicts its answers; hit rate and MAE show how well that works.",
                "Hit rate 62 % with three options (chance 33 %)."),
    "bic": ("BIC", "BIC, AIC3 and CAIC", "Fit against the number of parameters, smaller is better. AIC3 penalises "
            "less and tends to suggest more segments, CAIC fewer.",
            "BIC 4,812 with three, 4,830 with four segments: three fit better."),
    "entropy": ("Entropy", "Entropy R²", "How clearly the people are assigned to a segment, from 0 to 1. Low values: "
                "read segments only proportionally.", "0.86: most people clearly belong to one segment."),
    "membership": ("Membership probability", "Membership probability", "How likely a person belongs to each segment; "
                   "together 100 %. The sizes and descriptions of the segments are computed with it.",
                   "70 % “Price-conscious”, 30 % “Quality first”."),
    "g": ("δ*", "Standardised difference δ*", "Like Cohen’s d or Hedges’ g the difference between two means in "
          "standard deviations, here in the average of both variances, so that the interval holds like Welch’s test "
          "even with unequal spread; like Hedges’ g the value is corrected for small groups. About 0.2 small, "
          "0.5 medium, 0.8 large.", "δ* = 0.41 [0.12; 0.70]: a small to "
          "medium difference, but uncertain."),
    "omega2": ("ω²", "Omega squared", "Which share of the spread goes back to the groups, with more than two groups; "
               "less biased than η². About 0.01 small, 0.06 medium, 0.14 large.",
               "ω² = 0.05: the groups explain 5 % of the differences."),
    "chi2": ("χ²", "Chi-square", "How far a cross table departs from what would be expected without a relationship. "
             "Its size depends on the number of cases; Cramér’s V shows the strength.",
             "χ²(2, N = 180) = 9.4: the distribution differs between the groups."),
    "cramer_v": ("V", "Cramér’s V", "How strong the relationship in a cross table is, from 0 (none) to 1. About 0.1 "
                 "weak, 0.3 medium, 0.5 strong (with two columns).", "V = 0.23: a weak to medium relationship."),
    "u": ("U", "Mann-Whitney U", "A rank test: how often values of one group lie above those of the other. It needs "
          "no means; the strength is given as r next to it.", "U = 3,120, r = 0.21: the first group answers "
          "somewhat higher."),
    "gini": ("Gini", "Gini coefficient", "How unequally a value is distributed: 0 means all equal, values near 1 mean "
             "a few have almost everything. Only for values with a true zero.",
             "Gini = 0.48 for monthly spending: clearly unequal."),
    "difficulty": ("P", "Difficulty", "How high a statement is answered on average, from 0 (everyone at the lowest "
                   "point) to 100. Very high or low values hardly tell people apart.", "P = 82: nearly everyone agrees."),
    "rit": ("r_it", "Item-total correlation", "How closely a statement goes with the sum of the others. Below about "
            "0.30 it probably measures something other than the scale.", "r_it = 0.18: check this statement."),
    "loading": ("λ", "Loading", "How strongly a statement measures what the scale has in common, standardised in the "
                "one-factor model; ω is built from the loadings.", "λ = 0.74: the statement measures the scale well."),
    "odds_ratio": ("OR", "Odds ratio", "By which factor the odds of being in a segment rather than the reference "
                   "segment rise: against the reference answer or per standard deviation. 1 means no difference.",
                   "OR = 2.1 for students: their odds are about twice as high."),
    "n_eff": ("n_eff", "Effective sample size", "How many unweighted participations the weighted sample is worth in "
              "precision. The more unequal the weights, the smaller.",
              "n = 400, n_eff = 310: the weighting costs about a quarter of the precision."),
    "d_eff": ("D-efficiency", "D-efficiency", "How well a design separates the effects of the factors: 100 means "
              "factors uncorrelated and levels equally often. From about 90 a design counts as good.",
              "D-efficiency 94: hardly worse than a full design."),
    "psm_points": ("OPP · IPP · PMC · PME", "Van Westendorp price points", "The crossing points of the four price "
                   "curves: PMC and PME bound the acceptable range, OPP (too cheap × too expensive) and IPP (neither "
                   "cheap nor expensive) lie within. They describe perceptions, not willingness to buy.",
                   "PMC €8, OPP €10, IPP €12, PME €15: €8 to €15 is acceptable."),
    "beta": ("β", "Standardised path", "How strongly one variable predicts another in the structural equation model "
             "with the other paths taken into account, in standard deviations. Mostly between −1 and 1; with strongly "
             "correlated predictors it can lie beyond. For a 0/1 variable (e.g. a condition) β is not an effect per "
             "standard deviation: then the difference between the groups counts.",
             "β = 0.45: one standard deviation more readiness for technology goes with 0.45 standard deviations more "
             "intention to use."),
    "indirect": ("ab", "Indirect effect", "How much of an effect runs through a mediator: the product of the paths x → m "
                 "and m → y. Supported when both paths are significant (joint significance); the bootstrap interval shows "
                 "how large the effect is.",
                 "ab = 0.30, 95% CI [0.19, 0.42]: part of the effect runs through the mediator."),
    "interaction": ("β(x × w)", "Moderation (interaction)", "Whether the strength of a path depends on a third "
                    "variable, the moderator. Estimated as the path of a product of predictor and moderator; the simple "
                    "slopes show the path where the moderator is low (−1 SD) and high (+1 SD), the SD from the estimated "
                    "model; for a 0/1 moderator in each of the two groups. A positive interaction means: the path grows "
                    "with the moderator (if it is negative, it becomes weaker in size). With a moderator of groups "
                    "outside a mediation the path is estimated in each group and compared.",
                    "β(x × w) = 0.20; simple slopes 0.15 (−1 SD) and 0.55 (+1 SD): the higher w, the larger the path "
                    "from x to y."),
    "cfi": ("CFI · TLI", "Comparative fit indices", "How much better the model fits than a model without any "
            "relationship; 1 is perfect, from about 0.95 the fit counts as good (a convention, not a test).",
            "CFI = 0.98: the model fits almost as well as a model without restrictions."),
    "rmsea": ("RMSEA", "Root mean square error of approximation", "The error of the model per degree of freedom; up to "
              "about 0.06 the fit counts as good, above 0.10 as poor. With a 90 % interval.",
              "RMSEA = 0.04 [0.02, 0.06]: good fit, even the upper bound is acceptable."),
    "srmr": ("SRMR", "Standardised root mean square residual", "The mean difference between the observed correlations and "
             "those the model expects; up to about 0.08 it counts as small.", "SRMR = 0.04: the correlations are met well."),
    "ave": ("AVE · CR", "Average variance extracted and composite reliability", "AVE: how much of the spread of the "
            "statements the construct explains on average, from 0.50 more than the measurement error. CR: reliability "
            "from the loadings, from 0.70.", "AVE = 0.61, CR = 0.86: the construct is measured well."),
    "htmt": ("HTMT", "Heterotrait-monotrait ratio", "How alike two constructs are measured: correlations between their "
             "statements relative to those within. Above 0.85 they hardly separate.",
             "HTMT = 0.92: the two scales measure almost the same."),
    "robust_fit": ("CFI · TLI · RMSEA robust", "Robust fit indices", "CFI, TLI and RMSEA corrected for non-normal "
                   "answers (skewed or with many extreme values, as often with Likert statements). They use the same "
                   "bounds; with normally distributed answers they are almost the usual values.",
                   "CFI = 0.94, robust CFI = 0.96: with the correction the model fits well."),
    "residual_cor": ("r − r̂", "Residual correlation", "How far the observed correlation of two statements differs from "
                     "the one the model expects. Above 0.10 the model explains this relationship poorly. A hint to "
                     "understand the fit, no reason to adjust the model afterwards.",
                     "0.14 between two statements of the same scale: they share more than the factor, e.g. a similar "
                     "wording."),
    "control": ("Control", "Control variable", "A variable whose influence is taken out of the paths of the hypotheses: "
                "it gets a path onto every explained variable. A path then holds at the same value of the control "
                "variable, e.g. at the same age.",
                "Controlled for age: β(readiness for technology → use) = 0.31 holds for people of the same age."),
    "single_item": ("rel", "Single question with a reliability", "A single question holds measurement error like every "
                    "statement of a scale. With an assumed reliability (often 0.70) the model takes this error out; "
                    "without it the question counts as error-free and its paths come out too small.",
                    "Reliability 0.70: 30% of the spread of the answers counts as measurement error."),
    "f2": ("f²", "Effect size f²", "How much explained variance (R²) of the outcome is lost when a path is left out, "
           "relative to the unexplained. From 0.02 small, from 0.15 medium, from 0.35 large.",
           "f² = 0.18: the path contributes a medium part to the explanation."),
    "vif": ("VIF", "Variance inflation factor", "How strongly a variable is related to the other predictors of the same "
            "target (or a formative statement to the other statements). From 5 they overlap too much and their paths or "
            "weights become unsure; below 3 is good.", "VIF = 6.2: two predictors measure almost the same."),
    "formative": ("Mode B", "Formative construct", "The statements form the construct instead of measuring it: leaving one "
                  "out changes its meaning (e.g. an index of equipment features). Estimated with weights (PLS mode B); "
                  "AVE, CR and HTMT only apply to reflective constructs.",
                  "Weight 0.42, p = .003: the statement adds clearly to the index."),
    "redundancy": ("Redundancy analysis", "Redundancy analysis", "Checks the convergent validity of a formative "
                   "construct: it predicts a global measure of the same construct, mostly a single question that "
                   "measures it as a whole. From a path of 0.70 (R² from 0.50) it counts as sufficient.",
                   "Path 0.74: the index captures what the global question means."),
    "plspredict": ("Q²predict", "Predictive power (PLSpredict)", "How well the model predicts the statements of the "
                   "explained constructs for new cases, checked by cross-validation: Q²_predict above 0 is better than "
                   "the mean; the error is compared with a simple linear model.",
                   "Predictive power medium: for most statements the model predicts better than the linear model."),
    "construct_score": ("Construct score", "Construct score", "The value of each participation on a construct of the PLS "
                        "model: the weighted sum of its statements, standardised (mean 0, SD 1). For further analyses "
                        "elsewhere, e.g. a cluster analysis.",
                        "1.2: this participation lies 1.2 standard deviations above the mean."),
    "fiml": ("FIML", "Full information maximum likelihood", "Estimates the model from all participations, also those "
             "with gaps: each counts with the answers it gave. Unbiased when gaps depend only on what was answered "
             "(MAR); leaving cases out listwise needs the stricter assumption that they are purely random (MCAR).",
             "N = 412 with FIML instead of 287 complete: more power, less bias."),
    "wlsmv": ("WLSMV", "Ordered categories (WLSMV)", "Treats the answers to a statement as ordered levels instead of "
              "numbers: behind each statement lies a continuous tendency that thresholds cut into levels. The model "
              "works with polychoric correlations. More precise than ML with fewer than five levels or very skewed "
              "answers.", "4-point statements: loadings of 0.78 with WLSMV instead of 0.71 with ML."),
    "weighted_sem": ("w", "Weighted model", "With the weights of the survey (page “Quotas”) each participation counts "
                     "as much as its group makes up of the target population: covariances weighted, standard errors robust "
                     "with the weights. n_eff is the sample the weighted one is worth.",
                     "n = 400, n_eff = 310: the weights cost precision like 90 participations fewer."),
    "second_order": ("2nd order", "Second-order factor", "A construct over the subscales of an instrument: it explains "
                     "what the subscales have in common and enters the model with one path instead of one per subscale. "
                     "Fits when the manual provides a total score.",
                     "The Big Five subscales stay apart; the three facets of readiness for technology form a second-order "
                     "factor."),
    "higher_order": ("HOC", "Higher-order construct", "A construct over several lower-order constructs (the subscales "
                     "of an instrument or several scales). With PLS estimated in two stages: stage 1 gives the scores of "
                     "the parts, stage 2 takes them as indicators. Reflective when the whole shapes the parts; formative "
                     "when the parts form the whole.",
                     "Operating, informing and communicating together form “digital competence” (formative)."),
    "model_compare": ("ΔAIC", "Model comparison", "Two models set in advance, one a restriction of the other (e.g. "
                      "without the direct effect): the difference test shows whether what was left out is missing; AIC "
                      "and BIC weigh fit against parsimony, smaller is better.",
                      "Δχ²(1) = 0.8, p = .37; ΔBIC = −5.2: full mediation suffices."),
    "efa": ("EFA", "Exploratory factor analysis", "Searches the answers to the statements for how many common factors lie "
            "behind them and which statement belongs to which, without a given structure. For self-built scales and to "
            "check subscales; confirming it afterwards needs new data.",
            "Two factors: statements 1 to 4 load on F1, 5 to 8 on F2 – like the two planned subscales."),
    "parallel": ("PA", "Parallel analysis", "How many factors an analysis should keep: as many as eigenvalues of the data lie "
                 "above those of random data of the same size (95th percentile). More reliable than the rule of thumb "
                 "“eigenvalue above 1”.", "Eigenvalues 2.9 and 1.6 against 1.2 and 1.1 at random: two factors."),
    "mc_power": ("Power (simulation)", "Power by simulation", "How often a test comes out significant in many artificial "
                 "data sets from the planned model. More precise than the formulas where these only approximate: for "
                 "mediation, moderation and small samples.",
                 "H3 74% at N = 250: in three of four studies of this size the mediation would be shown."),
    "riclpm": ("RI-CLPM", "Random intercept cross-lagged panel model", "Tests over several waves whether X in one wave "
               "changes Y in the next, after stable differences between people (random intercepts) are taken out. The "
               "paths describe fluctuations of a person around their own level.",
               "β = .22 from X to Y: whoever has more X than usual in one wave has more Y in the next."),
    "growth": ("I · S", "Latent growth curve", "Describes the course over the waves with an initial level (I) and a slope "
               "(S) per person; their means show the average course, their variances how differently people start and "
               "change.", "M(S) = 0.26 per wave, Var(S) = 0.02: on average a rise, with little difference between people."),
    "invariance": ("Invariance", "Measurement invariance", "Whether a scale measures the same in all groups: the same "
                   "structure (configural), the same loadings (metric), also the same intercepts (scalar) and the same "
                   "residuals (strict). Relations can be compared between groups only with metric invariance, means only "
                   "with scalar.",
                   "Scalar holds across the languages: the means of the German and English versions can be compared."),
    "latent_mean": ("α", "Latent mean", "The mean of a construct in a group, compared with the first group, free of "
                    "measurement error. d gives the difference in standard deviations.",
                    "d = 0.40: the second group lies 0.4 standard deviations higher."),
    "jn": ("Johnson-Neyman", "Johnson-Neyman region", "For which values of the moderator the path is significant: the "
           "bounds at which the 95% interval of the slope touches zero. Adds to the simple slopes, which show only two "
           "points (−1 SD and +1 SD).", "Significant where w lies above −0.40 SD: for all but the people with very low w."),
    "power": ("Power", "Power (1 − β)", "How likely a test is to find an effect of the assumed size if there is one. "
              "Usually 80 % is aimed for.", "Power 80 %: eight in ten studies would find the effect."),
    "mde": ("Reachable effect", "Reachable effect", "The smallest effect that would still be found with the given "
            "sample size and power. If it is larger than the smallest effect of interest, the sample is too small.",
            "d = 0.45 with 160 people: smaller differences would probably go unnoticed."),
    "p_holm": ("p (Holm)", "Holm-corrected p", "The p-value after the correction for several hypotheses; it is "
               "compared with 0.05 as usual.", "p = 0.012, corrected for three hypotheses 0.036: still significant."),
    "kripp": ("α", "Krippendorff’s alpha", "How much two coders agree beyond chance: 1 = completely the same, 0 = as "
              "by chance. From about 0.80 reliable.", "α = 0.74: usable for tentative conclusions."),
    "similarity": ("Similarity", "Similarity of two cards", "The share of people who put two cards on the same pile, "
                   "among those who sorted both.", "62 %: most see the two cards together."),
    "iat_d": ("D", "IAT D score", "How much faster a person is with one pairing than with the other, in standard "
              "deviations of their times. By a widespread convention about 0.15 weak, 0.35 medium, 0.65 strong.",
              "D = 0.42: a medium association."),
    "k_discount": ("k (delay)", "k (delay discounting)", "How strongly a later amount loses value; larger means more "
                   "impatient. Usually reported as ln(k) because k is very skewed.",
                   "k = 0.01: an amount in 100 days is worth about half."),
    "bt_worth": ("Value", "Value in a paired comparison (Bradley-Terry)", "How strongly an option is preferred, "
                 "estimated from all comparisons and scaled to a sum of 100. The ratio of two values is the odds that "
                 "one is preferred to the other.", "Values 40 and 20: A is preferred to B in about two of three "
                 "comparisons."),
    "zeta": ("ζ", "Kendall's coefficient of consistence", "How free of contradictions a person compared: 1 means no "
             "circular triads (A over B, B over C, C over A), 0 as many as possible.",
             "ζ = 0.90: few contradictions, the person chose with thought."),
    "icc": ("ICC(1)", "Intraclass correlation ICC(1)", "The share of the variance that lies between the people; the rest "
            "lies within the people, from entry to entry. Small means: the answers vary mainly in a person's everyday "
            "life.", "ICC(1) = 0.30: 30% of the variance separates the people, 70% varies in everyday life."),
    "within_between": ("within · between", "Effect within and between people", "Within: whether a person reports "
                       "more Y in moments when X is above their own mean. Between: whether people with a higher mean X "
                       "also have a higher Y. The multilevel model estimates both separately.",
                       "within b = 0.41: one point more X than usual goes with 0.41 points more Y."),
    "random_slope": ("random slope", "Random slope", "The effect within people may differ between them; the model "
                     "estimates its mean and how much it varies. Otherwise the same effect holds for everyone.",
                     "Variance of the slope 0.06: for most people the effect lies between about 0.0 and 0.9."),
    "omega_levels": ("ω within · between", "Reliability on two levels", "How reliably a scale tells moments of the same "
                     "person apart (within) and how reliably it tells people apart (between), from the two-level factor "
                     "analysis.", "ω within .62, between .91: good for people, weaker for fluctuations in everyday life."),
    "compliance": ("Compliance", "Compliance", "The share of the prompts a person answered out of all the prompts they "
                   "received. Below half it usually counts as conspicuous.", "18 of 24 prompts: compliance 75%."),
    "rho_a": ("ρA", "Reliability ρA", "The reliability of a construct in PLS, estimated from the weights. It usually "
              "lies between Cronbach's alpha (lower bound) and CR (upper bound); like them from 0.70, above 0.95 the "
              "statements say almost the same.", "α = 0.78, ρA = 0.81, CR = 0.86: the construct is measured reliably."),
    "r2": ("R²", "Explained variance (R²)", "Which share of the differences in an outcome is explained by the variables "
           "pointing at it, from 0 to 1. The adjusted R² takes the number of predictors into account; in PLS from 0.25 "
           "counts as weak, from 0.50 moderate, from 0.75 substantial, depending on the field.",
           "R² = 0.42: the predictors explain a good two fifths of the differences in purchase intention."),
    "total_effect": ("Total effect", "Total effect", "The direct effect plus the sum of all indirect routes from one "
                     "variable to another. It shows how strongly a variable acts overall, even when the direct path is "
                     "small; an interval without zero speaks for a total effect.",
                     "direct 0.08, indirect 0.21, total 0.29 [0.17; 0.40]: the effect runs mainly through the mediator."),
    "outer_weight": ("w", "Weight (PLS)", "How strongly a statement enters the score of its construct. For a formative "
                     "construct its own contribution beside the other statements, like a regression weight; a small "
                     "weight then does not mean unimportant, the loading counts too.",
                     "w = 0.12, not significant, loading 0.63: the statement stays, it only overlaps with the others."),
    "bic_weight": ("BIC weight", "BIC weight (model comparison)", "From the difference of the BIC of two models: the "
                   "probability that a model is the better one among those compared, when both counted as equally likely "
                   "beforehand. Smaller BIC is better; it rewards explanation and penalises extra paths.",
                   "BIC weight 88.0% for version 3: it is very likely the better of the two."),
}

# title and note of each section of SECTIONS, in the same order
SECTIONS_EN = (
    ("Basics", ""),
    ("Reliability", ""),
    ("Item analysis", ""),
    ("Differences and relationships", ""),
    ("Data quality", ""),
    ("Weighting and design", ""),
    ("Price", ""),
    ("Paired comparison and kinds of conjoint", ""),
    ("Choice models", "R̂ and ESS check the computation of the HB estimation, not its result."),
    ("Segments", ""),
    ("Structural equation models", ""),
    ("SEM: estimation and fit", ""),
    ("PLS", ""),
    ("Longitudinal and group comparison", ""),
    ("Planning and power", ""),
    ("Open answers and sorting", ""),
    ("IAT and delay discounting", ""),
    ("Diary survey", ""),
)


# where each fold "Warum?" stands, for the section "Entscheidungen" of the glossary page (E8): (German, English)
REASON_PLACES = {
    "transcript_check": ("Interviews, Transkript", "interviews, transcript"),
    "answer_mode": ("jede Frage im Editor; Startliste", "every question in the editor; start list"),
    "scale_points": ("Likert-Frage im Editor", "Likert question in the editor"),
    "missing_option": ("Einfachauswahl, Option „fehlend“", "single choice, option “missing”"),
    "randomize": ("Likert-Frage, Reihenfolge der Aussagen", "Likert question, order of the statements"),
    "attention_check": ("Startliste in der Datenerhebung", "start list in data collection"),
    "speeder": ("Qualitätssicherung, Speeder-Regel", "quality assurance, speeder rule"),
    "data_pool": ("Qualitätssicherung und Ergebnisse, Grundlage der Auswertung",
                  "quality assurance and results, basis of the analysis"),
    "quota_hard": ("„Ablauf“ › „Quoten“", "“Flow” › “Quotas”"),
    "weighting": ("„Ablauf“ › „Quoten“, Gewichtung", "“Flow” › “Quotas”, weighting"),
    "sample_size": ("Stichprobengröße; Studienplan", "sample size; research plan"),
    "conjoint_tasks": ("Conjoint im Editor, Aufgaben", "conjoint in the editor, tasks"),
    "none_option": ("Conjoint im Editor, „Keine davon“", "conjoint in the editor, “None of these”"),
    "conjoint_kind": ("CBC, ACBC und Bewertungs-Conjoint im Editor", "CBC, ACBC and ratings-based conjoint in the editor"),
    "hb_or_logit": ("Ergebnisse, HB-Schätzung", "results, HB estimation"),
    "holdout": ("Holdout-Aufgaben im Editor; Startliste", "holdout tasks in the editor; start list"),
    "segments_k": ("Ergebnisse, Segmente", "results, segments"),
    "segments_covariates": ("Ergebnisse, Segmente, Kovariaten", "results, segments, covariates"),
    "segments_describe": ("Ergebnisse, Segmente beschreiben", "results, describing segments"),
    "geo_level": ("Standort im Editor", "location in the editor"),
    "geo_fallback": ("Standort im Editor", "location in the editor"),
    "dropdown": ("Einfachauswahl und Bundesland, Darstellung", "single choice and federal state, appearance"),
    "slider": ("Schieberegler im Editor", "slider in the editor"),
    "likert_grid": ("Likert-Frage im Editor", "Likert question in the editor"),
    "side_by_side": ("Side-by-Side-Matrix im Editor", "side-by-side matrix in the editor"),
    "progress": ("Einstellungen, Teilnahme", "settings, taking part"),
    "length": ("Ablauf, Kennzahlen; Startliste", "flow, key figures; start list"),
    "random_assignment": ("Ablauf, Kennzahlen", "flow, key figures"),
    "which_test": ("Ergebnisse, Nach Gruppen aufteilen", "results, split by groups"),
    "weighting_scope": ("Ergebnisse, gewichtet", "results, weighted"),
    "card_sort_mode": ("Kartensortierung im Editor", "card sorting in the editor"),
    "q_sort_grid": ("Q-Sort im Editor, Raster", "Q-sort in the editor, grid"),
    "personal_code": ("Persönlicher Code im Editor", "personal code in the editor"),
    "holm": ("Studienplan, Korrektur für mehrere Hypothesen", "study plan, correction for several hypotheses"),
    "preregistration": ("Export, Präregistrierung", "export, pre-registration"),
    "study_material": ("Export, Dokumentation, Befragungsmaterial", "export, documentation, survey material"),
    "second_coding": ("Offene Antworten kategorisieren, Zweitkodierung", "categorising open answers, second coding"),
    "reminders": ("Einladungen, Erinnerungen", "invitations, reminders"),
    "voice": ("Sprachnachricht im Editor; Startliste", "voice message in the editor; start list"),
    "ai_chat": ("KI-Chat im Editor; KI-Chat: Modellzugang", "AI chat in the editor; AI chat: model access"),
    "guide_sub": ("Interviewleitfaden, Unterfrage", "interview guide, sub-question"),
    "guide_probe_if": ("Interviewleitfaden, konkrete Fragen und Nachfragen", "interview guide, specific questions and follow-ups"),
    "conference": ("Interview führen, Aufnahme", "conducting an interview, recording"),
    "interview_task": ("Interviewleitfaden, Aufgabe", "interview guide, task"),
    "interview_screen": ("Interview führen, Teilnehmerbildschirm", "conducting an interview, participant screen"),
    "transcript_export": ("Transkript, Export als Word und PDF", "transcript, export as Word and PDF"),
    "diary_trigger": ("Tagebuchstudie, Kurzbefragungen", "diary survey, short surveys"),
    "paper_check": ("Papierfragebogen, Prüfen", "paper questionnaire, checking"),
    "moderation_mediation": ("Studienplan, Modell zeichnen", "study plan, drawing the model"),
    "sem_controls": ("Studienplan, Strukturgleichungsmodell, Kontrollvariablen", "research plan, structural equation model, control variables"),
    "sem_estimation": ("Studienplan, Strukturgleichungsmodell, Schätzung", "research plan, structural equation model, estimation"),
    "sem_compare": ("Studienplan, Strukturgleichungsmodell, Konkurrenzmodell", "research plan, structural equation model, competing model"),
    "sem_invariance": ("Studienplan, Strukturgleichungsmodell, Messinvarianz", "research plan, structural equation model, measurement invariance"),
    "clpm_or_riclpm": ("Studienplan, Wellen, Längsschnitt auf der Ergebnisseite", "research plan, waves, longitudinal analysis on the results page"),
    "centring": ("Studienplan, Mehrebenenmodell der Tagebuchstudie", "research plan, multilevel model of the diary survey"),
    "sem_formative": ("Studienplan, Strukturgleichungsmodell, formative Konstrukte (PLS)", "research plan, structural equation model, formative constructs (PLS)"),
    "sem_higher": ("Studienplan, Strukturgleichungsmodell, Konstrukte höherer Ordnung (PLS)", "research plan, structural equation model, higher-order constructs (PLS)"),
    "org_design": ("Verwaltung, Design; Erscheinungsbild", "management, design; appearance"),
    "seal": ("Einstellungen, Siegelkette", "settings, seal chain"),
    "archive_where": ("Export, In Coscine ablegen", "export, store in Coscine"),
    "sampling_procedure": ("Studienplan, Rekrutierung", "research plan, recruitment"),
    "time_method": ("Studienplan, Studiendesign", "research plan, research design"),
    "random_kind": ("Studienplan, Art der Zufallsstichprobe", "research plan, kind of random sample"),
    "population": ("Studienplan, Grundgesamtheit", "research plan, population"),
    "ddi_export": ("Export, Dokumentation, DDI Lifecycle 4.0", "export, documentation, DDI Lifecycle 4.0"),
    "validated_scale": ("Konstrukte einfügen", "inserting constructs"),
    "copy_or_loop": ("Fragebogen, Block duplizieren oder Schleife", "questionnaire, duplicating a block or a loop"),
    "ink_slides": ("Live-Abstimmung, Präsentieren", "live poll, present"),
    "sem_two_steps": ("Ergebnisse, Strukturgleichungsmodell, Schritt 1", "results, structural equation model, step 1"),
    "sem_drop": ("Ergebnisse, Messmodell, in neuer Version weglassen", "results, measurement model, leave out in a new version"),
    "sem_versions": ("Ergebnisse, Versionen des Modells", "results, versions of the model"),
}


def on(user=None):
    """Are the learning aids shown to this person (the current one by default)?"""
    user = user if user is not None else current_user
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "learning_aids", False))


def _source(ref):
    if isinstance(ref, tuple):
        return getattr(importlib.import_module(ref[0]), ref[1])
    return ref


def reason(key):
    """A fold "Warum?" in the language of the page (title and text; the sources stay as they are)."""
    title, text, refs = REASONS[key]
    if english():
        title, text = REASONS_EN[key]
    return {"key": key, "title": title, "text": text, "sources": [_source(r) for r in refs]}


# folds "Warum?" that only explain the basics to beginners: shown only with the learning aids on. Every other fold
# weighs a methodological choice right at the decision and is shown to everybody (UI/UX audit 2026-10-05, V28)
BEGINNER_REASONS = {"answer_mode", "scale_points", "missing_option", "randomize"}


# folds that would repeat under every group comparison of a page: only the first one is drawn (per request)
ONCE_REASONS = {"which_test", "sem_drop"}


def show_reason(key):
    """Is the fold "Warum?" of this decision shown to the current person (and not shown on this page yet)?"""
    if key in BEGINNER_REASONS and not on():
        return False
    if key in (getattr(current_user, "learning_hidden", None) or []):
        return False  # "Nicht mehr zeigen" (E7)
    if key in ONCE_REASONS:
        from flask import g

        seen = g.setdefault("learning_reasons_shown", set())
        if key in seen:
            return False
        seen.add(key)
    return True


# terms everybody meets on every page: their "?" only once per page (UI/UX audit 2026-10-05, V27)
SIMPLE_TERMS = {"n", "m", "sd", "md"}


def show_term(key):
    """Should this "?" be drawn? A simple term only at its first place on the page (per request)."""
    from flask import g

    if key not in SIMPLE_TERMS:
        return True
    seen = g.setdefault("learning_terms_shown", set())
    if key in seen:
        return False
    seen.add(key)
    return True


def term(key):
    """A reading aid in the language of the page (symbol, name, meaning, example; the sources stay as they are)."""
    symbol, name, text, example, refs = TERMS[key]
    if english():
        symbol, name, text, example = TERMS_EN[key]
    return {"key": key, "symbol": symbol, "name": name, "text": text, "example": example,
            "sources": [_source(r) for r in refs]}


def glossary():
    """[(section, note, [term, …])] in the order of SECTIONS, each section alphabetical by name."""
    from flask import has_request_context

    from app import experimental

    diary = not has_request_context() or experimental.enabled("diary")  # outside a page (tests, scripts): all
    sections = [SECTIONS_EN[i] + (keys,) if english() else (title, note, keys)
                for i, (title, note, keys) in enumerate(SECTIONS) if title != DIARY_SECTION or diary]
    return [(title, note, sorted((term(k) for k in keys), key=lambda t: t["name"].lower()))
            for title, note, keys in sections]


# folds of an experimental feature (app/experimental.py): on the glossary page only for whom it is on
REASON_FEATURES = {"ai_chat": "ai_chat"}  # the participant screen is regular since 2026-10-22

# the groups of the section "Entscheidungen" of the glossary page, named like the groups of the tabs (_tabs.html;
# concept "Lernhilfen: fünfte Runde", H3); a fold belongs where its decision is taken. Not listed: "Planen".
PHASES = (("Planen", "Plan"), ("Erheben", "Collect"), ("Analyse", "Analysis"))
REASON_PHASES = {key: 1 for key in ("attention_check", "speeder", "reminders", "conference", "interview_screen",
                                    "paper_check", "ink_slides")}
REASON_PHASES.update({key: 2 for key in ("data_pool", "hb_or_logit", "segments_k", "segments_covariates",
                                         "segments_describe", "which_test", "weighting_scope", "preregistration",
                                         "second_coding", "transcript_export", "transcript_check", "archive_where",
                                         "sem_two_steps", "sem_drop", "sem_versions")})


def decisions():
    """Every fold "Warum?" with where it stands, alphabetical by title, for the glossary page."""
    from flask import has_request_context

    from app import experimental

    rows = []
    for key in REASONS:
        if key in REASON_FEATURES and has_request_context() and not experimental.enabled(REASON_FEATURES[key]):
            continue
        r = reason(key)
        r["place"] = REASON_PLACES[key][1 if english() else 0]
        r["phase"] = REASON_PHASES.get(key, 0)
        rows.append(r)
    return sorted(rows, key=lambda r: r["title"].lower().lstrip("„“\"'"))


def decision_groups():
    """[(name of the group, [fold, …])] for the section "Entscheidungen": Planen, Erheben, Analyse (H3)."""
    rows = decisions()
    return [(name[1 if english() else 0], [r for r in rows if r["phase"] == i]) for i, name in enumerate(PHASES)]


# --- the glossary page -------------------------------------------------------------------------------------------
from flask import Blueprint, render_template  # noqa: E402
from flask_login import login_required  # noqa: E402

bp = Blueprint("learning", __name__, url_prefix="/learn")


@bp.route("/glossary", endpoint="glossary")
@login_required
def glossary_page():
    """Every reading aid with its meaning, an example and its sources; the links of term() point here."""
    return render_template("learning/glossary.html", sections=glossary(), decisions=decisions(),
                           decision_groups=decision_groups())


@bp.route("/why/<key>/hide", methods=["POST"], endpoint="hide")
@login_required
def hide(key):
    """„Nicht mehr zeigen“ in a fold "Warum?": this fold no longer appears for the person (E7)."""
    from flask import abort, jsonify

    from app.extensions import db

    if key not in REASONS:
        abort(404)
    hidden = list(current_user.learning_hidden or [])
    if key not in hidden:
        current_user.learning_hidden = hidden + [key]
        db.session.commit()
    return jsonify(ok=True)


@bp.route("/why/<key>/show", methods=["POST"], endpoint="unhide")
@login_required
def unhide(key):
    """„Rückgängig“ right after „Nicht mehr zeigen“: this one fold appears again (UI/UX audit 2026-10-07, W13)."""
    from flask import abort, jsonify

    from app.extensions import db

    if key not in REASONS:
        abort(404)
    hidden = list(current_user.learning_hidden or [])
    if key in hidden:
        current_user.learning_hidden = [k for k in hidden if k != key] or None
        db.session.commit()
    return jsonify(ok=True)


@bp.route("/why/again", methods=["POST"], endpoint="show_hidden")
@login_required
def show_hidden():
    """„Ausgeblendete Erklärungen wieder zeigen“ on the page "Anzeige und Sprache"."""
    from flask import flash, redirect, url_for

    from app.extensions import db

    current_user.learning_hidden = None
    db.session.commit()
    flash(de_en("Alle Erklärungen „Warum?“ werden wieder gezeigt.", "All “Why?” explanations are shown again."), "success")
    return redirect(url_for("accounts.appearance") + "#lernhilfen")


# --- method checks before the start (package D) -------------------------------------------------------------------
PRETEST_MIN = 5  # test runs before the start: enough to see whether the questions are understood (Döring 2023)
LENGTH_MAX = 20  # minutes; above it fewer start and later answers get worse (Galesic & Bosnjak, 2009)


def method_checks(survey, tests, open_comments):
    """What good practice expects before the start, beside the pre-launch check (which only stops errors): a pretest
    with a few people and the comments read, an attention check in longer matrix questions. [(ok, text, hint,
    anchor or None, reason key)]; nothing stops the start."""
    from app import quality

    rows = sum(len(quality.rows_of(i)) for b in survey.blocks for i in b.items if i.type == "likert")
    # one line for the pretest, with how far it is (UI/UX audit 2026-10-07, W8; before: "Noch nicht getestet" too)
    checks = [(tests >= PRETEST_MIN and not open_comments,
               de_en("Pretest: {t} von {n} Testdurchläufen, {c} offene Kommentare",
                     "Pretest: {t} of {n} test runs, {c} open comments", t=tests, n=PRETEST_MIN, c=open_comments),
               de_en("Vorschau oder Pretest-Link unter „Testen“; Kommentare lesen", "preview or pretest link under "
                     "“Testing”; read the comments") if tests < PRETEST_MIN or open_comments else "", "#testing", None)]
    if rows >= 10:
        has_check = any(quality.checks_of(i) for b in survey.blocks for i in b.items)
        checks.append((has_check, de_en("Aufmerksamkeitsprüfung in den Likert-Fragen",
                                        "Attention check in the Likert questions"),
                       de_en("{n} Aussagen; eine Aussage kann eine vorgegebene Antwort verlangen",
                             "{n} statements; a statement can ask for a set answer", n=rows), None, "attention_check"))
    # choice tasks (Conjoint, MaxDiff) with a holdout task that tests the predictions (Orme 2009)
    for item in (i for b in survey.blocks for i in b.items if hasattr(i.kind, "holdout_count")):
        checks.append((item.kind.holdout_count(item) > 0,
                       de_en("Holdout-Aufgabe in „{code}“", "Holdout task in “{code}”", code=item.code),
                       de_en("eine feste Aufgabe, die nicht in die Schätzung eingeht",
                             "a fixed task that is left out of the estimation"), None, "holdout"))
    # concept "Lernhilfen: Erweiterungen und Verbesserungen", E5: required questions and length, only when they matter
    asked = [i for b in survey.blocks for i in b.items if i.kind.collects_answer and i.kind.has_answer_mode
             and i.type != "consent"]
    required = sum(i.required for i in asked)
    if len(asked) >= 4 and required * 2 > len(asked):
        checks.append((False, de_en("Höchstens die Hälfte der Fragen als Pflicht", "At most half of the questions required"),
                       de_en("{r} von {n} Fragen sind Pflicht; „Nachfragen“ markiert die Frage einmal und lässt dann weiter",
                             "{r} of {n} questions are required; “Request” marks the question once and then lets people go on",
                             r=required, n=len(asked)), None, "answer_mode"))
    # side-by-side matrices with more than three columns (concept side-by-side, E14): the more questions in one grid,
    # the more is skipped or clicked through
    for item in (i for b in survey.blocks for i in b.items if i.type == "side_by_side"):
        n = len(item.kind.columns(item))
        if n > 3:
            checks.append((False, de_en("Höchstens drei Spalten in „{code}“", "At most three columns in “{code}”", code=item.code),
                           de_en("{n} Spalten nebeneinander; eine Bedingung oder eine zweite Matrix halten sie kurz",
                                 "{n} columns side by side; a condition or a second grid keeps it short", n=n), None, "side_by_side"))
    # concept "Lernhilfen: vierte Runde", G6: a required voice message without typing as a way out
    for item in (i for b in survey.blocks for i in b.items if i.type == "voice"):
        if item.required and not item.kind.text_fallback(item):
            checks.append((False, de_en("Sprachnachricht „{code}“ nicht als Pflicht", "Voice message “{code}” not required",
                                        code=item.code),
                           de_en("Pflicht und ohne Tippen als Ausweg; viele mögen nicht laut sprechen",
                                 "required and without typing as a way out; many do not like to speak aloud"),
                           None, "voice"))
    # what is set on later pages but fixed at the start, because the consent names it (UI/UX audit 2026-10-16, 3): the
    # data quality options of "Qualitätssicherung" and the return links of a panel
    from flask import has_request_context, url_for

    from app import panel

    def link(endpoint, anchor):  # a link on the page; without a request (tests, scripts) none
        return url_for(endpoint, survey_id=survey.id, _anchor=anchor) if has_request_context() else None

    quality_on = (survey.paradata or survey.page_timing or survey.focus_paste or survey.ai_bait
                  or (survey.tempo_hint or "off") != "off")
    checks.append((quality_on, de_en("Datenqualität vor dem Start eingestellt", "Data quality set before the start"),
                   de_en("Paradaten, Zeit je Seite, Tempo-Hinweis, Köder für KI-Agenten; nach dem Start nicht mehr änderbar",
                         "paradata, time per page, tempo note, bait for AI agents; cannot be changed after the start")
                   if not quality_on else "", link("surveys.quality", "prevent"), None))
    cfg = panel.config(survey)
    if cfg["params"]:
        returns = any((url or "").strip() for url in cfg["redirects"].values())
        checks.append((returns, de_en("Abschlusslinks für das Panel", "Return links for the panel"),
                       de_en("Kennungen werden übernommen, aber niemand wird am Ende zurückgeleitet",
                             "identifiers are kept, but nobody is sent back at the end") if not returns else "",
                       link("surveys.field", "panel"), None))
    from app import duration

    minutes = duration.minutes(duration.estimate(survey)["typical"])
    if minutes > LENGTH_MAX:
        checks.append((False, de_en("Dauer bis {m} Minuten", "Duration up to {m} minutes", m=LENGTH_MAX),
                       de_en("geschätzt etwa {n} Minuten", "estimated about {n} minutes", n=minutes), None, "length"))
    return checks
