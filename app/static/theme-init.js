// Light or dark before the first paint (no flash of the wrong theme).
// Loaded in <head> without defer (base.html, surveys/quota_state.html, interview pages): an inline script is not
// allowed (CSP). Signed in with a saved "Darstellung" (app/display.py) the server puts data-mode and data-look on
// <html>: light or dark as chosen there, "auto" after the device. Without it: the choice kept in this browser,
// else the brand's standard (data-default-mode, app/brand.py: POLLtergeist dark, Askurious light), else the device.
// A device that asks for more contrast gets the style "Kontrast" while the style is Askurious.
(function () {
  var root = document.documentElement, mode = root.getAttribute("data-mode"), t = null;
  var mm = window.matchMedia ? function (q) { return matchMedia(q).matches; } : function () { return false; };
  if (mode === "light" || mode === "dark") t = mode;
  else if (!mode) {
    try { t = localStorage.getItem("theme"); } catch (e) {}
    if (t !== "dark" && t !== "light") t = root.getAttribute("data-default-mode");
  }
  if (t !== "dark" && t !== "light") t = mm("(prefers-color-scheme: dark)") ? "dark" : "light";
  root.setAttribute("data-theme", t);
  // the text size of the interview view only (interview/conduct.html, data-iv-page), kept on this device
  if (root.hasAttribute("data-iv-page")) {
    var size = null;
    try { size = localStorage.getItem("askurious-iv-size"); } catch (e) {}
    if (/^(90|100|112|125|140|160)$/.test(size || "")) {
      root.setAttribute("data-iv-size", size);
      root.style.setProperty("--iv-bar-zoom", String(parseInt(root.getAttribute("data-size") || "100", 10) / parseInt(size, 10)));
    }
  }
  var look = root.getAttribute("data-look");
  if ((!look || look === "askurious") && mm("(prefers-contrast: more)")) root.setAttribute("data-look", "kontrast");
})();
