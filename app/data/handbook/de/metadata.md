# Metadaten, Archivierung und Veröffentlichung

Daten sind nur so nützlich wie ihre Beschreibung. Diese Seite erklärt, welche Beschreibung Askurious zu einer Befragung schreibt, woher die Angaben kommen, was sie über Studien hinweg vergleichbar macht und wie eine Befragung bei Zenodo oder Coscine archiviert und veröffentlicht wird.

## 1. Wozu Metadaten

Wer Daten später nachnutzen will, muss wissen, was erhoben wurde, bei wem, wann, wie und unter welchen Bedingungen. Ohne diese Angaben ist ein Datensatz eine Tabelle mit Spaltennamen. Die FAIR-Grundsätze fassen das zusammen: Daten sollen **auffindbar** (Titel, Schlagwörter, Thema, eine feste Kennung), **zugänglich** (ein Ort und eine Lizenz), **interoperabel** (Standards, die Programme lesen können) und **nachnutzbar** (Fragetexte, Antwortcodes, Stichprobe, Ablauf) sein.

Askurious schreibt diese Beschreibung aus dem, was Sie beim Anlegen und im Studienplan ohnehin eintragen. Sie füllen kein eigenes Metadatenformular aus.

## 2. Was Askurious beschreibt und wer es liest

Unter Export › Dokumentation stehen drei Dateien, und dieselben Dateien liegen in den Paketen für Zenodo und Coscine. Keine von ihnen enthält Antworten.

| Datei | Standard | Wer sie liest |
|---|---|---|
| DDI Lifecycle 4.0 (XML) | Data Documentation Initiative, Entwurf 4.0 zur öffentlichen Prüfung | Datenarchive und Werkzeuge, die DDI Lifecycle verarbeiten; enthält den ganzen Fragebogen mit Ablauf |
| DDI Codebook 2.5 (XML) | Data Documentation Initiative, Fassung 2.5, nach den Profilen von CESSDA | der Datenkatalog von CESSDA und die European Question Bank (über die Archive, die dort melden) |
| CDIF (JSON-LD) | Cross-Domain Interoperability Framework 1.1 (CODATA), schema.org | fachübergreifende Kataloge, etwa die der European Open Science Cloud |

Beide DDI-Dateien beschreiben die Studie (Titel in allen Sprachen, Team, Zusammenfassung, Zeitraum, Land, Stichprobe, Auswahlverfahren, Erhebungsart, Lizenz, Förderung, Veröffentlichungen) und jede Spalte des Datensatzes (Fragetext, Antwortcodes mit fehlenden Codes, Format). Erhebungsart, Zeitbezug, Auswahlverfahren, Analyseeinheit und Datenart stehen als Begriffe der kontrollierten Vokabulare der DDI Alliance darin, mit Vokabular- und Begriffsadresse.

Die Datei DDI Lifecycle 4.0 führt außerdem die Hinweise für Interviewende eines Interviewleitfadens (den Hinweis einer Leitfrage und das „Wann stellen?“ einer Unterfrage) als eigene Anweisungen, die an ihrer Frage hängen, und die Einrichtungen des Teams als eigene Organisationen: Der Herausgeber verweist auf seine Einrichtung, jede Person mit ORCID auf die ihre. Darin stehen nur die Namen, die die Datei ohnehin enthält.

![Aufbau der Askurious-Metadaten in der Datei DDI Lifecycle 4.0](img/ddi-structure.svg)

So lesen Sie die Abbildung: Jeder Kasten ist ein Eintrag der Datei, das blaue Schild nennt seinen Namen in DDI. Ein durchgezogener Pfeil zeigt, was ein Eintrag enthält oder wohin er innerhalb eines Bereichs führt. Ein gestrichelter Pfeil ist ein Verweis über die Bereiche hinweg (per URN). Alle Einträge stehen flach nebeneinander in einem ItemContainer, die Studie ist sein Einstieg (TopLevelReference).

Unterschiede zur Abbildung 1 des GESIS-Leitfadens (DDI-Lifecycle-Profil des DDILimDAS-Projekts, DDI 3.2):

