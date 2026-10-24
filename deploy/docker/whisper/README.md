# Lokaler WhisperX-Dienst (vorbereitet, standardmäßig ausgeschaltet)

Separater CPU-Container für Askurious: WhisperX 3.8.6, PyTorch 2.8.0 CPU,
pyannote.audio 4.0.7 / Community-1. Keine GPU erforderlich. Zur Laufzeit werden
keine Modelle heruntergeladen; das interne Docker-Netz hat keinen Internetzugang.
Nur der Forschenden-Container erreicht den Dienst. Kein öffentlicher Port,
keine Datenbankzugänge oder sonstigen App-Schlüssel im Transkriptionscontainer.

## Grenzen und Verhalten

### Sicherheitsprüfung der festgelegten Laufzeit (14.10.2026, erneuert 24.10.2026)

Seit 24.10.2026 sind NLTK 3.10.4 (statt 3.10.3, GHSA-8mgp-746c-j5xp behoben) und
setuptools 84.1.0 (statt 78.1.0, alle gemeldeten Befunde behoben) sowie filelock
4.2.1 (statt 3.32.3, GHSA-v4x8-3wr5-3wq8 und zwei Windows-Befunde behoben) festgelegt;
setuptools bleibt installiert, weil PyTorch 2.8 es unter Python 3.12 verlangt
(`pip check` im Dockerfile), der Dienst importiert es nicht. Für PyTorch 2.8.0
und Transformers 4.57.6 meldet OSV weiter Befunde: WhisperX 3.8.6 verlangt
`torch~=2.8.0` und `huggingface-hub<1.0`, die Korrekturen stecken erst in PyTorch
2.9.1 bis 2.13 bzw. Transformers 5.x (braucht `huggingface-hub>=1.5`). Sie stehen
mit Verantwortlichem und Ablaufdatum in `osv-accepted.txt` und werden mit der
nächsten WhisperX-Version angehoben. Für diesen Dienst wurden die betroffenen
Aufrufwege geprüft:

- PyTorch: keine fremden Checkpoints, PT2-Archive, TorchScript-Quelltexte oder
  frei gewählten Tensoroperationen. Geladen werden ausschließlich die vorab
  geprüften lokalen Modelle; deren Manifest-Hashes sind vor Aktivierung erneut
  zu prüfen. Audiodateien werden als PCM dekodiert, nicht als Modellarchive geladen.
- Transformers: keine Trainer-Checkpoints, LightGlue-/X-CLIP-Konvertierungen,
  generativen Modelle, Chat-Template-Exporte oder extern gewählten Modell-Repositories.
  Die Laufzeit ist offline; nur die vorbereiteten Alignment-Modelle sind verfügbar.

