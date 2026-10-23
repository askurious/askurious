# Feldphase

Diese Seite beschreibt, was während der Datenerhebung geschieht: Teilnahmelinks und Einladungen, die Qualitätssicherung und was Teilnehmende sehen.
Wie eine ganze Studie mit einem Panelanbieter abläuft, steht unter [Anleitungen](guides.md#panelstudie-mit-quoten).

## 11. Datenerhebung

![Datenerhebung: der Teilnahmelink mit QR-Code, Kurzname, Links je Gruppe und Panel-Anbindung](img/field-links.jpg)

**Was es ist.**
Alles rund um die Feldphase:

- **Testen**: Vorschau, Pretest-Links, Testdurchläufe und **Dummy-Daten** (simulierte Personen, die durch den echten Ablauf gehen, um Auswertungsskripte vor der Feldphase zu prüfen).
- **Status der Befragung**: die Kennzahlen (seit wann, bis wann, abgeschlossen gegen das Ziel, pro Feldtag, Bearbeitungszeit), der **Zeitverlauf** der Teilnahmen je Tag, wer gerade in der Befragung ist, und die Knöpfe zum Starten, Pausieren, Schließen und Archivieren.
- **Teilnahmelink**: ein Link für alle, ein Kurzname (`/s/mensa-studie`), ein QR-Code, eigene Links je Person oder Gruppe (z. B. je Kurs, um zu sehen, wie viele über welchen Link teilnehmen) und die **Panel-Anbindung** (Kennungen aus dem Link, Abschlusslinks für abgeschlossen, Screen-out und Quote voll).
- **Einladungen per E-Mail** (wenn die Plattform E-Mails verschickt): eine Adressliste, je Person ein persönlicher Link, Erinnerungen. Die Liste begrenzt die Teilnahme nicht, solange „Nur mit persönlichem Link teilnehmen“ aus ist: Der allgemeine Link funktioniert weiter, und ein weitergegebener persönlicher Link setzt den Stand der eingeladenen Person. Mit dem Häkchen nimmt die Befragung nur Personen mit persönlichem Link an, jeden Link für eine Teilnahme.
- **Quoten** in Kurzform.

Wer vor dem Ende der Feldphase (Feldende, Schließen, Pause) begonnen hat, kann noch 24 Stunden lang abschließen; danach zeigt die Befragung „beendet“, und die Teilnahme bleibt „begonnen“. Eine archivierte Befragung nimmt nichts mehr an.

![Testen: Pretest-Links und Dummy-Daten](img/field.jpg)

**Warum.**
„Datenerhebung“ ist der Name des Reiters; „im Feld“ ist der Zustand einer laufenden Befragung.
Jede Teilnahme trägt ihre Quelle (echt, Test, Dummy), und nur echte zählen für Quoten, die Verteilung auf Versuchsbedingungen und das automatische Schließen (Entscheidung vom 2026-09-26); Testen kann die Daten also nie verderben.

### Panel-Anbindung {kind:survey}

Kennungen aus dem Link, Abschlusslinks, Regeln je Anbieter, signierte Links und der Quotenstand für den Anbieter: Die ganze Studie mit einem Panelanbieter beschreibt die Anleitung [Panelstudie mit Quoten](guides.md#panelstudie-mit-quoten).

---

## 12. Qualitätssicherung

![Qualitätssicherung: Grundlage der Auswertung und Bearbeitungszeiten mit der Schwelle für Speeder](img/quality.jpg)

**Was es ist.**
Prüfungen der Datenqualität, jede mit den Teilnahmen, die sie markiert:

- **Grundlage der Auswertung**: welche Quellen und Zustände zählen (Datenpool), ob markierte Teilnahmen herausfallen, und Gewichtung.
  Dieselbe Grundlage gilt für Ergebnisse, Dashboards, Exporte und den Methodenteil.
- **Bearbeitungszeiten und Speeder**: ein Histogramm der Bearbeitungszeiten mit einstellbarer Schwelle, nach Gesamtdauer (schneller als ein Anteil des Medians, Vorgabe 50 %; Leiner, 2019) oder nach Relative Speed Index (je Seite).
- **Antwortmuster**: immer gleiche Antworten, lange Reihen gleicher Antworten, geringe Streuung innerhalb einer Person.
- **Aufmerksamkeitsprüfungen**, **Bots und KI-Agenten**, **Tempo je Seite**.
- Ein Absatz **für den Methodenteil**.

Die Kennzahlen rechnet die Seite einmal und hält sie, bis sich Teilnahmen, Datenpool, Schwelle, Markierungsregel oder Fragebogen ändern; jede solche Änderung gilt beim nächsten Aufruf sofort. Ab 5000 Teilnahmen läuft die erste Berechnung in einem eigenen Prozess: Bis sie fertig ist, zeigt eine Warteseite „Die Auswertung wird vorbereitet“, danach öffnet sich die Seite von selbst.

| Wahl | Abwägung |
|---|---|
| Gesamtdauer oder Relative Speed Index | Die Gesamtdauer ist leicht zu erklären, benachteiligt aber Fragebögen mit unterschiedlich langen Pfaden; der RSI vergleicht jede Seite mit ihrem eigenen Median und ist genauer, braucht aber die Zeit je Seite. |
| Welche Teilnahmen zählen | Ergebnisse gelten für echte, abgeschlossene Teilnahmen. Abgebrochene einzubeziehen erhöht die Fallzahl, verzerrt aber die Ergebnisse, wenn bestimmte Menschen häufiger abbrechen. |
| Markierte Teilnahmen herausnehmen oder nur markieren | Herausnehmen schützt die Ergebnisse vor nachlässigen Antworten; Behalten vermeidet, sorgfältige, aber schnelle Menschen auszuschließen. Die Seite zeigt beides, und die Ausschlüsse legt man am besten vor der Datenerhebung fest. |

**Warum.**
Diese Seite speichert nichts und entfernt nichts: Sie markiert, die Forschenden entscheiden, und der Methodenteil berichtet die Regel und die Zahl.
Manche Prüfungen melden sich erst ab einer Mindestzahl von Fällen, damit eine einzelne auffällige Person nicht wie ein Muster aussieht.

---

## 16. Was Teilnehmende sehen

![Die Einwilligung in der Vorschau](img/preview-consent.jpg)

1. **Einwilligung** (Block 0): ein strukturierter Text, der immer Freiwilligkeit, Anonymität und das Recht zum Abbruch nennt, dazu, was die Befragung erhebt; ausdrückliche Häkchen sind zum Zustimmen nötig, nicht zum Ablehnen.
   Wer ablehnt, hinterlässt keine Daten: Gespeichert wird nur ein Zähler ohne Zeitpunkt.
   Antworten vor der Einwilligung (z. B. ein Screening) werden bei einer Ablehnung verworfen.
2. **Seiten**: Die Reihenfolge wird einmal je Person auf dem Server festgelegt; „Weiter“ prüft Pflichtantworten (rot) und Hinweise (gelb), immer mit einem Text, nie nur über die Farbe.
   Eine Sitzung hält sieben Tage, sodass man unterbrechen und zurückkommen kann.
3. **Rückmeldung** (auf Wunsch): Teilnehmende sehen, wo sie im Vergleich zu allen Bisherigen stehen, z. B. als Figur in einer Menschenmenge oder auf einer Glockenkurve; das erhöht die Zufriedenheit mit der Befragung (Kühne & Kroh, 2018).
   Legt die Rückmeldung die Antworten fest, bleiben sie beim Zurückgehen sichtbar, lassen sich aber nicht mehr ändern; eine Antwort, die sich nicht als Text einsetzen lässt (Karte, Bild markieren, Q-Sort), steht zusätzlich in Worten darunter.
   Eine gestartete Zeitaufgabe (IAT, Zuordnung, Discounting) bricht ab, wenn der Tab verborgen wird oder jemand „Aufgabe abbrechen“ wählt, nicht schon, wenn eine Systemmeldung den Fokus kurz wegnimmt.
   Auch auf einem 320 px schmalen Bildschirm mit doppelter Schriftgröße, links- wie rechtsläufig, bleibt alles auf der Seite: Lange Wörter brechen um, die 2D-Positionierung stellt den Namen der senkrechten Achse dann über das Feld.
4. **Endseite**: Dank, der Löschcode mit Knopf zum Kopieren, ein Link zur Datenschutzseite und Ergebnisse, wenn die Befragung sie zeigt.

![Rückmeldung: die teilnehmende Person als Figur in der Menge der bisherigen Teilnehmenden](img/crowd.jpg)

Die Seiten für Teilnehmende sind standardmäßig anonym, laden keine fremden Schriften oder Tracker und funktionieren mit Tastatur und Screenreader.
Jede Befragung hat eine Datenschutzseite, eine Erklärung zur Barrierefreiheit und ein Beschwerdeformular, alles ohne Konto.
