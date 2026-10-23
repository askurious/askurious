# Anleitungen

Die anderen Seiten des Handbuchs erklären Askurious Seite für Seite. Diese Seite geht vom Ziel aus: Jede Anleitung führt in Schritten durch eine typische Studie und verweist für die Einzelheiten auf die Beschreibung der jeweiligen Seite.

---

## Meine erste Befragung

Diese Anleitung führt einmal durch eine ganze Befragung, vom Anlegen bis zum Export. Wer Askurious zum ersten Mal nutzt, beginnt am besten mit einer Vorlage: Sie bringt einen fertigen Studienplan, einen Fragebogen und 120 Dummy-Teilnahmen mit, sodass jede Seite schon etwas zeigt.

**1. Anlegen.** Unter „Meine Befragungen“ legt der Knopf „Fragebogen“ eine neue Befragung an. Der Assistent bietet drei Wege: leer beginnen, aus einer der sechs Vorlagen oder aus einer Befragungsdatei, die z. B. eine Kollegin exportiert hat. Leer beginnen führt Schritt für Schritt durch Titel, Beschreibung, Studienplan, Sprachen, Einwilligung und Erscheinungsbild. Die **Beschreibung** ist wichtig: Sie steht später in der Einwilligung unter „Worum geht es?“ und gibt der KI-Unterstützung ihren Zusammenhang. Mehr dazu unter [Eine Befragung anlegen](build.md#2-eine-befragung-anlegen).

![Die sechs Studienvorlagen](img/new-from-template.jpg)

**2. Planen.** Im Reiter [Studienplan](build.md#5-studienplan) stehen die Forschungsfrage in einem Satz, das Erkenntnisziel (beschreiben, Zusammenhänge prüfen, Gruppen vergleichen, eine Wirkung prüfen, Präferenzen messen) und die Hypothesen. Eine Hypothese nennt zwei Fragen des Fragebogens und die erwartete Richtung; „auto“ wählt den passenden Test nach dem Messniveau. Aus dem kleinsten Effekt, der Sie interessiert, macht die Poweranalyse ein Stichprobenziel, z. B. „84 verwertbar · 94 Abschlüsse · bei 10 % Ausschlüssen“. Ein Plan, der vor den Daten steht, schützt davor, Hypothesen erst nach dem Blick auf die Ergebnisse aufzustellen.

![Studienplan: Forschungsfrage, Erkenntnisziele, Design und Hypothesen mit ihren Tests](img/research-plan.jpg)

**3. Fragen bauen.** Im [Fragebogen-Editor](build.md#6-fragebogen-editor) ziehen Sie Elementtypen von links in Blöcke in der Mitte. Validierte Skalen kommen fertig aus den [Konstrukten](more.md#konstrukte), mit Wortlaut, Quelle und Antwortformat; eigene Fragen schreiben Sie selbst. Jeder neue Fragebogen beginnt mit der Einwilligung und einem Block mit Alter und Geschlecht. Die **Startprüfung** rechts nennt, was vor dem Start noch fehlt oder geprüft werden sollte. Gespeichert wird von selbst; „Rückgängig“ nimmt jede Änderung zurück.

![Der Fragebogen-Editor: Elementtypen links, Blöcke in der Mitte, Startprüfung rechts](img/editor.jpg)

**4. Einwilligung und Einstellungen.** In den [Einstellungen](build.md#4-einstellungen) legen Sie Sprachen, den Text der Einwilligung, die Datenschutzinformation, Fristen für das Löschen von Kennungen und das Feldende fest. Prüfen Sie im Text der Einwilligung vor allem Namen, Kontakt und Zweck der Studie.

**5. Testen.** Die [Vorschau](build.md#10-vorschau-und-pretest) zeigt die Befragung wie für Teilnehmende, in Desktop- oder Handybreite. Ein **Pretest-Link** lässt Kolleginnen und Kollegen ohne Konto durchklicken und zu jeder Seite einen Kommentar hinterlassen, auf Wunsch mit Bildschirmfoto. Testdurchläufe und Dummy-Daten zählen nie für Quoten oder Ergebnisse der echten Teilnahmen; mit Dummy-Daten lassen sich Auswertung und Export schon vor der Feldphase ausprobieren.

![Die Vorschau einer Likert-Matrix mit dem Kommentarfeld für Pretest-Personen](img/preview-likert.jpg)

**6. Starten.** Im Reiter [Datenerhebung](field.md#11-datenerhebung) startet „Befragung starten“ die Feldphase, oder „Start planen“ legt einen Zeitpunkt fest. Darüber zeigt „Stand gegen die Poweranalyse“, wie viele verwertbare Teilnahmen noch fehlen. Darunter stehen der Teilnahmelink, ein Kurzname wie `/s/mensa-studie` und ein QR-Code. Beim Start hält Askurious den Studienplan als Präregistrierung mit Zeitstempel fest.

![Datenerhebung: der Teilnahmelink mit QR-Code, Kurzname, Links je Gruppe und Panel-Anbindung](img/field-links.jpg)

**7. Qualität sichern.** Die [Qualitätssicherung](field.md#12-qualitätssicherung) markiert auffällige Teilnahmen: zu schnell (Speeder), immer dieselbe Antwort, nicht bestandene Aufmerksamkeitsprüfungen, Bots. Sie entscheiden, ob markierte Teilnahmen herausfallen; diese **Grundlage der Auswertung** gilt dann für Ergebnisse, Dashboards, Export und Methodenteil gleich. Legen Sie die Regel am besten vor der Datenerhebung fest.

![Qualitätssicherung: Grundlage der Auswertung und Bearbeitungszeiten mit der Schwelle für Speeder](img/quality.jpg)

**8. Auswerten.** Die [Ergebnisse](analysis.md#13-ergebnisse) prüfen jede Hypothese mit dem geplanten Test, sagen „gestützt“ oder „nicht gestützt“, liefern den Satz für den Bericht und nennen, was der Test mit dem tatsächlichen n hätte finden können. Je Frage gibt es Kennwerte und Verteilungen, je Skala α und ω.

![Ergebnisse je Hypothese: Test, Ergebnis, Satz für den Bericht und was der Test hätte finden können](img/results-hypotheses.jpg)

**9. Exportieren.** Der [Export](analysis.md#15-export-und-offene-daten) gibt die Antworten als CSV und für SPSS, JASP, jamovi und R aus, mit Codebuch. Der **Methodenteil** schreibt aus den Zahlen der Befragung einen Entwurf für den Bericht, und die Daten lassen sich als offene Daten zu Zenodo oder Coscine hochladen.

![Export: Datenpool, Export der Antworten, Methodenteil, Formate](img/export.jpg)

---

## Panelstudie mit Quoten {kind:survey}

Ein Panelanbieter schickt Personen aus seinem Pool in die Befragung und vergütet sie für ihre Teilnahme. Dafür braucht er drei Dinge: einen Teilnahmelink, an den er die Kennung jeder Person hängt, Abschlusslinks, über die die Personen am Ende zu ihm zurückkommen, und einen Blick auf die Quoten. Links und Kennungen stehen im Reiter „Datenerhebung“ im Block „Teilnahmelink“ unter **Panel-Anbindung**, die Quoten unter „Ablauf“ › „Quoten“.

**Der schnellste Weg.** Meist schickt der Anbieter eine Liste seiner Rücksprungadressen (abgeschlossen, Screen-out, Quote voll, Qualität, Duplikat). Unter „Links vom Panel einfügen“ kommt diese Liste in einem Stück hinein. „Erkennen“ ordnet jede Zeile nach ihren Wörtern einem Ausgang zu, setzt die Kennung am Ende der Adresse als Platzhalter ein und legt den Parameter für die Kennung als „speichern, jeder Wert nur einmal“ an; eine Vorschau zeigt alles, bevor es übernommen wird. Zeilen für zu schnelles Ausfüllen oder für das Land aus der IP-Adresse übernimmt Askurious nicht von selbst, denn dafür gibt es keinen eigenen Ausgang: Sie gehören an ein Ende im Ablauf mit der passenden Bedingung.

![Links vom Panel einfügen: die erkannte Liste vor dem Übernehmen](img/guide-panel-paste.jpg)

**Kennungen aus dem Teilnahmelink.** Jeder Wert, den der Anbieter an den Link hängt (z. B. `?PID=4711`), muss als Parameter angelegt sein; andere Parameter werden ignoriert. Je Parameter wählen Sie, was mit dem Wert geschieht:

| Umgang | Was passiert |
|---|---|
| nicht speichern | Der Wert gilt nur während der Teilnahme: für Abschlusslinks, Bedingungen im Ablauf, Quoten und Texte. In den Daten steht er nicht. |
| speichern (pseudonym) | Der Wert steht zusätzlich in den Daten. Die Daten sind damit pseudonym, und die Einwilligung weist von selbst darauf hin. |
| speichern, jeder Wert nur einmal | wie „speichern“, und jede Kennung nimmt nur einmal teil; ein zweiter Aufruf aus einem anderen Browser geht zum Ausgang „Duplikat“. Das ist die Einstellung für die Kennung des Panels. |

„Pflicht“ und eine Liste erlaubter Werte weisen Links ohne den Parameter oder mit einem anderen Wert ab: Sie zeigen „Dieser Link ist unvollständig“. Die Kennung des Panels sollte Pflicht sein, sonst kann jemand den Link ohne sie beliebig oft öffnen; die Startprüfung erinnert daran. Unter den Abschlusslinks steht der fertige **Link für das Panel** mit allen Parametern, in den der Anbieter an den Stellen „…“ seine Werte einsetzt. Gespeicherte Kennungen leert die Einstellung „Kennungen und Kontaktdaten löschen nach …“ nach der gewählten Frist (neue Befragungen: 6 Monate); das reicht für die Abrechnung mit dem Anbieter.

![Panel-Anbindung: Parameter aus dem Link und Abschlusslinks mit den Platzhaltern](img/guide-panel-params.jpg)

**Abschlusslinks.** Für jeden Ausgang einer Teilnahme lässt sich ein Link hinterlegen, zu dem Askurious die Person am Ende schickt:

| Ausgang | Wann |
|---|---|
| abgeschlossen | Die Person hat die Befragung beendet. |
| Screen-out | Ein Ende im Ablauf der Art „Screen-out“ (die Person gehört nicht zur Zielgruppe), auch mit dem Auslöser „Keine Teilquote passt“. |
| Quote voll | Ein Ende mit dem Auslöser „Quote voll“ oder „Teilquote voll“; außerdem jeder neue Aufruf, nachdem sich die Befragung geschlossen hat, weil alle Quoten voll sind. |
| Ausschluss | Ein Ende der Art „Ausschluss“, z. B. nach einer nicht bestandenen Aufmerksamkeitsprüfung. |
| Duplikat | Die Kennung oder dieser Browser hat schon teilgenommen; es wird nichts Neues gespeichert oder gezählt. |
| Einwilligung abgelehnt | Die Person hat der Teilnahme nicht zugestimmt. |
| Befragung geschlossen | Die Befragung nimmt gerade niemanden an (pausiert, geschlossen oder nach dem Feldende). |

In einen Link setzen die Knöpfe unter „Platzhalter einfügen“ Werte ein, die beim Weiterleiten ausgefüllt werden: jeden angelegten Parameter unter seinem Namen (z. B. `{{PID}}` für die Kennung des Panels), die Teilnahme-ID von Askurious, die Sprache, die Befragung und, wenn unten erlaubt, den Code der Teilquote (`{{quota}}`). So reicht Askurious die Kennung, die das Panel mitgeschickt hat, unverändert zurück, und der Anbieter weiß, wem er die Teilnahme gutschreibt. Antworten gehen nie in einen Link. Nur `https://` ist erlaubt, und Platzhalter dürfen erst hinter dem Rechnernamen stehen; das Ziel kommt also immer aus den Einstellungen, nie aus dem Aufruf. Die Datenschutzseite der Befragung nennt die Ziele der Weiterleitung.

Je Link wählen Sie, wie die Person geht: sofort weitergeleitet, über die Endseite mit einem Knopf oder über die Endseite, die nach 5 Sekunden von selbst weitergeht. Ein Ende im Ablauf kann einen eigenen Link haben, der dann vor dem Link des Ausgangs gilt. **Regeln** wählen einen anderen Link je Anbieter oder je Teilquote, z. B. „Quote voll, wenn Parameter `source` = b, dann …“. Die erste zutreffende Regel eines Ausgangs gilt vor dem allgemeinen Link; eine Regel ohne Link lässt die Endseite stehen. Bei „Duplikat“, „Einwilligung abgelehnt“ und „Befragung geschlossen“ gibt es noch keine Antworten, dort prüfen Regeln nur Parameter.

![Eine Regel für einen zweiten Anbieter und darunter der Link für das Panel](img/guide-panel-rules.jpg)

**Quoten mit einem Panel.** Quoten legen Sie unter „Ablauf“ › „Quoten“ an (siehe [Quoten](build.md#8-quoten)). Ihre Bedingungen können sich nach Antworten richten oder nach einem Parameter, den der Anbieter mitschickt, etwa Alter oder Geschlecht aus seinem Pool. Ein solcher Parameter braucht eine Liste erlaubter Werte oder einen signierten Einstiegslink, sonst kann sich jede Person mit dem Link den Wert selbst geben; die Startprüfung nennt das. Wo die Quotenberechnung im Ablauf steht, entscheidet, wann ein Platz belegt wird; ein Ende mit dem Auslöser „Quote voll“ dahinter schickt alle, für die kein Platz mehr ist, zum Abschlusslink „Quote voll“. Eine harte Quote passt, wenn das Panel jede Teilnahme berechnet. Wer gerade teilnimmt, behält seinen Platz für die Haltezeit; abgebrochene Teilnahmen geben ihn wieder frei. Als Admin können Sie im laufenden Feld Soll, Art, Toleranz und Haltezeit ändern und einzelne Teilquoten schließen. Manche Anbieter wollen keine automatische Filterung während der Befragung. Deshalb füllen Teilnahmen, die die Qualitätssicherung markiert, ihren Platz, solange der Haken „Von der Qualitätssicherung markierte Teilnahmen füllen keinen Quotenplatz“ aus ist.

![Der Quotenstand, gefiltert auf die Teilnahmen eines Anbieters](img/guide-panel-quota-state.jpg)

**Quotenstand für den Anbieter.** Der Kasten „Quoten“ zeigt je Teilquote Soll, abgeschlossen, laufend, offen und abgewiesen, nur aus echten Teilnahmen. Arbeiten mehrere Anbieter in einer Befragung, bekommt jeder einen eigenen Wert in einem gespeicherten Parameter (z. B. `source=a` und `source=b` in ihren Teilnahmelinks); dann lässt sich der Stand auf die Teilnahmen eines Anbieters filtern. Den Stand gibt es zum Kopieren als Tabelle (für eine E-Mail oder das Portal des Anbieters), als CSV und als Druckansicht. Unter „Links“ erzeugen Sie einen Link, der diese Übersicht ohne Anmeldung und nur lesend zeigt, auf Wunsch nur mit den Teilnahmen eines Anbieters und ohne die Bedingungen der Teilquoten, befristet oder bis zum Widerruf. Er zeigt jeden Abschluss sofort; bei kleinen Teilquoten lässt sich eine gerade abgeschlossene Teilnahme so ihrer Teilquote zuordnen. Geben Sie ihn deshalb nur dorthin, wo die Zahlen zum Steuern gebraucht werden. Auch ein [Dashboard](analysis.md#14-dashboards) kann einen Link für einen Anbieter bekommen: Er zeigt die Kennzahlen der Datenerhebung und die Quoten nur aus dessen Teilnahmen, in Stufen statt genau, und lässt Kacheln mit Ergebnissen von Fragen weg.

![Die Quotenübersicht, die ein Anbieter über seinen Link sieht](img/guide-panel-quota-link.jpg)

![Ein Dashboard-Link für einen Anbieter: „Zeigt die Teilnahmen von“](img/guide-panel-board-link.jpg)

**Einmal-Kennungen.** Als „Duplikat“ gilt eine Kennung erst, wenn ihre frühere Teilnahme beendet ist oder über die erste Seite hinaus ging. Hat jemand mit der Kennung nur die erste Seite (die Einwilligung) abgeschickt und ist seit 10 Minuten nicht weitergekommen, nimmt die nächste Person mit dieser Kennung teil; die frühere Teilnahme geht ohne die Kennung weiter. So sperrt niemand, der eine fremde Panel-ID kennt, die echte Person mit einer einzigen Seite aus. Im selben Browser geht eine begonnene Teilnahme wie gewohnt weiter.

**Signierte Links.** Manche Anbieter verlangen, dass ein Abschlusslink eine Signatur trägt, damit niemand ihn von Hand aufrufen kann. Unter „Signierte Links“ tragen Sie den Schlüssel ein, den der Anbieter vergibt, und wählen Verfahren, Schreibweise und Umfang so, wie er sie nennt; `{{sig}}` setzt die Signatur in den Abschlusslink ein. Ein Beispiel mit Testwerten hilft beim Abgleich mit dem Anbieter.

Erfundene Kennungen und selbst gewählte Werte für Quoten verhindert nur ein **signierter Einstiegslink** (Haken „Auch den Einstiegslink signieren lassen“). Anleitung für das Panel: Es hängt an den Einstiegslink als letzten Parameter die Signatur an (Name einstellbar, Standard `sig`), z. B. `https://…/s/abc123?PID=4711&sig=…`. Signiert wird mit demselben Schlüssel, Verfahren und derselben Schreibweise wie die Abschlusslinks die aufgerufene Adresse vor diesem Parameter (mit oder ohne das `&` davor, wie unter „Signiert wird“ gewählt); angenommen wird auch die Signatur nur über den Teil hinter dem `?` (`PID=4711&`). Alle URL-Parameter der Befragung müssen vor der Signatur stehen. Ein Link ohne gültige Signatur zeigt „Dieser Link ist unvollständig“, auch der einfache Teilnahmelink. Richten sich Quoten oder Bedingungen nach URL-Parametern ohne Liste erlaubter Werte und ohne signierten Einstieg, nennt das die Startprüfung.

**Vor dem Start.** Vorschau und Testdurchläufe zählen für keine Quote. Probieren Sie den Link für das Panel mit einer Testkennung aus und lassen Sie sich vom Anbieter bestätigen, dass die Rücksprünge mit der Kennung ankommen; die Startprüfung meldet fehlerhafte Abschlusslinks und Regeln.

---

## Online-Experiment {kind:survey}

Ein Online-Experiment teilt die Teilnehmenden zufällig auf Bedingungen auf, zum Beispiel zwei Fassungen eines Textes, und misst danach bei allen dasselbe. Weil allein der Zufall entscheidet, wer welche Bedingung sieht, lässt sich ein Unterschied zwischen den Gruppen als Wirkung der Bedingung lesen. Die Vorlage „Online-Experiment mit zwei Bedingungen“ zeigt den ganzen Aufbau und ist ein guter Ausgangspunkt.

**1. Plan und Stichprobe.** Im [Studienplan](build.md#5-studienplan) wählen Sie als Erkenntnisziel „eine Wirkung prüfen“ und als Design „Experiment“. Die Hypothese nennt, welche Bedingung welche Wirkung haben soll, etwa „Wer Text B liest, bewertet das Angebot besser“. Die Poweranalyse fragt nach dem kleinsten Effekt, der Sie interessiert, und nennt die nötige Zahl je Gruppe. Gleich große Gruppen geben die meiste Power.

![Studienplan eines Online-Experiments: Hypothese mit Test und Stichprobenziel](img/guide-experiment-plan.jpg)

**2. Bedingungen bauen.** Jede Bedingung kommt in einen eigenen Block des Fragebogens, zum Beispiel „Text A“ und „Text B“. Die Messung danach steht in einem gemeinsamen Block, den alle sehen: dieselben Fragen in derselben Reihenfolge, oft eine validierte Skala aus den Konstrukten. Eine Kontrollfrage, ob die Bedingung angekommen ist (z. B. „Wie positiv war der Text?“), hilft später bei der Deutung. In einer Likert-Matrix lässt sich mit „Aufmerksamkeitsprüfung einbauen“ eine Aussage mit vorgegebener Antwort einfügen.

**3. Zufällig zuteilen.** Im [Ablauf](build.md#7-ablauf) ziehen Sie eine **Randomisierung** vor den Messblock und legen die Bedingungsblöcke hinein. Sie zeigt jeder Person einen der Blöcke, entweder ganz zufällig oder **ausgeglichen**: Ausgeglichen füllt zuerst die am seltensten gezeigte Bedingung und hält die Gruppen so auch bei wenigen Personen gleich groß. Askurious legt die Zuteilung einmal je Teilnahme auf dem Server fest und speichert sie; Neuladen und „Zurück“ ändern sie nicht, und der Export enthält sie als eigene Spalte.

![Der Ablauf eines Online-Experiments: Einwilligung, eine Randomisierung zwischen zwei Texten, danach dieselbe Skala](img/flow-experiment.jpg)

**4. Testen.** In der Vorschau springen Sie zwischen den Seiten und sehen jede Bedingung. Dummy-Daten gehen durch den echten Ablauf und verteilen sich wie echte Teilnahmen auf die Bedingungen; so sehen Sie schon vor der Feldphase, wie die Ergebnisseite aussehen wird. Testdurchläufe und Dummy-Daten zählen nie zu den echten Daten.

![Eine der beiden Bedingungen in der Vorschau](img/guide-experiment-preview.jpg)

**5. Auswerten.** Die [Ergebnisse](analysis.md#13-ergebnisse) prüfen die Hypothese mit dem geplanten Test, bei zwei Gruppen meist Welchs t-Test, und sagen „gestützt“ oder „nicht gestützt“, mit dem Satz für den Bericht. Darunter steht, welchen Effekt der Test mit dem tatsächlichen n hätte finden können; so wird „nicht gestützt“ bei einer zu kleinen Stichprobe nicht als „kein Effekt“ gelesen. Jede Frage lässt sich nach der Versuchsbedingung aufteilen.

![Ergebnisse eines Online-Experiments: Welchs t-Test für die Hypothese und die Verteilung der Antworten](img/results-experiment-a.jpg)

**Varianten.** Sollen alle Personen mehrere Bedingungen sehen, oder gibt es mehrere Faktoren mit Stufen (eine Vignettenstudie), baut eine **Schleife** im Ablauf das Design: je Person k aus N Zeilen einer Tabelle oder Kombinationen von Faktorstufen. Das spart Personen, birgt aber Reihenfolge- und Lerneffekte. Die Vorlage „Vignettenstudie mit 2 × 3 Faktoren“ zeigt so einen Aufbau.

---

## Seminar mit eigenen Links je Person {kind:survey}

In vielen Seminaren werben die Studierenden selbst Teilnehmende, etwa im Freundeskreis. Mit einem eigenen Link je Person sehen Sie und die Studierenden, wie viele über jeden Link teilgenommen haben. In den Daten steht dabei nur ein zufälliger Code des Links, kein Name.

**1. Links anlegen.** Im Reiter „Datenerhebung“ öffnen Sie im Block „Teilnahmelink“ den Teil **„Eigene Links für Personen oder Gruppen“**. Tragen Sie die Namen ein, einen je Zeile, zum Beispiel aus einer Tabelle kopiert, oder nur eine Anzahl, wenn die Namen später kommen. Jede Zeile ergibt einen Link mit einem zufälligen Code wie `…?w=k7mp2q`. Die Namen sehen nur Sie auf dieser Seite; sie gehen weder in den Link noch in die Daten.

![Eigene Links anlegen: ein Name je Zeile und der Haken für Dashboard-Links](img/guide-seminar-add.jpg)

**2. Dashboard-Link je Person.** Mit dem Haken **„Für jede Person einen Dashboard-Link anlegen“** bekommt jede Person zusätzlich einen Link zu einem Dashboard, das nur die Zahl ihrer eigenen Teilnahmen zeigt. So können die Studierenden selbst nachsehen, wie weit sie sind, ohne Zugang zur Befragung. Beim nächsten Mal nutzt Askurious dasselbe Dashboard wieder.

**3. Verteilen.** Die Links gibt es als Liste zum Kopieren und als **QR-Karten zum Ausschneiden**, je Person eine Karte. Wenn die Plattform E-Mails verschickt, schickt „Links per E-Mail verschicken“ jeder Person ihren Link; die Adressen werden dabei nicht gespeichert.

![QR-Karten zum Ausschneiden, eine je Person](img/guide-seminar-cards.jpg)

**4. Zählen.** Die Tabelle zeigt je Link, wie viele Personen begonnen und abgeschlossen haben und wie viele davon **verwertbar** sind, also nicht von der Qualitätssicherung markiert. Die Spalte „Ihr Stand“ enthält die Dashboard-Links, „Liste herunterladen (CSV)“ und „QR-Karten zum Ausschneiden“ stehen darunter. Steht an einem Link „auffällig“, heißt das nur: genauer hinsehen, etwa wenn viele Abschlüsse kurz hintereinander kommen oder viele markiert sind; „Was die Zahlen bedeuten“ erklärt das. IP-Adressen werden dafür nicht verwendet.

![Die Tabelle der eigenen Links mit den Teilnahmen je Link](img/guide-seminar-table.jpg)

**5. Was die Studierenden sehen.** Der Dashboard-Link zeigt nur die eigene Zahl, keine Antworten und keine Zahlen der anderen.

![Das Dashboard einer Person: nur die Zahl der eigenen Teilnahmen](img/guide-seminar-dashboard.jpg)

**6. Begrenzen.** Unter **„Höchstzahl je Link“** lässt sich festlegen, wie viele Personen über einen Link höchstens abschließen dürfen, z. B. 10. Ist ein Link voll, sehen weitere Personen das Ende „Quote voll“. Askurious legt dafür eine Quote mit einem Teil je Link an; vor dem Start lässt sie sich so anlegen, danach nur noch die Zahl ändern.

**Gut zu wissen.** Ein eigener Link verhindert nicht, dass jemand zweimal teilnimmt; dafür gibt es die Einstellung zur Teilnahme im selben Browser. Und der Code im Link macht die Daten nicht persönlich: Nur wer die Namensliste dieser Seite hat, kann einen Code einer Person zuordnen.

---

## Längsschnitt über mehrere Wellen {kind:survey}

Ein Längsschnitt befragt dieselben Personen mehrmals, etwa vor und nach einem Seminar oder einmal im Semester. Um die Antworten einer Person über die Wellen zusammenzuführen, ohne Namen oder E-Mail-Adressen zu speichern, nutzt Askurious einen **persönlichen Code**.

**1. Erste Welle bauen.** Legen Sie die erste Welle als normale Befragung an. Fügen Sie das Element **„Persönlicher Code“** ein, am besten früh im Fragebogen. Es bildet einen Code aus 3 bis 6 kurzen Fragen, die man jedes Mal gleich beantwortet, zum Beispiel der Anfangsbuchstabe des Vornamens der Mutter oder der Tag des eigenen Geburtstags. Jeder Teil hat ein eigenes Feld mit Prüfung und „weiß nicht“. Bei kleinen Gruppen nehmen Sie weniger Teile, damit sich niemand am Code erkennen lässt.

![Der persönliche Code aus Sicht der Teilnehmenden](img/guide-waves-code.jpg)

**2. Weitere Wellen anlegen.** Jede Welle ist eine eigene Befragung. Am einfachsten entsteht Welle 2 mit „Duplizieren“ unter „Meine Befragungen“: Dann hat sie denselben persönlichen Code mit denselben Teilen und dieselben Skalen, und Sie ändern nur, was sich unterscheiden soll.

**3. Wellen verknüpfen.** Im Reiter „Studienplan“ steht unter dem Plan der Abschnitt **„Wellen“**. Dort wählen Sie die Befragungen der einzelnen Wellen und geben jeder einen Namen, etwa „vorher“ und „nachher“. Die Liste gilt für alle Wellen darin. Das Verknüpfen gehört nicht zum Plan selbst: Eine Welle später hinzuzufügen, ist keine Abweichung von der Präregistrierung. Ebenfalls im Studienplan legen Sie fest, welche Größen über die Zeit modelliert werden; eine Hypothese der Art **„Wirkung über die Zeit“** (X in einer Welle auf Y in der nächsten) wird im Cross-Lagged-Panel-Modell geprüft.

![Der Abschnitt „Wellen“ im Studienplan mit drei verknüpften Befragungen](img/guide-waves-plan.jpg)

**4. Zusammenführen.** Auf der Seite „Export“ jeder Welle führt **„Wellen zusammenführen“** die Daten über den Code zusammen: breit (eine Zeile je Person, die Spalten jeder Welle mit `t1_`, `t2_` …) oder lang (eine Zeile je Person und Welle), als CSV, .sav oder .rds, dazu ein Abgleichsbericht. Codes, die in einer Welle doppelt vorkommen, werden nicht verknüpft. Mit „tolerant“ wird ein Code, der sonst allein bliebe, auch mit einem Code verknüpft, der sich in genau einem Teil unterscheidet, aber nur, wenn das in beiden Richtungen eindeutig ist; die Spalte zur Güte des Abgleichs sagt das.

**5. Auswerten.** Die Ergebnisse jeder Welle zeigen den [Längsschnitt über die Wellen](analysis.md#13-ergebnisse): Mittelwerte je Welle, Messinvarianz über die Zeit, Wachstumskurven und das Cross-Lagged-Panel-Modell, ab drei Wellen das RI-CLPM. Gerechnet wird mit dem Knopf „Längsschnitt rechnen“ über alle verknüpften Personen. Messinvarianz und Wachstumskurve brauchen mindestens 100 verknüpfte Personen mit einem Wert, das Cross-Lagged-Panel-Modell mindestens zwei Größen. Das RI-CLPM rechnet die Seite ab drei Wellen und 200 verknüpften Personen, sonst das einfache Modell mit einem Hinweis.

![Längsschnitt über die Wellen in den Ergebnissen](img/guide-waves-results.jpg)

**Gut zu wissen.** Der Code verknüpft nur, wer sich an seine Antworten erinnert. Rechnen Sie mit einigen Personen, die sich nicht verknüpfen lassen, und planen Sie die Stichprobe entsprechend größer. Kurze Abfragen über Tage oder Wochen statt weniger großer Wellen passen besser zu einer [Tagebuchstudie](kinds.md#17-tagebuchstudie).
