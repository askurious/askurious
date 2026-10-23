# KI in Askurious

Askurious setzt KI als Assistenz der Forschenden ein, nie als Teilnehmende, als Auswertung oder als Entscheidung.
Diese Seite nennt jede Stelle, an der KI eingesetzt wird, was sie erhält, was sie erzeugt, wie ihre Ergebnisse gekennzeichnet sind und was KI bewusst nicht tut.

## Die Regeln in Kürze

1. **Die KI schlägt vor, Sie entscheiden.**
   Jede KI-Funktion läuft nur, wenn Forschende auf einen Knopf „mit KI“ klicken.
   Sie liefert Vorschläge, oft mit Begründung; die Forschenden haken an, bearbeiten und übernehmen, was passt, als ein Schritt, der sich rückgängig machen lässt.
2. **Keine Daten von Teilnehmenden gehen an eine KI.**
   Keine KI-Funktion erhält die Antworten von Teilnehmenden.
   Der Methodenteil erhält nur zusammengefasste Zahlen (n, Mittelwerte, α, Testergebnisse), nie einzelne Antworten.
   Interviews, Aufnahmen und Transkripte gehen nie an einen KI-Anbieter.
3. **Was die KI erzeugt, bleibt als KI erkennbar.**
   Von der KI entworfene Skalen sind im Editor, auf der Seite des Elements, in der Startprüfung und im Codebuch als „KI-generiert, nicht validiert“ markiert.
   KI-Übersetzungen bleiben „KI, ungeprüft“, bis eine Person sie bestätigt.
   KI-Bilder tragen Quelle, Modell und Prompt in den Export.
4. **KI sieht nie validiert aus.**
   KI-Items tragen keine Autorinnen und Autoren, Literaturangaben oder Skalennamen, damit sie nicht mit einem veröffentlichten Instrument verwechselt werden.
   Ihre Reliabilität ist im Pretest zu prüfen.
5. **Die Verantwortung bleibt bei den Forschenden.**
   Die Nutzungsbedingungen sagen: Ergebnisse der KI prüfen Sie selbst, und in KI-Funktionen geben Sie keine personenbezogenen Daten ein.
   Wo ein Freitextfeld an die KI geht (Live-Abstimmung, Interviewleitfaden), sagt die Seite das neben dem Feld.

## Wer die KI stellt

- **Text und Bilder**: OpenAI über die offizielle Schnittstelle.
  Die Admins der Plattform wählen das Textmodell und das Bildmodell in den Einstellungen des Servers.
  Was in eine KI-Funktion eingegeben wird, geht zur Verarbeitung an OpenAI; die Nutzungsbedingungen (Abschnitt 6, „Dienste Dritter“) nennen das.
- **Sprache zu Text**: ein Whisper-Server, den die Einrichtung selbst betreibt (oder betreiben lässt), nicht OpenAI. Mit dem optionalen lokalen WhisperX-Dienst sind automatische Sprecherlabels möglich. Sie gelten nur innerhalb eines Aufnahmeteils und müssen geprüft und Rollen zugeordnet werden.
- Beim lokalen WhisperX-Dienst wählen Sie vor dem Transkriptionsstart je Interview `small` oder `large-v3-turbo` (vorgewählt ist `large-v3-turbo`, genauer, aber langsamer). Die Interviewliste zeigt Warteschlangenplatz, Verarbeitungsphase und fertige Aufnahmeteile; dort können Sie auch abbrechen. Ein Modellwechsel transkribiert die Teile neu. Korrigierter Text bleibt erhalten; ein neuer Entwurf wird daneben angeboten.
- Die ungefähre Restzeit bezieht sich auf den aktuellen Aufnahmeteil einschließlich bekannter Wartezeit, nicht auf alle weiteren Teile. Sie wird als Bereich angezeigt und aus Audiodauer, Modell und bisherigen Laufzeiten der einzelnen Verarbeitungsphasen geschätzt. Abgeschlossene Phasen zählen nicht erneut zur Restzeit. Bei fehlenden Angaben oder längerer Bearbeitung weist die Anzeige darauf hin. Erreicht ein Auftrag sein Speicherlimit, wählen Sie gegebenenfalls `small` oder teilen die Aufnahme in kürzere Dateien auf; bei unveränderter Modellwahl bleiben bereits fertige Teile erhalten.
- Nach einer Unterbrechung wegen Serverlast oder einem Neustart setzt der lokale Dienst denselben Auftrag ab der letzten abgeschlossenen Verarbeitungsphase fort. Nach einem Verarbeitungsfehler mit vorhandenem Zwischenstand versucht er dies einmal automatisch; Speicher- und Zeitlimits werden nicht automatisch wiederholt. Zwischentranskripte werden bei Abschluss, endgültigem Fehler, Abbruch oder Fristablauf gelöscht. Ein anschließend neu erteilter Auftrag beginnt von vorn.
  Tonaufnahmen von Interviews verlassen die Infrastruktur der Einrichtung deshalb nie.

