// A dashboard page (templates/boards/view.html): asks now and then whether there is a new state and then swaps the
// tiles. Values of bars and dots are kept across the swap (data-k), so they grow to the new value instead of
// jumping. Pauses while the tab is in the background. Nothing is stored in the browser.
// "Kopieren" beside the participation link on the dashboard of one person (X57): the address to the clipboard
document.querySelectorAll("[data-b-copy]").forEach(function (button) {
  button.addEventListener("click", function () {
    var field = document.querySelector(button.getAttribute("data-b-copy"));
    if (!field) return;
    var label = button.textContent;
    function done() { button.textContent = button.getAttribute("data-done") || label; setTimeout(function () { button.textContent = label; }, 2000); }
    field.select();
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(field.value).then(done, function () {});
    else { try { document.execCommand("copy"); done(); } catch (e) { /* selected: copy by hand */ } }
  });
});

(function () {
  var page = document.getElementById("board");
  if (!page) return;
  var tiles = document.getElementById("board-tiles");
  var every = Number(page.dataset.every) || 0, url = page.dataset.url, version = page.dataset.version;
  var busy = false;   // a request for the state is under way
  var again = false;  // someone asked meanwhile: ask once more when it is back
  var held = null;    // the tile that is being dragged in the designer's preview
  var PENDING_SECONDS = 3;  // how often a page without its first state asks for it

  // Where the tiles stand. The grid has twelve columns and rows of 4px (board.css); this gives every tile its
  // column and its rows, measured again when the tiles, the width of the window or the fonts change.
  //   - A tile stands as high up as it can without standing above the tile before it, so tiles of different
  //     heights leave no holes and the order stays the reading order.
  //   - It begins at a column that is a multiple of its width (a half at 1 or 7, a third at 1, 5 or 9, ...),
  //     the leftmost one unless one further right brings it clearly higher up, so tiles line up.
  //   - A tile with a column of its own (data-x) begins there, and what is free to its left at that height stays
  //     free down to its lower edge: the gap someone left on purpose.
  // Narrow windows as in board.css: quarters and thirds become halves, then everything is one column. Without
  // this script and in print the plain grid of board.css holds.
  var ROW = 4, GAP = 16, COLS = 12;
  function pack() {
    var list = Array.prototype.slice.call(tiles.querySelectorAll(".b-tile"));
    var wide = window.innerWidth > 1000, narrow = window.innerWidth <= 760;
    tiles.classList.add("b-packed");
    var spans = list.map(function (tile) {
      var w = Number(tile.dataset.w) || 6;
      return narrow ? COLS : wide ? w : (w <= 4 ? 6 : w >= 8 ? COLS : w);
    });
    list.forEach(function (tile, i) {  // its width first: the height depends on it
      tile.style.gridRow = ""; tile.style.marginBottom = "";
      tile.style.gridColumn = "span " + spans[i];
    });
    var heights = list.map(function (tile) { return tile.getBoundingClientRect().height; });
    var sky = [], before = 0, k;  // sky: the first free row of every column; before: where the tile before begins
    for (k = 0; k < COLS; k++) sky.push(0);
    list.forEach(function (tile, i) {
      var w = spans[i], rows = Math.ceil((heights[i] + GAP) / ROW), own = wide ? Number(tile.dataset.x) || 0 : 0;
      function topAt(col) { var top = before; for (var c = col; c < col + w; c++) top = Math.max(top, sky[c]); return top; }
      var col = 0, top;
      if (own && own + w - 1 <= COLS) {
        col = own - 1;
        top = topAt(col);
        for (k = 0; k < col; k++) if (sky[k] <= top) sky[k] = top + rows;  // the gap stays free
      } else {
        var step = w === 8 ? 4 : w === 9 ? 3 : w;
        top = topAt(0);
        // further right only where that brings it clearly higher up (more than 32px): otherwise left first
        for (k = step; k + w <= COLS; k += step) if (topAt(k) < top - 8) { top = topAt(k); col = k; }
      }
      for (k = col; k < col + w; k++) sky[k] = top + rows;
      before = top;
      tile.style.gridColumn = (col + 1) + " / span " + w;
      tile.style.gridRow = (top + 1) + " / span " + rows;
      tile.style.marginBottom = GAP + "px";
    });
  }
  pack();
  window.addEventListener("resize", pack);
  window.addEventListener("load", pack);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(pack);

  function swap(html) {
    var before = {};
    tiles.querySelectorAll("[data-k]").forEach(function (el) { before[el.dataset.k] = el.style.getPropertyValue("--v"); });
    tiles.innerHTML = html;
    var moved = [];
    tiles.querySelectorAll("[data-k]").forEach(function (el) {
      var now = el.style.getPropertyValue("--v"), was = before[el.dataset.k];
      if (was === undefined) was = "0%";  // new on the page: grows from nothing
      if (was !== now) { el.style.setProperty("--v", was); moved.push([el, now]); }
    });
    if (page.hasAttribute("data-images")) saveButtons();
    if (page.hasAttribute("data-arrange")) removeButtons();
    pack();
    if (!moved.length) return;
    void tiles.offsetWidth;  // the old values are laid out before the new ones are set
    requestAnimationFrame(function () { moved.forEach(function (m) { m[0].style.setProperty("--v", m[1]); }); });
  }

  // One request at a time, and never new tiles while one is being dragged: the dragged tile would be put back
  // among the new ones and stand there twice. now: the designer asks after a change, also in a background tab.
  function ask(now) {
    if (document.hidden && now !== true) return;
    if (busy || held) { if (now === true) again = true; return; }
    busy = true;
    again = false;
    fetch(url + (url.indexOf("?") < 0 ? "?" : "&") + "v=" + encodeURIComponent(version), { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.html || data.v === version) return;
        if (held) { again = true; return; }  // a drag began while the answer was on its way
        version = data.v;
        swap(data.html);
        var stand = document.getElementById("board-stand"), n = document.getElementById("board-n");
        if (stand && data.stand) stand.textContent = data.stand;
        if (n && data.n !== null && data.n !== undefined) {
          n.textContent = data.n;
          var wrap = n.closest("[data-board-n]");
          if (wrap) wrap.hidden = false;
        }
        if (first) { clearInterval(first); first = null; }
      })
      .catch(function () {})
      .then(function () { busy = false; if (again && !held) ask(true); });
  }

  if (every > 0 && url) {
    setInterval(ask, every * 1000);
    document.addEventListener("visibilitychange", ask);
  }
  // the first state is still being computed by another request (boards.pending): ask every few seconds until it is
  // there, also where the page is not live (an ended survey)
  var first = page.hasAttribute("data-pending") && url ? setInterval(ask, PENDING_SECONDS * 1000) : null;
  // the designer reloads its preview after every change (app.js calls it in the preview's frame)
  window.boardRefresh = function () { ask(true); };

  // The designer's preview: a tile is taken with the mouse or pen and put where it is dropped; the designer
  // gets the new order and saves it. (A finger scrolls the preview; the list next to it has handles.)
  // ... and every tile has a small x that takes it off the dashboard.
  // in the dashboard's language (boards/view.html, data-t; read here: removeButtons runs before T below is set)
  var removeText = "Vom Dashboard nehmen";
  try { removeText = JSON.parse(page.dataset.t || "{}").remove || removeText; } catch (e) { /* German */ }
  function removeButtons() {
    tiles.querySelectorAll(".b-tile").forEach(function (tile) {
      if (tile.querySelector(".b-remove")) return;
      var button = document.createElement("button");
      button.type = "button"; button.className = "b-remove"; button.textContent = "×";
      button.title = removeText; button.setAttribute("aria-label", removeText);
      tile.appendChild(button);
    });
  }
  var STARTS = [1, 4, 5, 7, 9, 10];  // the columns a tile may begin in (boards.STARTS)
  function place(tile, start) {
    if (start) { tile.classList.add("b-x"); tile.dataset.x = start; }
    else { tile.classList.remove("b-x"); delete tile.dataset.x; }
  }
  if (page.hasAttribute("data-arrange")) {
    removeButtons();
    tiles.addEventListener("click", function (e) {
      var button = e.target.closest(".b-remove");
      if (!button) return;
      var tile = button.closest(".b-tile"), key = tile.dataset.key;
      tile.remove();
      pack();
      window.parent.postMessage({ type: "board-remove", key: key }, location.origin);
    });
    tiles.addEventListener("pointerdown", function (e) {
      var tile = e.target.closest(".b-tile");
      if (!tile || e.button || e.pointerType === "touch" || e.target.closest(".b-remove")) return;
      held = { tile: tile, x: e.clientX, y: e.clientY, id: e.pointerId, active: false, grip: e.clientX - tile.getBoundingClientRect().left };
    });
    window.addEventListener("pointermove", function (e) {
      if (!held || e.pointerId !== held.id) return;
      if (!held.active) {
        if (Math.abs(e.clientX - held.x) + Math.abs(e.clientY - held.y) < 8) return;
        held.active = true;
        // the page keeps its height while a tile is dragged: were it to get shorter, the browser would scroll and
        // another tile would slide under the pointer
        tiles.style.minHeight = tiles.offsetHeight + "px";
        held.tile.classList.add("b-dragged");
        document.body.classList.add("b-dragging");
        if (window.getSelection) window.getSelection().removeAllRanges();
      }
      e.preventDefault();
      if (e.clientY < 48) window.scrollBy(0, -24); else if (e.clientY > window.innerHeight - 48) window.scrollBy(0, 24);  // towards tiles further up or down
      var over = document.elementFromPoint(e.clientX, e.clientY);
      over = over && over.closest(".b-tile");
      if (!over || over === held.tile || over.parentNode !== tiles) {
        // over a free place (or moved sideways within its own): the tile begins in the column under its left
        // edge, the nearest one a tile may begin in, and the place to its left stays free
        var grid = tiles.getBoundingClientRect(), self = held.tile.getBoundingClientRect();
        if (e.clientX < grid.left || e.clientX > grid.right || window.innerWidth <= 1000) return;
        var step = (grid.width + 16) / 12, width = Number(held.tile.dataset.w) || 6;
        var wanted = (e.clientX - held.grip - grid.left) / step + 1, now = Math.round((self.left - grid.left) / step) + 1;
        var start = STARTS.filter(function (s) { return s + width - 1 <= 12; })
          .reduce(function (best, s) { return Math.abs(s - wanted) < Math.abs(best - wanted) ? s : best; }, 1);
        if (start !== now) { place(held.tile, start); pack(); }
        return;
      }
      place(held.tile, 0);  // onto another tile: in its row again, where the tiles before it leave room
      // before or after the tile under the pointer, by the half the pointer is in (left or right in the same
      // row, else upper or lower): where the tile already stands nothing moves, so it does not flip back and forth
      var box = over.getBoundingClientRect(), own = held.tile.getBoundingClientRect();
      var sameRow = Math.abs(box.top - own.top) < Math.min(box.height, own.height) / 2;
      var before = sameRow ? e.clientX < box.left + box.width / 2 : e.clientY < box.top + box.height / 2;
      if (before ? held.tile.nextElementSibling === over : over.nextElementSibling === held.tile) return;
      tiles.insertBefore(held.tile, before ? over : over.nextElementSibling);
      pack();
    });
    var release = function (e) {
      if (!held || e.pointerId !== held.id) return;
      var moved = held.active;
      tiles.style.minHeight = "";
      held.tile.classList.remove("b-dragged");
      document.body.classList.remove("b-dragging");
      held = null;
      if (!moved) { if (again) ask(true); return; }
      var keys = Array.prototype.map.call(tiles.querySelectorAll(".b-tile"), function (t) { return t.dataset.key; });
      keys = keys.filter(function (key, i) { return keys.indexOf(key) === i; });
      var starts = {};
      tiles.querySelectorAll(".b-tile").forEach(function (t) { starts[t.dataset.key] = Number(t.dataset.x) || 0; });
      window.parent.postMessage({ type: "board-order", keys: keys, starts: starts }, location.origin);
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    tiles.addEventListener("dragstart", function (e) { e.preventDefault(); });
  }

  var print = document.getElementById("board-print");
  if (print) print.addEventListener("click", function () { window.print(); });

  // Members of the survey: a tile as a picture (PNG, twice the size on screen) for slides and reports. The tile
  // is copied with the styles the browser computed for it, drawn as SVG and then onto a canvas.
  function image(tile) {
    function inline(from, to) {
      var computed = getComputedStyle(from), text = "";
      for (var i = 0; i < computed.length; i++) text += computed[i] + ":" + computed.getPropertyValue(computed[i]) + ";";
      to.setAttribute("style", text);
      for (var j = 0; j < from.children.length; j++) inline(from.children[j], to.children[j]);
    }
    return new Promise(function (resolve, reject) {
      tile.classList.add("b-saving");
      var box = tile.getBoundingClientRect(), copy = tile.cloneNode(true);
      inline(tile, copy);
      tile.classList.remove("b-saving");
      copy.style.margin = "0";
      var w = Math.ceil(box.width), h = Math.ceil(box.height), scale = 2;
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '"><foreignObject width="100%" height="100%">'
        + new XMLSerializer().serializeToString(copy) + "</foreignObject></svg>";
      var img = new Image();
      img.onload = function () {
        var canvas = document.createElement("canvas");
        canvas.width = w * scale; canvas.height = h * scale;
        var ctx = canvas.getContext("2d");
        ctx.scale(scale, scale);
        ctx.fillStyle = getComputedStyle(document.body).backgroundColor;
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        try { canvas.toBlob(function (blob) { if (blob) resolve(blob); else reject(new Error("empty")); }, "image/png"); } catch (e) { reject(e); }
      };
      img.onerror = function () { reject(new Error("image")); };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    });
  }
  // the texts on the tiles of the dashboard's page in its language (boards/view.html, data-t)
  var T = {};
  try { T = JSON.parse(page.dataset.t || "{}"); } catch (e) { T = {}; }
  function saveButtons() {
    tiles.querySelectorAll(".b-tile:not(.b-shape-text)").forEach(function (tile) {
      if (tile.querySelector(".b-save")) return;
      var button = document.createElement("button");
      // the download icon of the figures on the results page (drawn here: dashboards do not load that script), not a word
      // (Ralf, 2026-10-17)
      button.type = "button"; button.className = "b-save";
      button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11M7 11l5 5 5-5M5 20h14"/></svg>';
      button.title = T.image_title || "Diese Kachel als Bild (PNG) speichern";
      button.setAttribute("aria-label", button.title);
      tile.insertBefore(button, tile.firstChild);
    });
  }
  if (page.hasAttribute("data-images")) {
    saveButtons();
    tiles.addEventListener("click", function (e) {
      var button = e.target.closest(".b-save");
      if (!button) return;
      var tile = button.closest(".b-tile"), title = tile.querySelector(".b-title");
      image(tile).then(function (blob) {
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = ((title ? title.textContent : "kachel").trim().replace(/[^0-9A-Za-zÄÖÜäöüß]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "kachel") + ".png";
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
      }).catch(function () { button.textContent = T.image_failed || "nicht möglich"; });
    });
  }

  var full = document.getElementById("board-full");
  if (full) {
    if (!document.documentElement.requestFullscreen) full.hidden = true;
    full.addEventListener("click", function () {
      if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen();
    });
    document.addEventListener("fullscreenchange", function () { full.textContent = document.fullscreenElement ? full.dataset.on : full.dataset.off; });
  }
})();
