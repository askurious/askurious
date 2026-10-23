# Rechenknecht: Schätzungen auf einem zweiten Rechner

Die HB-Schätzungen (MaxDiff, CBC, ACBC) und die latenten Klassen lasten einen Prozessorkern 5 bis 14 Minuten lang voll aus (gemessen bei 2.000 Teilnahmen am 06.10.2026). Alles andere in Askurious dauert Sekunden. Ein zweiter Rechner, der „Rechenknecht“, kann diese Schätzungen übernehmen. Dasselbe gilt für die Bootstraps der Strukturgleichungsmodelle und die Omega-Intervalle der Ergebnisseite (rund 20 s bei 2.000 Teilnahmen), für die Schätzung des Strukturgleichungsmodells der Ergebnisseite und für die Modelle von „Längsschnitt rechnen“ (bei 1.500 verknüpften Personen 2 bis über 10 Minuten); sie gehen ohne jede Kennung hinaus, nur als Zahlen. Der Askurious-Server bleibt dann frei für Teilnehmende und Forschende.

Der Code ist vorbereitet (`app/compute.py`, `app/compute_node.py`). Solange in der `.env` des Servers kein `COMPUTE_URL` steht, ist er aus und alles läuft wie bisher.

## Wie es arbeitet

- Klickt jemand auf „HB schätzen“ oder „Segmente schätzen“, baut der Server die Eingabe wie bisher. Statt selbst zu rechnen, schickt er sie an den Rechenknecht.
- Die Teilnahmenummern ersetzt er vorher durch 1, 2, 3 …. Der Rechenknecht sieht nur die Wahlen, keine Nummern, keine Texte, keine IP. Er hat keinen Zugang zur Datenbank.
- Der Rechenknecht meldet Fortschritt und Ergebnis an `https://<Server>/rechenknecht/…` zurück. Der Server setzt die echten Nummern wieder ein und legt das Ergebnis ab wie bisher.
- Jede Nachricht ist mit `COMPUTE_SECRET` signiert (HMAC-SHA256) und gzip-gepackt. Nachrichten älter als 5 Minuten werden abgelehnt. Seit dem Audit vom 18.10.2026 (CFG-07) deckt die Signatur 2 auch Richtung (Anfrage oder Antwort, je mit eigenem, per HKDF abgeleitetem Schlüssel), Methode und eine Zufallsnummer (Nonce) ab; eine Antwort nennt die Nonce ihrer Anfrage, eine wiederholte Anfrage mit derselben Nonce wird abgelehnt. Die alte Signatur 1 wird während des Übergangs mitgeschickt und angenommen (`COMPUTE_SIGNATURE_V1`, Standard 1). Sobald Server und Rechenknecht diesen Stand haben, auf beiden `COMPUTE_SIGNATURE_V1=0` setzen.
- **Rückfall:** Ist der Rechenknecht nicht erreichbar oder hat er einen anderen Stand des Schätzcodes, rechnet der Server selbst. Das gilt auch, wenn er einen Auftrag verliert (Neustart) oder 4 Minuten lang nichts von sich hören lässt.
- Jeder Rechenprozess läuft mit niedrigster Priorität (nice 19). Andere Dienste auf dem Rechner haben Vorrang.
- Der Rechenknecht hält Aufträge nur im Speicher. Ein fertiges Ergebnis behält er höchstens eine Stunde, bis es abgegeben ist.
- Unter **Administration > Datenbank > Rechenknecht** steht, ob er erreichbar ist, ob der Stand passt und was dort läuft.

## Einrichten

### 1. Rechner
Ein kleiner Linux-Server (VPS) in der EU mit Auftragsverarbeitungsvertrag des Anbieters. Zwei bis vier **dedizierte** Kerne, 4 GB Arbeitsspeicher. Eine Schätzung braucht rund 350 MB und einen Kern. Python 3.11 oder neuer, pip, ein eigener Name mit HTTPS (z. B. `rechner.example.org`).

