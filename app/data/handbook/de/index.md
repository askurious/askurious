# Askurious: das Konzept

> **Askurious ist ein Werkzeug für wissenschaftliche Befragungen: Fragebogen gestalten, ins Feld bringen, auswerten und die Daten offen teilen, alles an einem Ort.**
>
> Der Name kommt von **ask** (fragen) und **curious** (neugierig), gesprochen /ɑːˈskjʊə.ri.əs/.

Dieses Handbuch beschreibt Askurious für die Menschen, die damit arbeiten: Forschende in den Sozialwissenschaften und Studierende, die empirische Forschung lernen.
Es erklärt, wofür das Werkzeug da ist, welche Ideen es prägen, welche Begriffe es verwendet, was jede Seite tut und warum sie so gebaut ist.

| Seite | Inhalt |
|---|---|
| [Das Konzept](index.md) (diese Seite) | Die Idee, die Grundsätze, die drei Arten von Befragungen, der Lebenslauf einer Befragung, die Begriffe |
| [Anleitungen](guides.md) | Typische Studien Schritt für Schritt: die erste Befragung, eine Panelstudie mit Quoten, ein Online-Experiment, ein Seminar mit eigenen Links je Person, ein Längsschnitt |
| [Eine Befragung aufbauen](build.md) | Die Seiten, auf denen eine Befragung entsteht: was die Einstellungen bedeuten, welche Abwägungen dahinterstehen und warum es sie gibt |
| [Feldphase](field.md) | Datenerhebung, Qualitätssicherung und was Teilnehmende sehen |
| [Auswerten](analysis.md) | Ergebnisse, Dashboards, Export und offene Daten |
| [Weitere Befragungsarten](kinds.md) | Tagebuchstudie, Interviewleitfaden und Live-Abstimmung |
| [Weitere Seiten](more.md) | Konstrukte, Gruppen, Designs, Stichprobengröße, Konto, Elementtypen und Lernhilfen |
| [Metadaten, Archivierung und Veröffentlichung](metadata.md) | Welche Beschreibung Askurious für Datenarchive schreibt (DDI, CDIF), woher die Angaben kommen, was sie vergleichbar macht, was bei Zenodo und Coscine ins Paket kommt und was nie mitgeht |
| [KI in Askurious](ai.md) | Wo und wie KI eingesetzt wird, was übermittelt wird, wie KI-Ergebnisse gekennzeichnet sind und was KI bewusst nicht tut |

Die Bildschirmfotos zeigen eine Demo-Befragung, die Studienvorlagen und erfundene Personen mit Dummy-Daten (Oktober 2026), aufgenommen mit tools/handbook_shots.
Die Oberfläche ist in der Sprache dieses Handbuchs zu sehen; die Fragebögen aus den Vorlagen sind deutsch, in der Sprache der Vorlagen.
Jede Seite der Oberfläche gibt es auf Deutsch und Englisch (Konto › Anzeige und Sprache).

## Für wen

- **Forschende**, die Fragebogenstudien, Experimente, Wahlexperimente (Conjoint, MaxDiff), Interviews oder Live-Abstimmungen in Vorlesungen durchführen.
- **Studierende und Einsteigende**, die eine Abschluss- oder Hausarbeit schreiben: Lernhilfen erklären jede methodische Entscheidung dort, wo sie getroffen wird, und Studienvorlagen zeigen vollständige, lauffähige Designs mit Studienplan und Daten.
- **Teams**: Mehrere Forschende arbeiten gleichberechtigt an einer Befragung, und Gruppen teilen Befragungen und Designs mit einem ganzen Lehrstuhl oder Projekt.

## Grundsätze

Diese Grundsätze prägen das ganze Werkzeug.
Askurious bietet sinnvolle Voreinstellungen an und erklärt sie, erzwingt aber keine davon: Die Forschenden entscheiden.
Die Seitenbeschreibungen ab [Eine Befragung aufbauen](build.md) zeigen, wo jeder Grundsatz vorkommt.

1. **Die Methode zuerst.**
   Eine Befragung beginnt mit einer Forschungsfrage und Hypothesen (dem Studienplan), nicht mit einer Liste von Fragen.
   Der Plan bestimmt das Stichprobenziel (Poweranalyse), welchen Test die Ergebnisse verwenden und was in den Methodenteil kommt.
