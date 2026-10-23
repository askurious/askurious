// Keeps the display awake while a live vote is presented (Ralf, 2026-10-23: no standby or dimming in the two-screen
// set-up). The presenter view and the audience view (live/screen.html) ask for it while they are visible; "Präsentieren"
// (live/present.html) only while its window shows the audience view in full screen on the second screen. Screen Wake
// Lock API: the browser drops the lock when the page is hidden, so it is asked for again once the page is visible.
// Browsers without it (older Firefox) keep their own power settings.
(function () {
  var script = document.currentScript;
  var mode = (script && script.dataset.wake) || "always";
  if (!("wakeLock" in navigator)) return;
  var lock = null, asking = false;
  function wanted() {
    return document.visibilityState === "visible" && (mode !== "fullscreen" || !!document.fullscreenElement);
  }
  function update() {
    if (wanted() && !lock && !asking) {
      asking = true;
      navigator.wakeLock.request("screen").then(function (l) {
        asking = false;
        lock = l;
        l.addEventListener("release", function () { lock = null; });
        if (!wanted()) { l.release().catch(function () {}); }
      }).catch(function () { asking = false; lock = null; });  // e.g. battery saver: nothing to do
    } else if (!wanted() && lock) {
      lock.release().catch(function () {});
      lock = null;
    }
  }
  document.addEventListener("visibilitychange", update);
  document.addEventListener("fullscreenchange", update);
  // some browsers grant the lock only after the page was used once: ask again on the first click or key
  document.addEventListener("pointerdown", update, { once: true });
  document.addEventListener("keydown", update, { once: true });
  update();
})();
