// Map with markers (items/map_marker.py), on participant pages, in previews and in the element editor.
// Leaflet is bundled in static/vendor/leaflet. Participants place markers by click/tap (or "marker in the
// centre" without a mouse), drag them, and remove them in the list below the map; each list entry holds
// the follow-up questions for its marker. The hidden input carries [{lat, lon, q: {id: value}}] as JSON.
// Place search (Nominatim): only on explicit submit, no autocomplete, at most one request per second.
(function () {
  if (window.AskuriousMap) { window.AskuriousMap.init(); return; }  // loaded once per map on the page
  // the map is drawn before participant.js (and its window.uiT) is loaded: the same lookup here, in the page's
  // language and with the values filled in (UI/UX audit 2026-10-05, V33)
  function uiT(de, en, values) {
    if (window.uiT) return window.uiT(de, en, values);
    var lang = document.documentElement.lang || "de", t = {};
    try { t = JSON.parse((document.body && document.body.getAttribute("data-ui")) || "{}"); } catch (e) { t = {}; }
    var text = t[en] || (lang === "de" ? de : en);
    Object.keys(values || {}).forEach(function (k) { text = text.split("{" + k + "}").join(values[k]); });
    return text;
  }
  var SVG = {
    pin: '<path d="M14 1C7.4 1 2 6.2 2 12.7 2 21.5 14 35 14 35s12-13.5 12-22.3C26 6.2 20.6 1 14 1z"/>',
    circle: '<circle cx="14" cy="14" r="12"/>',
    square: '<rect x="3" y="3" width="22" height="22" rx="3"/>',
    diamond: '<path d="M14 1l13 13-13 13L1 14z"/>',
    triangle: '<path d="M14 2l13 23H1z"/>',
    star: '<path d="M14 1.5l3.8 8 8.7 1-6.4 6 1.7 8.6L14 20.8l-7.8 4.3 1.7-8.6-6.4-6 8.7-1z"/>'
  };
  var lastSearch = 0;  // shared by all maps on the page: Nominatim allows 1 request/s per user

  function icon(L, shape, color, label, active) {
    var pin = shape === "pin", h = pin ? 36 : 28, ty = pin ? 17 : (shape === "triangle" ? 20 : 18.5);
    var html = '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="' + h + '" viewBox="0 0 28 ' + h + '">'
      + '<g fill="' + color + '" stroke="' + (active ? "#111" : "rgba(0,0,0,.55)") + '" stroke-width="' + (active ? 2.5 : 1.5) + '">'
      + (SVG[shape] || SVG.pin) + "</g>"
      + (label ? '<text x="14" y="' + ty + '" text-anchor="middle" font-size="12" font-weight="700" font-family="system-ui,sans-serif" fill="#fff" stroke="rgba(0,0,0,.45)" stroke-width="2" paint-order="stroke">' + label + "</text>" : "")
      + "</svg>";
    return L.divIcon({ html: html, className: "q-map-icon" + (active ? " is-active" : ""), iconSize: [28, h],
                       iconAnchor: pin ? [14, h - 1] : [14, 14] });
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in (attrs || {})) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function round6(v) { return Math.round(v * 1e6) / 1e6; }

  // --- place search, shared by participants and editor ---------------------------------------------
  function setupSearch(box, map, url, onPick) {
    var bar = box.querySelector("[data-map-search]"), list = box.querySelector("[data-map-results]");
    if (!bar || !url) return;
    var input = bar.querySelector("[data-map-query]"), go = bar.querySelector("[data-map-go]");
    bar.hidden = false;
    // what the search did, for screen readers: "Suche läuft", the number of places, or the failure (WCAG 4.1.3)
    var said = document.createElement("span");
    said.setAttribute("role", "status");
    said.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap";
    bar.appendChild(said);
    function failed() {
      list.innerHTML = "";
      list.appendChild(el("li", { class: "p-map-empty" }, uiT("Die Ortssuche ist gerade nicht erreichbar. Bitte erneut versuchen oder die Karte verwenden.",
                                                              "The place search is unavailable right now. Please try again or use the map.")));
      list.hidden = false;
      said.textContent = list.firstChild.textContent;
    }
    function show(results) {
      said.textContent = results.length ? uiT("Gefundene Orte: {n}", "Places found: {n}", { n: results.length }) : uiT("Nichts gefunden.", "Nothing found.");
      list.innerHTML = "";
      if (!results.length) list.appendChild(el("li", { class: "p-map-empty" }, uiT("Nichts gefunden.", "Nothing found.")));
      results.forEach(function (r) {
        var b = el("button", { type: "button" }, r.display_name);
        b.addEventListener("click", function () { pick(r); });
        list.appendChild(el("li")).appendChild(b);
      });
      list.appendChild(el("li", { class: "p-map-credit" }, uiT("Suche: Nominatim · Daten © OpenStreetMap-Mitwirkende", "Search: Nominatim · data © OpenStreetMap contributors")));
      list.hidden = false;
    }
    function pick(r) {
      var bb = (r.boundingbox || []).map(parseFloat);
      if (bb.length === 4 && bb.every(isFinite)) map.fitBounds([[bb[0], bb[2]], [bb[1], bb[3]]], { maxZoom: 17 });
      else map.setView([parseFloat(r.lat), parseFloat(r.lon)], 16);
      list.hidden = true;
      if (onPick) onPick();
    }
    function search() {
      var q = input.value.trim();
      if (!q || go.disabled) return;
      var wait = Math.max(0, lastSearch + 1100 - Date.now());
      go.disabled = true;
      setTimeout(function () {
        lastSearch = Date.now();
        var u = url + (url.indexOf("?") < 0 ? "?" : "&") + "format=jsonv2&limit=5&q=" + encodeURIComponent(q)
          + "&accept-language=" + encodeURIComponent(document.documentElement.lang || "de");
        // a failing service (429, 503 ...) is not "nothing found"; a hanging one ends after 10 s, and the button
        // comes back either way
        var ctrl = window.AbortController ? new AbortController() : null;
        var limit = setTimeout(function () { if (ctrl) ctrl.abort(); }, 10000);
        said.textContent = uiT("Suche läuft …", "Searching …");
        fetch(u, { headers: { Accept: "application/json" }, signal: ctrl ? ctrl.signal : undefined })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (results) {
            results = Array.isArray(results) ? results : [];
            if (results.length === 1) { said.textContent = uiT("Gefundene Orte: {n}", "Places found: {n}", { n: 1 }); pick(results[0]); } else show(results);
          })
          .catch(failed)
          .then(function () { clearTimeout(limit); setTimeout(function () { go.disabled = false; }, Math.max(0, lastSearch + 1000 - Date.now())); });
      }, wait);
    }
    go.addEventListener("click", search);
    // Enter searches instead of submitting the page (or the editor form)
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); search(); } });
  }

  function baseMap(L, box, cfg) {
    var canvas = box.querySelector("[data-map-canvas]");
    var map = L.map(canvas, { scrollWheelZoom: false, worldCopyJump: true }).setView(cfg.center, cfg.zoom);
    // a dark look with a tile server for dark maps: its tiles, always or when the device is set to dark
    var dark = cfg.tiles_dark && (cfg.dark === "always" ||
      (cfg.dark === "auto" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches));
    L.tileLayer(dark ? cfg.tiles_dark : cfg.tiles, { maxZoom: 19, attribution: dark ? cfg.attribution_dark : cfg.attribution }).addTo(map);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
    // zoom with the wheel only after the map was clicked once, so the page still scrolls past it
    map.on("focus click", function () { map.scrollWheelZoom.enable(); });
    map.on("blur", function () { map.scrollWheelZoom.disable(); });
    if (window.ResizeObserver) new ResizeObserver(function () { map.invalidateSize(); }).observe(canvas);
    return map;
  }

  // --- participants ----------------------------------------------------------------------------------
  function participant(L, box) {
    var cfg = JSON.parse(box.dataset.map);
    var input = box.querySelector("[data-map-input]"), list = box.querySelector("[data-map-list]");
    var count = box.querySelector("[data-map-count]"), centerBtn = box.querySelector("[data-map-center]");
    var map = baseMap(L, box, cfg), max = cfg.max || 1, uid = 0, active = null;
    var markers = [];  // {id, lat, lon, q, marker, card}
    var stored = [];
    try { stored = JSON.parse(input.value || "[]") || []; } catch (e) { stored = []; }

    function sync() {
      var data = markers.map(function (m) { return { lat: round6(m.lat), lon: round6(m.lon), q: m.q }; });
      input.value = data.length ? JSON.stringify(data) : "";
      var open = [];  // markers with unanswered required questions (the server checks again)
      markers.forEach(function (m, i) {
        m.marker.setIcon(icon(L, cfg.shape, cfg.color, String(i + 1), m === active));
        m.card.querySelector("[data-map-no]").textContent = uiT("Marker {n}", "Marker {n}", {n: i + 1});  // in the page's language (UI/UX audit 2026-10-16, 101)
        var missing = cfg.questions.some(function (q) { return q.required && (m.q[q.id] === undefined || m.q[q.id] === ""); });
        m.card.classList.toggle("is-incomplete", missing);
        if (missing) open.push(i + 1);
      });
      var n = markers.length;
      count.textContent = n + " / " + max + " " + (n === 1 ? uiT("Marker", "marker") : uiT("Marker", "markers"))
        + (n >= max ? " · " + uiT("Maximum erreicht", "maximum reached")
           : cfg.min && n < cfg.min ? " · " + uiT("mindestens {n}", "at least {n}", {n: cfg.min}) : "")
        + (open.length ? " · " + uiT("Fragen offen bei Marker {list}", "questions open for marker {list}", {list: open.join(", ")}) : "");
      box.classList.toggle("is-full", n >= max);
      centerBtn.disabled = n >= max;
      if (n) { var item = box.closest(".p-item"); if (item) item.classList.remove("has-error", "has-nudge"); }
    }
    function focus(m, scroll) {
      active = m;
      markers.forEach(function (x) { x.card.classList.toggle("is-active", x === m); });
      sync();
      if (scroll) m.card.scrollIntoView({ block: "nearest", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") });
    }
    function card(m) {
      var li = el("li", { class: "p-map-card" });
      var head = el("div", { class: "p-map-card-head" });
      var no = el("button", { type: "button", class: "p-map-no", "data-map-no": "", title: uiT("Auf der Karte zeigen", "Show on the map") });
      no.addEventListener("click", function () { map.panTo([m.lat, m.lon]); focus(m, false); });
      var del = el("button", { type: "button", class: "p-map-btn p-map-del" }, uiT("✕ Entfernen", "✕ Remove"));
      del.addEventListener("click", function () { remove(m); });
      head.appendChild(no); head.appendChild(del); li.appendChild(head);
      cfg.questions.forEach(function (q) {
        var name = "m" + box.dataset.mapMarker + "_" + m.id + "_" + q.id;
        var wrap = el("div", { class: "p-map-q" });
        var label = el("label", { class: "p-map-q-text", for: name }, q.text);
        if (q.required) {
          var star = el("span", { class: "p-required" });
          star.appendChild(el("span", { "aria-hidden": "true" }, " *"));
          star.appendChild(el("span", { class: "p-sr-only" }, uiT(" (Pflichtfrage)", " (required)")));
          label.appendChild(star);
        }
        wrap.appendChild(label);
        if (q.kind === "scale") {
          label.removeAttribute("for");
          label.id = name + "_q";
          var row = el("div", { class: "p-scale p-map-scale", role: "radiogroup", "aria-labelledby": name + "_q", style: "--points:" + q.points });
          for (var v = 1; v <= q.points; v++) {
            var opt = el("label", { class: "p-point" });
            var r = el("input", { type: "radio", name: name, value: String(v) });
            if (m.q[q.id] === v) r.checked = true;
            r.addEventListener("change", function (e) { m.q[q.id] = parseInt(e.target.value, 10); sync(); });
            opt.appendChild(r); opt.appendChild(el("span", { class: "p-point-label" }, String(v)));
            row.appendChild(opt);
          }
          wrap.appendChild(row);
          if (q.left || q.right) {
            var ends = el("div", { class: "p-endpoints" });
            ends.appendChild(el("span", {}, q.left || "")); ends.appendChild(el("span", {}, q.right || ""));
            wrap.appendChild(ends);
          }
        } else if (q.kind === "choice") {  // one option, stored as its place (1, 2, ...)
          label.removeAttribute("for");
          label.id = name + "_q";
          var group = el("div", { class: "p-options p-map-options", role: "radiogroup", "aria-labelledby": name + "_q" });
          (q.options || []).forEach(function (text, k) {
            var opt = el("label", { class: "p-option p-radio" });
            var r = el("input", { type: "radio", name: name, value: String(k + 1) });
            if (m.q[q.id] === k + 1) r.checked = true;
            r.addEventListener("change", function (e) { m.q[q.id] = parseInt(e.target.value, 10); sync(); });
            opt.appendChild(r); opt.appendChild(el("span", {}, text));
            group.appendChild(opt);
          });
          wrap.appendChild(group);
        } else {
          var t = el("input", { type: "text", id: name, maxlength: "500", class: "p-map-text" });
          t.value = m.q[q.id] || "";
          t.addEventListener("input", function () { if (t.value.trim()) m.q[q.id] = t.value; else delete m.q[q.id]; sync(); });
          t.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
          wrap.appendChild(t);
        }
        li.appendChild(wrap);
      });
      list.appendChild(li);
      return li;
    }
    function add(latlng, answers) {
      if (markers.length >= max) { box.classList.remove("bump"); void box.offsetWidth; box.classList.add("bump"); return null; }
      var w = L.latLng(latlng).wrap();
      var m = { id: ++uid, lat: w.lat, lon: w.lng, q: answers || {} };
      m.marker = L.marker([m.lat, m.lon], { draggable: true, keyboard: true, autoPan: true,
                                           title: uiT("Ziehen zum Verschieben", "Drag to move") }).addTo(map);
      // the icon is only redrawn after the drag: replacing it mid-drag would end the drag
      m.marker.on("dragend", function () { var p = m.marker.getLatLng().wrap(); m.lat = p.lat; m.lon = p.lng; focus(m, false); });
      m.marker.on("click", function () { focus(m, true); });
      m.card = card(m);
      markers.push(m);
      return m;
    }
    function remove(m) {
      map.removeLayer(m.marker);
      m.card.remove();
      markers = markers.filter(function (x) { return x !== m; });
      if (active === m) active = null;
      sync();
    }

    stored.forEach(function (s) { if (isFinite(s.lat) && isFinite(s.lon)) add([s.lat, s.lon], s.q || {}); });
    if (markers.length) map.fitBounds(L.latLngBounds(markers.map(function (m) { return [m.lat, m.lon]; })).pad(0.3), { maxZoom: cfg.zoom > 14 ? cfg.zoom : 15 });
    map.on("click", function (e) { var m = add(e.latlng); if (m) focus(m, false); });
    centerBtn.addEventListener("click", function () { var m = add(map.getCenter()); if (m) focus(m, false); });
    // "Meine Position": only moves the map there (the browser asks first); nothing of it is stored
    var locate = box.querySelector("[data-map-locate]");
    if (locate && navigator.geolocation && window.isSecureContext) {
      locate.hidden = false;
      locate.addEventListener("click", function () {
        locate.disabled = true;
        navigator.geolocation.getCurrentPosition(function (pos) {
          locate.disabled = false;
          map.setView([pos.coords.latitude, pos.coords.longitude], Math.max(map.getZoom(), 15));
        }, function () {
          locate.disabled = false;
          count.textContent = locate.getAttribute("data-failed");
        }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
      });
    }
    setupSearch(box, map, cfg.search);
    sync();
  }

  // --- element editor: start view --------------------------------------------------------------------
  function editor(L, box) {
    var cfg = JSON.parse(box.dataset.map), form = box.closest("form");
    var lat = form.querySelector("[data-map-lat]"), lon = form.querySelector("[data-map-lon]"), zoom = form.querySelector("[data-map-zoom]");
    var map = baseMap(L, box, cfg);
    map.scrollWheelZoom.enable();
    var cross = el("div", { class: "map-editor-cross", "aria-hidden": "true" });
    box.querySelector("[data-map-canvas]").appendChild(cross);
    var quiet = false;
    function take() {
      if (quiet || !cfg.editor) return;
      var c = map.getCenter().wrap(), a = c.lat.toFixed(5), b = c.lng.toFixed(5), z = String(map.getZoom());
      if (parseFloat(lat.value) === parseFloat(a) && parseFloat(lon.value) === parseFloat(b) && zoom.value === z) return;  // e.g. a resize
      lat.value = a; lon.value = b; zoom.value = z;
      lat.dispatchEvent(new Event("change", { bubbles: true }));  // live preview follows
    }
    map.on("moveend", take);
    [lat, lon, zoom].forEach(function (f) {
      f.addEventListener("change", function () {
        var a = parseFloat(String(lat.value).replace(",", ".")), b = parseFloat(String(lon.value).replace(",", ".")), z = parseInt(zoom.value, 10);
        if (!isFinite(a) || !isFinite(b) || !isFinite(z)) return;
        var c = map.getCenter();
        if (Math.abs(c.lat - a) < 1e-5 && Math.abs(c.lng - b) < 1e-5 && map.getZoom() === z) return;
        quiet = true; map.setView([a, b], z); quiet = false;
      });
    });
    setupSearch(box, map, cfg.search);
    // follow-up questions: the cell for the scale's ends or for the options of a single choice, by the kind chosen
    form.addEventListener("change", function (e) {
      if (e.target.name !== "mq_kind") return;
      var cell = e.target.closest("tr").querySelector("[data-mq-kind]");
      if (cell) cell.setAttribute("data-mq-kind", e.target.value);
    });
  }

  function init() {
    var L = window.L;
    if (!L) return;
    document.querySelectorAll("[data-map-marker], [data-map-editor]").forEach(function (box) {
      if (box.dataset.mapReady) return;
      box.dataset.mapReady = "1";
      try { if (box.hasAttribute("data-map-editor")) editor(L, box); else participant(L, box); }
      catch (e) { box.classList.add("is-broken"); if (window.console) console.error(e); }
    });
  }
  window.AskuriousMap = { init: init, icon: icon, baseMap: baseMap };  // icon and base map also serve the results page
  init();
})();