Die bekannten Befunde bleiben für die Bibliotheksversionen bestehen; die
betroffenen Eingaben/Funktionen sind nicht Teil dieser API. Diese Beurteilung
gilt nur für die beschriebene isolierte Laufzeit. Modellwechsel, Bibliothekswechsel
oder eine Erweiterung der API erfordern eine erneute Prüfung. Die OSV-Prüfung
der App-Abhängigkeiten meldete keine bekannten Befunde. Läuft ein Datum in
`osv-accepted.txt` ab, meldet die wöchentliche Prüfung die Kennung wieder, bis sie
neu beurteilt ist. Referenzen:
[PyTorch-Checkpoints](https://osv.dev/vulnerability/PYSEC-2026-2286),
[Transformers-Modellkonfiguration](https://osv.dev/vulnerability/GHSA-29pf-2h5f-8g72).

| Einstellung | Vorbereitung |
|---|---|
| CPU | höchstens 8 CPUs, 8 Rechenthreads (`WHISPER_THREADS`), `nice 15`, CPU-Shares 64 |
| Speicher | 5 GiB einschließlich Dateicache, kein zusätzlicher Swap (seit 2026-10-24; vorher 4 GiB, Spitze mit `large-v3-turbo` und deutschem Alignment 3,27 GiB) |
| Host-Speicherdruck | `oom_score_adj=500`: der Kernel bevorzugt den Transkriptionsprozess gegenüber den App-Prozessen |
| Prozesse | höchstens 256 einschließlich Threads |
| Parallelität | ein Rechenauftrag; höchstens 8 offene Aufträge im Dienst, weitere Anfragen warten in der App auf einen freien Platz (`/v1/status` meldet `free`) |
| Modell-Cache | ein Whisper-Modell mit VAD; höchstens 5 Minuten Leerlauf / 8 Aufträge; Freigabe bei Hostdruck, Fehler, Abbruch oder mehr als 3 GiB RSS |
| Dateien | höchstens 400 MiB und 60 Minuten pro Aufnahmeteil |
| Modelle | `large-v3-turbo` als Standard (seit 18.10.2026); optional `small`, jeweils CPU `int8` |
| Pipeline | Transkription, Wort-Zeitmarken, Sprecherzuordnung; Deutsch/Englisch |
| Hostschutz | kein neuer Auftrag bei weniger als 4 GiB verfügbarem Host-RAM, weniger als 2 GiB plus 400 MiB freiem Plattenplatz, Load über `WHISPER_MAX_LOAD` (Standard 12, compose.yaml 20 für 24 vCPU) oder erfolglosem App-Healthcheck |
| Temporäre Daten | Audio nach Abschluss/Fehler/Abbruch entfernt; offene Aufträge nach 24 h, Ergebnisse nach 1 h |

Das sind harte Containergrenzen, keine Garantie für unveränderte Antwortzeiten.
Bei Hostdruck wird der Rechenprozess beendet und der Auftrag wieder eingereiht;
ein angefangener Aufnahmeteil wird dann vollständig neu berechnet. Die Queue
überlebt Containerneustarts. Nach einem Neustart läuft höchstens ein Auftrag weiter.
Bereinigung erfolgt alle 15 Sekunden im Leerlauf, spätestens nach einer Minute
während eines Auftrags, oder nach dem nächsten Start eines ausgeschalteten Dienstes.
Modelle sind schreibgeschützt; Audio, Warteschlange und Upload-Spool liegen im
privaten Volume `whisper-jobs`. Dieses Volume enthält Forschungsdaten und darf
nicht in Modellarchive oder langfristige Backups aufgenommen werden.

Der einzelne Rechenprozess kann das zuletzt verwendete Whisper-Modell samt VAD
für Folgeaufträge derselben Sprache und Modellwahl wiederverwenden. Ein Wechsel
verwirft das alte Modell vor dem Laden des neuen. Alignment und Sprecherzuordnung
werden nach jedem Auftrag freigegeben. Prompts werden pro Auftrag gesetzt und
anschließend zurückgesetzt; Audio, Transkripte und VAD-Zustände werden nicht als
Wiederverwendungscache geführt. Die harte 4-GiB-Grenze bleibt unverändert.
Ein Abbruch oder Hostdruck beendet den Rechenprozess einschließlich Modell-Cache.
Bei `large-v3-turbo` werden die großen ASR-Gewichte vor Alignment/Sprecherzuordnung
vollständig entladen. Der kleine Laufzeitkontext und VAD bleiben wiederverwendbar;
die Gewichte werden bei einem Folgeauftrag wieder geladen. Damit bleiben nicht
gleichzeitig Turbo, Alignment und Sprecherzuordnung vollständig im Speicher.
Leerlaufgrenze und RSS-Prüfung greifen spätestens beim nächsten 15-Sekunden-Tick.

Die Interviewliste zeigt den Queue-Platz des aktuellen Aufnahmeteils, die
Verarbeitungsphase und die Anzahl fertiger Teile. Die Anzeige aktualisiert sich
alle fünf Sekunden (bei verdecktem Browserfenster alle 15 Sekunden). Der
Fortschrittsbalken zählt fertige Teile; er behauptet keine zeitliche Prozentprognose.
Die Abbruchschaltfläche beendet auch den entfernten Auftrag. Statusabrufe verlangen
Bearbeitungsrechte am konkreten Leitfaden und enthalten keine entfernten Auftrags-IDs.
Vor jedem Start lässt sich `small` oder `large-v3-turbo` auswählen. Die Auswahl
wird je Auftrag gespeichert und verändert weder den Serverstandard noch andere
Interviews. Ein Modellwechsel startet die generierten Aufnahmeteile neu und
verwirft alte entfernte Aufträge; bereits korrigierter Transkripttext bleibt
erhalten und bekommt nach Abschluss gegebenenfalls einen neuen Entwurf daneben.
Auch bei unterschiedlicher Auswahl wird nur ein Auftrag gleichzeitig berechnet.

Die App speichert Auftrags-IDs pro Aufnahmeteil und verwendet sie beim erneuten
Start, sofern Datei und Optionen unverändert sind. Verschiedene Interviews erhalten
eigene Aufträge, auch bei identischen Dateien: das Löschen eines Interviews darf
keinen Auftrag eines anderen Interviews abbrechen. Netzwerkfehler beim Upload
können einen nicht zugeordneten Auftrag zurücklassen; auch dieser verfällt nach 24 h.
Löschungen werden nach erfolgreichem Datenbank-Commit an den Dienst weitergegeben.
Ist er nicht erreichbar, greifen dessen Ablaufzeiten; die App protokolliert nur
einen allgemeinen Hinweis, keine Inhalte oder Schlüssel.

Sprecherlabels wie `P1-S1` bezeichnen Stimme 1 in Aufnahmeteil 1. Sie erkennen
keine Personen, Namen oder Interviewrollen. Die Zuordnung kann fehlerhaft sein,
insbesondere bei überlappender Sprache; Forschende müssen sie prüfen.

## Vor einer Aktivierung

Im Verzeichnis `deploy/docker` zunächst das Image bauen. Ein Build ist getrennt
vom laufenden Stack vorzunehmen und ebenfalls auf Ressourcen zu begrenzen.
Compose verwendet ausschließlich das vorbereitete lokale Image; ein normaler
App-Deploy baut oder lädt den WhisperX-Dienst nicht automatisch neu.
`restart.sh` startet den aktivierten WhisperX-Dienst nach dem Healthcheck der
Forschenden-Seite und wartet auf seine Bereitschaft, bevor die abschließende
Isolationsprüfung alle aktiven Dienste und Netze verifiziert.

```sh
# Auf dem geprüften Server ist der klassische Builder vorhanden (Buildx fehlt).
# Die Grenzen gelten für dessen RUN-Schritte; der Image-Export läuft im Docker-Daemon.
DOCKER_BUILDKIT=0 docker build --cpu-quota=400000 --cpu-period=100000 \
  --cpu-shares=64 --memory=4g --memory-swap=4g --force-rm \
  -t askurious-whisperx:3.8.6-phase1 ./whisper
```

Die Community-1-Dateien müssen zuvor im eigenen Hugging-Face-Konto rechtmäßig
heruntergeladen worden sein. Die Vorbereitung erwartet `config.yaml`,
`embedding/pytorch_model.bin`, `segmentation/pytorch_model.bin`, `plda/plda.npz`
und `plda/xvec_transform.npz` aus Revision
`3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`; deren SHA256-Werte werden geprüft.
Zugangstoken gehören weder in das Image noch in
die App. Originaldateien und Lizenzhinweise aufbewahren (Community-1: CC BY 4.0).

```sh
models="$HOME/askurious-whisper-models"
mkdir -p "$models"
# AUTHORISED_PYANNOTE_DIR ist der absolute Pfad zu den fünf heruntergeladenen Dateien.
docker run --rm --cpus=4 --cpu-shares=64 --memory=4g --memory-swap=4g \
  --pids-limit=256 --user "$(id -u):$(id -g)" \
  -v "$models:/models" -v "$AUTHORISED_PYANNOTE_DIR:/authorised:ro" \
  -e TMPDIR=/tmp --entrypoint nice askurious-whisperx:3.8.6-phase1 \
  -n 15 python -m whisper_service.prepare --pyannote-dir /authorised
```

Dieser **einmalige Vorbereitungsschritt benötigt Internet**. Er lädt festgelegte
Whisper-Modellrevisionen und Alignment-Modelle, NLTK-Daten und Silero VAD in Version
v6.2.3. `manifest.json` enthält Dateihashes, Revisionen und die Lizenz jedes Modells.

Nur frei lizenzierte Modelle (Urheberrechts-Audit 2026-10-16, U2): Whisper-Gewichte,
englisches Alignment (`WAV2VEC2_ASR_BASE_960H`) und Silero VAD stehen unter MIT,
Community-1 unter CC BY 4.0, die NLTK-Satzdaten `punkt_tab` (Jan Strunk) unter Apache 2.0
(Lizenz des Repositorys nltk_data; das Paket nennt keine eigene). Deutsch richtet `jonatasgrosman/wav2vec2-large-xlsr-53-german`
(Apache 2.0, feste Revision, rund 1,3 GB) aus, nicht der WhisperX-Standard
`VOXPOPULI_ASR_BASE_10K_DE`, der unter CC BY-NC 4.0 steht und keine kommerzielle Nutzung
erlaubt. Modelle, die vor dieser Änderung vorbereitet wurden, richten Deutsch weiter mit
dem alten Modell aus, bis die Vorbereitung erneut läuft. Vor dem Einschalten Laufzeit und
Speicher des deutschen Alignments unter dem 4-GiB-Limit neu messen. Die Lizenzen aller
Pakete stehen in `app/data/THIRD_PARTY_LICENSES.txt`.
Er benötigt mehrere GB Plattenplatz und darf nicht gleichzeitig mit einem
laufenden Transkriptionsauftrag ausgeführt werden.

Anschließend in der geschützten Server-`.env` konfigurieren (Schlüssel mit
`python -c 'import secrets; print(secrets.token_urlsafe(48))'` erzeugen):

```dotenv
# Vorhandene COMPOSE_PROFILES beibehalten und whisper ergänzen, sofern nötig.
COMPOSE_PROFILES=whisper
WHISPER_URL=http://whisper:8091
WHISPER_TOKEN=<zufälliger Schlüssel mit mindestens 32 Zeichen>
WHISPER_JOBS=1
WHISPER_MODEL=large-v3-turbo
WHISPER_ALIGN=1
WHISPER_DIARIZE=1
WHISPER_CANCEL_URL=http://researcher:8000/guide/internal/whisper/cancel
WHISPER_TIMEOUT=21600
```

Nur der Researcher-Container ist im Whisper-Netz und hat `WHISPER_TOKEN`. Housekeeping und Teilnahme-Container
erreichen den Dienst über seine internen Relais: `…/whisper/cancel` zum Löschen und `…/whisper/resume` (aus
`WHISPER_CANCEL_URL` abgeleitet) zum Wiederaufnehmen einer Transkription, deren Thread ein Neustart beendet hat. Ein
kurzer Verbindungsabriss beim Abfragen eines Auftrags (bis 10 Minuten) gilt nicht als Fehler.

`WHISPER_MODELS_DIR` kann den Host-Modellpfad überschreiben. `WHISPER_PATH` wird
bei `WHISPER_JOBS=1` nicht verwendet. Für `small` als Standard nur `WHISPER_MODEL=small`
setzen; fehlt die Zeile, gilt `large-v3-turbo`. Zum Abschalten `WHISPER_JOBS=0` und die frühere `WHISPER_URL` wiederherstellen
oder leeren, `whisper` aus `COMPOSE_PROFILES` entfernen und den Dienst stoppen. Das Queue-Volume nur nach dem Löschen
verbliebener Forschungsdaten bzw. bewusstem Verwerfen offener Aufträge entfernen.

Vor dem regulären Restart: Compose mit den **aktiven Isolation-Overlays** prüfen,
den Dienst separat starten, Healthcheck abwarten und eine Testdatei durch die
vollständige Pipeline schicken. Danach erst den Forschenden-Container mit den
neuen Einstellungen starten. Das normale `restart.sh` und `compose-env.sh`
verwenden; Isolation-Overlays nicht übergehen. Die Vorbereitung aktiviert nichts.
Hintergrundbereinigung und Löschcode-Anfragen verwenden den internen Löschrelay
des Forschenden-Containers mit einem aus `SECRET_KEY` abgeleiteten Schlüssel
(`SECRET_KEY` mindestens 32 Zeichen; der Rechenknecht hat ihn nicht), von außen
antwortet nginx mit 404 (Abgleich 2026-10-15, NB-9). Dieser erlaubt nur das Verwerfen vorhandener Aufträge, keinen Lesezugriff
auf Audio oder Ergebnisse. Der Whisper-Schlüssel bleibt ausschließlich im
Forschenden- und Whisper-Container. Bei fehlendem Relay-Zugang gelten die Ablaufzeiten.

## API und Prüfumfang

### Queue, Speicherfehler und Restzeit

Abgeschlossene Transkription, Alignment und Sprecherzuordnung werden je Auftrag
atomar als JSON-Zwischenstand (höchstens 16 MiB, Dateirechte 0600) im privaten
Spool gesichert. Derselbe Auftrag setzt nach Worker-/Container-Neustart oder
Hostdruck ab der letzten abgeschlossenen Phase fort; Audio wird erneut dekodiert.
Auftragsfingerprint (Audio und Optionen), Modellmanifest, Bibliotheksversionen,
Threadzahl und Zwischenstandsformat müssen übereinstimmen. Beschädigte oder
inkompatible Zwischenstände werden neu berechnet; es gibt keine Wiederverwendung
zwischen verschiedenen Aufträgen. Der Modell-Cache enthält weiterhin keine
Transkripte.

Ein allgemeiner Verarbeitungsfehler mit vorhandenem Zwischenstand wird einmal
automatisch wiederholt. Speicher-, Dauer- und Zeitlimits werden nicht erneut
versucht. Danach ist der Fehler endgültig. Zwischenstände einschließlich
temporärer Schreibdateien werden bei Abschluss, endgültigem Fehler, Abbruch
oder Ablauf zusammen mit dem temporären Audio gelöscht (offene Aufträge maximal
24 Stunden, Bereinigung bei laufendem Dienst oder nächstem Start). Ein neuer
Auftrag nach endgültigem Fehler startet ohne den gelöschten Zwischenstand.

Neue Aufträge wecken den Worker unmittelbar nach dem erfolgreichen Queue-Commit.
Die bisherige Leerlaufpause von bis zu 15 Sekunden entfällt; bei Hostdruck bleibt
die Pause bestehen, damit neue Uploads keine dauernde Prüf-Schleife auslösen.
Die eigentliche Modelllade- und Rechenzeit wird dadurch nicht verkürzt.

Der Worker unterscheidet gemeldete Allokationsfehler und vom cgroup-Zähler
bestätigte OOM-Abbrüche von anderen Prozessfehlern. Die App empfiehlt dann das
kleinere Modell `small` oder kürzere Aufnahmeteile. Ein SIGKILL allein ist kein
Nachweis für Speichermangel. Audio wird auch bei diesen Fehlern entfernt;
bereits abgeschlossene Aufnahmeteile bleiben bei gleicher Modellwahl erhalten.
Die laufende Fortschrittsanzeige zeigt den Hinweis in der aktuellen Seitensprache.

Die private Job-Antwort enthält optional eine grobe Restzeitschätzung für den
aktuellen Aufnahmeteil einschließlich aller davor wartenden/laufenden Aufträge.
Fehlt bei einem davon die Audiodauer, wird keine Gesamtzeit erfunden. `ffprobe`
liest verfügbare Dauerangaben mit drei Sekunden Zeitlimit; bei WebM ohne diese
Angabe ergänzt die tatsächliche Dekodierung die Dauer. Die Anzeige nennt einen
breiten Bereich (60–180 %), wenige Vergleichswerte oder eine überschrittene
Schätzung ausdrücklich. Weitere Aufnahmeteile sind nicht eingeschlossen.

Laufzeiten werden zusätzlich getrennt für Transkription, Alignment und
Sprecherzuordnung als geglätteter Sekunden-pro-Audiosekunde-Wert je Kombination
aus Modell, Sprache, Alignment, Sprecherzuordnung und Threadzahl zusammengefasst.
Die Restzeit summiert ausschließlich die noch offenen Phasen und zieht nur
die bereits verstrichene Zeit der aktuell laufenden Phase ab. Lade-/Dekodierzeit
bleibt eine grobe Pauschale. Unterbrechungen und Queue-Wartezeiten fließen nicht
in die Phasenmessungen ein. Vor ausreichenden Messungen gelten grobe Vorgaben;
bisherige Gesamtlaufzeitwerte bleiben als Übergang für alte Aufträge verwendbar.
Diese Werte enthalten weder Auftragskennungen noch Prompts, Audio oder Transkripte
und verfallen nach sieben Tagen ohne neue Messung. Es gibt keine eigene Historie
einzelner Interviews. Erst ab drei vergleichbaren Läufen entfällt der Hinweis
auf wenige Vergleichswerte. Die öffentliche Statusantwort bleibt unverändert.

Die Erweiterung benötigt das separat gebaute und geprüfte Image
`askurious-whisperx:3.8.6-phase1`. Die Bibliotheksversionen und Modelle bleiben
unverändert; die oben dokumentierten Paketbefunde gelten weiterhin.

Die Phasen-Erweiterung wurde am 15.10.2026 separat mit synthetischem deutschem
Zwei-Stimmen-Audio (27,23 s, `small`, vier CPUs / 4 GiB, Modell-Cache aktiv)
geprüft: absichtlicher Fehler nach dem Alignment, anschließend neuer Prozess.
Der zweite Prozess durfte weder ASR noch Alignment laden und schloss nur die
Sprecherzuordnung ab. Alle drei Phasen wurden als Laufzeitwerte erfasst und
sämtliche temporären Auftragsdateien anschließend gelöscht. Gesamtdauer beider
Versuche 143,67 s, cgroup-Peak 1,676 GiB, keine Speicherlimit-/OOM-Ereignisse.
Je App-Seite 16 begleitende Healthchecks ohne Fehler (Maxima 38,9 / 26,6 ms);
mindestens 11.590 MiB Hostspeicher verfügbar, Hostlast maximal 7,05. Der Test
stoppt bei Hostdruck oder echten Transkriptionsaufträgen; keine Forschungsaudios.
Geprüftes Image: `sha256:15e23ef529347a30d69e308d780f21fb1782ff9556100f34e394930503625766`.
Die fünf Laufzeitmodule wurden per SHA256 mit dem Arbeitsstand verglichen.

52 gezielte WhisperX-Tests bestanden; Linux-Prüfung der Bereiche Plattform,
Interview, Datenschutz und der drei Sprachprüfungen: 1.130 bestanden,
47 übersprungen, zwei auch auf dem unveränderten Hauptzweig `bf0efe16`
reproduzierte Fehler: Formatierungs-Zeitgrenze in
`test_formatting_is_quick_on_60_kb[lines]` und fehlende `compose-env.sh` im
isolierten Backup-Testaufbau von `test_concurrent_writer_and_real_backup_protocol`.
Der reale Modelltest prüft die Wiederaufnahme, keine Interview-Transkriptqualität.

Der isolierte Scheduler-Vergleich auf demselben Server ergab beim Einreichen
in die bereits wartende Queue **15,01 s** vor der Änderung und **0,19 s** danach
(einschließlich kurzer Metadatenprüfung, ohne eigentliche Modellberechnung).
Ein gesonderter Test mit absichtlich nur **512 MiB RAM** löste einen echten
cgroup-OOM-Abbruch aus: Fehlercode `memory_limit`, API weiter HTTP 200,
Worker weiter aktiv, temporäres Audio entfernt. Die regulären **4 GiB** wurden
dafür nicht geändert.

### Längere Aufnahmen und Integration (15.10.2026)

Synthetisches deutsches Audio mit zwei Stimmen, aus derselben kurzen Testaufnahme
wiederholt, jeweils vollständige Transkription, Wortzeitmarken und Sprecherzuordnung:

| Modell | Audiodauer | Rechenzeit | Container-Peak | Sprecherlabels |
|---|---:|---:|---:|---:|
| `small` | 10 min | 2.391,11 s (39:51 min) | 1,895 GiB | 2 |
| `large-v3-turbo` | 30 min | 8.464,17 s (2:21:04 h) | 2,660 GiB | 2 |

Beide Läufe blieben bei vier CPUs und 4 GiB ohne zusätzlichen Swap; keine
Speicherlimit- oder OOM-Ereignisse. Audio und Ergebnisse wurden nach der Prüfung
entfernt. Der Zehn-Minuten-Lauf ist die Referenz im bisherigen Cache-Image;
der Dreißig-Minuten-Lauf verwendet die neue Pipeline. Modelle und Bibliotheken
sind unverändert. Pipeline, Worker, Queue und Settings des Turbo-Testimages und
des finalen Images wurden per SHA256 als bytegleich geprüft; lediglich die
separat getestete Überschreitungsgrenze der Restzeitschätzung wurde nachgezogen.
Finales Image: `sha256:f65defa8f410f7a1d10ed5c1d696723d09d1d9b5c83cae90bb04b6f4caf73960`.

Beim Small-Lauf waren alle 233 Healthchecks je App-Seite erfolgreich (Median
19,2/18,2 ms, Maximum 55,7/96,3 ms). Beim Turbo-Lauf gab es 817 Proben je Seite:
fünf fehlgeschlagene Forschenden-Proben und eine fehlgeschlagene Teilnahme-Probe.
Diese konzentrieren sich auf 22:43–22:52 UTC rund um den parallelen App-Deploy
`8adadd96` (Deployment-Zeitstempel 22:47 UTC); die genaue Ursache jeder Probe ist
daraus nicht ableitbar. Erfolgreiche Proben: Median 19,5/18,3 ms, Maximum
91,5/58,4 ms. Der Hostschutz musste den Test nicht abbrechen.

Die Langzeittests zeigen die Laufzeit und den Speicherbedarf dieser synthetischen
Aufnahmen, keine Qualitäts- oder Kapazitätsgarantie für beliebige reale Interviews.
Die Sprecherzuordnung beansprucht den größten Teil der Rechenzeit. Die aktuelle
Fassung einschließlich der inzwischen gemergten Hauptzweigänderungen bestand
unter Linux mit **1.499 Tests**, 54 ausgelassen; ausschließlich der bekannte,
unveränderte Formatierungs-Zeitgrenzentest blieb fehlgeschlagen. Zusätzlich wurden
die vollständigen Interview- und drei Sprachtestdateien mit **232 Tests** geprüft,
einschließlich der sofortigen deutschen/englischen Speicherwarnung. Der reguläre
Deploy verwendet wegen des dokumentierten Zeitgrenzentests `SKIP_TESTS=1`; diese
Ausnahme wird in `deployed_at.txt` und Administration > Server ausgewiesen.
OSV-, Hauptzweig-, Migrations-, Rolling- und Isolationsprüfungen bleiben aktiv.
Testnachweise: `checks/queue-validation-summary.json`, `queue-merged-tests.log`,
`queue-image-parity.json` und `queue-live-estimate-check.json` im oben genannten
Vorbereitungsverzeichnis.

Cache-/Fortschrittserweiterung vom 14.10.2026, synthetische 27,23-Sekunden-Aufnahme,
jeweils Transkription + Alignment + Sprecherzuordnung:

| Modell | Kalter Rechenprozess | Folgeauftrag mit wiederverwendetem Kontext |
|---|---:|---:|
| `small` | 108,68 s | 103,56 s |
| `large-v3-turbo` | 145,76 s | 140,71 s |

Beide Läufe je Modell lieferten zwei Sprecherlabels. Peak des Testcontainers:
**3,49 GiB**, keine Speicherlimit- oder OOM-Ereignisse, kein Swap. Queue-Position,
Abbruch und erneuter Start wurden geprüft. Das sind Einzelmessungen kurzer
synthetischer Aufnahmen, keine Zusage für lange reale Interviews. Die vollen
Interview-/Plattform- und Übersetzungstests bestanden mit **964 Tests**, 45
ausgelassen; ausschließlich der bekannte Baseline-Formatierungs-Zeitgrenzentest
blieb fehlgeschlagen. Zusätzliche gezielte Tests prüfen Modellwechsel, Zugriff,
Promptrücksetzung, Gewichtsfreigabe, Cache-Lebensdauer, Abbruch und veraltete
Ergebnisse nach einem Neustart.

Die öffentliche Statusseite zeigt WhisperX als deaktiviert, betriebsbereit,
beschäftigt oder nicht erreichbar. Wartende und laufende Aufträge erscheinen als
Anzahlen; Beschäftigung zählt nicht als Ausfall. Deaktivierte Messungen zählen
nicht zur Verfügbarkeit. Der Statuscontainer fragt dafür nur den internen
App-Relay `/healthz/whisper` ab; nginx sperrt diesen Pfad nach außen. Er bekommt
weder den Whisper-Schlüssel noch Zugriff auf das private Whisper-Netz.
`GET /v1/status` erfordert den Bearer-Schlüssel und liefert ausschließlich
Betriebszustand und Queue-Zähler. Kennungen, Audio und Transkripte werden nicht
übernommen. Die bestehende Statushistorie hält diese Messungen sieben Tage vor
und zeigt 24 Stunden. Unter Administration > Server erscheint der aktivierte
Whisper-Container mit CPU-Nutzung gegen sein Kern-Limit und RAM-Nutzung
gegen sein 4-GiB-Limit. Ein gesonderter `CONTAINER_SERVICES`-Filter muss `whisper`
enthalten, wenn die Installation einen solchen Filter verwendet.

Alle Daten-Endpunkte erfordern `Authorization: Bearer <WHISPER_TOKEN>`.
`GET /healthz` prüft den Queue-Worker. `POST /v1/jobs` akzeptiert Multipart-Felder
`file`, `model`, `language`, `prompt`, `align`, `diarize` und liefert `202 {id}`.
`GET /v1/jobs/{id}` liefert Status und bei `done` das Ergebnis.
`DELETE /v1/jobs/{id}` verwirft den Auftrag und seine temporären Daten.
Eine synchrone Kompatibilitätsroute `/v1/audio/transcriptions` ist vorhanden;
für Askurious ist das Job-Protokoll wegen Wiederaufnahme und Abbruch vorzuziehen.
Höchstens zwei Upload-/Kompatibilitätsanfragen gleichzeitig lassen API-Threads
für Status, Health und Abbruch frei. Uploads werden gestreamt, nicht vollständig
in den Speicher des App-Workers gelesen.

Die Servermessung vom 14.10.2026 mit synthetischen 27,23 s deutschem Audio ergab
für `small` einschließlich Alignment und Sprecherzuordnung 155,6 s mit 2 CPUs
und 112,1 s mit 4 CPUs. Turbo benötigte für Text+Alignment 53,0 s mit 4 CPUs;
die modellunabhängige Sprecherzuordnung kam zusätzlich hinzu. Die Messung
belegt weder die Qualität realer Interviews noch den RAM-Bedarf langer Dateien.
Längere reale Aufnahmen müssen vor dem Regelbetrieb innerhalb dieser Grenzen
getestet werden; der Container darf bei Speichermangel nur seinen Auftrag verlieren.

Der vollständige Offline-Test des fertigen Images am 14.10.2026 bestand für beide
Modelle: `small` **110,33 s**, `large-v3-turbo` **140,39 s**, jeweils einschließlich
Alignment und Sprecherzuordnung. Beide lieferten zwei Sprecherlabels für dieselbe
synthetische Aufnahme. Peak des gesamten Containers **2,74 GiB**, keine
Speicherlimit- oder OOM-Ereignisse. Alle **106** App-Healthchecks waren erfolgreich:
Forschenden-Seite median 19,78 ms / maximal 57,92 ms, Teilnahme median 18,83 ms /
maximal 27,65 ms. Audio wurde nach Abschluss entfernt, das Verwerfen der Ergebnisse
per API geprüft. Der Test lief mit `--network=none`, schreibgeschütztem Root-Dateisystem
und den oben genannten CPU-/Speichergrenzen. Der Testcontainer wurde entfernt.

Die vorbereiteten Serverartefakte liegen getrennt vom produktiven Stack:

- Image mit Status-API: `askurious-whisperx:status-preparation-20261014`
- Ursprünglich für die Modellmessung verwendetes Image: `askurious-whisperx:preparation-20261014`
- Modelle: `~/whisperx-service-preparation-20261014/models` (2,8 GiB)
- Testnachweise: `~/whisperx-service-preparation-20261014/checks`

Bei einer späteren Aktivierung können diese geprüften Modelle über
`WHISPER_MODELS_DIR` verwendet werden. Die Cache-Erweiterung erfordert ein separat
geprüftes Image `askurious-whisperx:3.8.6-cache1`; das frühere Status-Image darf dafür
nicht lediglich umgetaggt werden. Das ursprüngliche Image als `askurious-whisperx:3.8.6`
getaggt werden. Zuvor muss auch die vorbereitete App-Anbindung regulär übernommen
und gebaut werden. Weder Server-`.env` noch laufender App-Stack wurden hierfür geändert.
Die endgültige Compose-Konfiguration wurde zusätzlich als eigener Test-Stack
gestartet: UID 1000, vier CPUs, 4 GiB RAM, null Swap, 256 Prozesse, OOM-Score 500,
schreibgeschütztes Root-Dateisystem, beschreibbarer privater Upload-Spool und
gesperrter Internetzugang wurden im Container geprüft. Sein Netzwerk und sein
leeres Queue-Volume wurden anschließend entfernt.

App-Validierung: **888 Tests bestanden**, 43 wegen fehlender optionaler Dienste
oder als langsame Tests ausgelassen. Ein vorhandener Formatierungs-Performancetest
(`test_formatting_is_quick_on_60_kb[lines]`, Grenze 200 ms) scheitert auf dieser VM:
unveränderter Ausgangscommit `786ce78b` 341 ms, vorbereitete Fassung separat 346 ms.
Die Formatierungsfunktion wurde nicht geändert. Der Gesamtstatus ist daher nicht
vollständig grün; alle 16 neuen Whisper-Tests und die vollständigen Modelltests bestehen.
Zusätzlich bestehen 57 gezielte Tests für die neue Anbindung und bestehende
Interviewabläufe unter Windows. Der breite Windows-Lauf wurde wegen hängender
Backup-/Rollback-Testhelfer beendet; die Linux-Wiederholung stellte die von Windows
tar verlorenen Git-Ausführungsrechte wieder her und setzte `BACKUP_DAYS=0` für die Tests.

Status-Erweiterung: **86 gezielte Tests bestanden** unter Windows, einschließlich
deutscher/englischer Oberfläche. Die vollständigen Interview-/Plattformtests und
die drei Übersetzungstests im begrenzten Linux-Testcontainer ergeben **952
bestanden, 44 ausgelassen**, mit ausschließlich dem oben beschriebenen vorhandenen
Formatierungs-Performancetest als Fehler. Das Status-Image baut direkt auf dem
bereits mit beiden Modellen geprüften Image auf und ersetzt nur `api.py` und
`queue.py`; Laufzeitbibliotheken und Modelle bleiben dieselben. Der API-Test im
fertigen Image ohne Netzwerk bestand: Authentifizierung erforderlich, korrekte
Queue-Zähler und keine Auftragsinhalte. Image-ID:
`sha256:6e99bb0e3f4d0e7efb6e5b357b5b061dc72f9d30497d9eadbc538f7796799efd`.
