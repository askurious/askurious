"""The public list of all features, /listofallfeatures (Philipp, 2026-10-14): what Askurious can do, by topic, in German
and English.

A feature that is experimental (app/experimental.py) names its key as the second part of its row; the page marks it
"experimentell" as long as the key is in experimental.KEYS, with a footnote: available on reasonable request. When a
feature leaves the experimental group, its key goes from the rows here too (tests/ui/test_feature_list.py fails until
then). A new feature gets a row in the group it belongs to."""
from app.lang import de_en


def groups():
    """[(anchor, title, [(text, experimental key or None)])] in the language of the page."""
    return [
        ("arten", de_en("Arten von Befragungen", "Kinds of surveys"), [
            (de_en("Fragebogen: standardisierte Befragungen, Online-Experimente, Wahlexperimente und Panelbefragungen",
                   "Questionnaire: standardised surveys, online experiments, choice experiments and panel surveys"), None),
            (de_en("Interviewleitfaden für leitfadengestützte, qualitative Interviews",
                   "Interview guide for guided, qualitative interviews"), None),
            (de_en("Live-Abstimmung für Vorlesungen und Veranstaltungen",
                   "Live poll for lectures and events"), None),
            (de_en("Tagebuchstudie (Experience Sampling) mit der Android-App",
                   "Diary survey (experience sampling) with the Android app"), "diary"),
            (de_en("Papierfragebogen: gedruckt, gescannt und auf dem Server ausgelesen",
                   "Paper questionnaire: printed, scanned and read on the server"), None),
            (de_en("Sechs Vorlagen mit Studienplan, Fragebogen und Dummy-Daten zum Ausprobieren",
                   "Six templates with research plan, questionnaire and dummy data to try out"), None),
        ]),
        ("planen", de_en("Planen", "Plan"), [
            (de_en("AInstein: Forschungsfrage im Gespräch mit der KI entwickeln, schärfen, operationalisieren und als "
                   "Befragung anlegen",
                   "AInstein: develop a research question in conversation with the AI, sharpen it, operationalise it "
                   "and create it as a survey"), "ainstein"),
            (de_en("Studienplan mit Forschungsfrage, Design, Hypothesen, Stichprobenziel und Auswertungsplan",
                   "Research plan with research question, design, hypotheses, sample target and analysis plan"), None),
            (de_en("Hypothesen als Modell zeichnen und gemeinsam als Strukturgleichungsmodell testen (kovarianzbasiert "
                   "oder PLS, mit Vermittlung und Moderation)",
                   "Draw hypotheses as a model and test them together as a structural equation model "
                   "(covariance-based or PLS, with mediation and moderation)"), None),
            (de_en("Stichprobengröße und Power: sechzehn Planer, Designeffekt, Untergruppen und die umgekehrte Richtung "
                   "(was sich mit einer gegebenen Stichprobe finden lässt)",
                   "Sample size and power: sixteen planners, design effect, subgroups and the reverse direction (what "
                   "a given sample can find)"), None),
            (de_en("Erwarteter Effekt aus einer früheren Befragung, mit „Safeguard Power“",
                   "Expected effect from an earlier survey, with “safeguard power”"), None),
            (de_en("Power der Teilnutzen eines Conjoints und der Werte eines MaxDiff aus dem eigenen Versuchsplan",
                   "Power of the part-worths of a conjoint and of the scores of a MaxDiff from the survey's own experimental design"),
             None),
            (de_en("Konstrukte: validierte Skalen (BFI-10, GESIS-Kurzskalen, TAM, KUT, Technikbereitschaft und weitere) "
                   "mit Quellen in APA, BibTeX und RIS; eigene Konstrukte teilen und zur Prüfung einreichen",
                   "Constructs: validated scales (BFI-10, GESIS short scales, TAM, KUT, Technikbereitschaft and others) "
                   "with sources in APA, BibTeX and RIS; share own constructs and submit them for review"), None),
            (de_en("Verknüpfte Elemente: dieselbe Frage in mehreren Befragungen, gemeinsam auswertbar",
                   "Linked elements: the same question in several surveys, analysed together"), None),
            (de_en("Wellen eines Längsschnitts, über den persönlichen Code zusammengeführt",
                   "Waves of a longitudinal design, merged by the personal code"), None),
            (de_en("Präregistrierung: beim Start der Datenerhebung als ZIP mit SHA-256-Prüfsummen und DFN-Zeitstempel "
                   "festgehalten",
                   "Pre-registration: frozen at the start of data collection as a ZIP with SHA-256 checksums and a DFN "
                   "timestamp"), None),
            (de_en("Lernhilfen: „Warum?“-Erklärungen mit Quellen an jeder methodischen Entscheidung, abschaltbar",
                   "Learning aids: “Why?” explanations with sources at every methodological decision, can be "
                   "switched off"), None),
        ]),
        ("editor", de_en("Fragebogen-Editor", "Questionnaire editor"), [
            (de_en("Ziehen und Ablegen, Vorschau, Speichern ohne Knopf, Rückgängig und Wiederholen",
                   "Drag and drop, preview, saving without a button, undo and redo"), None),
            (de_en("Startprüfung: was vor dem Start behoben sein muss; geschätzte Bearbeitungszeit",
                   "Pre-launch check: what must be fixed before the start; estimated completion time"), None),
            (de_en("Papierkorb für gelöschte Fragen", "Trash for deleted questions"), None),
            (de_en("Befragung duplizieren; Befragungsdatei (.json/.zip) zum Umziehen und Teilen",
                   "Duplicate a survey; survey file (.json/.zip) to move and share it"), None),
            (de_en("KI-Agenten-Schnittstelle (MCP): Ein eigener KI-Agent liest Fragebögen und bearbeitet Entwürfe, mit "
                   "persönlichem Token",
                   "AI agent interface (MCP): your own AI agent reads questionnaires and edits drafts, with a personal "
                   "token"), "agent"),
            (de_en("Fassungen: eine laufende Befragung zur Bearbeitung öffnen, die Daten jeder Fassung bleiben",
                   "Versions: open a running survey for editing, the data of every version stay"), None),
            (de_en("Antwortoptionen übernehmen: Eine spätere Frage zeigt nur die früher gewählten Optionen",
                   "Carry options forward: a later question shows only the options chosen before"), None),
            (de_en("KI auf Wunsch: Fragen und Skalen vorschlagen, Formulierungen prüfen, Bilder erzeugen",
                   "AI on request: suggest questions and scales, check wordings, create images"), None),
        ]),
        ("klassisch", de_en("Elementtypen: klassische Fragen", "Element types: classic questions"), [
            (de_en("Einfachauswahl (auch als Bilder, Dropdown oder Suchfeld) und Mehrfachauswahl",
                   "Single choice (also as images, dropdown or search field) and multiple choice"), None),
            (de_en("Abhängige Auswahllisten (Drill-down), Rangfolge, Konstantsumme", "Dependent drop-downs (drill-down), ranking, constant sum"), None),
            (de_en("Freitext und Zahl, Datum und Uhrzeit", "Text and number, date and time"), None),
            (de_en("Postleitzahl und Bundesland, mit Karten in den Ergebnissen",
                   "Postal code and German federal state, with maps in the results"), None),
            (de_en("Persönlicher Code zum Verbinden von Wellen", "Personal code to link waves"), None),
            (de_en("Rechenwert aus früheren Antworten", "Computed value from earlier answers"), None),
        ]),
        ("skalen", de_en("Elementtypen: Skalen", "Element types: scales"), [
            (de_en("Likert-Skala und Matrix, semantisches Differential, Side-by-Side-Matrix",
                   "Likert scale and matrix, semantic differential, side-by-side matrix"), None),
            (de_en("Bewertung und Schieberegler", "Rating and slider"), None),
            (de_en("Preisbereitschaft nach Van Westendorp", "Van Westendorp price sensitivity"), None),
        ]),
        ("wahl", de_en("Elementtypen: Wahlexperimente", "Element types: choice experiments"), [
            (de_en("Conjoint (CBC) und Adaptive Choice-Based Conjoint (ACBC), auch mit Bildern",
                   "Conjoint (CBC) and adaptive choice-based conjoint (ACBC), also with images"), None),
            (de_en("MaxDiff (Best-Worst) und Paarvergleich", "MaxDiff (best-worst) and paired comparison"), None),
            (de_en("Bewertungs-Conjoint (CVA): Angebote einzeln oder paarweise bewerten, Teilnutzen je Person auch per HB",
                   "Ratings-based conjoint (CVA): rate offers one by one or in pairs, part-worths per person also by HB"), None),
            (de_en("Adaptive Conjoint (ACA): Selbstauskunft und adaptive Paare für sehr viele Merkmale",
                   "Adaptive conjoint (ACA): self-explication and adaptive pairs for very many attributes"), "aca"),
            (de_en("Holdout-Aufgaben zum Prüfen der Vorhersage", "Holdout tasks to check the prediction"), None),
        ]),
        ("visuell", de_en("Elementtypen: räumlich und visuell", "Element types: spatial and visual"), [
            (de_en("2D-Positionierung, Bild, Bild markieren mit Hot Spots",
                   "2D positioning, image, image marking with hot spots"), None),
            (de_en("Karte mit Markern und Standort", "Map with markers and location"), None),
            (de_en("Video / Audio mit Abspielprotokoll", "Video / audio with a playback log"), None),
        ]),
        ("aufgaben", de_en("Elementtypen: Textaufgaben und Reaktionszeit",
                           "Element types: text tasks and reaction time"), [
            (de_en("Kartensortierung, Q-Sort, Zuordnungsaufgabe mit Reaktionszeiten",
                   "Card sorting, Q-sort, assignment task with reaction times"), None),
            (de_en("Impliziter Assoziationstest (IAT) und Belohnungsaufschub (Delay Discounting)",
                   "Implicit Association Test (IAT) and delay discounting"), None),
            (de_en("Freie Assoziationen, Lückentext, Texthervorhebung",
                   "Free associations, cloze text, text highlighting"), None),
            (de_en("Sprachnachricht: Teilnehmende sprechen ihre Antwort ein",
                   "Voice message: participants speak their answer"), None),
        ]),
        ("rueckmeldung", de_en("Rückmeldung an Teilnehmende", "Feedback to participants"), [
            (de_en("Stichprobenvergleich, 2D-Streubild und Rückmeldung als Menschenmenge: wo eine Person im Vergleich zu allen Bisherigen "
                   "steht",
                   "Sample comparison, 2D scatter and crowd feedback: where a person stands compared with everyone so far"), None),
            (de_en("Einordnung in Wertebereiche, Skalenprofil, Themenkarte und Themenprofil",
                   "Score bands, scale profile, topic map and topic profile"), None),
            (de_en("IAT-Ergebnis", "IAT result"), None),
        ]),
        ("ablauf", de_en("Ablauf und Experimente", "Flow and experiments"), [
            (de_en("Blöcke und Seiten; Randomisierung, ausgeglichen oder zufällig, mit gespeicherter Zuteilung",
                   "Blocks and pages; randomisation, balanced or random, with the assignment stored"), None),
            (de_en("Bedingungen (dann / sonst), auch je Frage und je Aussage, und nach URL-Parametern",
                   "Conditions (if / else), also per question and per statement, and by URL parameters"), None),
            (de_en("Schleifen über eine Tabelle, mit Platzhaltern in Texten und Bildern",
                   "Loops over a table, with placeholders in texts and images"), None),
            (de_en("Faktorielle Vignetten mit D-optimalen Versuchsplänen; Stimulus-Sampling",
                   "Factorial vignettes with D-optimal experimental designs; stimulus sampling"), None),
            (de_en("Antworten einsetzen (Piping) und Rechenwerte", "Answer piping and computed values"), None),
            (de_en("Ende mit Screen-out oder „Quote voll“", "End with screen-out or “quota full”"), None),
            (de_en("Externer Abschnitt: eine Web-App mitten in der Befragung, mit signierten Rückgabewerten",
                   "External section: a web app in the middle of a survey, with signed return values"), None),
            (de_en("Web Service im Ablauf: Der Server ruft eine Schnittstelle auf und speichert Felder der Antwort",
                   "Web service in the flow: the server calls an API and stores fields of the answer"), "webservice"),
            (de_en("Kennzahlen des Ablaufs: Dauer, Versuchsbedingungen, Pfade",
                   "Key figures of the flow: duration, experimental conditions, paths"), None),
        ]),
        ("sprachen", de_en("Sprachen und Gestaltung", "Languages and appearance"), [
            (de_en("Befragungen auf Deutsch (Sie und du), Englisch, Arabisch, Chinesisch, Französisch, Italienisch, "
                   "Niederländisch, Polnisch, Portugiesisch, Russisch, Spanisch und Türkisch",
                   "Surveys in German (formal and informal), English, Arabic, Chinese, French, Italian, Dutch, Polish, "
                   "Portuguese, Russian, Spanish and Turkish"), None),
            (de_en("Übersetzungsseite mit dem Stand jedes Textes; KI übersetzt auf Wunsch, was offen ist",
                   "Translation page with the state of every text; AI translates what is open on request"), None),
            (de_en("Oberfläche für Forschende auf Deutsch oder Englisch",
                   "Interface for researchers in German or English"), None),
            (de_en("Eigene Farben, Schrift und Logo; Kontrast von mindestens 4,5:1 geprüft; Designs mit der Gruppe "
                   "teilen",
                   "Own colours, font and logo; contrast of at least 4.5:1 checked; share designs with a group"), None),
            (de_en("Einheitlicher Stil für alle KI-Bilder einer Befragung",
                   "One style for all AI images of a survey"), None),
        ]),
        ("erhebung", de_en("Datenerhebung", "Data collection"), [
            (de_en("Teilnahmelink mit Kurzname und QR-Code; Links je Person oder Gruppe mit Zählung",
                   "Participation link with a short name and QR code; links per person or group with counts"), None),
            (de_en("Panel-Anbindung: Kennungen aus dem Link, Abschlusslinks je Ausgang",
                   "Panel providers: IDs from the link, completion links per outcome"), None),
            (de_en("Einladungs-E-Mails an Teilnehmende mit Erinnerungen",
                   "Invitation emails to participants with reminders"), None),
            (de_en("Vorschau in Desktop- und Handybreite; Pretest-Links ohne Konto; Kommentare mit Bildschirmfoto",
                   "Preview in desktop and phone width; pretest links without an account; comments with a "
                   "screenshot"), None),
            (de_en("Testdurchläufe und Dummy-Daten, getrennt von echten Teilnahmen",
                   "Test runs and dummy data, kept apart from real participations"), None),
            (de_en("Geplanter Start, Feldende nach Datum oder Zahl der Teilnahmen",
                   "Planned start, field end by date or number of participations"), None),
            (de_en("Zeitverlauf der Teilnahmen und wer gerade in der Befragung ist",
                   "Timeline of participations and who is in the survey right now"), None),
            (de_en("Wiederholte Teilnahme im selben Browser sperren (Cookie ohne Kennung)",
                   "Block repeated participation in the same browser (cookie without an ID)"), None),
            (de_en("Sitzungen halten sieben Tage: unterbrechen und später weitermachen",
                   "Sessions last seven days: interrupt and continue later"), None),
        ]),
        ("quoten", de_en("Quoten und Gewichtung", "Quotas and weighting"), [
            (de_en("Harte und weiche Quoten mit Teilquoten, Haltezeit und Toleranz",
                   "Hard and soft quotas with sub-quotas, holding time and tolerance"), None),
            (de_en("Quotenassistent „Bevölkerung Deutschland“ nach Alter, Geschlecht und Bundesland (Zensus 2022, "
                   "Destatis)",
                   "Quota assistant “Population of Germany” by age, gender and federal state (census 2022, Destatis)"),
             None),
            (de_en("Gewichtung durch Raking, nach den eigenen Quoten oder der Bevölkerung",
                   "Weighting by raking, by the survey's own quotas or the population"), None),
        ]),
        ("qualitaet", de_en("Qualitätssicherung", "Quality control"), [
            (de_en("Speeder nach Gesamtdauer (Leiner, 2019) oder Relative Speed Index je Seite",
                   "Speeders by total duration (Leiner, 2019) or Relative Speed Index per page"), None),
            (de_en("Antwortmuster: immer gleiche Antworten, lange Reihen, geringe Streuung",
                   "Response patterns: straightlining, long strings, low variance"), None),
            (de_en("Aufmerksamkeitsprüfungen", "Attention checks"), None),
            (de_en("Schutz vor Bots und KI-Agenten, auch mit einem Köder, den Menschen nicht sehen",
                   "Protection against bots and AI agents, also with a lure humans do not see"), None),
            (de_en("Tempo-Hinweis: Eine zu schnell abgeschickte Seite erscheint einmal erneut",
                   "Tempo hint: a page sent too quickly appears once more"), None),
            (de_en("Paradaten auf Wunsch: Zeit je Seite, Fensterwechsel und Einfügen, grobe Geräteangaben",
                   "Paradata on request: time per page, tab switches and pasting, rough device details"), None),
            (de_en("Markieren statt löschen: Die Forschenden entscheiden, der Methodenteil berichtet",
                   "Mark instead of delete: researchers decide, the methods section reports"), None),
        ]),
        ("ergebnisse", de_en("Ergebnisse und Statistik", "Results and statistics"), [
            (de_en("Deskriptive Statistik mit Konfidenzintervallen und Verteilungen, laufend aktuell",
                   "Descriptive statistics with confidence intervals and distributions, always up to date"), None),
            (de_en("Cronbachs α und McDonalds ω mit Intervallen, Itemanalyse",
                   "Cronbach's α and McDonald's ω with intervals, item analysis"), None),
            (de_en("Skalen im Überblick mit Korrelationen, als APA-Tabelle für Word",
                   "Scales at a glance with correlations, as an APA table for Word"), None),
            (de_en("Hypothesen des Studienplans getestet, mit Satz im APA-Stil und Sensitivitätsanalyse",
                   "Hypotheses of the research plan tested, with an APA-style sentence and a sensitivity analysis"), None),
            (de_en("Gruppenvergleiche mit Tests und Effektgrößen", "Group comparisons with tests and effect sizes"),
             None),
            (de_en("Wahlmodelle: Zählwerte, Logit, Hierarchical Bayes, Zahlungsbereitschaft, Latent-Class-Segmente und "
                   "Marktsimulator",
                   "Choice models: counts, logit, hierarchical Bayes, willingness to pay, latent class segments and a "
                   "market simulator"), None),
            (de_en("Offene Antworten kategorisieren (nach Mayring), mit KI-Vorschlägen",
                   "Categorise open answers (after Mayring), with AI suggestions"), "coding"),
            (de_en("Methodenteil auf Deutsch oder Englisch aus den Zahlen der Befragung, mit oder ohne KI",
                   "Methods section in German or English from the survey's own figures, with or without AI"), None),
            (de_en("Dashboards mit Feld- oder Ergebnisbericht, öffentlichen Links, Live-Aktualisierung und Einbetten; "
                   "nur zusammengefasste Zahlen",
                   "Dashboards with a field or results report, public links, live updates and embedding; summarised "
                   "figures only"), None),
        ]),
        ("export", de_en("Export und offene Daten", "Export and open data"), [
            (de_en("CSV, SPSS und JASP (.sav), jamovi (.omv), R (.rds), Langformate, mit Codebuch",
                   "CSV, SPSS and JASP (.sav), jamovi (.omv), R (.rds), long formats, with codebook"),
             None),
            (de_en("Fragebogen als Word (Codebuch oder Druckfassung), Literatur als .bib und .ris",
                   "Questionnaire as Word (codebook or print version), references as .bib and .ris"), None),
            (de_en("Lese-Tokens für R und Python", "Read tokens for R and Python"), None),
            (de_en("Datenpaket zu Zenodo (als Entwurf, Veröffentlichen ist ein eigener Schritt)",
                   "Data package to Zenodo (as a draft, publishing is a step of its own)"), None),
            (de_en("Coscine (Forschungsdatenarchiv der RWTH Aachen): Datenpaket, Präregistrierung und Fassungen eines "
                   "Interviewleitfadens ablegen, auf Wunsch automatisch",
                   "Coscine (research data archive of RWTH Aachen University): store the data package, "
                   "pre-registration and revisions of an interview guide, automatically if you wish"), None),
            (de_en("Video und Ton aus Coscine für das Element „Video / Audio“ übernehmen (im Hintergrund geladen)",
                   "Take video and sound from Coscine for the element “Video / audio” (loaded in the background)"),
             "coscine_media"),
            (de_en("Siegelkette mit DFN-Zeitstempeln: Zeigt, ob ein Fall unbemerkt entfernt wurde; stündliche Prüfung, "
                   "öffentliche Prüfseite und eigenständiges Prüfprogramm",
                   "Seal chain with DFN timestamps: shows whether a case was removed unnoticed; hourly check, public "
                   "check page and a standalone checker"), None),
            (de_en("Große Berichte ab 5.000 Teilnahmen in eigenen Prozessen",
                   "Large reports from 5,000 participations in processes of their own"), None),
        ]),
        ("interview", de_en("Interviewleitfaden", "Interview guide"), [
            (de_en("Themenblöcke und Leitfragen nach Helfferich (2011), Kurzfragebogen und Postskript; Vorlagen für "
                   "problemzentrierte, Experten- und narrative Interviews",
                   "Topic blocks and guiding questions after Helfferich (2011), short questionnaire and postscript; "
                   "templates for problem-centred, expert and narrative interviews"), None),
            (de_en("Leitfaden mit KI entwickeln (sammeln, prüfen, sortieren, subsumieren)",
                   "Develop the guide with AI (collect, check, sort, subsume)"), None),
            (de_en("Interviewansicht für Tablet oder Laptop mit Uhr und Häkchen je Aspekt",
                   "Interview view for tablet or laptop with a clock and ticks per aspect"), None),
            (de_en("Aufgaben im Gespräch: Rangfolge, Kartensortierung, Bewertung und Bildauswahl zur Leitfrage, zum Zeigen "
                   "im Vollbild; Ergebnis in Fallübersicht, Transkript und .qdpx",
                   "Tasks during the interview: ranking, card sort, rating and image choice with a guiding question, "
                   "shown full screen; result in the case overview, transcript and .qdpx"), None),
            (de_en("Teilnehmerbildschirm: Die interviewte Person löst eine freigegebene Aufgabe oder füllt den "
                   "Kurzfragebogen auf dem eigenen Gerät aus, auch im Videointerview",
                   "Participant screen: the interviewee solves a released task or fills in the short questionnaire on "
                   "their own device, also in a video interview"), None),
            (de_en("Offline-Modus: Interviews ohne Netz führen, mit reservierten Kennungen und Löschcodes, verschlüsselt "
                   "auf dem Gerät, abends abgleichen",
                   "Offline mode: conduct interviews without a network, with reserved identifiers and deletion codes, "
                   "encrypted on the device, synced in the evening"), "offline_interviews"),
            (de_en("Interviewlinks: Interviewende ohne Konto führen, nehmen auf und transkribieren nur ihre eigenen "
                   "Interviews, bis sie das Transkript übergeben",
                   "Interviewer links: interviewers without an account conduct, record and transcribe only their own "
                   "interviews until they hand the transcript over"), None),
            (de_en("Tonaufnahme, auch mit einem zweiten Gerät als Aufnahmegerät",
                   "Audio recording, also with a second device as the recorder"), None),
            (de_en("Online-Interviews: Tonaufnahme aus Zoom, Teams, Webex oder BigBlueButton hochladen, eine Datei je "
                   "Stimme mit Sprecher I oder B",
                   "Online interviews: upload the audio recording from Zoom, Teams, Webex or BigBlueButton, one file per "
                   "voice with speaker I or B"), None),
            (de_en("Ton der Videokonferenz im Browser mit aufnehmen (Chrome, Edge)",
                   "Record the sound of the video conference in the browser (Chrome, Edge)"), None),
            (de_en("Transkription auf dem eigenen Whisper-Server, nie bei Dritten",
                   "Transcription on the institution's own Whisper server, never with third parties"), None),
            (de_en("Suche in den Transkripten (Keyword in Context) und Fallzusammenfassungen (Kuckartz & Rädiker, 2022)",
                   "Search in the transcripts (keyword in context) and case summaries (Kuckartz & Rädiker, 2022)"), None),
            (de_en("Fassungen festhalten; Export als Word und REFI-QDA (.qdpx, .qdc) für MAXQDA, ATLAS.ti und NVivo",
                   "Freeze revisions; export as Word and REFI-QDA (.qdpx, .qdc) for MAXQDA, ATLAS.ti and NVivo"), None),
        ]),
        ("live", de_en("Live-Abstimmung", "Live poll"), [
            (de_en("Bewertungsskalen, Einfachauswahl, Wortwolken, Freitext und Quizfragen",
                   "Rating scales, single choice, word clouds, text and quiz questions"), None),
            (de_en("Ansicht zum Vortragen und Ansicht fürs Publikum; QR-Code und kurze Adresse; ohne Anmeldung",
                   "Presenter view and audience view; QR code and short address; no sign-in"), None),
            (de_en("Durchgänge je Sitzung, Proben, Ergebnisfolien als PDF",
                   "Runs per session, rehearsals, result slides as PDF"), None),
            (de_en("Fragen von der KI vorschlagen lassen", "Have the AI suggest questions"), None),
            (de_en("Kurztexte ausblenden, bevor sie auf dem Beamer erscheinen",
                   "Hide short texts before they appear on the projector"), None),
            (de_en("Live-Feedback zum Vortrag: Folien aus PDF, Emojis und Fragen je Folie, Rückblick am Ende",
                   "Live feedback on a talk: slides from PDF, emojis and questions per slide, review at the end"),
             None),
        ]),
        ("tagebuch", de_en("Tagebuchstudie", "Diary survey"), [
            (de_en("Eigene App für Tagebuchstudien und Experience Sampling: für Android, iOS folgt",
                   "Own app for diary surveys and experience sampling: for Android, iOS to follow"), "diary"),
            (de_en("Beitreten per QR-Code oder Beitrittscode; Eingangs-, Kurz- und Abschlussbefragungen",
                   "Join by QR code or join code; entry, short and exit surveys"), "diary"),
            (de_en("Benachrichtigungen zu festen oder zufälligen Zeiten in Zeitfenstern, Ruhezeiten, Erinnerungen",
                   "Prompts at fixed or random times within windows, quiet hours, reminders"), "diary"),
            (de_en("Antwortquote je Person, Verlauf über Tage und Stunden mit ICC(1), Export im Langformat",
                   "Compliance per person, course over days and hours with ICC(1), export in long format"), "diary"),
            (de_en("KI-Chat für Teilnehmende: Gespräch mit einem KI-Assistenten über den eigenen Modellzugang der "
                   "Befragung, jede Nachricht als Daten gespeichert",
                   "AI chat for participants: a conversation with an AI assistant over the survey's own model access, "
                   "every message stored as data"), "ai_chat"),
        ]),
        ("papier", de_en("Papierfragebogen", "Paper questionnaire"), [
            (de_en("Druck als PDF mit nummerierten Bögen, Filter- und Screen-out-Hinweisen",
                   "Printed as a PDF with numbered sheets, filter and screen-out notes"), None),
            (de_en("Scans hochladen, Kreuze auf dem Server auslesen, unsichere Kreuze von Hand prüfen, auf Wunsch "
                   "mit Vier-Augen-Prüfung",
                   "Upload scans, read ticks on the server, check uncertain ticks by hand, optionally with a "
                   "four-eyes check"), None),
            (de_en("Handschriftliche Zahlen lokal als Vorschlag gelesen; Handschrift auf Wunsch per KI",
                   "Handwritten numbers read locally as a suggestion; handwriting by AI if you wish"), None),
            (de_en("Auch CBC, MaxDiff und Hot Spots; Ergebnisse nach Modus getrennt",
                   "CBC, MaxDiff and hot spots too; results split by mode"), None),
        ]),
        ("datenschutz", de_en("Datenschutz und Sicherheit", "Privacy and security"), [
            (de_en("Anonym als Voreinstellung: keine IP-Adresse, kein Browser-Fingerabdruck, keine Tracker oder fremden "
                   "Schriften",
                   "Anonymous by default: no IP address, no browser fingerprint, no trackers or third-party fonts"),
             None),
            (de_en("Einwilligung als strukturiertes Element mit Assistent; nennt von selbst, was erhoben wird",
                   "Consent as a structured element with an assistant; automatically names what is collected"), None),
            (de_en("Löschcode: Teilnehmende löschen ihre eigenen Antworten, auch von einem anderen Gerät",
                   "Deletion code: participants delete their own answers, also from another device"), None),
            (de_en("Aufbewahrungsfristen löschen Kennungen, Kontaktdaten und Teilnahmen von selbst",
                   "Retention periods delete IDs, contact details and participations automatically"), None),
            (de_en("Getrennter Abschnitt für Kontaktdaten, nicht mit den Antworten verbindbar",
                   "Separated section for contact details, not linkable to the answers"), None),
            (de_en("Region aus der IP-Adresse auf Wunsch geschätzt, ohne die Adresse zu speichern",
                   "Region estimated from the IP address on request, without storing the address"), None),
            (de_en("Datenschutzseite, Erklärung zur Barrierefreiheit und Beschwerdeformular je Befragung",
                   "Privacy page, accessibility statement and complaint form per survey"), None),
            (de_en("Eigene Adresse für Teilnehmende, getrennt von der Oberfläche der Forschenden",
                   "Own address for participants, separate from the researchers' interface"), None),
            (de_en("Datenschutzkonzept aus dem Code erzeugt", "Privacy concept generated from the code"), None),
            (de_en("Zweiter Faktor für die Admins der Plattform; Sicherheitsprotokoll",
                   "Second factor for platform admins; security log"), None),
            (de_en("Updates ohne Fehlerseite: Die Server für Teilnehmende starten nacheinander neu",
                   "Updates without an error page: the participant servers restart one after the other"), None),
        ]),
        ("teilnehmende", de_en("Für Teilnehmende", "For participants"), [
            (de_en("Barrierearm nach BITV NRW / WCAG 2.1 AA: Tastatur, Screenreader, Farbe nie das einzige Signal",
                   "Accessible after BITV NRW / WCAG 2.1 AA: keyboard, screen reader, colour never the only signal"),
             None),
            (de_en("Für Handys gebaut und sparsam mit mobilen Daten",
                   "Built for phones and sparing with mobile data"), None),
            (de_en("Bilder werden als WebP verkleinert ausgeliefert", "Images are served smaller as WebP"), None),
            (de_en("Schneller blättern, auch bei schwachem Netz: Ein Funkloch hält niemanden auf, jede Seite wird im Hintergrund gespeichert",
                   "Page through faster, even on a weak connection: a dead spot stops nobody, every page is saved in the background"), None),
        ]),
        ("team", de_en("Zusammenarbeit und Konten", "Teamwork and accounts"), [
            (de_en("Anmelden mit ORCID oder mit E-Mail und Passwort", "Sign in with ORCID or email and password"),
             None),
            (de_en("Rollen je Befragung: Admin, Bearbeiten, Ansehen", "Roles per survey: Admin, Edit, View"), None),
            (de_en("Gruppen teilen Befragungen, Designs und Konstrukte; Beitrittslinks mit Gültigkeit",
                   "Groups share surveys, designs and constructs; join links with a validity"), None),
            (de_en("Beispielbefragungen, die alle Forschenden ansehen und kopieren können",
                   "Example surveys every researcher can view and copy"), None),
            (de_en("Rückmeldung an das Projekt über den Käfer in der Kopfzeile",
                   "Feedback to the project via the bug in the header"), None),
            (de_en("Neuigkeiten als Blog mit Atom-Feed", "News as a blog with an Atom feed"), None),
        ]),
        ("ki", de_en("KI", "AI"), [
            (de_en("Jede KI-Funktion ist optional, auf Wunsch und gekennzeichnet; KI sieht nie die Antworten der "
                   "Teilnehmenden",
                   "Every AI function is optional, on request and marked; AI never sees participants' answers"), None),
            (de_en("Über den Zugang der Plattform oder einen eigenen KI-Schlüssel (KI:connect.nrw, OpenAI oder ein "
                   "kompatibler Dienst)",
                   "Through the platform's access or your own AI key (KI:connect.nrw, OpenAI or a compatible service)"),
             None),
            (de_en("Seite „KI-Nutzung“ mit den Aufrufen der letzten 30 Tage und einem Schalter, der generative KI ganz "
                   "abschaltet",
                   "Page “AI usage” with the calls of the last 30 days and a switch that turns generative AI off "
                   "altogether"), None),
        ]),
    ]