- Es gibt keine Hülle aus DDIInstance, Group und ResourcePackage. DDI 4.0 kennt nur den flachen ItemContainer.
- Eine Reihe (Series) ist kein eigener Eintrag: Panelwellen stehen als SeriesStatement in der Studie.
- Die Geografie steht in der Abdeckung der Studie (Land, Region), nicht in einem eigenen Geografie-Schema.
- RepresentedVariable, ManagedRepresentation und RecordLayout schreibt Askurious nicht. Die Variable trägt ihre Darstellung selbst, die Datei data.csv ist mit Namen und Sprachen beschrieben.

CESSDA nimmt selbst keine Studien an; der Katalog sammelt die Beschreibungen der Archive, die zu CESSDA gehören. Ob eine Datei die Profile erfüllt, können Sie zusätzlich selbst im CESSDA Metadata Validator prüfen (die Datei DDI Codebook 2.5 hochladen, Profil CDC oder EQB wählen).

## 3. Woher die Angaben kommen

| Sie tragen ein (Assistent bzw. Einstellungen) | So steht es in den Metadaten |
|---|---|
| Titel, Übersetzungen des Titels | Titel und Paralleltitel je Sprache |
| Beschreibung | Zusammenfassung (abstract); ohne Beschreibung die Forschungsfrage, ohne die der Titel |
| Forschungsfrage | Teil der Studienbeschreibung |
| Schlagwörter, Thema aus der Themenliste von CESSDA | Schlagwörter und Themenklassifikation mit Vokabularadresse |
| Sprachen | Sprachen der Studie und der Datendatei |
| Studiendesign, Wellen eines Panels | Zeitbezug (Querschnitt, Panel, Trend) |
| Rekrutierung, Art des Panels oder der Zufallsstichprobe | Auswahlverfahren |
| Grundgesamtheit, Land, Region | Universum, Land (ISO-Code), geografische Abdeckung |
| Stichprobenziel, erwartete Ausschlüsse, erwarteter Rücklauf, Quoten | geplante Stichprobe |
| Team mit ORCID und Organisation | Urheber mit Organisation und ORCID |
| Förderung: Fördergeber und Förderkennzeichen | Förderung |
| Lizenz in der Einwilligung | Nutzungsbedingungen |
| Veröffentlichungen unter Export › Veröffentlichungen | verwandte Veröffentlichungen mit DOI |

Was Askurious selbst setzt:

- **Herausgeber:** die Organisation der Person, die die Befragung angelegt hat, sonst die erste Organisation im Team, sonst ihr Name. Nicht der Betreiber der Installation: Herausgeber sind die Forschenden.
- **Studiennummer:** die feste Kennung der Befragung, die sich nie ändert, unter der **Agency** `de.askurious`. Die Agency ist der Namensraum aller Kennungen in einer DDI-Datei und bei der DDI Alliance eingetragen; so stößt eine Kennung aus Askurious nie mit der eines anderen Archivs zusammen.
- **DOI:** sobald die Befragung bei Zenodo veröffentlicht ist.
- **Erhebungsart, Datenart, Analyseeinheit, Erhebungszeitraum** aus der Art der Befragung und den Daten des Starts und Abschlusses.

Der Kasten **„Für Datenarchive“** auf der Übersicht des Einrichtungsassistenten zeigt, welche dieser Angaben schon da sind, welche Askurious selbst setzt und welche noch fehlen, jeweils mit einem Knopf zum Schritt, in dem sie eingetragen werden.

## 4. Vergleichbarkeit

