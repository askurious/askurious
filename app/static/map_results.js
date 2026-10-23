// Results page: markers of all participants of a map item, as a heatmap (Leaflet.heat, static/vendor)
// or as all markers in the item's shape and colour with a popup (resp_id, marker number, answers).
// The map zooms to all markers; the switch changes the layer without reloading; "Als Bild" saves it as a PNG.
(function () {
  if (window.AskuriousMapResults) { window.AskuriousMapResults(); return; }
  // the script loads in the middle of the page, app.js (and its de_en) only after it (defer): the same choice
  // here, by the page's language (code review 2026-10-07, F22)
  function de_en(de, en) { return typeof window.de_en === "function" ? window.de_en(de, en) : (document.documentElement.lang === "en" ? en : de); }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function init() {
    var L = window.L, Q = window.AskuriousMap;
    if (!L || !Q) return;
    document.querySelectorAll("[data-map-results]").forEach(function (box) {
      if (box.dataset.mapReady) return;
      box.dataset.mapReady = "1";
      var cfg = JSON.parse(box.dataset.mapResults);
      var map = Q.baseMap(L, box, cfg), pts = cfg.points, layers = {};
      function heat() {
        // radius grows a little with fewer points so single markers stay visible
        var r = pts.length > 500 ? 18 : pts.length > 50 ? 24 : 30;
        // every person weighs the same: each of k markers of one person counts 1/k (w, items/map_marker.py)
        return L.heatLayer(pts.map(function (p) { return [p.lat, p.lon, p.w || 1]; }), { radius: r, blur: r * 0.75, maxZoom: 17, minOpacity: 0.35 });
      }
      function markers() {
        var g = L.featureGroup();
        pts.forEach(function (p) {
          var marker = L.marker([p.lat, p.lon], { icon: Q.icon(L, cfg.shape, cfg.color, String(p.k), false), keyboard: false });
          if (p.a) {  // the role "Ansehen" gets positions only: no participation number, no answers
            marker.bindPopup("<strong>" + de_en("Teilnahme ", "Participation ") + esc(p.p) + " · Marker " + p.k + "</strong>"
              + p.a.map(function (a) { return "<br>" + esc(a[0]) + ": <em>" + esc(a[1]) + "</em>"; }).join(""), { maxWidth: 280 });
          }
          marker.addTo(g);
        });
        return g;
      }
      function show(view) {
        Object.keys(layers).forEach(function (k) { if (k !== view && map.hasLayer(layers[k])) map.removeLayer(layers[k]); });
        if (!layers[view]) layers[view] = view === "markers" ? markers() : (L.heatLayer ? heat() : markers());
        layers[view].addTo(map);
      }
      var bounds = L.latLngBounds(pts.map(function (p) { return [p.lat, p.lon]; }));
      if (bounds.isValid()) map.fitBounds(bounds.pad(0.15), { maxZoom: 16 });
      box.querySelectorAll("[data-map-view]").forEach(function (r) {
        r.addEventListener("change", function () { if (r.checked) show(r.value); });
      });
      show(cfg.view === "markers" ? "markers" : "heat");
      // "Als Bild": the map as it is shown now (tiles, heatmap or markers, the credit of the map data) as a PNG,
      // for a report or a paper. The tiles are loaded once more with CORS; a tile server that does not allow it
      // leaves them out (the markers and the credit stay).
      var save = box.querySelector("[data-map-png]");
      if (save) save.addEventListener("click", function () {
        var frame = box.querySelector("[data-map-canvas]"), at = frame.getBoundingClientRect();
        var ratio = Math.min(window.devicePixelRatio || 1, 2), w = Math.round(at.width), h = Math.round(at.height);
        var canvas = document.createElement("canvas");
        canvas.width = w * ratio; canvas.height = h * ratio;
        var g = canvas.getContext("2d");
        g.scale(ratio, ratio);
        g.fillStyle = "#f2efe9";
        g.fillRect(0, 0, w, h);
        var missing = 0;
        function place(node) { var r = node.getBoundingClientRect(); return [r.left - at.left, r.top - at.top, r.width, r.height]; }
        function picture(src, cors) {
          return new Promise(function (done) {
            var img = new Image();
            if (cors) img.crossOrigin = "anonymous";
            img.onload = function () { done(img); };
            img.onerror = function () { done(null); };
            img.src = src;
          });
        }
        save.disabled = true;
        var tiles = Array.prototype.slice.call(frame.querySelectorAll(".leaflet-tile-pane img.leaflet-tile-loaded"));
        Promise.all(tiles.map(function (t) {
          var p = place(t);
          return picture(t.src, true).then(function (img) { if (img) g.drawImage(img, p[0], p[1], p[2], p[3]); else missing += 1; });
        })).then(function () {
          frame.querySelectorAll(".leaflet-overlay-pane canvas").forEach(function (c) {  // the heatmap
            var p = place(c);
            g.drawImage(c, p[0], p[1], p[2], p[3]);
          });
          // in the order the map stacks them (Leaflet: southern markers in front)
          var icons = Array.prototype.slice.call(frame.querySelectorAll(".leaflet-marker-pane .leaflet-marker-icon svg")).sort(function (a, b) {
            return (parseInt(a.parentNode.style.zIndex, 10) || 0) - (parseInt(b.parentNode.style.zIndex, 10) || 0);
          });
          return Promise.all(icons.map(function (svg) {
            var p = place(svg), copy = svg.cloneNode(true);
            copy.setAttribute("width", p[2]); copy.setAttribute("height", p[3]);
            return picture("data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(copy)), false)
              .then(function (img) { return { img: img, p: p }; });
          })).then(function (drawn) {
            drawn.forEach(function (d) { if (d.img) g.drawImage(d.img, d.p[0], d.p[1], d.p[2], d.p[3]); });
          });
        }).then(function () {
          var credit = (frame.querySelector(".leaflet-control-attribution") || {}).textContent || "";
          credit = credit.replace(/^\s*Leaflet\s*\|?\s*/, "").trim();
          // in a picture the link is lost: OpenStreetMap asks for the licence and the address written out (U17)
          if (/OpenStreetMap/.test(credit) && credit.indexOf("openstreetmap.org/copyright") < 0) credit += " · ODbL · openstreetmap.org/copyright";
          if (credit) {
            g.font = "11px system-ui, sans-serif";
            var tw = g.measureText(credit).width;
            g.fillStyle = "rgba(255,255,255,0.85)";
            g.fillRect(w - tw - 10, h - 18, tw + 10, 18);
            g.fillStyle = "#333";
            g.fillText(credit, w - tw - 5, h - 5);
          }
          canvas.toBlob(function (blob) {
            save.disabled = false;
            if (!blob) return;
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = (cfg.name || "karte") + ".png";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
            var note = box.querySelector("[data-map-png-note]");
            if (note) note.hidden = !missing;
          }, "image/png");
        }).catch(function () { save.disabled = false; });
      });
    });
  }
  window.AskuriousMapResults = init;
  init();
})();
