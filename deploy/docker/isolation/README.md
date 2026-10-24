# DP-15: Datenbankrechte und Docker-Netze

Stand: 2026-10-14. **Auf askurious.de aktiviert und live abgenommen.** Laufzeitrolle `askurious_run`, separate
Migrationsjobs und Zielnetze sind produktiv aktiv. Ein bestehender Server ohne Isolation benötigt weiterhin die
folgende Bereitstellung und Abnahme; ohne Opt-in bleibt dort der bisherige Betrieb erhalten, aber nie mehr als
Superuser: `compose.yaml` bricht ohne `DB_APP_USER`/`DB_APP_PASSWORD` ab, und `restart.sh` verweigert den Start, wenn
eine Rolle aus einer `DATABASE_URL` fehlt, Superuser ist oder Rollen oder Datenbanken anlegen, replizieren oder RLS
umgehen darf. Ein **neuer Server** ist von Anfang an isoliert (Abschnitt „Neuer Server“).

## Neuer Server

`NEW_ENV=1 deploy/docker/deploy-docker.sh` schreibt in die neue `~/askurious-env/.env` neben den Schlüsseln
`DB_ISOLATION=1`, die relative `COMPOSE_FILE`-Auswahl und je eine Rolle mit zufälligem Passwort:
`DB_APP_USER=askurious_app` (Eigentümerin, nur im Migrationsjob), `DB_RUN_USER=askurious_run` (Laufzeit) und
`PARTICIPANT_DB_USER=askurious_participant` (Teilnahme-Container, Rechte aus `app/participant_grants.py`).
`POSTGRES_PASSWORD` dient nur dem ersten Start des Datenbank-Images. Der erste `restart.sh` erkennt die neue Installation
(keine Teilnahme-Container, keine Datenbankversion, kein `~/askurious-env/isolation-db-*.json`), startet die Datenbank
mit `compose.yaml` allein wie einen Server von vor der Isolation und richtet auf dem noch leeren Schema mit
`db-app-role.sh --new-install` die Rollen ein (Laufzeitrolle über `runtime-role.sql`; Passwörter nur über stdin). Dabei
verlieren `postgres` und `template1` CONNECT/TEMP für PUBLIC, und der Superuser `askurious` verliert sein
Netzwerkpasswort (wie `db-app-role.sh --lock`). Danach läuft der Deploy wie jeder isolierte: Netze, Migrationsjob,
Rechteprüfung, Forschende-App, Teilnahme-Container nacheinander, `finalize`. Voraussetzungen wie unten: Host-Python 3 und
Docker Compose >= 2.24.4. Ohne eigene Teilnahmerolle: die beiden `PARTICIPANT_DB_*`-Zeilen vor dem ersten Start löschen.

## Rollen

| Rolle | Einsatz | Rechte |
| --- | --- | --- |
| `askurious` | lokale Administration im DB-Container, Sicherung und Wiederherstellung | bestehender Superuser; Netzwerkpasswort bleibt gesperrt |
| `askurious_app` | kurzlebiger Migrationscontainer | bestehende Eigentümerin von Datenbank, Schema und Objekten |
| `askurious_run` | Forschende, Teilnehmende, Haushaltung und deren Unterprozesse | CONNECT, Schema-USAGE, SELECT/INSERT/UPDATE/DELETE, Sequenz-USAGE/SELECT; kein CREATE, TRUNCATE, Rollenrecht oder Eigentum |
| `askurious_participant` | vorbereitet, aus (CFG-04): nur die Teilnahme-Container, sobald `PARTICIPANT_DB_USER`/`PARTICIPANT_DB_PASSWORD` in der `.env` stehen | CONNECT, Schema-USAGE, je Tabelle nur die Rechte aus `app/participant_grants.py`, Sequenz-USAGE/SELECT nur für deren Einfügetabellen; keine Default Privileges; der Migrationsjob gibt sie über `PARTICIPANT_ROLE` nach jeder Migration neu (BETRIEB.md, „Eigene Datenbankrolle der Teilnahmeseite“) |