Jede forschende Person hat ein Tageslimit für KI-Texte und -Bilder (Vorgabe 100 Texte und 200 Bilder).
Jeder Aufruf wird für die Übersicht der Admins zu Verbrauch und Kosten gezählt, ohne Person, Befragung oder Inhalt.
KI-Knöpfe haben alle dieselbe hellviolette Farbe und ein Funkel-Symbol, sodass KI-Aktionen auf einen Blick erkennbar sind.

## Wo KI eingesetzt wird

| # | Funktion | Wo | Was die KI erzeugt | Was übermittelt wird | Kennzeichnung und Kontrolle |
|---|---|---|---|---|---|
| 1 | **Mit KI formulieren** (Einwilligung) | Element Einwilligung | einen Entwurf der Information und Einwilligung für Teilnehmende aus den Einstellungen der Befragung | die Eckdaten der Befragung; Namen, Einrichtungen und die Kontakt-E-Mail der Forschenden werden vor dem Senden durch Platzhalter ersetzt und danach wieder eingesetzt | „KI-Entwurf eingesetzt. Bitte sorgfältig prüfen: Sie sind für den Text verantwortlich.“ Die KI darf nur vorhandene Eckdaten umformulieren; fehlende werden zu [Platzhaltern], und die Startprüfung sperrt den Start, solange welche bleiben. |
| 2 | **Vorschlagen** (Konstrukte) | Editor, Seitenleiste | drei bis fünf Konstrukte, die der Befragung noch fehlen könnten, ohne Skalennamen oder Literatur | Titel, Projekt, Beschreibung, Forschungsfrage, eine Gliederung des Fragebogens; keine Antworten | nur Vorschläge; braucht etwas Zusammenhang (etwa 20 Wörter) |
| 3 | **Skala mit KI entwerfen**, **Weitere Items vorschlagen** | Editor | Likert-Aussagen zu einer Konstrukt-Idee, in allen Sprachen der Befragung | derselbe Zusammenhang; keine Antworten | „KI-generiert, nicht validiert“ in Editor, Elementseite, Startprüfung und Codebuch |
| 4 | **Element mit KI prüfen**, **Texte von der KI prüfen lassen** | Elementseite; Startprüfung | Vorschläge zur Formulierung mit Begründung, nach Regeln der Fragebogengestaltung (Porst; Dillman; Tourangeau) | die Texte für Teilnehmende in der Ausgangssprache; nicht die Einwilligung, nicht gesperrte Konstrukte, nicht die Übersetzungen | jeder Vorschlag wird von Hand angehakt und übernommen, nur im Entwurf |
| 5 | **Bilder mit KI erzeugen** | Element Bild, Schleifen, Conjoint, Antwortoptionen | Stimulusbilder aus einem Stil, einer Szene und festen Regeln (kein Text, keine Logos, keine emotionale Rahmung) | nur der Bild-Prompt; Namen und Definitionen der Merkmale bleiben draußen | neue Bilder müssen vor dem Start freigegeben werden; ein Bildstil je Befragung (oder „Keine KI-Bilder“); Quelle, Modell und Prompt gehen in den Export |
| 6 | **Prompts von der KI prüfen lassen** | Conjoint-Bilder | einen klareren Basis-Prompt und Bildhinweise | nur, was das Bildmodell sieht | von Hand übernommen; vorhandene Bilder behalten ihren Prompt |
| 7 | **Alles Offene mit KI übersetzen** | Reiter Übersetzung | Übersetzungen fehlender Texte | Titel, Beschreibung und Forschungsfrage als Zusammenhang, dazu bestätigte Übersetzungen als Glossar | „KI, ungeprüft“, bis eine Person bestätigt oder bearbeitet; in einer laufenden Befragung wird vorher gefragt, weil Teilnehmende das Ergebnis sofort sehen |
| 8 | **Methodenteil mit KI** | Export › Methodenteil | den Methodenteil auf Deutsch oder Englisch, in drei Längen | nur zusammengefasste Zahlen: n, Feldzeit, Ausschlüsse, α und ω, Mittelwerte, Hypothesen, Testergebnisse | daneben gibt es „Entwurf ohne KI“, der den Text aus denselben Fakten ganz ohne KI baut |
| 9 | **KI-Hilfe für Vignetten** | Vignettendesign | vorgeschlagene Faktoren, Stufen, unplausible Kombinationen, Vignettentext | Zusammenhang der Studie und Gliederung des Fragebogens; keine Antworten | Übernommenes ist als „KI-generiert“ markiert, weil die Wahl der Faktoren eine theoretische Begründung braucht |
| 10 | **Fragen mit KI vorschlagen** | Live-Abstimmung, erster Schritt und Fragen | fünf Rückmeldefragen für eine Veranstaltung | die Beschreibung der Vortragenden und die vorhandenen Fragen | Hinweis auf der Seite: keine Namen oder anderen personenbezogenen Daten |
| 11 | **Mit KI entwickeln**, **Leitfrage mit KI ergänzen**, **Leitfaden mit KI prüfen**, **Definitionen mit KI** | Interviewleitfaden | Schritte des SPSS-Verfahrens nach Helfferich (sammeln, prüfen, sortieren, subsumieren), Aspekte, eine Prüfung, Definitionen fürs Kodieren (Kuckartz) | Titel, Forschungsfrage, Zielgruppe, Form, Texte und Notizen des Leitfadens; nie Interviews | jeder Schritt lässt sich überspringen; Hinweis: keine Namen oder personenbezogenen Daten |
| 12 | **Transkribieren** | Interviewleitfaden › Interviews | einen Transkriptentwurf nach Dresing & Pehl, mit Zeitmarken | die Aufnahme, an den eigenen Whisper-Server der Einrichtung, nicht an OpenAI | „Entwurf von Whisper“; automatische Sprecherlabels sind mit WhisperX möglich, Rollen werden beim Prüfen zugeordnet; „Transkript geprüft“ markiert es als geprüft, danach wird die Aufnahme gelöscht |
| 13 | **AInstein** (experimentell) | eigene Seite, je Person von einem Admin eingeschaltet | ein Gespräch, das eine Forschungsleinwand füllt (Frage, Design, Stichprobe, Hypothesen, Messung, Auswertung) und daraus eine Befragung baut | Projektname, Idee, Leinwand, Gespräch, öffentliche Konstrukte; keine Antworten, keine Daten von Teilnehmenden | Abzeichen „KI“ und „AInstein kann irren“; alles, was es baut, ist als „KI-generiert“ markiert |
| 14 | **Kategorien vorschlagen lassen (KI)**, **Zuordnung vorschlagen lassen (KI)** (experimentell) | Ergebnisse › Kategorisieren | Kategorien mit Definition und einem umschriebenen Ankerbeispiel; für noch offene Antworten passende Kategorien | bis zu 150 verschiedene Antworttexte von Teilnehmenden (je höchstens 300 Zeichen), nur echte Teilnahmen, ohne weitere Angaben; nur über einen eigenen KI-Zugang (Konto › Verbindungen), nie über den Zugang der Plattform | erst nach dem Häkchen „Teilnehmende wurden über die Übermittlung informiert“ je Befragung, mit einem Absatz für die Einwilligung; Vorschläge gelten erst nach dem Übernehmen, Sammelübernahmen nennen Kodierleitfaden und Methodenteil |
| 15 | **KI-Chat** (experimentell) | Element im Fragebogen oder Tagebuchmodul, je Person von einem Admin eingeschaltet | die Antworten eines Assistenten im Gespräch mit Teilnehmenden, nach den Anweisungen der Forschenden | die Nachrichten der Teilnehmenden mit dem bisherigen Gespräch und den Anweisungen; E-Mail-, Webadressen und Telefonnummern vorher maskiert, keine Kennung der Teilnahme; nur über den eigenen Modellzugang der Studie (Seite „KI-Chat: Modellzugang“), nie über den Zugang der Plattform oder einen persönlichen | fester Hinweis „Sie schreiben mit einer KI“ und „KI“ an jeder Antwort; eigenes Häkchen in der Einwilligung; alle Nachrichten beider Seiten als Studiendaten gespeichert; Herkunft des Modells und Anweisungen in der Dokumentation der Befragung; Hinweis auf Hilfe bei Krisenwörtern |

