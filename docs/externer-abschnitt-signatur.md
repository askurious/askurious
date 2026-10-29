# Externer Abschnitt: Rückgabewerte signieren (für Entwickler einer Web-App)

Ein Beispiel zum Ausprobieren ist die Wetter-Demo (ganz unten).

Ein externer Abschnitt schickt die teilnehmende Person zu einer Web-App und erwartet sie über den Rückkehrlink
`{{return}}` zurück. Die Web-App hängt die deklarierten Rückgabewerte an (`&score=17&label=gut%20gemacht`). Ist in
Askurious „Rückgabewerte signieren“ eingeschaltet, speichert Askurious die Werte nur mit gültiger Signatur. Den Schlüssel
sehen die Admins der Befragung im Ablauf beim Abschnitt.

## Signatur, Version 2 (ab 2026-10-07)

Die Web-App hängt zusätzlich an:

    &sig_v=2&sig=<hex>

`sig` ist HMAC-SHA256 mit dem Schlüssel (als UTF-8-Text) über diesen Text, hexadezimal (Groß- und Kleinschreibung
zählen nicht):

    sig_v=2&t=<Token>&<name1>=<wert1>&<name2>=<wert2>…

- `<Token>` ist der Wert des Parameters `t` im Rückkehrlink.
- Danach folgen die deklarierten Rückgabewerte, die die Web-App anhängt, in der Reihenfolge der Liste im Ablauf.
  Werte, die sie nicht anhängt, fehlen auch hier. Ein leerer angehängter Wert steht als `name=` darin.
- Token und Werte sind prozentkodiert nach RFC 3986: Jedes Byte der UTF-8-Form außer `A–Z a–z 0–9 - . _ ~` wird zu
  `%XX` (Großbuchstaben). Ein Leerzeichen ist `%20`, nicht `+`. So kann kein Wert ein `&name=` enthalten, das Text in
  einen anderen Wert verschiebt.
- Berechnet wird die Signatur auf dem Server der Web-App, nie im Browser. Sonst ist der Schlüssel nicht geheim.

Wie die Werte in der Adresse selbst kodiert sind (`+` oder `%20` für ein Leerzeichen), spielt keine Rolle: Askurious
liest sie URL-dekodiert und kodiert sie für die Prüfung neu.

### Python

```python
import hashlib, hmac
from urllib.parse import parse_qs, quote, urlsplit

def signed_return(return_link, key, values):
    """values: [(name, value)] in der Reihenfolge der Deklaration."""
    t = parse_qs(urlsplit(return_link).query)["t"][0]
    text = "sig_v=2&t=" + quote(t, safe="") + "".join(f"&{n}={quote(str(v), safe='')}" for n, v in values)
    sig = hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
    query = "".join(f"&{n}={quote(str(v), safe='')}" for n, v in values)
    return f"{return_link}{query}&sig_v=2&sig={sig}"
```

### JavaScript (Node.js)

```js
const crypto = require("crypto");
// encodeURIComponent lässt ! ' ( ) * stehen; RFC 3986 kodiert sie
const enc = (v) => encodeURIComponent(String(v)).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());

function signedReturn(returnLink, key, values) { // values: [[name, value], ...]
  const t = new URL(returnLink).searchParams.get("t");
  const pairs = values.map(([n, v]) => `&${n}=${enc(v)}`).join("");
  const sig = crypto.createHmac("sha256", key).update(`sig_v=2&t=${enc(t)}${pairs}`, "utf8").digest("hex");
  return `${returnLink}${pairs}&sig_v=2&sig=${sig}`;
}
```

### PHP

```php
function signed_return($return_link, $key, $values) { // $values: [[name, value], ...]
    parse_str(parse_url($return_link, PHP_URL_QUERY), $q);
    $pairs = "";
    foreach ($values as [$n, $v]) { $pairs .= "&" . $n . "=" . rawurlencode((string)$v); }
    $sig = hash_hmac("sha256", "sig_v=2&t=" . rawurlencode($q["t"]) . $pairs, $key);
    return $return_link . $pairs . "&sig_v=2&sig=" . $sig;
}
```

## Version 1 (Übergang bis 31.03.2027)

Bis zum 2026-10-07 lautete der signierte Text `t=<Token>&name1=wert1&name2=wert2` mit den Werten unkodiert, und
`sig_v` fehlte. Diese Form war mehrdeutig: `a="x&b=5", b="3"` und `a="x", b="5&b=3"` ergaben denselben Text. Askurious
nimmt sie noch bis einschließlich 31.03.2027 an (`V1_UNTIL` in `app/external.py`), damit bestehende Web-Apps
umstellen können, und nur für Schlüssel, die vor dem 29.10.2026 erzeugt wurden. Ein Schlüssel, der seitdem erzeugt
wurde (neuer Abschnitt, „Neuen Schlüssel erzeugen“, Kopie einer Befragung), nimmt nur Version 2 an. Danach gelten Rückgaben ohne `sig_v=2` als nicht signiert: Die Werte werden verworfen, die Rückkehr
zählt, und der Export zeigt `<code>_sig` = 0.

## Beispiel: Wetter-Demo

Askurious bringt eine kleine Web-App mit, die einen externen Abschnitt vorführt (`app/demo_webapp.py`): Die Person nennt
eine Stadt, die App fragt bei Open-Meteo (frei, ohne Schlüssel) die aktuelle Temperatur ab, zeigt sie an und schickt
Stadt und Temperatur über den Rückkehrlink an die Befragung zurück.

So einrichten, im Reiter „Ablauf“ einen externen Abschnitt hinzufügen:

- Adresse: `https://ask.queryous.de/demo/weather?lang={{lang}}&return={{return}}`
  (auf einem Server ohne eigene Teilnahmeadresse dessen Hauptadresse, z. B. `https://queryous.de/demo/weather?…`)
- Rückgabewerte: `city` (Text) und `temperature` (Zahl)
- „Rückgabewerte signieren“ aus: Die Demo kennt den Schlüssel eines Abschnitts nicht und signiert deshalb nicht.

Die Demo leitet nur zu Rückkehrlinks derselben Installation zurück (`…/s/<Code>/back?t=…`), nie zu einer anderen
Adresse. Ohne Rückkehrlink aufgerufen, funktioniert sie zum Ausprobieren genauso, schickt aber nichts zurück.
Sie fragt Open-Meteo sparsam: Jede Stadt bleibt 10 Minuten im Speicher, je Absender gehen 20 neue Städte in
10 Minuten, alle zusammen 600; darüber meldet die Seite, dass der Wetterdienst nicht erreichbar ist.