`runtime-role.sql` legt die Laufzeitrolle zunächst **ohne Anmeldung** an. Sie verweigert die Ausführung bei
unerwarteter Datenbank/Eigentümerschaft oder schon vorhandener Laufzeitrolle, statt fremde Rechte zu überschreiben.
Vor Ausführung alle bestehenden Rollen, PUBLIC-Rechte, Verbindungen und Marken prüfen: Der Entzug von PUBLIC-Rechten
auf `askurious` betrifft auch weitere Nutzer. Benötigte fremde Zugänge vorher ausdrücklich berechtigen.
`postgres` und jede Marken-Datenbank getrennt prüfen; keine clusterweite PUBLIC-Sperre ohne Bestandsaufnahme.
Verbindungen der Laufzeitrolle dorthin müssen bei der Bereitstellung separat gesperrt werden; `runtime-role.sql`
ändert den standardmäßigen PUBLIC-Zugang zu `postgres` nicht. Auf askurious.de wurde er zusätzlich entzogen.
Temporäre Tabellen werden durch den Entzug von PUBLIC-TEMP ebenfalls
unterbunden. Funktionen und Trigger dürfen keine zusätzlichen Eigentümerrechte über SECURITY DEFINER vermitteln.

Passwort erst nach Prüfung zufällig erzeugen, per stdin an PostgreSQL übergeben und als `DB_RUN_USER=askurious_run`
und `DB_RUN_PASSWORD` in der geschützten `~/askurious-env/.env` speichern; niemals ausgeben, in Befehlsargumente
schreiben oder einchecken. Dann LOGIN gewähren. `DB_APP_*` bleiben ausschließlich beim Migrationsjob.
**Nicht erneut `db-app-role.sh` zum Umschalten aufrufen:** Das bisherige Skript richtet eine Eigentümerrolle ein.

`roles.yaml` ersetzt die DATABASE_URL der vier Hauptanwendungsdienste und setzt `DATABASE_SCHEMA_MODE=check`;
die Marken-App übernimmt ihren Laufzeitzugang aus `.env.brand`, ebenfalls mit Schema-Prüfmodus.
Der Start prüft den exakten Alembic-Stand, ohne Migrationen oder den ergänzenden Linked-Index anzulegen. Datenpflege
bleibt erlaubt. Bei fehlendem/veraltetem Schema bricht der Start ab. Der separate Job `python -m app.schema_migrate`
führt Migrationen, den Linked-Index und die Prüfung der Revisionstrigger aus, ohne Web-App, Haushaltung oder
externe Dienste zu starten. Er bekommt nur den Eigentümerzugang und den Namen `RUNTIME_ROLE`, keine App-Schlüssel,
keine Uploads und keine Ports. Nach der Migration berechtigt er vorhandene und künftige Tabellen/Sequenzen und
entzieht wieder das Schreibrecht auf `alembic_version` (auch nach einem Restore ohne ACLs).

Neue Tabellen und Sequenzen des Migrationsbesitzers erhalten die DML-Rechte über Default Privileges. Bestehende
Objekte werden ausdrücklich berechtigt. Die Laufzeitrolle darf `alembic_version` nur lesen. Eine Migration, die diese
Tabelle ersetzt oder Objekte unter einer anderen Rolle erzeugt, muss die Rechte ausdrücklich wiederherstellen.

## Zielnetze

| Netz | Mitglieder |
| --- | --- |
| frontend | nginx, Haupt-App, Status |
| backend (intern) | DB, Haupt-App, Haushaltung, Migrationsjob |
| compute-net (intern) | Haupt-App, Haushaltung, Haupt-Rechenknecht |
| brand-frontend | nginx, Status, Marken-App |
| brand-backend (intern) | DB, Marken-App, Marken-Haushaltung |
| brand-compute-net (intern) | Marken-App, Marken-Haushaltung, Marken-Rechenknecht |
| outbound | Haupt-Haushaltung für nötige externe Aufrufe |
| brand-outbound | Marken-Haushaltung für nötige externe Aufrufe |
| certificate-outbound | certbot für Zertifikatserneuerung |

nginx und Status erreichen keine Datenbank; Rechenknechte erreichen nur ihre eigene App, keine DB und keine andere
Marke. Haupt- und Marken-App teilen kein Netz. Die gemeinsame PostgreSQL-Instanz hängt in beiden DB-Netzen;
die Datenbankrechte müssen die Marken deshalb zusätzlich trennen. Nur nginx veröffentlicht Ports.
Die nicht internen Netze erlauben weiterhin ORCID, Mail, KI, Karten, Webservices und Zertifikatserneuerung.
`status` hat keine direkten DB-/Compute-TCP-Prüfungen. App-HTTP-Checks bleiben; den Rechenknecht prüft die
Forschenden-App über einen signierten `/stand`-Aufruf mit Versionsprüfung. Die Statusseite fragt nur
`http://researcher:8000/healthz/compute` ab (200 gesund, 503 nicht gesund), ohne Auftragsdaten oder Schlüssel.
nginx sperrt diesen Prüfpfad öffentlich. Brand-Checks verwenden entsprechend die Marken-Forschenden-App;
keine TCP-Prüfungen in fremde Netze konfigurieren. Die Datenbank gesondert vom Host prüfen.