### 2. Code und Pakete (auf dem Rechenknecht)
```
sudo adduser --system --group askurious
sudo -u askurious git clone https://github.com/askurious/askurious.git /opt/askurious
cd /opt/askurious
sudo -u askurious python3 -m venv .venv
sudo -u askurious .venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock
```
Die Pakete kommen mit festen Versionen und Prüfsummen aus `requirements.lock` wie auf dem Server (Audit 2026-10-13, DP-17): ein verändertes Paket auf dem Weg wird abgelehnt.
Der Rechenknecht braucht denselben Stand wie der Server. Nach jedem Deploy des Servers auch hier `git pull` und den Dienst neu starten (Schritt 4). Hat er einen anderen Stand, rechnet der Server einfach selbst.

### 3. Schlüssel
Einen zufälligen Schlüssel erzeugen, z. B. `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Er kommt auf beide Rechner und sonst nirgends hin.

### 4. Dienst (systemd)
`/etc/systemd/system/askurious-rechenknecht.service`:
```
[Unit]
Description=Askurious Rechenknecht
After=network-online.target

[Service]
User=askurious
WorkingDirectory=/opt/askurious
# der Schlüssel in einer Datei nur für root lesbar, nicht als Environment= in der Unit (die ist für alle lesbar,
# systemctl show zeigt sie jedem Benutzer; Audit 2026-10-18, CFG-07)
EnvironmentFile=/etc/askurious-rechenknecht.env
ExecStart=/opt/askurious/.venv/bin/python -m app.compute_node
Restart=always

[Install]
WantedBy=multi-user.target
```
`/etc/askurious-rechenknecht.env` (Rechte 600, Eigentümer root; systemd liest sie vor dem Wechsel zum Benutzer):
```
COMPUTE_SECRET=<Schlüssel>
COMPUTE_CALLBACK=https://<Askurious-Server>/rechenknecht
COMPUTE_BIND=127.0.0.1:8090
# COMPUTE_PARALLEL=3      (Standard: Kerne minus 1)
# COMPUTE_SIGNATURE_V1=0  (sobald der Server den Stand mit Signatur 2 hat)
```
```
sudo install -m 600 -o root -g root /dev/null /etc/askurious-rechenknecht.env   # dann mit einem Editor füllen
sudo systemctl daemon-reload
sudo systemctl enable --now askurious-rechenknecht
```
Statt des eingebauten Servers von werkzeug geht auch gunicorn (wie im Docker-Betrieb, `deploy/docker/compose.yaml`): `pip install gunicorn` in die venv und `ExecStart=/opt/askurious/.venv/bin/gunicorn --workers 1 --worker-class gthread --threads 8 --bind 127.0.0.1:8090 "app.compute_node:create()"`. Immer nur **ein** Worker: die Aufträge liegen in seinem Speicher. Vor beiden steht nginx mit HTTPS (Schritt 5).

### 5. HTTPS davor (nginx)
```
server {
    listen 443 ssl;
    server_name rechner.example.org;
    # ssl_certificate … (z. B. certbot --nginx)
    client_max_body_size 128m;
    location / {
        allow <IP des Askurious-Servers>;
        deny all;
        proxy_pass http://127.0.0.1:8090;
        proxy_read_timeout 120s;
    }
}
```
Die Sperre auf die IP des Askurious-Servers ist ein zweiter Riegel neben der Signatur.

### 6. Askurious-Server einschalten
In die `.env` des Servers:
```
COMPUTE_URL=https://rechner.example.org
COMPUTE_SECRET=<derselbe Schlüssel>
```
Danach die App neu starten (Plesk: Neustart). Unter Administration > Datenbank sollte „Erreichbar: ja“ und „Programmstand: gleich wie hier“ stehen.

**Ausschalten:** die beiden Zeilen aus der `.env` nehmen und neu starten. Laufende Aufträge holt der Server noch ab, solange der Rechenknecht läuft. Sonst rechnet er sie nach spätestens 4 Minuten selbst.

## Datenschutz
- Der Rechenknecht verarbeitet pseudonymisierte Antworten (Wahlen ohne Kennung). Das sind trotzdem personenbezogene Daten, weil der Server sie zuordnen kann. Der Anbieter muss deshalb ins Verzeichnis der Verarbeitungstätigkeiten, und es braucht einen AV-Vertrag.
- Eingaben schreibt der Rechenknecht nicht auf die Platte. Sein Protokoll enthält nur Auftragsnummer, Art und Dauer.
