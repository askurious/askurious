// /docs/dialogs (templates/handbook/overview.html): "Seiten und Dialoge" was one page until 2026-10-22; an old link
// with a section (/docs/dialogs#4-einstellungen) goes on to the page that has that section now.
(function () {
  var box = document.querySelector("[data-handbook-anchors]");
  var anchor = decodeURIComponent((location.hash || "").slice(1));
  if (!box || !anchor) return;
  var targets;
  try { targets = JSON.parse(box.getAttribute("data-handbook-anchors")); } catch (e) { return; }
  if (Object.prototype.hasOwnProperty.call(targets, anchor)) location.replace(targets[anchor] + location.hash);
})();