2. **Sinnvolle Voreinstellungen, erklärt.**
   Jede Einstellung hat eine Voreinstellung nach der Literatur zur Befragungsmethodik und eine „Warum?“-Erklärung, die die Abwägung mit Quelle benennt (Krosnick & Presser, 2010; Döring, 2023, und andere).
   Nichts ist erzwungen: Forschende können anders entscheiden, und die Seite sagt ihnen, worauf sie damit verzichten.
3. **Nichts verschwindet stillschweigend.**
   Speeder, Teilnahmen mit immer gleichen Antworten und mutmaßliche Bots werden *markiert*, nicht gelöscht.
   Die Forschenden entscheiden, was zählt, und der Methodenteil berichtet es.
4. **Datenschutz von Anfang an.**
   Teilnehmende sind standardmäßig anonym: keine IP-Adresse, kein Browser-Fingerabdruck.
   Die Einwilligung ist ein strukturiertes Element, das immer nennt, was das Werkzeug zusichert.
   Kontaktdaten für eine Verlosung werden so gespeichert, dass sie sich nicht mit den Antworten verbinden lassen.
   Teilnehmende können ihre eigenen Antworten mit einem Löschcode löschen.
   Aufbewahrungsfristen löschen Daten, ohne dass jemand daran denken muss.
5. **Offene Daten leicht gemacht.**
   Der Weg vom Feld zum zitierbaren Datenpaket ist kurz: Codebuch, SPSS, jamovi, R, Zenodo, eine Präregistrierung, die beim Start der Datenerhebung festgehalten wird, und eine Siegelkette, die Änderungen an den Fällen nachweisbar macht.
6. **Wiederverwenden statt neu erfinden.**
   Validierte Skalen kommen aus einem gemeinsamen Bestand an Konstrukten.
   Sie stehen im Fragebogen als gewöhnliche Elemente, gesperrt in ihrem validierten Wortlaut, und jede Befragung, die sie nutzt, trägt zu ihrer gemeinsamen Reliabilität bei.
   Auch eigene Fragen lassen sich über Befragungen hinweg wiederverwenden, als verknüpfte Elemente: Ihre Antworten lassen sich dann gemeinsam auswerten, was Vergleiche und Metaanalysen über Studien hinweg einfach macht.
7. **KI, wo sie passt: Vorschläge, nie Entscheidungen.**
   KI entwirft, prüft und übersetzt, wenn Forschende sie darum bitten.
   Die Forschenden prüfen und übernehmen, was passt.
   KI sieht nie die Antworten der Teilnehmenden, und von KI Erzeugtes bleibt gekennzeichnet („KI-generiert, nicht validiert“).
   Siehe [KI in Askurious](ai.md).
8. **Die Teilnehmenden zuerst.**
   Die Seiten für Teilnehmende sind barrierearm (Tastatur, Screenreader, Farbe nie das einzige Signal) und sparsam mit mobilen Daten.
   Updates starten die beiden Server für Teilnehmende nacheinander neu, sodass Teilnehmende normalerweise keine Fehlerseite sehen.

## Drei Arten von Befragungen

Ein Werkzeug, drei Arten zu fragen, mit demselben Editor, Erscheinungsbild, Datenschutz und Export.

| Art | Wofür | Das Besondere |
|---|---|---|
| **Fragebogen** | standardisierte Befragungen, Online-Experimente, Wahlexperimente, Panelstudien | Studienplan, Ablauf mit Randomisierung und Schleifen, Quoten, Qualitätssicherung, Ergebnisse mit Tests, Dashboards | {kind:survey}
| **Interviewleitfaden** | leitfadengestützte, qualitative Interviews | Themenblöcke und Leitfragen nach Helfferich (2011), eine Interviewansicht für das Tablet, Tonaufnahme, Transkription auf eigenem Server, Export nach MAXQDA, ATLAS.ti und NVivo | {kind:interview}
| **Live-Abstimmung** | Rückmeldung in Vorlesungen und auf Veranstaltungen | Ansicht zum Vortragen und Ansicht fürs Publikum, Wortwolken, Antworten auf dem Handy ohne Anmeldung | {kind:live}

## Der Lebenslauf eines Fragebogens

```
  Idee ─► Studienplan ─► Fragebogen ─► Ablauf ─► Vorschau & Pretest ─► Start
                                                                         │
  offene Daten ◄─ Export ◄─ Ergebnisse ◄─ Qualitätssicherung ◄─ Datenerhebung ◄─┘
```

