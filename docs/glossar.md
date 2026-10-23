# Glossar der Oberfläche

Begriffe, die die Oberfläche für Forschende einheitlich verwendet. Entstanden aus #50 (UI-Audit vom 01.10.2026) und den UI/UX-Audits vom 03.10. und 04.10.2026 (`audit-ui-ux-2026-10-03.md` und `audit-ui-ux-2026-10-04.md`, jeweils Paket D). Neue Seiten und Texte halten sich daran (die Zeilen zu Wahlaufgaben, Segmenten und Standort kamen am 05.10.2026 dazu, die zu Übernehmen, Versuchsplan, Startprüfung, Antwortmodus, Abbruch, Beteiligten und E-Mail mit dem Wording-Audit vom 16.10.2026).

`tests/ui/test_glossary.py` liest die Spalte „nicht“ beider Tabellen aus dieser Datei: ein Wort dort ist in jeder Verwendung falsch und darf weder in den Templates, den Meldungen im Python-Code (flash, Befunde) noch in den deutschen Hälften von `de_en()` stehen, auch nicht in zusammengesetzten Wörtern („Buttontext“); die englischen Wörter nicht in den englischen Hälften von `de_en()`, `Both()` und von `ui()` außerhalb der Seiten für Teilnehmende. Ein Wort mit einer Anmerkung in Klammern („Gruppe (für Ablauf-Bausteine)“) ist nur in dieser Verwendung falsch und bleibt der Durchsicht überlassen. Ein zusammengesetztes Wort, das selbst als Begriff in einer der Tabellen steht („Tagebuchstudie“, „Studienplan“), ist kein Fund.

