// A figure of a results page as a picture (Ralf, 2026-10-16): a small icon beside every chart, map and word cloud
// saves it as PNG, drawn in the browser without reloading the page. Loaded on the results pages (surveys/results.html,
// interview/results.html) and on the presenter's live screen (live/screen.html); not on dashboards, they have their
// own button (board.js).
//
// How: the figure is copied with the styles the browser computes for it in the light colours (also in dark mode),
// put on white (the live screen: on its own background) with the question above it and n below it, drawn as SVG at three times its size on screen and then
// onto a canvas. SVG and HTML are redrawn at that size, so lines and letters are sharp, not enlarged pixels.
// Safari refuses to read back a canvas that drew HTML (foreignObject); a figure that is one SVG is then drawn on its
// own, an HTML figure says that it cannot be saved there.
(function () {
  "use strict";
  // the language of the person's interface; on the live screen the page has the survey's language (data-ui-lang)
  var shown = document.querySelector("[data-ui-lang]");
  var EN = (shown ? shown.getAttribute("data-ui-lang") : document.documentElement.lang) === "en";
  function de_en(de, en) { return EN ? en : de; }
  // the figures; a match inside another match is part of it. The Leaflet map has its own button (map_results.js).
  var FIGURES = [
    "ul.res-bars", ".res-stacks", "figure.res-hist", "svg.res-ranks", "svg.res-dates", "svg.pw-chart", "svg.sem-slopes",
    ".sem-diagram > svg", "figure.psm-chart", "figure.pos2d-res", "figure.sd-profile", ".res-state-map", ".hot-res-stage",
    ".res-cloud", "table.diary-time-table", "table.coverage-table", "[data-fig-live]"
  ].join(",");
  var SCALE = 3, MAX_PIXELS = 16e6;  // Safari draws no canvas above about 16.7 million pixels
  var SCRIPT = document.currentScript && document.currentScript.src;
  // the same icon as icon("download") in _icons.html
  var ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>';

  function text(el) { return el ? el.textContent.replace(/\s+/g, " ").trim() : ""; }
  // the head of a section without its hints and badges
  function head(h) {
    if (!h) return "";
    var copy = h.cloneNode(true);
    copy.querySelectorAll(".hint, .badge, .res-filtered, sup, .term").forEach(function (x) { x.remove(); });
    return text(copy);
  }

  // what stands with the figure: the question, the column of a list with several, the variable and n
  function context(fig, wrap) {
    if (fig.hasAttribute("data-fig-live")) {
      var board = fig.closest(".live-board") || document;
      var q = text(board.querySelector("[data-q]"));
      return { title: q, n: text(board.querySelector("[data-n]")), name: q.slice(0, 30), survey: fig.getAttribute("data-fig-live") };
    }
    var section = fig.closest("section, .panel"), title = "", sub = "", name = "", n = "";
    if (section) {
      var h = section.querySelector("summary h2, h2");
      title = h && h.querySelector(":scope > span:first-child:not(.hint)") ? text(h.querySelector(":scope > span:first-child")) : head(h);
      if (section.id && section.id.indexOf("res-") === 0) name = section.id.slice(4);
    }
    var h3 = previous(wrap, "h3");
    var part = fig.closest(".res-freq"), cap = fig.querySelector("figcaption") || (part && part.querySelector(".res-freq-head"));
    if (cap) {
      var code = cap.querySelector("code");
      if (code) name = text(code);
      if (!fig.contains(cap)) sub = text(cap.querySelector(".hint")) || text(cap);  // a caption in the figure is in the picture
    } else if (h3) sub = head(h3);
    if (sub && sub !== title) title = title ? title + " · " + sub : sub;
    // n: in the figure's own label, else in the note under it
    var labels = [fig.getAttribute("aria-label") || ""];
    fig.querySelectorAll("[aria-label]").forEach(function (x) { if (labels.length < 3) labels.push(x.getAttribute("aria-label")); });
    for (var el = wrap.nextElementSibling, i = 0; el && i < 2; el = el.nextElementSibling, i++) {
      if (el.matches("p")) labels.push(el.textContent);
    }
    for (var j = 0; j < labels.length && !n; j++) {
      var m = /\bn = ([\d.,\s]+\d|\d+)/.exec(labels[j]);
      if (m) n = "n = " + m[1].trim();
    }
    // else the number who answered the question, from the line under its head
    var answered = section && section.querySelector(":scope > details > p.muted.small, :scope > p.muted.small");
    var a = answered && /(?:Beantwortet von|Answered by) ([\d.,]+)/.exec(answered.textContent);
    if (!n && a) n = "n = " + a[1];
    // the page's title is "Ergebnisse · <survey>"
    return { title: title, n: n, name: name, survey: document.title.split(" · ").slice(1).join(" · ") };
  }
  function previous(el, tag) {
    for (var x = el.previousElementSibling; x; x = x.previousElementSibling) if (x.matches(tag)) return x;
    return null;
  }

  // the file: survey title, variable (on the live screen the question) and day, without characters a file system dislikes
  function filename(c) {
    var survey = (c.survey || "").trim(), day = new Date(), pad = function (v) { return (v < 10 ? "0" : "") + v; };
    var parts = [survey, c.name, day.getFullYear() + "-" + pad(day.getMonth() + 1) + "-" + pad(day.getDate())];
    return parts.filter(Boolean).map(function (p) {
      return p.replace(/[^0-9A-Za-zÄÖÜäöüß_-]+/g, "-").replace(/-+/g, "-").slice(0, 50).replace(/^-|-$/g, "");
    }).filter(Boolean).join("_") + ".png";
  }

  // every computed style written into the copy, element by element (the copy has the same tree)
  // (::before and ::after as spans of their own, an image does not see the style sheet that makes them)
  function styles(computed) {
    var css = "";
    for (var i = 0; i < computed.length; i++) css += computed[i] + ":" + computed.getPropertyValue(computed[i]) + ";";
    return css;
  }
  function inline(from, to) {
    to.setAttribute("style", styles(getComputedStyle(from)));
    for (var j = 0; j < from.children.length; j++) inline(from.children[j], to.children[j]);
    if (from instanceof SVGElement) return;
    ["::before", "::after"].forEach(function (which) {
      var pseudo = getComputedStyle(from, which), content = pseudo.content;
      if (!content || content === "none" || content === "normal") return;
      var span = document.createElement("span");
      span.setAttribute("style", styles(pseudo));
      span.textContent = /^["'].*["']$/.test(content) ? content.slice(1, -1).replace(/\\(.)/g, "$1") : "";
      if (which === "::before") to.insertBefore(span, to.firstChild); else to.appendChild(span);
    });
  }
  // pictures in the figure (hot spots, marks) as data, an SVG image may not load anything
  function pictures(from, to) {
    var a = from.querySelectorAll("img"), b = to.querySelectorAll("img");
    for (var i = 0; i < a.length; i++) {
      try {
        var c = document.createElement("canvas");
        c.width = a[i].naturalWidth; c.height = a[i].naturalHeight;
        c.getContext("2d").drawImage(a[i], 0, 0);
        b[i].setAttribute("src", c.toDataURL("image/png"));
      } catch (e) { b[i].removeAttribute("src"); }
    }
  }
  // the typeface of the live screen, which the system has not got (participant.css)
  var fontCss = null;
  function fonts(family) {
    if (!/Atkinson/i.test(family) || !SCRIPT) return Promise.resolve("");
    if (fontCss) return fontCss;
    var url = SCRIPT.replace(/[^/]*$/, "") + "fonts/atkinson-hyperlegible-next/AtkinsonHyperlegibleNext-wght.woff2";
    fontCss = fetch(url).then(function (r) { return r.ok ? r.blob() : null; }).then(function (blob) {
      if (!blob) return "";
      return new Promise(function (resolve) {
        var reader = new FileReader();
        reader.onload = function () { resolve('@font-face{font-family:"Atkinson Hyperlegible Next";font-weight:200 800;src:url(' + reader.result + ') format("woff2");}'); };
        reader.onerror = function () { resolve(""); };
        reader.readAsDataURL(blob);
      });
    }).catch(function () { return ""; });
    return fontCss;
  }

  // the light colours while the copy takes its styles; the page itself is not painted in between
  function light(work) {
    var root = document.documentElement, was = root.getAttribute("data-theme");
    root.setAttribute("data-theme", "light");
    try { return work(); } finally { if (was === null) root.removeAttribute("data-theme"); else root.setAttribute("data-theme", was); }
  }

  // white; the live screen keeps the background of the survey's look, its letters may be light
  function ground(fig) {
    if (!fig.hasAttribute("data-fig-live")) return "#ffffff";
    for (var el = fig; el && el.nodeType === 1; el = el.parentElement) {
      var c = getComputedStyle(el).backgroundColor;
      if (c && c !== "transparent" && !/rgba\(.*,\s*0\)$/.test(c)) return c;
    }
    return "#ffffff";
  }
  function render(svg, w, h, bg) {
    var scale = Math.min(SCALE, Math.sqrt(MAX_PIXELS / (w * h)));
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
        var ctx = canvas.getContext("2d");
        ctx.scale(scale, scale);
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        try { canvas.toBlob(function (blob) { if (blob) resolve(blob); else reject(new Error("empty")); }, "image/png"); } catch (e) { reject(e); }
      };
      img.onerror = function () { reject(new Error("image")); };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    });
  }
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }

  // the picture: the figure on white, the question above it, n below it
  function picture(fig, c) {
    var made = light(function () {
      var box = fig.getBoundingClientRect(), copy = fig.cloneNode(true);
      if (!box.width || !box.height) throw new Error("hidden");
      inline(fig, copy);
      pictures(fig, copy);
      copy.style.margin = "0";
      var root = getComputedStyle(document.body), fg = getComputedStyle(fig).color, bg = ground(fig);
      var family = getComputedStyle(fig).fontFamily || root.fontFamily;
      var w = Math.max(Math.ceil(box.width), 320);
      var frame = document.createElement("div");
      frame.setAttribute("style", "position:fixed;left:-100000px;top:0;box-sizing:content-box;padding:20px 24px;background:" + bg + ";"
        + "width:" + w + "px;font-family:" + family + ";color:" + fg + ";line-height:1.4;");
      if (c.title) {
        var t = document.createElement("div");
        t.setAttribute("style", "font-size:16px;font-weight:600;margin:0 0 14px;");
        t.textContent = c.title;
        frame.appendChild(t);
      }
      copy.style.width = Math.ceil(box.width) + "px";
      copy.style.height = Math.ceil(box.height) + "px";
      frame.appendChild(copy);
      if (c.n) {
        var n = document.createElement("div");
        n.setAttribute("style", "font-size:13px;margin:12px 0 0;opacity:.75;");
        n.textContent = c.n;
        frame.appendChild(n);
      }
      document.body.appendChild(frame);
      var size = frame.getBoundingClientRect();
      frame.style.position = frame.style.left = frame.style.top = "";
      var html = new XMLSerializer().serializeToString(frame);
      frame.remove();
      return { html: html, w: Math.ceil(size.width), h: Math.ceil(size.height), family: family, fg: fg, bg: bg,
        svg: fig.matches("svg") ? { copy: copy, w: Math.ceil(box.width), h: Math.ceil(box.height) } : null };
    });
    return fonts(made.family).then(function (css) {
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + made.w + '" height="' + made.h + '">'
        + (css ? "<style>" + css + "</style>" : "")
        + '<foreignObject width="100%" height="100%">' + made.html + "</foreignObject></svg>";
      return render(svg, made.w, made.h, made.bg).catch(function (err) {
        if (!made.svg) throw err;
        return render(plain(made, c), made.svg.w + 48, made.svg.h + 40 + (c.title ? 30 : 0) + (c.n ? 26 : 0), made.bg);
      });
    });
  }
  // Safari: an SVG figure without HTML around it, the question and n as SVG text on one line each
  function plain(made, c) {
    var s = made.svg, top = 20 + (c.title ? 30 : 0), font = esc(made.family), out = new XMLSerializer();
    s.copy.setAttribute("x", 24); s.copy.setAttribute("y", top);
    s.copy.setAttribute("width", s.w); s.copy.setAttribute("height", s.h);
    var title = c.title.length > 90 ? c.title.slice(0, 89) + "…" : c.title;
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + (s.w + 48) + '" height="' + (top + s.h + 20 + (c.n ? 26 : 0)) + '">'
      + '<rect width="100%" height="100%" fill="' + esc(made.bg) + '"/>'
      + (c.title ? '<text x="24" y="38" font-family="' + font + '" font-size="16" font-weight="600" fill="' + esc(made.fg) + '">' + esc(title) + "</text>" : "")
      + out.serializeToString(s.copy)
      + (c.n ? '<text x="24" y="' + (top + s.h + 22) + '" font-family="' + font + '" font-size="13" fill="' + esc(made.fg) + '" fill-opacity=".75">' + esc(c.n) + "</text>" : "")
      + "</svg>";
  }

  function save(button) {
    // a box with a wide and a narrow form of the same figure (part-worth bars, pw-wide / pw-narrow): the one shown
    var wrap = button.closest(".fig-wrap"), figs = wrap ? Array.prototype.slice.call(wrap.querySelectorAll(".fig-target")) : [];
    var fig = figs.filter(function (f) { return f.getClientRects().length; })[0] || figs[0];
    if (!fig) return;
    var c = context(fig, wrap);
    button.disabled = true;
    Promise.resolve().then(function () { return picture(fig, c); }).then(function (blob) {
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename(c);
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
    }).catch(function () {
      var note = wrap.querySelector(".fig-save-note") || document.createElement("span");
      note.className = "fig-save-note small";
      note.setAttribute("role", "status");
      note.textContent = de_en("In diesem Browser nicht möglich, bitte Chrome oder Firefox nehmen", "Not possible in this browser, please use Chrome or Firefox");
      wrap.appendChild(note);
      setTimeout(function () { note.remove(); }, 6000);
    }).then(function () { button.disabled = false; });
  }

  // every figure gets its icon beside it, also those that come later (segments, HB, simulator, live slides)
  function equip(scope) {
    var found = [];
    if (scope.matches && scope.matches(FIGURES)) found.push(scope);
    scope.querySelectorAll(FIGURES).forEach(function (fig) { found.push(fig); });
    found.forEach(function (fig) {
      if (fig.classList.contains("fig-target") || fig.closest(".fig-target, .b-tile")) return;
      if (fig.parentElement && fig.parentElement.closest(FIGURES)) return;
      // a scrolling figure keeps its scroll box (and the rules on it); the icon stands beside the box
      var outer = fig.parentElement && fig.parentElement.matches(".res-scroll, .pw-scroll") ? fig.parentElement : fig;
      // the second form of a figure in the same box (pw-narrow beside pw-wide): one icon for the box, not two
      if (outer !== fig && outer.parentElement && outer.parentElement.classList.contains("fig-wrap")) {
        fig.classList.add("fig-target");
        return;
      }
      var wrap = document.createElement("div");
      wrap.className = "fig-wrap" + (fig.hasAttribute("data-fig-live") ? " is-live" : "");
      outer.parentNode.insertBefore(wrap, outer);
      wrap.appendChild(outer);
      fig.classList.add("fig-target");
      var button = document.createElement("button");
      button.type = "button";
      button.className = "icon-btn fig-save";
      button.title = de_en("Als PNG speichern", "Save as PNG");
      button.setAttribute("aria-label", de_en("Abbildung als PNG speichern", "Save the figure as PNG"));
      button.innerHTML = ICON;
      wrap.appendChild(button);
    });
  }
  function start() {
    equip(document.body);
    document.addEventListener("click", function (e) {
      var button = e.target.closest && e.target.closest(".fig-save");
      if (button) { e.preventDefault(); save(button); }
    });
    var pending = null;
    new MutationObserver(function (records) {
      records.forEach(function (r) { r.addedNodes.forEach(function (n) { if (n.nodeType === 1 && !n.closest(".fig-target")) (pending = pending || []).push(n); }); });
      if (pending && !pending.timer) pending.timer = setTimeout(function () {
        var list = pending; pending = null;
        list.forEach(function (n) { if (n.isConnected) equip(n); });
      }, 50);
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