- **Entwurf**: Alles lässt sich ändern.
  Testdurchläufe und Dummy-Daten prüfen Fragebogen und Auswertung, bevor eine einzige echte Person teilnimmt.
- **Läuft**: Teilnehmende können teilnehmen.
  Textkorrekturen sind möglich; Änderungen an der Logik brauchen eine neue Fassung des Fragebogens, damit die Daten deutbar bleiben.
- **Pausiert / geschlossen**: keine neuen Teilnehmenden; wer begonnen hat, darf zu Ende machen.
  Eine geschlossene Befragung lässt sich wieder öffnen, wird aber nie wieder zum Entwurf.
- **Archiviert**: aus der Liste geräumt, für Entwürfe und geschlossene Befragungen.

Jede Teilnahme hat eine **Quelle**: echt, Testdurchlauf oder Dummy.
Nur echte Teilnahmen zählen für Quoten, die Verteilung auf Versuchsbedingungen und das automatische Schließen; der Export nennt immer, aus welcher Quelle eine Zeile stammt.

## Begriffe

Askurious verwendet in der ganzen Oberfläche feste Begriffe.
Hier stehen die, denen Sozialwissenschaftlerinnen, Sozialwissenschaftler und Studierende zuerst begegnen.

### Einen Fragebogen bauen

| Begriff | Englisch | Bedeutung |
|---|---|---|
| Befragung | survey | das Ganze, das Forschende anlegen, ins Feld bringen und auswerten |
| Element, Elementtyp | element, element type | eine Einheit des Fragebogens: eine Frage, ein Text, ein Bild, ein Seitenumbruch, ein Rechenwert; es gibt 38 Elementtypen |
| Aussage | statement | eine Zeile einer Likert-Matrix, eines Schiebereglers oder von MaxDiff (anderswo oft „Item“) |
| Option | option | eine Antwortmöglichkeit eines Elements |
| Block | block | eine Gruppe von Elementen; die Einheit von Ablauf, Randomisierung und Schleifen; die Einwilligung ist Block 0 |
| Seite | page | entsteht in einem Block durch Seitenumbrüche |
| Konstrukt | construct | eine validierte Skala oder Standardfrage aus dem gemeinsamen Bestand, z. B. das Big Five Inventory (BFI-10) |
| Subskala | subscale | eine Dimension einer mehrdimensionalen Skala, mit eigenem Skalenwert |
| Antwortmodus | answer mode | freiwillig, Nachfragen oder Pflicht, je Frage |

### Die Studie planen

| Begriff | Englisch | Bedeutung |
|---|---|---|
| Studienplan | research plan | Forschungsfrage, Erkenntnisziele, Design, Hypothesen, Stichprobenziel und Auswertungsplan |
| Hypothese | hypothesis | ein Zusammenhang, Unterschied oder eine Vermittlung zwischen zwei Variablen des Fragebogens, mit dem geplanten Test |
| Ablauf | flow | die Reihenfolge der Blöcke für Teilnehmende, mit Randomisierung, Bedingungen und Schleifen |
| Randomisierung | randomisation | zeigt einen (oder mehrere) von mehreren Blöcken zufällig; ausgeglichen oder rein zufällig |
| Versuchsbedingungen | experimental conditions | die Zellen eines Between-Subject-Designs, die aus Randomisierungen entstehen |
| Bedingung | condition | zeigt Blöcke nur bei bestimmten Antworten („dann“ / „sonst“) |
| Schleife | loop | wiederholt Blöcke je Tabellenzeile oder je Vignette; ihre Spalten sind Platzhalter |
| Vignette, Faktor, Stufe | vignette, factor, level | kurze Beschreibungen aus Faktoren und ihren Stufen (faktorieller Survey) |
| Merkmal, Stufe | attribute, level | die Eigenschaften einer Wahlaufgabe (Conjoint) und ihre Werte, z. B. Preis mit 9 €, 12 €, 15 € |
| Quote, Teilquote | quota, sub-quota | Sollwerte je Gruppe, z. B. nach Alter und Geschlecht; hart oder weich |
| externer Abschnitt | external section | schickt Teilnehmende zu einer anderen Web-App (z. B. einem Reaktionszeitexperiment) und zurück, mit signierten Rückgabewerten |
| getrennter Abschnitt | separated section | was danach kommt (z. B. eine E-Mail-Adresse für eine Verlosung), wird so gespeichert, dass es sich nicht mit den Antworten verbinden lässt |