| Begriff | Bedeutung | nicht |
|---|---|---|
| **Befragung** | das Ganze, das Forschende anlegen, ins Feld bringen und auswerten; „Studie“ nur in Texten für Teilnehmende (z. B. der Einwilligung, siehe „Teilnehmende und App“) und im festen Namen **Tagebuchstudie**; eine **Vorstudie** ist eine andere, frühere Studie (Stichprobenplanung); seit dem UI-Audit vom 12.10.2026 auch in der Fußzeile „Befragung“ | Umfrage, Studie |
| **Fragebogen**, **Tagebuchstudie**, **Interviewleitfaden**, **Live-Abstimmung** | die vier Arten einer Befragung, überall mit diesem Namen (Knopf „+ Fragebogen“, Etikett, Filter „Fragebögen“) | Neue Befragung (als Knopf), Interview (als Art), Live (als Etikett) |
| **Element** | eine Einheit im Fragebogen: Frage, Text, Bild, Seitenumbruch, Rechenwert; seine Art heißt **Elementtyp** | Item (außer bei Skalen: „Items“ einer Skala), Fragetyp |
| **Sprachnachricht** | der Elementtyp, mit dem Teilnehmende eine Frage im Browser einsprechen; auf Wunsch auch tippen | Audioantwort, Voice-Frage |
| **Abspielprotokoll** | was ein Video- oder Audio-Element über das Abspielen festhält: wie lange, bis wohin, wie oft angehalten | Tracking (für das Abspielprotokoll), Videoanalyse |
| **Option** | eine Antwortmöglichkeit oder ein Begriff innerhalb eines Elements (Auswahl, Rangfolge, Konstantsumme); in Zuordnung und 2D-Positionierung „Begriff“ | Element (für eine Antwortmöglichkeit) |
| **Aussage** | eine Zeile einer Likert-Matrix, eines Schiebereglers oder von MaxDiff | Item (für eine Zeile) |
| **Antwortmodus**: **Pflicht**, **Nachfragen**, **Freiwillig** | wie eine Frage auf eine fehlende Antwort reagiert: Pflicht lässt nicht weiter; Nachfragen markiert die Frage einmal, ein zweiter Klick auf „Weiter“ geht ohne Antwort weiter; Freiwillig lässt sofort weiter. Das Etikett im Editor heißt wie der Modus | Hinweis (als Antwortmodus) |
| **Side-by-Side-Matrix**, **Zeile**, **Spalte** | ein Element mit mehreren Fragen nebeneinander: die Zeilen sind, was bewertet wird (z. B. Verkehrsmittel), jede Spalte ist eine Frage zu allen Zeilen mit eigener Antwortart | Side-by-Side-Frage, Mehrspaltige Matrix |
| **Subskala** | eine Dimension einer mehrdimensionalen Skala (z. B. Extraversion der Big Five); ihre Aussagen oder Gegensatzpaare ergeben einen eigenen Skalenwert; in der Matrix mit „Unterdimensionen zuordnen“ | |
| **Siegelkette** | die Hash-Kette einer Befragung: jede echte Teilnahme, die zu Ende kam (abgeschlossen, Screen-out, Quote voll oder ausgeschlossen), ist ein **Fall** mit **Fallnummer**, jede Löschung hinterlässt einen **Löschvermerk**; gestempelt vom Zeitstempeldienst des DFN | Blockchain, Grabstein |
| **Stempelrunde** | ein Zeitstempel des DFN für die neuen Enden aller Siegelketten zugleich; ein Merkle-Baum verbindet sie, jede Kette bekommt ihren Pfad zum gestempelten Wert (app/seal.py) | Blockchain, Sammelsiegel |
| **Web Service** | Element im Ablauf (experimentell): der Server ruft eine Schnittstelle auf und speichert Felder der Antwort als Werte ({{ws1_temp}}); die Person verlässt die Befragung nicht, anders als beim **externen Abschnitt** | Web-Service, API-Aufruf |
| **Block** | Gruppe von Elementen, Baustein des Ablaufs; die Einwilligung ist Block 0 | |
| **Ablauf** | der Reiter und die Reihenfolge der Blöcke | (nicht für die Art der Weiterleitung, siehe dort) |
| **Reiter** | eine der Seiten einer Befragung in der Leiste oben (Fragebogen, Ablauf, Datenerhebung, Export …); im Text z. B. im Reiter „Ablauf“ | Registerkarte, Export-Seite |
| **Planen**, **Erheben**, **Analyse** | die drei Gruppen der Reiter einer Befragung (in der Leiste nur durch Striche getrennt, als Namen in der Auswahlliste am Handy und für Screenreader); die Quoten sind eine Seite unter „Ablauf“ (UI-Audit 12.10.2026, E4); „Analyse“ statt „Auswerten“, englisch „Analysis“ statt „Evaluate“, das nach Evaluationsforschung klingt (UI/UX-Audit 16.10.2026, 57). Die Administration gliedert ihre Reiter ebenso in **Personen und Organisationen**, **Inhalte** und **Betrieb** | Auswerten (als Name der Gruppe) |
| **Folie** | eine Seite einer Live-Abstimmung auf der Leinwand, mit oder ohne Frage; der Reiter heißt „Folien“ (UI-Audit 12.10.2026, E5) | Frage (für eine Textfolie) |
| **Teilnahmelink** | der Link, über den Teilnehmende eine Befragung öffnen | Teilnahme-Link |
| **Kategorisieren**, **Kategorie**, **Kodierleitfaden** | offene Antworten in Kategorien einordnen (Seite „Kategorisieren“ von der Ergebnisseite aus); jede Kategorie mit Definition, **Ankerbeispiel** und wahlweise **Oberkategorie**; der Kodierleitfaden ist die Datei mit allen Kategorien; eine Kategorie in zwei teilen heißt **Aufteilen** | Codieren, Codierleitfaden, Teilen (für eine Kategorie) |
| **Teilnahmeadresse** | die eigene Adresse für Teilnehmende (ask.queryous.de), neben der Hauptadresse der Forschenden | Teilnehmer-Domain |
| **Datenerhebung** | der Reiter mit Testen, Status (mit Zeitverlauf), Teilnahmelink und Kurzfassung der Quoten; Oberbegriff für das Erheben | Feld (als Name des Reiters), Feld → |
| **Feldphase**, **Feldende**, **im Feld** | der Zeitraum, in dem eine Befragung Antworten annimmt, sein geplantes Ende und der Zustand einer laufenden Befragung; „Datenerhebung“ bleibt Reitername und Oberbegriff (UI/UX-Audit 16.10.2026, 53) | Feldzeit |
| **KI-Nutzung**, **KI-Nutzung aller Konten** | die eigene Seite der KI-Aufrufe (Menü am Namen) und die Seite der Administration über alle Konten (UI/UX-Audit 16.10.2026, 51) | KI-Verbrauch |
| **Änderung der anderen Person laden**, **Meine Änderung behalten** | die Knöpfe nach einem Konflikt beim automatischen Speichern (UI/UX-Audit 16.10.2026, 54) | Deren Stand laden, Meinen Stand behalten |
| **Bitte erneut versuchen.** | die feste Formel nach einem Fehler, englisch „Please try again.“; kurze Anweisungen im Infinitiv („Bitte … angeben.“), ganze Sätze mit „Sie“ (UI/UX-Audit 16.10.2026, 55) | Bitte versuchen Sie es erneut |
| **ankreuzen**, **einschalten**, **markiert** | ankreuzen: ein Kästchen; einschalten: ein Schalter; markiert: nur Teilnahmen der Qualitätssicherung und Markierungen in Bildern (UI/UX-Audit 16.10.2026, 55) | anhaken, angehakt |
| **Kontonummer** | die laufende Nummer der ersten zwölf Konten auf der Seite „Konto“ (UI/UX-Audit 16.10.2026, 22) | Karteikartennummer |
| **Testdurchlauf** | ein Durchgang in der Vorschau (oder über einen Pretest-Link); gespeichert mit `source = test` | Testlauf, Testläufe, Testdaten, Testphase |
| **Durchgang** | ein Abschnitt der Datenerhebung: in einer Live-Abstimmung jede Runde mit dem Publikum, in einem Fragebogen ab „Neuer Durchgang“ (Reiter „Datenerhebung“); Menschenmenge, Streubild und Vergleiche zählen nur den aktuellen, die Daten bleiben alle, der Export nennt ihn in der Spalte `run`; nicht zu verwechseln mit einer Welle, die eine eigene Befragung ist | |
| **Reaktion** | ein Emoji, das das Publikum einer Live-Abstimmung zu einer Folie schickt; gezählt je Folie und Durchgang | Unklar-Knopf |
| **Datenfluss** | die Übersicht, welche Daten einer Befragung wohin gehen, als Word-Datei je Befragung („Datenfluss (Word)“) | Datenweg |
| **Wortwolke** | die Folie einer Live-Abstimmung, auf der das Publikum Wörter schickt; je häufiger ein Wort, desto größer; auch auf der Ergebnisseite | Tag-Cloud, Wordcloud |
| **Ergebnisfolien** | die Ergebnisse einer Live-Abstimmung zum Drucken oder Speichern, eine Seite je Frage | Handout, Bericht (für Live) |
| **Pretest-Link** | ein Link zum Testen der Befragung ohne Anmeldung, auch der aus der Vorschau fürs Handy; Antworten und Kommentare zählen als Testdurchlauf | Smartphone-Link |
| **Grundlage der Auswertung** | Datenpool, Umgang mit markierten Teilnahmen und Gewichtung, oben in Qualitätssicherung und Ergebnissen | |
| **Endseite** | was nach der letzten Seite kommt; ihr Text heißt „Text der Endseite“, die Gruppe der Einstellungen „Endseiten“ | Abschlusstext |
| **Startprüfung** | die Liste im Editor, was vor dem Start der Befragung behoben sein muss oder geprüft werden sollte | Design-Check |
| **Kurzname im Link** | der sprechende Teil eines Teilnahmelinks (`/s/mensa-studie`) | Label (für den Kurznamen), Link-Label |
| **Bedingung** | das Ablauf-Element, das Blöcke nur bei bestimmten Antworten zeigt („dann“ / „sonst“); die Kennzahl, die sie zählt, heißt „Verzweigungen“ | |
| **Auslöser** | was ein Ende auslöst (Screen-out, Quote voll, …) | |
| **Regel** | ein anderer Abschlusslink für einen Panelanbieter oder eine Teilquote | |
| **Neuigkeiten**, **Eintrag** | die öffentliche Seite /blog über Neuerungen der Plattform und ein Beitrag darauf; der Reiter der Administration heißt „Blog“ | Changelog, Versionshinweise |
| **Versuchsbedingungen** | Zellen eines Between-Subject-Designs aus Randomisierungen; auch die Stufen, nach denen eine Schleife Varianten zieht | Bedingungen (für Versuchsbedingungen) |
| **Versuchsplan** | welche Person welche Aufgaben eines Wahlexperiments (Conjoint, ACBC, Bewertungs-Conjoint, MaxDiff, Paarvergleich) oder welche Vignetten sieht; der Knopf heißt „Versuchsplan erzeugen“, seine Teile „Versionen des Versuchsplans“. „Design“ heißt nur das Erscheinungsbild; Fachwörter wie „Between-Subject-Design“ und „Designeffekt“ bleiben | Designversion, Design (für einen Versuchsplan) |
| **Version** | eine Fassung des Versuchsplans von Conjoint, MaxDiff oder Vignetten (welche Person welche Aufgaben sieht); bei Vignetten heißt sie „Deck“ (die Zeilen, die eine Person sieht) | Fassung (für einen Versuchsplan) |
| **verbotene Kombinationen** | Stufen zweier Merkmale (oder Faktoren der Vignetten), die nicht zusammen in einer Aufgabe vorkommen dürfen | Ausschlüsse (für Stufenkombinationen) |
| **Schleife** | Blöcke je Tabellenzeile oder je **Vignette** wiederholen; ihre Spalten sind **Platzhalter**; die Zeilen kommen aus einer Tabelle oder aus Faktoren und Stufen | Loop (außer als Erklärung), Loop & Merge |
| **Merkmal**, **Stufe** | die Eigenschaften einer Wahlaufgabe (Conjoint, ACBC) und ihre Werte, z. B. Merkmal Preis mit den Stufen 9 €, 12 €, 15 €; bei Vignetten „Faktor“; was Personen auszeichnet und in ein Modell eingeht, heißt **Kovariate** | Attribut, Level (Ausprägung bleibt für die Werte eines Quotenmerkmals) |
| **Option**, **Variante**, **Produkt** (Wahlaufgaben) | was zur Wahl steht: im CBC die Optionen einer Aufgabe (zur Erklärung „Alternativen“), im ACBC die Varianten (die selbst zusammengestellte heißt **Wunschvariante**), im Marktsimulator die Produkte | |
| **Paarvergleich**, **Paar**, **Wert**, **zirkuläre Triade** | das Element, das je zwei Optionen zeigt (Paarvergleich, 2026-10-10); seine Zahlen heißen „Wert“ (Bradley-Terry, Summe 100), ein Widerspruch A vor B, B vor C, C vor A heißt zirkuläre Triade; post-hoc-Tests zwischen Gruppen heißen „paarweise Tests“, nicht Paarvergleiche | Paarweiser Vergleich, Duell |
| **Adaptive Conjoint (ACA)**, **Selbstauskunft**, **Kalibrierung**, **Paaraufgabe** | das experimentelle Element, in dem Personen erst Stufen und Wichtigkeit selbst einschätzen (Selbstauskunft), dann Paaraufgaben aus Teilangeboten lösen, die sich nach jeder Antwort anpassen; die Kalibrierung sind Angebote mit Kaufwahrscheinlichkeit am Ende; „Paar“ bleibt beim Paarvergleich | Self-Explicated (außer als Erklärung), Adaptive Conjoint Analysis (als Name des Elements) |
| **Bewertungs-Conjoint (CVA)**, **Angebot**, **R² je Person** | das Element, in dem Personen Angebote aus Merkmalen und Stufen bewerten statt wählen (2026-10-17): einzeln auf einer Skala oder paarweise abgestuft; was bewertet wird, heißt „Angebot“; eine Regression je Person ergibt Teilnutzen und das „R² je Person“; das Kürzel CVA steht nur in Klammern hinter dem Namen (Markenprüfung im Konzept aca-cva-konzept.md) | Ratingbasierte Conjoint, Conjoint Value Analysis (als Name des Elements) |
| **Segment** | eine Gruppe mit ähnlichen Vorlieben aus der Latent-Class-Analyse; „latente Klasse“ nur zur Erklärung | Cluster (für ein Segment) |
| **Standort** | das Element, das die Position aus dem Browser erfragt; die Schätzung aus der IP-Adresse heißt „aus der IP-Adresse“, nicht Standort | Geolocation (als Name des Elements) |
| **Quote**, **Teilquote** | eine Quote besteht aus Teilquoten mit Soll | Zelle (für eine Teilquote) |
| **Weiterleitung** | wie ein Abschlusslink verlässt: sofort, Endseite mit Knopf, Endseite mit Wartezeit | Ablauf (für die Art der Weiterleitung) |
| **Ansicht zum Vortragen**, **Ansicht fürs Publikum** | die beiden Ansichten einer Live-Abstimmung: für den eigenen Laptop (Steuerung, Notizen, Moderation) und für den Beamer | Präsentationsansicht, Referentenansicht, Zuschaueransicht |
| **Interviewleitfaden** | der Befragungstyp für leitfadengestützte Interviews (app/interview.py) | Interview-Leitfaden, Leitfadeninterview (als Typname) |
| **Tagebuchstudie** | der Befragungstyp für Experience Sampling mit der App (app/diary.py): eine Eingangsbefragung einmal, danach Kurzbefragungen zu festen Zeiten, zufällig oder auf Wunsch der Teilnehmenden, am Ende eine Abschlussbefragung; „Studie“ ist hier Teil des festen Namens. Dazu: **Eingangsbefragung**, **Kurzbefragung** (im Code „Modul“, nie in einem sichtbaren Text), **Abschlussbefragung**, **Eintrag** (eine beantwortete Kurzbefragung), **Zeitplan** (Reiter), **Studientag** (Tag seit dem Beitritt), **Beitrittscode** und **Testcode**; einen Testcode wieder verwendbar machen heißt „Code wieder freigeben“ | Erfahrungsstichprobe, Tagebuch-Befragung, Modul (für eine Kurzbefragung), Einstieg (für die Eingangsbefragung) |
| **Themenblock** | ein Block eines Interviewleitfadens | |
| **Leitfrage** | Elementtyp mit Erzählaufforderung, Aspekten, Aufrechterhaltungsfragen und konkreten Fragen | Hauptfrage |
| **Unterfrage** | eine Leitfrage, eingerückt unter die Leitfrage darüber: eine Nachfrage, die nur manchmal passt; „Wann stellen?“ sagt, wann (z. B. „falls zutreffend“) | Unterpunkt |
| **Nachfrage** (im Leitfaden) | eine konkrete Frage, mit → eingerückt unter die konkrete Frage darüber; passt nur, wenn diese zutrifft („trifft zu / trifft nicht zu“ in der Interviewansicht). Im Fragebogen heißt „Nachfragen“ dagegen ein Antwortmodus | Unterfrage (für eine eingerückte konkrete Frage) |
| **nur wenn …**, **Fallmerkmal** | die Bedingung einer konkreten Frage oder Unterfrage im Leitfaden: ein Aspekt der Leitfrage (kam von selbst oder nicht) oder eine Antwort im Kurzfragebogen, das Fallmerkmal; eine Frage, deren Bedingung nicht zutrifft, wird in der Interviewansicht blass, nie ausgeblendet | |
| **Interviewansicht** | der Leitfaden für Tablet oder Laptop im Gespräch; für ein gespeichertes Interview speichert sie mit; ohne Interview ist sie eine „Probe ohne Speichern“ | Leseansicht |
| **Kurzfragebogen**, **Postskript** | die standardisierten Teile vor oder nach dem Gespräch | Protokoll (für das Postskript) |
| **Interviewende** | wer das Gespräch führt | Interviewer |
| **Interviewlink** | der persönliche Link einer interviewenden Person ohne Konto für einen Leitfaden (app/interviewer.py): Interviews führen, aufnehmen, transkribieren, nur die eigenen sehen | Interviewer-Link, Interviewer-Zugang |
| **Ton der Videokonferenz** | die Aufnahme eines Interviews über Zoom, Teams oder BBB: der Browser nimmt den geteilten Tab oder Bildschirm mit auf, gespeichert wird nur der Ton; der Knopf zum Anhalten heißt „Aufnahme beenden“ | Bildschirmaufnahme, Mitschnitt |
| **Teilnehmerbildschirm** | die Seite auf dem eigenen Gerät der interviewten Person (experimentell): zeigt nur die Aufgabe, die die interviewende Person freigibt („Auf dem Teilnehmerbildschirm zeigen“); als Name der Funktion fest, sonst „Teilnehmende“ | Zweitbildschirm, Teilnehmer-Ansicht |
| **Feldmappe**, **Feldpaket** | Offline-Modus (experimentell): die Feldmappe ist die Seite auf dem Gerät für Interviews ohne Netz; ein Feldpaket sind die Interviews, die der Server dafür reserviert (Kennung, Löschcode), sieben Tage gültig | Offline-Paket, Reservierung |
| **Fassung** | ein festgehaltener Stand mit Nummer: des Fragebogens (Fragebogen-Fassung, nach „Fragebogen ändern (neue Fassung)“), eines Interviewleitfadens (Export → Fassungen) oder eines verknüpften Elements | Version (bei Conjoint vergeben), Fragebogen-Version |
| **Konstrukt** | eine validierte Skala oder Standardfrage; der Bereich in der Kopfzeile heißt „Konstrukte“ | Toolbox, Konstrukt-Toolbox |
| **Schnellbox** | die kurze Liste empfohlener Konstrukte der Plattform oder einer Organisation zum Einfügen im Editor; „Alle … Konstrukte durchsuchen“ öffnet die ganze Auswahl | Favoritenliste, Toolbox |
| **Meine Favoriten** | die Konstrukte, die eine Person mit einem Stern markiert hat; eine eigene Auswahl neben der Schnellbox | Favoriten (für die Schnellbox) |
| **Gruppe** | in Kopfzeile, Navigation und Rechten nur eine Arbeitsgruppe (Kopfzeile „Gruppen“); Randomisierung, Bedingung und Schleife werden nicht so genannt, Werte eines Links heißen „je Kurs oder Standort“; in der Statistik sind „Vergleichsgruppe“, „Teilgruppe“ und „Mehrgruppenmodell“ zulässig; in der Kartensortierung heißen die Gruppen **Stapel** | Gruppe (für Ablauf-Bausteine, Link-Werte oder Stapel der Kartensortierung) |
| **Beteiligte** | die Personen, die an einer Befragung mitarbeiten (Abschnitt „Personen“ der Einstellungen): „eine weitere beteiligte Person mit der Rolle „Bearbeiten““, „für Teilnehmende und Beteiligte“; „Team“ nur für die Teams eines Quiz in der Live-Abstimmung | Team (für die Personen einer Befragung) |
| **Organisation**, **Heimat** | die Ebene über den Gruppen (Lehrstuhl, Institut, Abteilung, Firma; app/orgs.py): ein Zaun, kein Zugriff. Jede Person hat eine Heimat-Organisation; „Admin der Organisation“ neben „Admin der Plattform“; ihr Bereich heißt „Verwaltung“ (die der Plattform „Administration“); wer nur über eine Einladung in eine Befragung kam, ist **Gast** | Abteilung (als Name der Ebene), Mandant, Workspace |
| **Organisationsdesign** | das Erscheinungsbild, das eine Organisation ihren Befragungen gibt (Verwaltung > Design); jede Befragung bekommt beim Anlegen eine Kopie | Hausdesign |
| **Betreuung** | die Personen, die ein Konto betreuen (z. B. bei einer Abschlussarbeit), auch mehrere; im Konto „Betreuung hinzufügen“ | Betreuer, Supervisor |
| **Nutzertyp** | eine Art von Konten einer Organisation mit eigener Laufzeit und eigenen Rechten (z. B. Mitarbeitende, Abschlussarbeiten, Lehrveranstaltung); jede Organisation beginnt mit „Mitarbeitende“. Teilnehmende, Forschende und Admins im Datenschutzkonzept sind **Personengruppen** | Kontoart, Rolle (Rolle heißt Admin, Bearbeiten, Ansehen), Nutzertyp (für eine Personengruppe) |
| **Knopf** | Schaltfläche | Button |
| **Rollen** | Ansehen, Bearbeiten, Admin | |
| **Öffnen ↗** | ein Knopf, der etwas in neuem Tab zeigt | Ansehen ↗, Ansehen (ist eine Rolle) |
| **Übernehmen** | einen Wert oder eine Einstellung wirksam machen (EN „Apply“) oder Vorschläge (KI, Leseergebnis, Vorlage, Liste) in den eigenen Stand holen (EN „Accept“, mehrere: „Ausgewählte übernehmen“ / „Accept selected“) | |
| **anlegen**, **erzeugen**, **hinzufügen** | anlegen: ein neues Objekt (Befragung, Gruppe, Quote, Link); erzeugen: etwas Berechnetes (Versuchsplan, Codes, Bilder, Dummy-Daten); hinzufügen: ein Teil zu einem Ganzen (Element in einen Block, Person zu einer Gruppe) | erstellen (für ein neues Objekt) |
| **Einrichtung** | wo eine forschende Person arbeitet (aus ORCID); der Reiter der Administration für das Design einer Einrichtung heißt „Einrichtungen“ | Zugehörigkeit (außer „Projektzugehörigkeit“ einer Befragung) |
| **Erscheinungsbild**, **Farbschema**, **Design** | Erscheinungsbild: wie eine Befragung für Teilnehmende aussieht, so heißt auch die Gruppe der Einstellungen; Farbschema: die Auswahl der Farben darin; Design: eine gespeicherte Sammlung von Farben, Schrift und Logos auf der Seite „Designs“ (Designs der Plattform, Design der Einrichtung). Wie die Seiten für eine forschende Person selbst aussehen, heißt auf „Konto“ **Darstellung** | Vorlage (für ein Farbschema oder Erscheinungsbild) |
| **Studiendesign** | die Anlage einer Studie im Studienplan (Querschnitt, Experiment, Längsschnitt …), Feld „Studiendesign“ (Ralf, 17.10.2026) | Design (für das Studiendesign) |
| **Vorlage** | nur eine Befragungsvorlage (Assistent „Aus einer Vorlage“); die Erscheinungsbilder auf „Designs“ heißen „Designs der Plattform“, das einer Einrichtung „Design der Einrichtung“ (UI/UX-Audit 07.10.2026, W94) | Vorlage (für ein Erscheinungsbild) |
| **Zugang geben** / **Zugang nehmen** | wer das Design einer Einrichtung nutzen darf; „Sperren“ hält nur ein Konto ganz draußen (W94) | Sperren (für ein Design), Freigeben (für ein Design) |
| **Admin der Plattform** | wer die Plattform verwaltet, so in der ganzen Oberfläche; „Betreiber“ nur in Rechtstexten (Nutzungsbedingungen, Datenschutz, Impressum) und wo ein Text auf sie verweist (W95) | Betreiber (in der Oberfläche) |
| **entfernen** / **löschen** | entfernen: ein Teil aus einem Ganzen (Zeile, Teilquote, Ablauf-Element); löschen: ein eigenständiges Objekt (Block, Element, Quote, Befragung, Konto) | |
| **Teilnehmende**, **Forschende** | Personenbezeichnungen; „Person“ (Statistik „je Person“, „Personen mit …“), wo nicht die Rolle in der Befragung gemeint ist | Teilnehmer, Befragte |
| **Teilnahme**: **abgeschlossen**, **Abbruch** | eine Teilnahme ist abgeschlossen (bis zur Endseite) oder abgebrochen (eine **abgebrochene Teilnahme**, ein **Abbruch**); was noch läuft, heißt „nicht abgeschlossen“; „beendet“ nur für Interviews und Durchgänge | Abbrecher, beendete Teilnahmen, nicht beendete Teilnahmen |
| **E-Mail**, **Test-E-Mail** | so geschrieben, auch in Zusammensetzungen („E-Mail-Adresse“) | Testmail, Mail (als eigenes Wort) |
| **Strukturgleichungsmodell**, **Messmodell** | das gemeinsame Modell der Hypothesen und der Teil, der die Konstrukte aus ihren Aussagen misst (app/sem_model.py) | SEM (als Wort im Text), Kausalmodell |
| **Kontrollvariable**, **Einzelfrage mit Reliabilität**, **Faktor zweiter Ordnung**, **Konkurrenzmodell** | Einstellungen des Strukturgleichungsmodells im Studienplan (SEM-Erweiterungen 2026-10-17) | Kovariate (im Text der Seiten), Second-Order-Faktor |
| **Messinvarianz** (konfigural, metrisch, skalar, strikt), **latenter Mittelwert** | ob eine Skala in allen Gruppen dasselbe misst, und der Mittelwert eines Konstrukts je Gruppe | Messäquivalenz |
| **Full Information Maximum Likelihood**, **geordnete Kategorien (WLSMV)** | Schätzung mit Lücken; Aussagen als geordnete Stufen | FIML-Imputation (es wird nichts ersetzt), ordinal (für WLSMV) |
| **robuste Fitindizes**, **Residualkorrelation**, **Johnson-Neyman-Bereich** | Ergebnisse des Strukturgleichungsmodells | Modifikationsindex (steht nur auskommentiert im R-Skript) |
| **Explorative Faktorenanalyse**, **Parallelanalyse** | unter jeder Skala ab vier Aussagen (app/efa.py) | EFA (als Wort im Text), Hauptkomponentenanalyse (für die EFA) |
| **Power per Simulation** | die Power der Hypothesen eines geplanten Strukturgleichungsmodells aus simulierten Datensätzen | Monte-Carlo-Power (im Text der Seiten) |
| **Welle**, **Längsschnitt über die Wellen**, **Wachstumskurve**, **Cross-Lagged-Panel-Modell** (RI-CLPM), **Wirkung über die Zeit** | verknüpfte Befragungen derselben Personen und ihre Modelle auf der Ergebnisseite (app/panel_models.py); „Wirkung über die Zeit“ ist die Art einer Hypothese im Studienplan | Panelwelle (als Wort im Text) |
| **Mehrebenenmodell**, **innerhalb/zwischen Personen**, **zufällige Steigung**, **zeitverzögert**, **Skalen in zwei Ebenen** | die Modelle einer Tagebuchstudie: Einträge in Personen (app/diary_model.py, app/diary_scales.py) | Multilevel-Modell (im deutschen Text), Hierarchisches Modell (im deutschen Text) |
| **Vermittlung**, **Mittler**, **indirekter Effekt** | eine Wirkung über eine dritte Größe; Art einer Hypothese im Studienplan | Mediation (im deutschen Text), Mediator (im deutschen Text) |
| **Stichprobenziel** | die Zahl der Abschlüsse, die die Befragung erreichen soll: verwertbare Teilnahmen plus die erwarteten Ausschlüsse; Studienplan, Rechner („Als Ziel übernehmen“) und Feldende meinen dieselbe Zahl (UI/UX-Audit 07.10.2026, W2, W3) | |
| **verwertbare Teilnahmen** | Abschlüsse, die nach den Qualitätsregeln in die Auswertung gehen; so viele braucht ein Test laut Poweranalyse | abgeschlossene Teilnahmen (für diese Zahl) |
| **Ausschlüsse** | nur Abschlüsse, die die Qualitätsregeln aus der Auswertung nehmen, als „erwartete Ausschlüsse (%)“ geplant; Abbrüche zählen nicht als Abschluss; unmögliche Stufenkombinationen heißen „verbotene Kombinationen“ | Ausfälle |
| **Stichprobenmodell**, **Rücklaufquote** | die eine Zeile „84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen“ auf Studienplan, Rechner, Datenerhebung, Quotenassistent, Export und Methodenteil (app/sample_model.py, S3); für ein Online-Panel mit Rücklaufquote dazu „188 einladen bei 50 % Rücklauf“ | Responsequote, Ausschöpfung |
| **Power** | die Wahrscheinlichkeit, einen Effekt der geplanten Größe zu finden; geschrieben „Power 80 %“, das Signifikanzniveau „α = ,05“ (ohne führende Null), der Test „χ²-Test“ | Teststärke (als Feldname), 0,80 (für die Power), Chi²-Test |
| **Fachkürzel** | HB, MNL, RLH und ähnliche Kürzel stehen bei der ersten Nennung je Seite ausgeschrieben oder mit Erklärung beim Darüberfahren (`<abbr title>`); Knöpfe sagen, was geschieht („Individuelle Nutzen schätzen (Hierarchical Bayes)“) | |

