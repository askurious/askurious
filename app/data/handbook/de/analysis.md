# Auswerten

Diese Seite beschreibt die Ergebnisse, die Dashboards und den Export.

## 13. Ergebnisse

![Ergebnisse je Hypothese: Test, Ergebnis, Satz für den Bericht und was der Test hätte finden können](img/results-hypotheses.jpg)

**Was es ist.**
Die Ergebnisse auf der oben gewählten Grundlage, laufend aktuell, während Daten eingehen:

- **Ergebnisse je Hypothese** aus dem Studienplan: der Test und warum er gewählt wurde, gestützt oder nicht, der Satz für den Bericht im APA-Stil und **was der Test hätte finden können** mit dem tatsächlichen n (eine Sensitivitätsanalyse), damit „nicht gestützt“ nicht als „kein Effekt“ gelesen wird, wenn die Stichprobe zu klein war.
- **Je Element**: n, M, SD, Median, Konfidenzintervalle, Verteilungen; bei Skalen Cronbachs α und McDonalds ω mit Intervallen sowie eine Itemanalyse.
- **Skalen im Überblick**: alle Skalenwerte mit α, ω und ihren Korrelationen, als Tabelle für Manuskripte (Word, Deutsch oder Englisch).
- **Nach Gruppen aufteilen**, z. B. nach Versuchsbedingung; bei zufälliger Zuteilung ist der Unterschied als Wirkung lesbar.
- **Wahlmodelle**: Zählwerte, aggregiertes Logit und Hierarchical Bayes (HB) je Person, Wichtigkeit und Teilnutzen, Zahlungsbereitschaft, Segmente (Latent-Class-Analyse), ein Marktsimulator.
- **Längsschnitt über die Wellen** (mit verknüpften Wellen): Mittelwerte je Welle, Messinvarianz über die Zeit, Wachstumskurven und das Cross-Lagged-Panel-Modell (ab drei Wellen das RI-CLPM), gerechnet auf Knopfdruck über alle verknüpften Personen; sichtbar mit Leserecht in jeder Welle.

![Skalen im Überblick: Skalenwerte mit α, ω und Korrelationen](img/results-overview.jpg)

![Ergebnisse eines Online-Experiments: Welchs t-Test für die Hypothese und die Verteilung der Antworten](img/results-experiment-a.jpg)

![Ergebnisse eines Conjoints: Wichtigkeit der Merkmale und Teilnutzen](img/results-conjoint-a.jpg)

| Wahl | Abwägung |
|---|---|
| Welcher Test | Folgt aus dem Messniveau: Pearson für zwei metrische Variablen, Spearman, wenn eine nur geordnet ist; Welchs t-Test oder Welchs Varianzanalyse für Gruppen, die keine gleichen Streuungen annehmen (Delacre et al., 2017); Rangtests für ordinale Punkte; χ² für zwei nominale Variablen. |
| Aggregiertes Logit oder HB | Das Logit beschreibt die Stichprobe als Ganzes in Sekunden; HB schätzt Werte je Person, zeigt so, wie verschieden die Vorlieben sind, und speist den Simulator, braucht aber Tausende Ziehungen. Wichtigkeiten können deutlich abweichen, wenn Menschen ein Merkmal gegensätzlich bewerten. |
| Zahl der Segmente | Das BIC schlägt eine Zahl vor; wählen Sie auch nach Inhalt: Segmente, die sich deutlich unterscheiden, sich benennen lassen und Holdout-Aufgaben besser vorhersagen. |
| Was gewichtet wird | Anteile, Mittelwerte und ihre Intervalle werden gewichtet; Fallzahlen, Reliabilität, Skalenwerte und Wahlmodelle bleiben ungewichtet. |

**Warum.**
Ergebnisse sollen von sich aus richtig sein und auch ohne Statistikstudium lesbar: Der Test wird aus dem Plan gewählt, jede Kennzahl hat ein Fragezeichen, das zum Glossar der Kennzahlen führt, und jede Seite nennt ihre Literatur.
Die Reliabilität wird als α und ω mit Intervallen berichtet (Hayes & Coutts, 2020), weil α allein annimmt, dass alle Aussagen gleich gut messen.

