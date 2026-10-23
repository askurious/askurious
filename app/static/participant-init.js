// Establish the first-paint map layout; retain native controls if the deferred helpers fail.
document.documentElement.classList.add("js");
window.addEventListener("load", function () {
  document.querySelectorAll("[data-state-pick]:not(.has-map)").forEach(function (box) {
    box.classList.add("map-fallback");
  });
});
