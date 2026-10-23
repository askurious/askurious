// Results page: the postal code areas of a postal code item on the map, coloured by the number of answers; a
// click shows code, place and count. The page names the postal regions (first two digits) that have answers;
// their boundaries (OpenStreetMap, app/geo.py) are loaded here, one file per region, kept by the browser. A
// region comes as a small topology (shared borders once, delta-encoded integer coordinates in 1e-5 degrees);
// the areas with answers are turned into GeoJSON here. The boundaries used to be part of the page itself:
// 7 MB with 1000 answers from all over Germany, 23 MB with 5000, on every visit.
(function () {
  if (window.AskuriousPostalMap) { window.AskuriousPostalMap(); return; }
  // the script loads in the middle of the page, app.js (and its de_en) only after it (defer): the same choice
  // here, by the page's language (code review 2026-10-07, F22)
  function de_en(de, en) { return typeof window.de_en === "function" ? window.de_en(de, en) : (document.documentElement.lang === "en" ? en : de); }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function decodeArcs(arcs) {
    return arcs.map(function (arc) {
      var x = 0, y = 0;
      return arc.map(function (d) { x += d[0]; y += d[1]; return [x / 1e5, y / 1e5]; });
    });
  }
  function ring(ids, arcs) {
    var out = [];
    ids.forEach(function (i, n) {
      var pts = i < 0 ? arcs[~i].slice().reverse() : arcs[i];
      out = out.concat(n ? pts.slice(1) : pts);  // consecutive arcs share their end point
    });
    return out;
  }
  function geometry(g, arcs) {
    var poly = function (rings) { return rings.map(function (r) { return ring(r, arcs); }); };
    return g.type === "MultiPolygon" ? { type: "MultiPolygon", coordinates: g.arcs.map(poly) }
                                     : { type: "Polygon", coordinates: poly(g.arcs) };
  }
  function arcIds(arcs, out) {
    arcs.forEach(function (a) { if (Array.isArray(a)) arcIds(a, out); else out[a < 0 ? ~a : a] = true; });
    return out;
  }
  // the features of the codes in `wanted` from one region file; only the arcs they use are decoded
  function regionFeatures(topo, wanted, properties) {
    var codes = Object.keys(topo.areas || {}).filter(function (plz) { return wanted[plz]; });
    var needed = {}, arcs = [];
    codes.forEach(function (plz) { arcIds(topo.areas[plz].arcs, needed); });
    Object.keys(needed).forEach(function (i) { arcs[i] = decodeArcs([topo.arcs[i]])[0]; });
    return codes.map(function (plz) {
      return { type: "Feature", properties: properties(plz), geometry: geometry(topo.areas[plz], arcs) };
    });
  }
  // a class colour is a token of the page ("var(--seq-3)", app.css): Leaflet writes it into an SVG attribute, where
  // var() does not work, so the value the page has for it goes there
  function token(el, value) {
    var m = /^var\((--[a-z0-9-]+)\)$/.exec(value || "");
    return m ? (getComputedStyle(el).getPropertyValue(m[1]).trim() || "#9ecae1") : value;
  }
  function init() {
    var L = window.L, Q = window.AskuriousMap;
    if (!L || !Q) return;
    document.querySelectorAll("[data-postal-map]").forEach(function (box) {
      if (box.dataset.mapReady) return;
      box.dataset.mapReady = "1";
      var cfg = JSON.parse(box.querySelector("[data-postal-data]").textContent);
      var map = Q.baseMap(L, box, cfg);
      function color(n) {
        var c = cfg.classes.filter(function (k) { return n >= k.lo && n <= k.hi; })[0];
        return token(box, c ? c.color : cfg.classes[cfg.classes.length - 1].color);
      }
      var layer = L.geoJSON(null, {
        style: function (f) { return { color: "#08306b", weight: 0.8, opacity: 0.7, fillColor: color(f.properties.n), fillOpacity: 0.75 }; },
        onEachFeature: function (f, l) {
          var p = f.properties;
          l.bindPopup("<strong>" + esc(p.plz) + "</strong> " + esc(p.place) + "<br>" + p.n + (p.n === 1 ? de_en(" Antwort", " answer") : de_en(" Antworten", " answers")));
          l.on("mouseover", function () { l.setStyle({ weight: 2.5, opacity: 1 }); });
          l.on("mouseout", function () { layer.resetStyle(l); });
        }
      }).addTo(map);
      // fit to all areas; again when the map first gets its real size (e.g. rendered in a background tab)
      // and when further regions have arrived, until someone moves the map
      var moved = false, canvas = box.querySelector("[data-map-canvas]");
      function fit() {
        var bounds = layer.getBounds();
        if (!moved && bounds.isValid() && canvas.clientWidth) map.fitBounds(bounds.pad(0.05), { maxZoom: 13 });
      }
      map.on("mousedown touchstart wheel keydown", function () { moved = true; });
      if (window.ResizeObserver) new ResizeObserver(fit).observe(canvas);
      function properties(plz) { return { plz: plz, n: cfg.counts[plz] || 0, place: cfg.places[plz] || "" }; }
      // a few regions at a time: a sample from all over Germany needs all 95 files on the first visit
      var queue = (cfg.regions || []).slice(), running = 0, drawn = 0;
      function next() {
        while (running < 6 && queue.length) {
          running += 1;
          fetch(cfg.area_url.replace("RR", queue.shift()), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (topo) {
              if (topo) layer.addData({ type: "FeatureCollection", features: regionFeatures(topo, cfg.counts, properties) });
            })
            .catch(function () { /* a region that did not arrive stays empty on the map; the counts below are complete */ })
            .then(function () {
              running -= 1;
              drawn += 1;
              if (drawn % 8 === 0 || (!queue.length && !running)) fit();
              next();
            });
        }
      }
      next();
    });
  }
  window.AskuriousPostalMap = init;
  init();
})();