### Daten erheben

| Begriff | Englisch | Bedeutung |
|---|---|---|
| Teilnahmelink | participation link | der Link, über den Teilnehmende die Befragung öffnen; kann einen Kurznamen haben (`/s/mensa-studie`) |
| Pretest-Link | pretest link | ein Link zum Testen ohne Konto; Antworten und Kommentare zählen als Testdurchlauf |
| Testdurchlauf | test run | ein Durchgang in der Vorschau, gespeichert als `source = test` |
| Dummy-Daten | dummy data | simulierte Teilnahmen durch den echten Ablauf, um die Auswertung vor der Feldphase auszuprobieren |
| Stichprobenziel | sample target | die Abschlüsse, die die Befragung erreichen soll: verwertbare Teilnahmen plus erwartete Ausschlüsse |
| verwertbare Teilnahmen | usable participations | Abschlüsse, die die Qualitätsregeln bestehen und in die Auswertung gehen |
| Ausschlüsse | exclusions | Abschlüsse, die die Qualitätsregeln herausnehmen; Abbrüche sind keine Abschlüsse |
| Endseite | end page | was nach der letzten Seite kommt, z. B. mit dem Löschcode |
| Screen-out | screen-out | gehört nicht zur Zielgruppe und wird früh beendet (im Panel „Screen out“); erklärt unter „Was bedeuten die Status?“ am Ende im Ablauf |
| Quote voll | quota full | gehört zur Zielgruppe, aber die Quote der eigenen Gruppe ist schon erfüllt (im Panel „Quota full“) |
| Duplikat | duplicate | hat schon teilgenommen; nichts Neues wird gespeichert |
| Löschcode | deletion code | ein Code, mit dem Teilnehmende ihre eigenen Antworten später löschen können, auch von einem anderen Gerät |

### Auswerten

| Begriff | Englisch | Bedeutung |
|---|---|---|
| Grundlage der Auswertung | basis of the analysis | Datenpool, Umgang mit markierten Teilnahmen und Gewichtung; oben in Qualitätssicherung und Ergebnissen |
| Datenpool | data pool | welche Quellen (echt, Test, Dummy) und welche Zustände (abgeschlossen, nicht beendet, Screen-out) zählen |
| Speeder | speeder | wer schneller ist als ein Anteil der Median-Zeit (Leiner, 2019) |
| Relative Speed Index (RSI) | Relative Speed Index | die Geschwindigkeit im Verhältnis zum Median jeder Seite; über 2 heißt doppelt so schnell wie üblich |
| α, ω | Cronbach’s alpha, McDonald’s omega | Reliabilität einer Skala; ω nimmt nicht an, dass alle Aussagen gleich viel beitragen |
| Teilnutzen, Wichtigkeit | part-worth, importance | Ergebnisse eines Wahlmodells: wie stark eine Stufe die Wahl begünstigt und wie stark ein Merkmal sie bewegt |
| Segment | segment | eine Gruppe mit ähnlichen Vorlieben aus einer Latent-Class-Analyse |
| Methodenteil | methods section | ein Entwurf des Methodenteils eines Manuskripts aus den Zahlen der Befragung |
| Siegelkette | seal chain | eine Hash-Kette über alle Teilnahmen mit Zeitstempeln des DFN, die zeigt, ob Daten nachträglich verändert oder entfernt wurden |

### Personen und Rollen

| Begriff | Englisch | Bedeutung |
|---|---|---|
| Forschende, Teilnehmende | researchers, participants | die zwei Arten von Personen; nie „Befragte“ |
| Rollen | roles | Admin, Bearbeiten, Ansehen, je Befragung |
| Gruppe | group | eine Arbeitsgruppe (Lehrstuhl, Projekt), die Befragungen und Designs teilt; sie besitzt nichts |
| Einrichtung | institution | wo eine forschende Person arbeitet, aus ORCID übernommen |
| Lernhilfen | learning aids | die „Warum?“-Erklärungen und das Glossar der Kennzahlen, für Einsteigende; abschaltbar unter Konto › Anzeige und Sprache |