`networks.yaml` benötigt Docker Compose >= 2.24.4. `!override` ersetzt Netzmitgliedschaften, statt sie anzuhängen.
Quellen: [Docker-Merge-Regeln](https://docs.docker.com/reference/compose-file/merge/),
[PostgreSQL-Default-Privileges](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html).

## Aktivierung und Deploymentablauf

1. Aktuellen Compose-Stand, Rollen/ACLs, verwendete Datenbanken und Version aufnehmen, **ohne Geheimnisse auszugeben**.
   Vollständige Sicherung erstellen; Wiederherstellungsweg lokal prüfen. Externe Sicherung bleibt wie vereinbart
   zurückgestellt. Noch keine laufenden Container verändern.
2. In einem isolierten Compose-Projekt mit synthetischen Daten die Overrides rendern und testen. Kein `config`
   mit produktiven Passwörtern in Logs schreiben. Runtime: CRUD, Sequenzen, Revisionstrigger und ganze App starten;
   CREATE/ALTER/DROP/TRUNCATE, Markeränderung, SET ROLE und Zugriff auf andere Datenbanken müssen scheitern.
   Eine neue additive Migration muss als Owner laufen und danach unmittelbar unter Runtime funktionieren.
3. Rollen bereitstellen und prüfen. Bei aktiver Marke: eigener Owner und Runtime, `.env.brand` mit **Runtime**-URL;
   `.env.brand-migration` mit **nur** `DATABASE_URL=<Owner-URL>` und `RUNTIME_ROLE=<Runtime-Rolle>`, Modus 600 in
   `~/askurious-env`. Der Deploy verlinkt wie bisher `.env*`. Die Runtime darf keine Rolle erben und kein Eigentum
   besitzen. Haupt- und Marken-Datenbanken müssen verschieden sein. Ein neuer Server braucht diese Schritte nicht
   (Abschnitt „Neuer Server“). Kein stiller Superuser-/SQLite-Fallback.
4. Erst nach dieser Prüfung in der geschützten `.env` setzen:
   ```dotenv
   DB_ISOLATION=1
   COMPOSE_FILE=compose.yaml:isolation/roles.yaml:isolation/networks.yaml
   ```
   Relative Pfade bleiben über Deploy und Rollback gültig. Auch direkte Compose-Aufrufe im Docker-Ordner wählen so
   die Zielkonfiguration. Für Shell-Arbeiten `. ./compose-env.sh` laden. Benutzerdefinierte weitere Overrides und
   unvollständige Rollenkonfiguration werden verweigert; Host-Python 3 und Compose >= 2.24.4 werden benötigt.
5. `restart.sh` prüft Migrationen/Markenmigrationen, Rollen, Netzwerk-/Container-Projektlabels, DB-Image/-Konfiguration
   und das DB-Volume **vor** einer Umstellung. Eine normale Aktualisierung darf DB-Image/Konfiguration nicht ändern.
   Nach erfolgreicher Sicherung erzeugt Compose mit `create --no-recreate` die eigenen Netze. `transition.py prepare`
   verbindet die neuen Endpunkte zusätzlich an laufende Container; bisherige Endpunkte bleiben erhalten.
6. DB bleibt bei normalen Deploys mit `up --no-recreate` derselbe Container. Migrationsjobs `migrate` und ggf.
   `brand-migrate` laufen vor dem ersten App-Neustart. Eine erneute Rechteprüfung erfasst auch neue Tabellen.
   Jeder Fehler stoppt vor dem ersten Teilnehmerneustart. nginx nutzt vorübergehend `transition.yaml` mit beiden
   Frontends. A aus Routing, Neustart, interner Healthcheck **und HTTP aus nginx**, A zurück; **erst danach B**.
7. Nach dem erfolgreichen Neustart aller Dienste wechselt nginx auf das Zielnetz. `transition.py finalize` prüft
   tatsächliche Mitgliedschaften, laufende Dienste, die Runtime-Zugänge und HTTP-Zugriff aus nginx. Erst dann
   trennt es den alten DB-Endpunkt ohne DB-Neustart. Der geschützte Zustand
   `~/askurious-env/isolation-db-<Projekt>.json` enthält nur Konfigurations-/Image-Hashes, keine Zugangsdaten.
   Wiederholte Deploys gleichen DB-Konfiguration und Mitgliedschaften erneut ab. Negative Verbindungsprüfungen aus
   nginx/compute/Status und zwischen Marken gehören zusätzlich zur Live-Abnahme.
8. Runtime-Zugänge/Containerumgebung prüfen, alten Ownerzugang aus allen dauerhaften App-Containern entfernen.
   Das Privacy-Konzept erst nach der Abnahme als aktive Schutzmaßnahme aktualisieren. Keine Änderung der verarbeiteten Daten,
   Empfänger, Fristen, Teilnahmebedingungen oder Einwilligung durch diese Vorbereitung.

## Rückweg

Vor Entfernen des alten Netzes kann auf dessen geprüfte Mitgliedschaften zurückgegangen werden. Teilnehmer auch
beim Rückweg nacheinander und mit Upstream-/Health-Prüfung umstellen. Die bestehende `askurious_app` bleibt Owner;
ein kontrollierter Rückweg kann wieder deren Zugang mit Schema-Modus `auto` verwenden. Runtime erst löschen, wenn
keine Container/Verbindungen mehr darauf angewiesen sind; Default Privileges/ACLs dabei ausdrücklich bereinigen.
Keine pauschale Neuerstellung aller Container, kein ALL_AT_ONCE und kein Überspringen inkompatibler Migrationen.
Nach einer neuen Migration gelten zusätzlich die bestehenden Regeln für schema-kompatiblen Rollback und Restore.

`maintenance.sh` sichert zuerst und setzt nur für seinen Restart `ISOLATION_MAINTENANCE=1`. In diesem Wartungsfenster
darf PostgreSQL auf das aktualisierte Image wechseln; seine bisherigen und neuen Endpunkte bleiben bis zum Ende
erreichbar. Der DB-Neustart kann wie bisher kurz unterbrechen. Kein normaler Deploy darf diesen Schalter benutzen.
Ein Wartungsfehler stoppt ebenfalls vor weiteren Teilnehmerneustarts. Sicherung und Containerstatistik laden
dieselbe Compose-Auswahl; der Zustand bleibt außerhalb der ausgetauschten Codeordner.

Rollback behält die aktuelle Restart-/Netzpolitik und verweigert alte Images ohne `app.schema_migrate` **vor** dem
Ordner-/Imageaustausch. Ein Rückweg vor DP-15 braucht einen geplanten Rollen-/Netzrückbau; `rename-back` ist im
isolierten Modus gesperrt. Nach Restore: Eigentum beim jeweiligen Migrationsbesitzer erhalten (`pg_restore
--no-owner --role=<Owner>`), den zum Restore passenden Code ohne Start auswählen, Runtime-Rechte mit
`python -m app.schema_migrate --grants-only` im jeweiligen Migrationsjob reparieren, Löschungen wie im bestehenden
Betriebshandbuch nachziehen und Rechte/Marker prüfen, dann `restart.sh`. Kein Owner-Zugang in dauerhaften Runtime-Containern.

## Produktive Abnahme am 2026-10-14

Vollständige lokale Sicherung (Datenbank und Uploads) vor dem Eingriff erfolgreich; externe Sicherung bleibt
zurückgestellt. Rollen-/Konfigurationssicherung liegt mit Modus 600 außerhalb des Codeordners auf dem Server.
Aktuell keine aktive Marke; künftige Marken benötigen weiterhin eigene Rollen und die oben beschriebenen Zugänge.

`DB_ISOLATION=1` und die relative Compose-Auswahl sind aktiv. Die Datenbank blieb beim Übergang derselbe laufende
Container. Migrationsjob erfolgreich; A erst gesund ins Routing zurück, danach B. Alte DB-Netzverbindung erst nach
Abnahme entfernt. Alle dauerhaften DB-Clients verwenden `askurious_run` im Schema-Prüfmodus und enthalten keine
Migrations-/Superuser-Zugangsdaten. CRUD und Sequenzen erfolgreich; CREATE/ALTER/DROP/TRUNCATE, Markeränderungen,
SET ROLE sowie Zugriffe auf andere Datenbanken verweigert. PUBLIC-CONNECT/TEMP auf `postgres`, `template0` und
`template1` entzogen; die vorhandene lokale Superuser-Administration bleibt möglich.

Tatsächliche Netzmitgliedschaften und Healthchecks geprüft; nginx, Status und Rechenknecht erreichen die App,
aber keinen DB-Endpunkt. Öffentliche Health-/Statusendpunkte HTTP 200. Schema bleibt 0059. Bei weiteren Deploys
und Wartungen gelten weiterhin die Vorprüfungen und der sequenzielle Neustart.