---

## 14. Dashboards

![Dashboards: ein Feldbericht, ein Ergebnisbericht oder ein leeres](img/dashboards.jpg)

**Was es ist.**
Seiten mit Ergebnissen, Quoten und Kennzahlen der Datenerhebung, die sich per Link ohne Konto weitergeben lassen: an Auftraggebende, Projektpartner oder einen Hörsaal.
Vorlagen: Feldbericht (Teilnahmen, Verlauf und Quoten), Ergebnisbericht (alle Fragen), leer.

**Warum.**
Ein Dashboard zeigt nur zusammengefasste Zahlen: keine Freitexte, keine Antworten einzelner Personen, Ergebnisse erst ab 10 Antworten (je Dashboard einstellbar), und Antworten, die weniger als 5-mal genannt wurden, werden zusammengefasst.
Solange die Befragung läuft, zeigt ein Link neue Zahlen erst nach jeweils 5 weiteren Teilnahmen, damit sich eine einzelne neue Teilnahme nicht an der Änderung ablesen lässt.
Die Abwägung: weniger Einzelheiten für die Lesenden, dafür Schutz der Teilnehmenden.

---

## 15. Export und offene Daten

![Export: Datenpool, Export der Antworten, Methodenteil, Formate](img/export.jpg)

**Was es ist.**
Alles, was die Plattform verlässt:

- **Antworten** als CSV (Komma oder Semikolon), SPSS/JASP (.sav), jamovi (.omv), R (.rds), mit **Codebuch**; auf Wunsch mit Skalenwerten.
- **Fragebogen** als Word (Codebuch oder Druckfassung), Konstrukte und **Literatur** (.bib, .ris).
- **Ergebnisbericht** als PDF, ohne KI erstellt, nach APA 7: alle berechneten Ergebnisse des gespeicherten Datenpools mit Datengrundlage, Inhaltsverzeichnis, Stichprobe, je Frage nummerierten Abbildungen und Tabellen (Nummer und Titel darüber, Anmerkung darunter), Skalen, Wahlexperimente und den zitierten Quellen; Freitexte stehen nicht darin.
- **Befragungsmaterial** als PDF, ohne KI erstellt: Teil 1 zeigt den Fragebogen, wie Teilnehmende ihn sehen, nummeriert; Teil 2 denselben mit farbigen Anmerkungen neben jedem Element (Anzeigelogik und Bedingungen, Randomisierung, Pflicht- und Qualitätsprüfungen, Variablennamen, Quellen), was eine Bedingung oder Randomisierung umfasst, eingerückt darunter. Das PDF trägt Logo und Akzentfarbe aus dem Design der Befragung, etwa einem Organisationsdesign; die Farben der Anmerkungen bleiben immer dieselben. Die Präregistrierung friert es mit ein, Zenodo und Coscine bekommen es ohne geschützte Instrumentwortlaute.
- **Datenfluss** als Word: von der Einwilligung bis zur Löschung je Schritt Daten, Maßnahmen und Verantwortliche, gefüllt aus den Einstellungen der Befragung (Kennungen, getrennte Angaben, Paradaten, KI-Kodierung, externe Dienste, Fristen, Open Data); für das Verzeichnis der Verarbeitungstätigkeiten oder die Datenschutzbeauftragte.
- **Befragungsdatei** (.json/.zip), um die ganze Befragung umzuziehen oder zu teilen; ab der Rolle „Bearbeiten“, wie das Duplizieren.
- **Präregistrierung**, beim Start der Datenerhebung festgehalten und mit Zeitstempel: Befragungsdatei, Fragebogen, Codebuch, Befragungsmaterial und Prüfsummen; ihre Prüfsumme steht auch in der Siegelkette. Darunter zeigt der Abgleich, ob sich der Fragebogen im Feld seit dem Einfrieren geändert hat und was.
- **Siegelkette** als ZIP mit einem eigenständigen Prüfprogramm und ein Prüfcode mit einer öffentlichen Seite, die jede Person ohne Konto öffnen kann, zum Beispiel [die Kette der Demo-Befragung](https://askurious.de/verify/42-6-f5997ff3715b31a9): 5 Fälle, einer davon mit Löschvermerk gelöscht.
- **Offene Daten**: Hochladen zu Zenodo (als Entwurf; Veröffentlichen ist ein eigener, bestätigter Schritt) oder Coscine (Projekt und Ressource anlegen, ablegen, jede Ablage in Coscine prüfen, auf Wunsch mit SHA-256, alle Ordner der Befragung in der Ressource ansehen, Mitforschende ins Coscine-Projekt einladen und die Veröffentlichung beantragen). Im Editor lassen sich Bilder eines Elements und Tabellen (Drill-down, „Liste einfügen“) mit „aus Coscine wählen“ aus einer Coscine-Ressource übernehmen; sie werden kopiert, „neu holen“ nimmt die Datei erneut. Mit dem Experiment „Video und Ton aus Coscine übernehmen“ geht das auch am Element „Video / Audio“; die Datei wird im Hintergrund geladen. Beim Anlegen „Aus einer Datei“ lässt sich eine Befragungsdatei (.json, .zip) aus Coscine wählen; sie läuft durch dieselbe Vorschau wie ein Upload.
- **Löschanfragen** von Teilnehmenden, die sich direkt an die Forschenden wenden.

**Aufbau der Seite.**
Oben steht „Datenpool und Format“: Datenquellen, Umgang mit markierten Teilnahmen, angehängte Skalenwerte und das Trennzeichen, alles offen, darunter eine Zeile mit dem Exportumfang. Darunter folgen Karten in zwei Spalten: **Antworten**, **Dokumentation**, **Weitergeben** (Befragungsdatei, Datenzugang für R und Python), **Nachweise** (Präregistrierung, Siegelkette, gesicherte Stände), **Archivieren und veröffentlichen** (Zenodo, Coscine, Bildrechte, Veröffentlichungen) und **Datenschutz** (Aufbewahrung, Antworten löschen, getrennter Abschnitt). Jeder Inhalt hat eine Zeile mit seinen Formaten als Knöpfe; das „i“ erklärt ihn genauer, und was mehr als einen Knopf braucht (Token, Siegelkette, Zenodo, Wellen), öffnet sich in einem Dialog. Live-Abstimmung, Interviewleitfaden und Tagebuchstudie zeigen dieselben Karten, soweit sie passen.

![Der Methodenteil: Feldzeit, Personen, Zeiten, Demographie und validierte Instrumente aus den Zahlen der Befragung](img/methods.jpg)

Der **Methodenteil** sammelt die Fakten der Befragung (Feldzeit, Personen, Ausschlüsse, Bearbeitungszeiten, Demographie, validierte Instrumente mit α und ω, Hypothesen und Tests) und schreibt einen Entwurf auf Deutsch oder Englisch, als Word-Datei, mit oder ohne KI.

Während eine Auswertung vorbereitet wird, nennt die Warteseite die Befragung und den zuletzt bestätigten Stand. Bei Verbindungsproblemen fragt sie denselben Auftrag erneut ab; „Stand prüfen“ stößt die Abfrage sofort an. Links führen zurück zum Feld und zum Export, auch wenn die Berechnung fehlschlägt.

**Warum.**
Offene Daten sollen ein paar Klicks sein, kein eigenes Projekt.
Der Export enthält immer die Spalte mit der Quelle, kodiert umgepolte Aussagen um, berichtet zufällige Reihenfolgen als Spalten `*_order` und enthält nie Vorschau-Durchgänge (Entscheidung vom 2026-09-26).
Die Seite sagt, ob seit dem letzten Herunterladen etwas gelöscht wurde, damit sich eine veröffentlichte Datei ersetzen lässt.