Ansprache: Forschende werden gesiezt; Teilnehmende je nach Fassung („Deutsch (Sie)“ oder „Deutsch (du)“).

Gendern (Wording-Audit 16.10.2026, W-09): 1. neutrale Form oder Partizip („Teilnehmende“, „Forschende“, „wer die Befragung besitzt“, „die Person, der die Befragung gehört“); 2. Umschreibung („die Person, die eingeladen hat“, „Adressliste“ statt „Empfänger“, „Die E-Mail kommt von …“ statt „Absender“); 3. die Paarform („Autorinnen und Autoren“) nur, wo beides nicht geht. Kein Doppelpunkt, Sternchen oder Binnen-I (Screenreader, Lesbarkeit), kein generisches Maskulinum oder Femininum.

Zeitangaben: „ca. 2:10 Min.“, unter einer Minute „40 s“.

Sprachnamen erscheinen in der eigenen Sprache („English“, „Français“), wie in der Sprachauswahl.

## Teilnehmende und App

Wörter in Texten für Teilnehmende (Web und App), vor allem in einer Tagebuchstudie (Wording-Audit 16.10.2026, W-11). Für Teilnehmende ist eine Befragung eine **Studie**.

| Begriff | Bedeutung | nicht |
|---|---|---|
| **Studie** | was Teilnehmende mitmachen, auch eine Tagebuchstudie; „Studie suchen“ für das Nachschlagen eines Beitrittscodes | Studie (als Kontakt: „kontaktieren Sie die Studie“) |
| **Eingangsbefragung** | der erste Teil einer Tagebuchstudie, einmal am Anfang; EN „entry survey“ | erster Fragebogen (für die Eingangsbefragung) |
| **Kurzbefragung** | ein Termin einer Tagebuchstudie, eine beantwortete heißt **Eintrag**; EN „short survey“ | Modul (für eine Kurzbefragung) |
| **Abschlussbefragung** | der letzte Teil einer Tagebuchstudie, einmal am Ende; EN „exit survey“, im Fließtext auch „final survey“ | |
| **die Forschenden** | wer die Studie durchführt und für Fragen da ist (Kontakt unter „Über diese Studie“); EN „the researchers“ | Forschungsteam, Studienteam (für die Forschenden) |
| **Telefon** | das Gerät, auf dem die App läuft; EN „phone“ | Gerät (für das Telefon in der App) |
| **die Betreiber der Plattform** | wer Askurious betreibt, wie auf den Datenschutzseiten im Web | Anbieter der App (für die Betreiber) |

