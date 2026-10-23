# Lasttest

Simulierte Teilnehmende füllen eine laufende Befragung Seite für Seite aus, so wie Menschen: Link öffnen, einwilligen, antworten, ein paar Sekunden Pause, weiter, abschicken, als neue Person von vorn. Die Zahl der Gleichzeitigen steigt in Stufen (25, 50, 100, 200, 400; je 3 Minuten). Am Ende steht ein Bericht mit den Antwortzeiten je Seite und Stufe und den Fehlern.

Werkzeug: [Locust](https://locust.io) (Python). Dateien hier:

- `lasttest-befragung.json`: die Testbefragung zum Einlesen (Einwilligung, Alter und Geschlecht, acht Seiten mit Matrix, Einfach- und Mehrfachauswahl, Dropdown, Zahl und Text; 38 Elemente). Neu bauen mit `python tools/lasttest/build_survey.py`.
- `locustfile.py`: die simulierten Teilnehmenden. Es liest die Fragen aus der Seite selbst und passt so auch zu anderen Befragungen aus den üblichen Fragetypen.

## 1. Server vorbereiten

1. Befragungen → Neue Befragung → **Befragung aus Datei einlesen**: `lasttest-befragung.json` wählen.
2. Die Befragung starten (Datenerhebung starten, Ende irgendwann später). Aus dem Teilnahmelink `https://<Server>/s/<Code>` den **Code** notieren.
3. In die `.env` des Servers vorübergehend diese drei Zeilen schreiben und die App neu starten:

   ```
   START_LIMIT=0
   SURVEY_SENDER_LIMIT=0
   SURVEY_START_LIMIT=0
   ```

   Sonst greifen die Schutzgrenzen gegen Skripte: Alle simulierten Personen kommen von Ihrer einen Adresse, und die erste Grenze liegt bei 30 neuen Teilnahmen je Absender und Befragung in 10 Minuten (300 je Absender über alle Befragungen). `SURVEY_START_LIMIT` weist niemanden ab, meldet aber im Reiter „Datenerhebung“ und in Slack.
4. Falls Plesk Ihre Adresse sperrt (Fehler 403 oder 429 im Bericht, die nicht von der App kommen): Werkzeuge & Einstellungen → IP-Adressensperre (Fail2Ban) → Vertrauenswürdige IP-Adressen → Ihre eigene eintragen.

## 2. Test laufen lassen (auf Ihrem PC)

Einmalig in einem Ordner Ihrer Wahl (PowerShell):

```
python -m venv lasttest-venv
lasttest-venv\Scripts\python -m pip install locust
```

Dann, mit Ihrem Code und Server:

```
$env:LASTTEST_CODE = "abc123"
lasttest-venv\Scripts\python -m locust -f tools\lasttest\locustfile.py --host https://askurious.example --headless --html lasttest-bericht.html --csv lasttest
```

Das dauert mit den Standardstufen 15 Minuten. Währenddessen in Plesk die Auslastung ansehen (Statistiken bzw. Server-Monitoring: Prozessor, Arbeitsspeicher). Abbrechen mit Strg+C, der Bericht wird trotzdem geschrieben.

Einstellbar:

| Variable | Bedeutung | Standard |
|---|---|---|
| `LASTTEST_CODE` | Code der Befragung (nach `/s/`) | – |
| `LASTTEST_STUFEN` | Gleichzeitige je Stufe | `25,50,100,200,400` |
| `LASTTEST_MINUTEN` | Minuten je Stufe | `3` |
| `LASTTEST_PAUSE` | Sekunden je Seite zum Lesen und Antworten | `3-8` |

Zum Ausprobieren erst klein: `$env:LASTTEST_STUFEN = "5"; $env:LASTTEST_MINUTEN = "1"`.

Lokal gegen die eigene App `--host http://127.0.0.1:<Port>` nehmen, nicht `localhost`: Unter Windows wartet jede Anfrage an `localhost` sonst zwei Sekunden auf IPv6.

## 3. Danach

1. Die zwei Zeilen wieder aus der `.env` nehmen und die App neu starten.
2. Die Testbefragung samt Teilnahmen löschen.
3. Den Bericht auswerten: `lasttest-bericht.html` (Antwortzeiten über die Zeit, je Seite) und `lasttest_stats.csv`. Worauf es ankommt: der Median und das 95-%-Perzentil je Seite (unter einer Sekunde ist gut), Fehler (sollten 0 sein) und ab welcher Stufe die Zeiten deutlich steigen. „Teilnahme abgeschlossen“ zählt die vollständigen Teilnahmen; ihre Zeit enthält die Pausen.

Grenze des Aufbaus: Ein Heimanschluss trägt einige hundert simulierte Personen. Für mehr den Test auf einem zweiten Rechner parallel starten oder Locust verteilt laufen lassen (`--master` / `--worker`).