### Bildschirmfotos

![Skala mit KI entwerfen: die Idee, die Zahl der Aussagen, die Hälfte umgepolt; der Hinweis sagt, was übermittelt wird und dass das Ergebnis nicht validiert ist](img/ai-scale-dialog.jpg)

![Übersetzen mit KI: Texte bleiben „KI, ungeprüft“, bis eine Person sie bestätigt](img/translation.jpg)

![Die Elementseite eines Conjoints mit „Element mit KI prüfen“ oben rechts](img/element-conjoint.jpg)

![Live-Abstimmung: Die Beschreibung geht an das Textmodell von OpenAI, mit der Bitte, keine personenbezogenen Daten einzugeben](img/live-assistant.jpg)

![Interviewleitfaden mit KI entwickeln, Schritt „Sammeln“: Die KI ergänzt, was die Forschenden zuerst selbst gesammelt haben](img/guide-ai.jpg)

![Wahlaufgaben mit KI-erzeugten Produktbildern, alle in einem Stil](img/cj-ai-images.jpg)

![Der Bildstil einer Befragung: alle KI-Bilder einer Befragung in einem Stil, damit Stimuli vergleichbar bleiben](img/image-styles.jpg)

[Probieren Sie eine Wahlaufgabe mit KI-Bildern selbst aus](https://ask.askurious.de/s/nubv72): eine kurze Demo-Befragung von etwa einer Minute.

## Warum KI so eingesetzt wird

**Vorschläge, nie Entscheidungen.**
Methodische Entscheidungen brauchen Gründe, die die Forschenden vertreten können: welches Konstrukt, welche Faktoren, welcher Wortlaut.
Eine KI kann vorschlagen und auf Probleme hinweisen, aber diese Verantwortung nicht tragen.
Daher überall dasselbe Muster: Die Forschenden fragen, die KI schlägt vor, die Forschenden übernehmen.

**Den Stimulus sauber halten.**
KI-Bilder erhalten nur Bildhinweise, nie das, was Teilnehmende lesen, und feste Regeln gegen Text, Logos und emotionale Rahmung.
Unterschiede zwischen den Bedingungen eines Experiments sollen aus dem manipulierten Inhalt kommen, nicht aus Launen des Modells.
Ein Stil je Befragung hält die Bilder vergleichbar; Bilder werden vor dem Start erzeugt, geprüft und freigegeben, nie während eine teilnehmende Person wartet.
Bilder von Stufen wie Marken lassen sich nur hochladen, weil eine KI Logos erfinden würde (Entscheidung vom 2026-10-06).

**Validiertes und Erzeugtes getrennt halten.**
Eine von der KI entworfene Skala kann ein guter Anfang sein, ist aber kein Instrument mit bekannter Reliabilität und Validität.
Die Kennzeichnung „nicht validiert“ folgt ihr ins Codebuch und ins Datenpaket, damit Lesende einer Studie wissen, was womit gemessen wurde.

**Menschen aus dem Prompt heraushalten.**
Der Entwurf der Einwilligung ersetzt die Namen der Forschenden vor dem Senden; Freitextfelder warnen vor personenbezogenen Daten; Interviewmaterial wird nie übermittelt.
Antworten von Teilnehmenden gehen nur an die KI, wenn Forschende beim Kategorisieren offener Antworten eine Kategorie oder Zuordnung vorschlagen lassen: dann nur die Antworttexte echter Teilnahmen, nachdem bestätigt wurde, dass die Teilnehmenden davon wissen.
Die einzige KI, die Aufnahmen berührt (die Transkription), läuft auf dem eigenen Server der Einrichtung.

**Lernen statt Abkürzung.**
Im Interviewleitfaden und in AInstein arbeitet die KI schrittweise und erst nach einem eigenen ersten Versuch der Forschenden, denn beim Sammeln und Prüfen von Fragen bemerken Forschende ihre eigenen Annahmen (Helfferich, 2011).
Simulierte Interviews mit KI und eine von der KI geschriebene Einwilligung für Interviews wurden bewusst weggelassen.

## Was KI in Askurious nicht tut

- Sie beantwortet keine Befragungen und kodiert keine Interviews; offene Antworten ordnet sie nicht selbst zu, sondern macht beim Kategorisieren nur Vorschläge, die Forschende übernehmen.
- Sie entscheidet nicht, welche Teilnahmen ausgeschlossen werden; die Qualitätssicherung verwendet nachvollziehbare Regeln (Tempo, Antwortmuster, Aufmerksamkeitsprüfungen).
- Sie läuft nicht im Hintergrund und nicht, während Teilnehmende teilnehmen.
- Sie veröffentlicht oder übersetzt nichts, ohne dass eine Person es bestätigt.

KI ist auch etwas, wogegen sich Askurious wehrt: Der wählbare **Köder für KI-Agenten** setzt einen Satz auf die Seiten für Teilnehmende, den Menschen weder sehen noch hören, und markiert Teilnahmen von KI-Agenten, die ihm folgen (siehe [Eine Befragung aufbauen](build.md#4-einstellungen), „Paradaten und Datenqualität“).