Anrede in der App wie im Web: „Sie“ (oder „du“ in der Fassung „Deutsch (du)“), keine Infinitive in ganzen Sätzen.

## Englisch

Die Oberfläche für Forschende und Admins gibt es auch auf Englisch (Konto › Sprache, app/lang.py; Konzept vom 06.10.2026). Jeder Text steht im Code als Paar `de_en("Deutsch", "English")`; die englische Hälfte nimmt diese Begriffe. `tests/ui/test_glossary.py` prüft die englischen Hälften auf die Wörter in der Spalte „nicht“ (ohne die mit einer Anmerkung in Klammern, siehe oben).

| Deutsch | Englisch | nicht |
|---|---|---|
| Befragung | survey | study |
| Fragebogen, Tagebuchstudie, Interviewleitfaden, Live-Abstimmung | questionnaire, diary survey, interview guide, live poll | diary study (für Forschende) |
| Interviewende, Interviewlink | interviewer, interviewer link | |
| Leitfrage, Unterfrage, Nachfrage (im Leitfaden), Fallmerkmal, trifft zu | guide question, sub-question, follow-up, case feature, applies | |
| Eingangsbefragung, Kurzbefragung, Abschlussbefragung (Teile einer Tagebuchstudie) | entry survey, short survey, exit survey | entry part, module, exit part |
| Element, Elementtyp | element, element type | question type, item type |
| Option, Aussage | option, statement | |
| Antwortmodus Pflicht, Nachfragen, Freiwillig | answer mode required, request, optional | nudge |
| Side-by-Side-Matrix, Zeile, Spalte | side-by-side matrix, row, column | |
| Subskala | subscale | |
| Paarvergleich, Paar, Wert, zirkuläre Triade | paired comparison, pair, value, circular triad | pairwise duel |
| Adaptive Conjoint (ACA), Selbstauskunft, Kalibrierung, Paaraufgabe | adaptive conjoint (ACA), self-explication, calibration, pair task | |
| Bewertungs-Conjoint (CVA), Angebot, R² je Person | ratings-based conjoint (CVA), offer, R² per person | rating conjoint |
| Siegelkette, Fall, Fallnummer, Löschvermerk, Zeitstempel | seal chain, case, case number, deletion record, timestamp | blockchain, tombstone |
| Stempelrunde | timestamp round | batch seal |
| Sprachnachricht, Abspielprotokoll | voice message, playback log | audio answer, tracking (für das Abspielprotokoll) |
| Ton der Videokonferenz, Aufnahme beenden | sound of the video conference, stop recording | screen recording |
| Teilnehmerbildschirm, Auf dem Teilnehmerbildschirm zeigen | participant screen, show on the participant screen | participant view |
| Feldmappe, Feldpaket | field kit, field pack | offline package |
| Wortwolke, Ergebnisfolien | word cloud, result slides | tag cloud, handout |
| Reaktion | reaction | |
| Datenfluss | data flow | |
| Nutzertyp, Personengruppe | user type, group of people | kind of user, account type |
| Schnellbox, Meine Favoriten | quick box, my favourites | favourites (für die Schnellbox) |
| Web Service, Testaufruf | web service, test call | API call |
| Teilnehmende, Forschende, Person | participants, researchers, person (Mehrzahl people) | respondents, persons |
| Teilnahme | participation | response (als Zählwort) |
| abgeschlossen, Abbruch, abgebrochene Teilnahme | completed, break-off, broken-off participation | dropout, drop-out, abandoned (für eine Teilnahme), unfinished (für eine Teilnahme) |
| Admin der Plattform | platform admin | operator |
| Übernehmen (Wert, Einstellung), Übernehmen (Vorschläge), Ausgewählte übernehmen | apply, accept, accept selected; im Fließtext applied, accepted, copied | take over, taken over |
| anlegen, erzeugen, hinzufügen | create, generate, add | make (für ein neues Objekt) |
| Zugang geben, Zugang nehmen | give access, remove access | clear (für ein Design), block (für ein Design) |
| Teilnahmelink, Pretest-Link | participation link, pretest link | survey URL |
| Teilnahmeadresse, Hauptadresse | participant address, main address | participant domain |
| Kategorisieren, Kategorie, Kodierleitfaden, Ankerbeispiel, Oberkategorie, Aufteilen | categorise, category, coding guide, anchor example, main category, split | code (als Verb für Kategorien) |
| Reiter | tab | |
| Planen, Erheben, Analyse; Personen und Organisationen, Inhalte, Betrieb | plan, collect, analysis; people and organisations, contents, operation | evaluate (als Name der Gruppe) |
| Folie | slide | question (for a text slide) |
| Ablauf, Block, Bedingung | flow, block, condition | routing |
| Datenerhebung, Feldphase, Feldende | data collection, fieldwork, end of fieldwork | fieldwork (als Name des Reiters) |
| KI-Nutzung, KI-Nutzung aller Konten | AI usage, AI usage of all accounts | |
| Änderung der anderen Person laden, Meine Änderung behalten | Load the other person’s version, Keep my version | Load their version |
| Bitte erneut versuchen. | Please try again. | |
| endgültig löschen | delete permanently | delete for good (als Knopf) |
| Präregistrierung | pre-registration | |
| anmelden | sign in | log in (für anmelden) |
| Kontonummer | account number | index card number |
| Testdurchlauf | test run | test data |
| Durchgang | run | |
| Merkmal, Stufe, Kovariate | attribute, level, covariate | feature (für ein Merkmal), characteristic (für ein Merkmal) |
| Faktor (Vignetten) | factor | |
| Versuchsplan, Version | experimental design, version | experimental plan, design version |
| verbotene Kombinationen | prohibitions | exclusions (für Stufenkombinationen) |
| Startprüfung | pre-launch check | design check |
| Endseite, Text der Endseite | end page, end page text | thank-you page, closing text (für den Text der Endseite) |
| Erscheinungsbild, Farbschema, Design, Darstellung (Konto) | appearance, colour scheme, design, appearance | theme (für ein Farbschema) |
| Studiendesign | research design | design (für das Studiendesign) |
| Datenpool | data pool | |
| Quote, Teilquote | quota, sub-quota | quota group |
| Fassung, Version | revision, version | |
| Konstrukt | construct | toolbox |
| Gruppe, Stapel (Kartensortierung) | group, pile | team (für eine Gruppe), group (in der Kartensortierung) |
| Beteiligte | people involved | team (für die Personen einer Befragung) |
| Organisation, Heimat-Organisation, Verwaltung, Gast | organisation, home organisation, management, guest | department (als Name der Ebene), tenant |
| Organisationsdesign, Betreuung | organisation design, supervisors | |
| Rollen Admin, Bearbeiten, Ansehen | Admin, Edit, View | |
| Lernhilfen | learning aids | tips (für Lernhilfen) |
| Einrichtung | institution | affiliation |
| Konto, Darstellung, Sprache | account, appearance, language | profile (für das Konto) |
| Rückmeldung | feedback | |
| Neuigkeiten, Eintrag | news, entry | changelog |
| löschen, entfernen | delete, remove | |
| E-Mail, Test-E-Mail | email, test email | e-mail |
| Strukturgleichungsmodell, Messmodell | structural equation model, measurement model | SEM (in running text), causal model |
| Kontrollvariable, Einzelfrage mit Reliabilität, Faktor zweiter Ordnung, Konkurrenzmodell | control variable, single question with a reliability, second-order factor, competing model | covariate (in page text) |
| Messinvarianz, latenter Mittelwert | measurement invariance, latent mean | measurement equivalence |
| Welle, Längsschnitt über die Wellen, Wachstumskurve, Cross-Lagged-Panel-Modell, Wirkung über die Zeit | wave, longitudinal analysis over the waves, growth curve, cross-lagged panel model, effect over time | panel wave (as a word in the text) |
| Mehrebenenmodell, innerhalb/zwischen Personen, zufällige Steigung, zeitverzögert, Skalen in zwei Ebenen | multilevel model, within/between people, random slope, lagged, scales in two levels | hierarchical model (in running text) |
| Full Information Maximum Likelihood, geordnete Kategorien | full information maximum likelihood, ordered categories | FIML imputation |
| robuste Fitindizes, Residualkorrelation, Johnson-Neyman-Bereich | robust fit indices, residual correlation, Johnson-Neyman region | modification index |
| Explorative Faktorenanalyse, Parallelanalyse | exploratory factor analysis, parallel analysis | principal component analysis (for the EFA) |
| Power per Simulation | power by simulation | Monte Carlo power (in page text) |
| Vermittlung, Mittler, indirekter Effekt | mediation, mediator, indirect effect | |
| Studienplan | research plan | study plan |
| Stichprobenziel, verwertbare Teilnahmen, Ausschlüsse | sample target, usable participations, exclusions | dropouts (für Ausschlüsse) |
| Stichprobenmodell, Rücklaufquote, einladen | sample model, response rate, invite | |
| Power, Signifikanzniveau | power („power 80%“), significance level („α = .05“) | statistical power |

Ansprache auf Englisch: „you“. Schreibweise: britisches Englisch (colour, analyse, randomisation), Satzanfang groß, auch bei Namen von Funktionen („Web service“), Knöpfe wie auf Deutsch kurz („Delete permanently“, nicht das umgangssprachliche „Delete for good“; „sign in“, nicht „log in“; „pre-registration“ mit Bindestrich; UI/UX-Audit 16.10.2026, 57). Zahlen und Daten folgen der Sprache (Paket C des Konzepts).

Englischer Stil statt Lehnübersetzung (Wording-Audit 16.10.2026, W-05): „people“ statt „persons“ (außer „per person“ in Formeln und Statistik); „comes / must come before“ statt „stands before“; „automatically“ statt „by itself“; „applies to“ statt „holds for“; „your own key“ / „its own“ statt „an own key“; „Anyone who …“ statt „Whoever …“ am Satzanfang; „Apply“ oder „Accept“ statt „Take over“.
