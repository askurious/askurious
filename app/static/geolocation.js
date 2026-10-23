// Location (items/geolocation.py): the position of the device, asked for only when the person taps "Standort
// übermitteln" (the browser then asks for permission itself). "Ich möchte meinen Standort nicht angeben" is an
// answer as well. With the tick "korrigieren" a small map (Leaflet, map_marker.js baseMap) shows the point and the
// radius of its accuracy; dragging the point stores it as corrected. The hidden input carries
// {way, lat, lon, acc} as JSON; the server rounds, finds the federal state and checks everything again.
(function () {
  function round6(v) { return Math.round(v * 1e6) / 1e6; }

  function setup(box) {
    var cfg = JSON.parse(box.dataset.geoCfg || "{}");
    var input = box.querySelector("[data-geo-input]"), actions = box.querySelector("[data-geo-actions]");
    var share = box.querySelector("[data-geo-share]"), refuse = box.querySelector("[data-geo-refuse]");
    var cancel = box.querySelector("[data-geo-cancel]"), accText = box.querySelector("[data-geo-acc]");
    var mapBox = box.querySelector("[data-geo-map]");
    var map = null, marker = null, circle = null, asking = 0;
    var state = null;
    try { state = JSON.parse(input.value || "null"); } catch (e) { state = null; }

    function msg(way) { return way === "device" ? "shared" : way; }
    function show(name) {
      box.querySelectorAll("[data-geo-msg]").forEach(function (p) { p.hidden = p.getAttribute("data-geo-msg") !== name; });
    }
    function store(answer) {
      state = answer;
      input.value = answer ? JSON.stringify(answer) : "";
      var item = box.closest(".p-item");
      if (answer && item) item.classList.remove("has-error", "has-nudge");
    }
    function draw() {
      if (!mapBox || !window.L || !window.AskuriousMap || !state || !isFinite(state.lat) || !isFinite(state.lon)) {
        if (mapBox) mapBox.hidden = true;
        return;
      }
      var L = window.L, here = [state.lat, state.lon];
      mapBox.hidden = false;
      if (!map) {
        map = window.AskuriousMap.baseMap(L, mapBox, cfg);
        marker = L.marker(here, { draggable: true, keyboard: true, autoPan: true }).addTo(map);
        var corrected = function () {
          var p = marker.getLatLng().wrap();
          store({ way: "corrected", lat: round6(p.lat), lon: round6(p.lng), acc: state.acc });
          show("corrected");
        };
        marker.on("dragend", corrected);
        // without a mouse: the map moves with the arrow keys, this button puts the point in its centre (D8)
        var center = mapBox.querySelector("[data-geo-center]");
        if (center) center.addEventListener("click", function () { marker.setLatLng(map.getCenter()); corrected(); });
      } else {
        marker.setLatLng(here);
      }
      if (circle) map.removeLayer(circle);
      circle = isFinite(state.acc) && state.acc > 0 && state.way === "device"
        ? L.circle(here, { radius: state.acc, weight: 1, fillOpacity: 0.12 }).addTo(map) : null;
      map.invalidateSize();
      if (circle) map.fitBounds(circle.getBounds(), { maxZoom: 17 }); else map.setView(here, 16);
    }
    function shared(pos) {
      var c = pos.coords;
      store({ way: "device", lat: round6(c.latitude), lon: round6(c.longitude), acc: Math.round(c.accuracy) });
      accText.textContent = isFinite(c.accuracy) ? accText.getAttribute("data-text").replace("{n}", Math.round(c.accuracy).toLocaleString(document.documentElement.lang || undefined)) : "";
      show("shared");
      draw();
    }
    function failed(way) {
      store({ way: way });
      show(way);
      draw();
    }
    function ask() {
      var mine = ++asking;
      if (!navigator.geolocation || !window.isSecureContext) { failed("unavailable"); return; }
      show("busy");
      navigator.geolocation.getCurrentPosition(function (pos) {
        if (mine === asking) shared(pos);
      }, function (err) {
        if (mine !== asking) return;
        failed(err.code === 1 ? "denied" : err.code === 3 ? "timeout" : "unavailable");
      }, { enableHighAccuracy: true, timeout: cfg.timeout || 30000, maximumAge: 0 });
    }

    actions.hidden = false;
    share.addEventListener("click", ask);
    refuse.addEventListener("click", function () { asking++; failed("refused"); });
    cancel.addEventListener("click", function () { asking++; show(state ? msg(state.way) : ""); });
    if (state && state.way) {
      show(msg(state.way));
      if (state.way === "device" && isFinite(state.acc)) accText.textContent = accText.getAttribute("data-text").replace("{n}", state.acc);
      draw();
    }
  }

  document.querySelectorAll("[data-geo]").forEach(function (box) {
    if (box.dataset.geoReady) return;
    box.dataset.geoReady = "1";
    try { setup(box); } catch (e) { if (window.console) console.error(e); }
  });
})();