- **Validierte Konstrukte** stehen als Konzept an ihren Variablen, mit Autorinnen, Jahr und DOI der Quelle.
- **Feste Kennungen:** jede Frage, Variable und Codeliste hat eine Kennung, die bei jedem Export gleich bleibt; die Version folgt der Fragebogenversion.
- **Der Ablauf ist maschinenlesbar:** eine Bedingung steht in Worten und als Ausdruck über die Variablen (etwa `gender == 1`), „sonst“ als ihr Gegenteil, eine Randomisierung als Aufteilung, eine Schleife als Schleife. Im Codebook 2.5 steht bei jeder Variablen, unter welcher Bedingung sie gefragt wird.
- **Abgeleitete Variablen** stehen mit ihrer Berechnung darin: Rechenwerte mit den Variablen, aus denen sie entstehen, umgepolte Aussagen und das Gewicht mit seinem Verfahren.
- **Panelwellen** stehen als Reihe; jede Welle nennt die anderen mit ihrer Bezeichnung und Studiennummer, nicht mit ihrem Titel.
- **Aussagenbatterien:** die Aussage als Frage, der Fragestamm davor, damit eine Fragendatenbank jede Aussage einzeln findet.

## 5. Archivieren und veröffentlichen

Askurious lädt zu **Zenodo** und zu **Coscine** hoch (Export). Eine Anbindung an andere Archive gibt es nicht; die DDI-Dateien können Sie einem Archiv aber selbst übergeben.

- **Zenodo:** ein Entwurf mit Titel, Team, Beschreibung, Schlagwörtern, Lizenz und Zeitraum, den Sie bei Zenodo prüfen und veröffentlichen; dabei entsteht die DOI. Nötig ist eine Lizenz in der Einwilligung (CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0 oder CC0 1.0).
- **Coscine:** dieselben Dateien in einen neuen Ordner Ihrer Coscine-Ressource, mit den Angaben ihres Profils.

Im Paket liegen die Antworten (data.csv), das Codebuch, der Fragebogen als Markdown, die Befragungsdatei, das Befragungsmaterial als PDF, beide DDI-Dateien und CDIF, die Konstrukte, der Kodierleitfaden, die langen Formate, die Tabellen der Schleifen, die Bilder mit dokumentierten Rechten, die Siegelkette und eine README mit Prüfsummen.

Was nie mitgeht:

- der Wortlaut von Konstrukten ohne allgemeine Erlaubnis zur Veröffentlichung (nur Kennungen und Werte);
- die Teilnahmekennung und URL-Parameter;
- Freitexte, genaue Kartenpositionen, Postleitzahlen und taggenaue Datumsangaben, außer Sie kreuzen das ausdrücklich an;
- Bilder ohne dokumentierte Rechte; die Archivierung ist dann gesperrt, bis sie geklärt sind.

Sagt die Einwilligung, dass die Daten nicht veröffentlicht werden, verlangt Askurious vor dem Hochladen eine ausdrückliche Bestätigung.

## 6. Nachvollziehbarkeit

- **Präregistrierung:** beim Start der Datenerhebung friert Askurious die Befragung ein, als ZIP mit Befragungsdatei, Fragebogen, Codebuch und Befragungsmaterial, mit der SHA-256 jeder Datei und einem Zeitstempel des DFN; die Prüfsumme des ZIP lässt sich in einer Präregistrierung zitieren.
- **Siegelkette:** jede beendete Teilnahme bekommt einen Eintrag, dessen Prüfsumme vom vorigen abhängt; Änderungen und Löschungen werden als neue Einträge geschrieben. So lässt sich auch ohne Askurious prüfen, dass im archivierten Datensatz kein Fall fehlt.
- **Prüfsummen:** jedes Paket hat eine Datei checksums.sha256.

## 7. Konformität

Die Dateien sind ohne Ihr Zutun regelkonform. Tests prüfen bei jeder Änderung an Askurious für jede Art von Befragung (Fragebogen, Tagebuchstudie, Interviewleitfaden), für eine Befragung nur mit Titel und für jede Studienvorlage, dass beide DDI-Dateien gegen ihr XML-Schema gültig sind und das Codebook 2.5 jede Pflichtangabe der CESSDA-Profile CDC und EQB enthält, als Download und als Datei des Pakets. Eine eigene Prüfung in der Oberfläche gibt es deshalb nicht; was fehlt, zeigt der Kasten „Für Datenarchive“ als Empfehlung.
