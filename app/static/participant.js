// After a failed "Next" the focus goes where the answer is given: the element's own target (data-focus-target: the
// image to mark, "Marker in der Mitte"), else its first field or button, else the message itself. Elements made of
// buttons only (ranking, card sorting, marking in a text) kept the focus at the top of the page, and a way out like
// "Ich kann hier nichts markieren" (data-not-first) must not be where the focus lands (UI/UX audit 2026-10-16, 77).
function focusGap(gap, item) {
  var target = gap.querySelector("[data-focus-target]") || item.querySelector("[data-focus-target]")
    || gap.querySelector("input:not([type=hidden]):not([disabled]):not([data-not-first]), textarea, select, "
                         + "button:not([disabled]):not([data-not-first]), [tabindex='0']");
  if (!target) {
    target = item.querySelector("[data-item-message]");
    if (target) target.setAttribute("tabindex", "-1");
  }
  if (target && !target.matches("input, select, textarea, button, a[href], [tabindex]")) target.setAttribute("tabindex", "-1");
  if (target) try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
}

// Two or more messages on a page (errors or nudges of a failed "Next"): a box above the first question names them, each
// with a link to its question; the page then starts at the box (UI/UX audit
// 2026-10-16, 92). `scope` holds the questions (the page, or one page of a block loaded at once); returns the box or null.
function messageSummary(scope, marked) {
  var old = scope.querySelector("[data-summary]");
  if (old) old.parentNode.removeChild(old);
  var items = scope.querySelector(".p-items");
  if (!items || marked.length < 2) return null;
  var errors = Array.prototype.some.call(marked, function (m) { return m.classList.contains("has-error"); });
  var box = document.createElement("div");
  box.className = "p-summary" + (errors ? "" : " is-nudge");
  box.setAttribute("data-summary", "");
  box.setAttribute("tabindex", "-1");
  var title = document.createElement("h2");
  title.className = "p-summary-title";
  title.textContent = uiT("Bitte prüfen Sie {n} Fragen:", "Please check {n} questions:", {n: marked.length});
  var list = document.createElement("ul");
  // the same message under every question ("Diese Frage ist noch offen …") is said once under the title, not on
  // every line: on a phone the box otherwise filled the screen (2026-10-17)
  var texts = Array.prototype.map.call(marked, function (item) {
    var m = item.querySelector("[data-item-message]");
    return m ? m.textContent.trim() : "";
  });
  var same = texts.every(function (t) { return t === texts[0]; });
  Array.prototype.forEach.call(marked, function (item) {
    var q = item.querySelector(".p-question"), message = item.querySelector("[data-item-message]");
    var name = "";
    if (q) {  // the question without its star and "(required)"
      var copy = q.cloneNode(true);
      copy.querySelectorAll(".p-required").forEach(function (r) { r.parentNode.removeChild(r); });
      name = copy.textContent.replace(/\s+/g, " ").trim();
    }
    var li = document.createElement("li"), a = document.createElement("a");
    a.href = "#" + item.id;
    a.textContent = name || (message ? message.textContent : item.id);
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var gap = item.querySelector(".p-missing") || item;
      gap.scrollIntoView({ block: "center" });
      focusGap(gap, item);
    });
    li.appendChild(a);
    if (name && message && !same) li.appendChild(document.createTextNode(" – " + message.textContent));
    list.appendChild(li);
  });
  box.appendChild(title);
  if (same && texts[0]) {
    var common = document.createElement("p");
    common.className = "p-summary-note";
    common.textContent = texts[0];
    box.appendChild(common);
  }
  box.appendChild(list);
  // above the note on the star, which stands right before the questions (participant/_block.html)
  var note = items.previousElementSibling;
  var before = note && note.classList.contains("p-required-note") ? note : items;
  before.parentNode.insertBefore(box, before);
  return box;
}

// Platform texts in the language of the page (app/ui_i18n.py): German, English, or the translation the page carries
// in <body data-ui> (only the texts the scripts need). uiT("{n} Wörter", "{n} words", {n: 3}).
window.uiT = function (de, en, values) {
  // the page carries the texts of its language that differ (also German with "du", whose <html lang> is "de")
  var lang = document.documentElement.lang || "de", t = {};
  try { t = JSON.parse(document.body.getAttribute("data-ui") || "{}"); } catch (e) { t = {}; }
  var text = t[en] || (lang === "de" ? de : en);
  Object.keys(values || {}).forEach(function (k) { text = text.split("{" + k + "}").join(values[k]); });
  return text;
};

// A page that came as the answer to a sent page (participate.page, "_direct"; body[data-replace]): its history entry
// becomes a plain GET of the same address, so reloading or coming back to it asks the server for the current page and
// never sends the form again (no "send again?" question of the browser)
(function () {
  if (!document.body.hasAttribute("data-replace") || !window.history || !history.replaceState) return;
  try { history.replaceState(history.state, "", location.href); } catch (e) { /* the browser keeps the entry */ }
})();

// A dropped connection on "Next" must not take the answers with it (page.html, form[data-keep]): the inputs of the
// page stay in the tab (sessionStorage) until the next page loads, and come back after a reload or the browser's
// way back to the page. Runs before the item scripts, so those start from the restored values as from the server's.
// What the server rendered wins: a field is filled from the tab only while the server shows what it showed when the
// person began (an error page shows what was sent, Back what is stored). Plain fields of every kind; hidden
// fields only with data-keep (sliders, ranking, highlighting, marks on an image), the rest of their state lives in
// the item's script. Never files, the trap fields, the page's own fields (csrf, pos, time) or the paradata ("_…").
var keepPage = null;  // {form, save, restored} on a page that keeps its inputs
(function () {
  var PREFIX = "keep:";
  var SKIP = /^(csrf_token|pos|page_lang|nudge_ack|nav|time_up|time_more|more_info|ki_kennwort|set_lang)$|^_/;
  var store = null;
  try { store = window.sessionStorage; store.getItem(PREFIX); } catch (e) { store = null; }  // private mode, blocked
  var form = document.querySelector(".p-container form[method=post][data-keep]");
  function drop(except) {
    if (!store) return;
    try {
      for (var i = store.length - 1; i >= 0; i--) {
        var k = store.key(i);
        if (k && k.indexOf(PREFIX) === 0 && k !== except) store.removeItem(k);
      }
    } catch (e) { /* nothing kept */ }
  }
  // the end of the participation: nothing of it stays in the tab (a shared computer). Not the page saying that the
  // survey is closed for a while (data-keep-inputs): the person goes on later, on the page with what was typed
  // (audit data storage 2026-10-21, A2)
  if (document.querySelector(".p-end:not([data-keep-inputs])")) drop(null);
  if (!form || !store) return;
  var key = PREFIX + form.getAttribute("data-keep");
  drop(key);  // another page or participation: sent, or no longer the person's

  function kept(el) {
    var t = (el.type || "").toLowerCase();
    if (!el.name || SKIP.test(el.name) || /^(file|password|submit|button|reset|image)$/.test(t)) return false;
    if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA" && el.tagName !== "SELECT") return false;
    if (t === "hidden" && !el.hasAttribute("data-keep")) return false;
    return !el.closest(".p-trap");
  }
  function fields() { return Array.prototype.filter.call(form.elements, kept); }
  // {name: [values]}: the ticked values of radios and checkboxes, the chosen options, else each field's text in order
  function snapshot() {
    var out = {};
    fields().forEach(function (el) {
      var list = out[el.name] || (out[el.name] = []);
      if (el.type === "radio" || el.type === "checkbox") { if (el.checked) list.push(el.value); }
      else if (el.tagName === "SELECT" && el.multiple) {
        Array.prototype.forEach.call(el.options, function (o) { if (o.selected) list.push(o.value); });
      } else list.push(el.value);
    });
    return out;
  }
  var same = function (a, b) { return JSON.stringify(a) === JSON.stringify(b); };
  var rendered = snapshot(), saved = null, touched = [];
  try { saved = JSON.parse(store.getItem(key) || "null"); } catch (e) { saved = null; }
  if (saved && saved.base && saved.now) {
    var els = fields();
    Object.keys(saved.now).forEach(function (name) {
      var want = saved.now[name];
      if (!(name in rendered) || !(name in saved.base) || !same(rendered[name], saved.base[name]) || same(rendered[name], want)) return;
      var group = els.filter(function (el) { return el.name === name; }), n = 0;
      var rank = group[0].closest("[data-rank]");
      if (rank) {  // the order of the list is the answer: the items move, each keeps its own code
        want.forEach(function (code) {
          var li = rank.querySelector('.p-rank-item[data-code="' + (window.CSS && CSS.escape ? CSS.escape(code) : code) + '"]');
          if (li) rank.appendChild(li);
        });
        rank.querySelectorAll(".p-rank-item").forEach(function (li, i) { li.querySelector(".p-rank-pos").textContent = i + 1; });
        return;
      }
      group.forEach(function (el) {
        if (el.type === "radio" || el.type === "checkbox") el.checked = want.indexOf(el.value) >= 0;
        else if (el.tagName === "SELECT" && el.multiple) {
          Array.prototype.forEach.call(el.options, function (o) { o.selected = want.indexOf(o.value) >= 0; });
        } else if (n < want.length) el.value = want[n++];
        else return;
        touched.push(el);
        var slider = el.type === "hidden" && el.closest("[data-slider]"), range = slider && slider.querySelector("input[type=range]");
        if (range && el.value !== "") range.value = el.value;  // the slider's own script shows it as moved
        var hl = el.hasAttribute("data-hl-value") && el.closest("[data-hl]");
        if (hl) {  // the words carry their markers (the script reads them from there)
          var marks = {};
          try { marks = JSON.parse(el.value || "{}") || {}; } catch (e) { marks = {}; }
          hl.querySelectorAll("[data-hl-w]").forEach(function (w) {
            if (marks[w.dataset.hlW]) w.dataset.cat = marks[w.dataset.hlW]; else delete w.dataset.cat;
          });
        }
      });
    });
    form.querySelectorAll("[data-rank-touched]").forEach(function (t) {
      var ok = form.querySelector('[data-rank-ok="' + t.getAttribute("data-rank-touched") + '"]');
      if (ok && t.value) ok.classList.add("is-done");
    });
  }
  var base = rendered;
  function save() {
    try { store.setItem(key, JSON.stringify({ base: base, now: snapshot() })); } catch (e) { /* full: nothing kept */ }
  }
  keepPage = { form: form, save: save, restored: touched };
})();

// Participant-side helpers. Loaded on participant pages and in editor previews.

// A number rounded half up (kaufmännisch) from its shortest decimal text: 2.5 -> 3, 1.005 -> 1.01 (toFixed and
// toLocaleString round the binary value: 1.005 -> 1.00); the same as app/units.py half_up
function roundHalfUp(v, places) {
  var p = places || 0, a = Math.abs(v), s = v < 0 ? -1 : 1;
  if (!isFinite(a)) return v;
  if (String(a).indexOf("e") >= 0) return s * Math.round(a * Math.pow(10, p)) / Math.pow(10, p);
  return s * Number(Math.round(Number(a + "e" + p)) + "e-" + p);
}

// The languages that write numbers like English: a decimal point, commas between thousands (app/units.py,
// writes_like_english)
// The arrow keys that move on in the reading direction: right, or left on a page written right to left (Arabic)
function forward() { return document.documentElement.dir === "rtl" ? "ArrowLeft" : "ArrowRight"; }
function back() { return document.documentElement.dir === "rtl" ? "ArrowRight" : "ArrowLeft"; }

function pointLang(lang) { return lang === "en" || lang === "zh-Hans" || lang === "ar"; }

// A slider value with the unit of its box (data-prefix/-suffix/-places/-signed/-lang, see app/units.py)
function format(box, v) {
  if (!box.hasAttribute("data-places")) return pointLang(document.documentElement.lang) ? String(v) : String(v).replace(".", ",");
  var places = parseInt(box.dataset.places, 10) || 0, en = pointLang(box.dataset.lang);
  var text = Math.abs(roundHalfUp(v, places)).toLocaleString(en ? "en-GB" : "de-DE", { minimumFractionDigits: places, maximumFractionDigits: places });
  var sign = v < 0 ? "−" : (box.dataset.signed && v > 0 ? "+" : "");
  return (box.dataset.prefix || "") + sign + text + (box.dataset.suffix || "");
}

(function () {
  // Sliders without a starting value: the hidden input stays empty until first touch
  document.querySelectorAll("[data-slider]").forEach(function (box) {
    var range = box.querySelector("input[type=range]");
    var hidden = box.querySelector("input[type=hidden]");
    var out = box.querySelector("output");
    function sync() {
      box.classList.remove("untouched");
      range.setAttribute("aria-valuetext", format(box, parseFloat(range.value)));  // with its unit, not "not set yet"
      var item = box.closest(".p-item"), hint = item && item.querySelector("[data-slider-hint]");
      if (hint && !item.querySelector("[data-slider].untouched")) hint.hidden = true;
      hidden.value = range.value;
      if (out && box.hasAttribute("data-show-value")) out.textContent = format(box, parseFloat(range.value));
      var pct = (range.value - range.min) / (range.max - range.min) * 100;
      box.style.setProperty("--pct", pct + "%");
      if (box.dataset.signed) {  // scales around zero: the bar grows from the zero point
        var zero = (0 - range.min) / (range.max - range.min) * 100;
        box.style.setProperty("--lo", Math.min(zero, pct) + "%");
        box.style.setProperty("--hi", Math.max(zero, pct) + "%");
      }
    }
    range.addEventListener("input", sync);
    // a tap on the track of an untouched slider counts as an answer too
    // and takes back "weiß nicht" as a moved slider does (G3): a tap without a change of value fires no input event,
    // and the tick, which wins on the server, would stay under a slider that shows a value
    range.addEventListener("pointerdown", function () {
      setTimeout(function () {
        sync();
        var item = box.closest(".p-item"), dk = item && item.querySelector("[data-dont-know]");
        if (dk && dk.checked) { dk.checked = false; dk.dispatchEvent(new Event("change", { bubbles: true })); }
      }, 0);
    });
    if (hidden.value !== "") sync();
  });

  // After a failed "Next": every marked question links its inputs to its message; the page goes to the
  // first gap (the first open row of a matrix or tick, else the question), and after an error the focus too.
  var markedItems = document.querySelectorAll(".has-error, .has-nudge");
  Array.prototype.forEach.call(markedItems, function (item) {
    var message = item.querySelector("[data-item-message]");
    var gaps = item.querySelectorAll(".p-missing");
    var scope = gaps.length ? gaps : [item];
    Array.prototype.forEach.call(scope, function (part) {
      part.querySelectorAll("input:not([type=hidden]), textarea, select").forEach(function (input) {
        // added to what the server wrote (hints, units: D3), not in their place
        var ids = (input.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
        if (message && ids.indexOf(message.id) < 0) input.setAttribute("aria-describedby", [message.id].concat(ids).join(" "));
        if (item.classList.contains("has-error")) input.setAttribute("aria-invalid", "true");
      });
    });
  });
  // the tempo note stands above the questions: the page starts there, and a screen reader reads it
  var tempoNote = document.querySelector("[data-tempo-note]");
  if (tempoNote && !document.querySelector(".has-error")) {
    tempoNote.scrollIntoView({ block: "start" });
    try { tempoNote.focus({ preventScroll: true }); } catch (e) { tempoNote.focus(); }
  }
  var marked = markedItems[0];
  var summary = marked ? messageSummary(marked.closest(".p-bpage") || document, markedItems) : null;
  if (summary && !(tempoNote && !document.querySelector(".has-error"))) {
    summary.scrollIntoView({ block: "start" });
    try { summary.focus({ preventScroll: true }); } catch (e) { summary.focus(); }
  } else if (marked && !summary && !(tempoNote && !marked.classList.contains("has-error"))) {
    var gap = marked.querySelector(".p-missing") || marked;
    var tall = gap.getBoundingClientRect().height > window.innerHeight * 0.8;
    gap.scrollIntoView({ block: tall ? "start" : "center" });
    if (marked.classList.contains("has-error")) focusGap(gap, marked);
  }

})();

// Text fields with a limit (items/text_input): how many characters are left, from the start below 500, else from 80 %
// of the limit; a screen reader hears it once the typing rests, and only near the limit (UI/UX audit 2026-10-16, 102)
(function () {
  document.querySelectorAll("[data-count-for]").forEach(function (line) {
    var field = document.getElementById(line.getAttribute("data-count-for"));
    var say = line.parentNode.querySelector("[data-count-say]"), max = parseInt(line.dataset.max, 10), timer = null;
    if (!field || !max) return;
    var always = !line.hidden;
    function update(e) {
      var used = field.value.length, near = used >= max * 0.8;
      line.hidden = !(always || near);
      line.classList.toggle("is-near", near);
      line.textContent = line.dataset.left.replace("{n}", Math.max(0, max - used));
      if (!e || !near || !say) return;
      clearTimeout(timer);
      timer = setTimeout(function () { say.textContent = line.textContent; }, 800);
    }
    field.addEventListener("input", update);
    update();
  });
})();

// "Weiß nicht" of a rating or a date (data-dont-know, V41): the tick takes back the answer given, an answer takes
// back the tick
(function () {
  function sync(e) {
    var el = e.target, item = el.closest && el.closest(".p-item"), dk = item && item.querySelector("[data-dont-know]");
    if (!dk || el.matches("input[type=hidden]")) return;
    if (el === dk) {
      if (!dk.checked) return;
      item.querySelectorAll("input[type=radio]").forEach(function (i) { i.checked = false; });
      item.querySelectorAll("select").forEach(function (s) { s.value = ""; });
      item.querySelectorAll("[data-slider]").forEach(function (box) {
        box.classList.add("untouched");
        var hidden = box.querySelector("input[type=hidden]"), out = box.querySelector("output");
        if (hidden) hidden.value = "";
        if (out) out.textContent = "";
        var hint = item.querySelector("[data-slider-hint]");
        if (hint) hint.hidden = false;
      });
      var stars = item.querySelector("[data-stars]");
      if (stars) stars.dispatchEvent(new Event("change"));
    } else if (el.matches("input[type=radio], input[type=range], select")) {
      dk.checked = false;
    }
  }
  document.addEventListener("change", sync);
  document.addEventListener("input", sync);
})();

// Stars: fill "this one and all lower" for the chosen and for the hovered star (the stars stand in their
// natural order, so CSS alone cannot reach the earlier ones in every browser)
document.querySelectorAll("[data-stars]").forEach(function (box) {
  var stars = Array.prototype.slice.call(box.querySelectorAll(".p-star"));
  function upTo(index, name) { stars.forEach(function (s, i) { s.classList.toggle(name, i <= index); }); }
  function paint() {
    var chosen = -1;
    stars.forEach(function (s, i) { if (s.querySelector("input").checked) chosen = i; });
    upTo(chosen, "on");
    var text = box.querySelector("[data-star-value]");  // "3 / 5" beside the stars (R55)
    if (text) text.textContent = chosen < 0 ? "" : stars[chosen].querySelector("input").value + " / " + box.dataset.of;
  }
  box.classList.add("is-live");
  box.addEventListener("change", paint);
  stars.forEach(function (s, i) {
    s.addEventListener("mouseenter", function () { box.classList.add("hovering"); upTo(i, "hov"); });
  });
  box.addEventListener("mouseleave", function () { box.classList.remove("hovering"); upTo(-1, "hov"); });
  paint();
});

// Conjoint: clicking anywhere in an alternative's column selects it; highlight the chosen column
document.querySelectorAll("[data-cj]").forEach(function (box) {
  function paint() {
    var checked = box.querySelector("input[type=radio]:checked");
    var alt = checked ? checked.value : null;
    box.querySelectorAll("td[data-alt]").forEach(function (td) { td.classList.toggle("on", td.dataset.alt === alt); });
  }
  box.addEventListener("click", function (e) {
    var td = e.target.closest("td[data-alt]");
    if (!td) return;
    var radio = box.querySelector('.cj-choose td[data-alt="' + td.dataset.alt + '"] input');
    if (radio && !radio.checked) {
      radio.checked = true;
      radio.dispatchEvent(new Event("change", { bubbles: true }));  // lets auto-advance notice the choice
    }
    paint();
  });
  box.addEventListener("change", paint);
  paint();
  // dual response (W46): the follow-up question appears once an option is chosen and names it ("Option 2")
  var dual = document.querySelector('[data-cj-dual="' + box.dataset.cjItem + '"]');
  if (!dual) return;
  var question = dual.querySelector(".p-question");
  var named = dual.dataset.cjNamed;
  function showDual() {
    var checked = box.querySelector("input[type=radio]:checked");
    var answered = dual.querySelector("input:checked");
    dual.hidden = !checked && !answered;
    if (named && question && checked && checked.dataset.cjName) question.textContent = named.replace("%s", checked.dataset.cjName);
  }
  box.addEventListener("change", showDual);
  box.addEventListener("click", showDual);
  showDual();
});

// MaxDiff: one statement cannot be both best and worst: choosing it in one column clears the other
document.querySelectorAll("[data-md]").forEach(function (box) {
  function paint() {
    box.querySelectorAll("tr[data-md-row]").forEach(function (tr) {
      tr.classList.toggle("md-b", !!tr.querySelector(".md-best input:checked"));
      tr.classList.toggle("md-w", !!tr.querySelector(".md-worst input:checked"));
    });
  }
  box.addEventListener("change", function (e) {
    var tr = e.target.closest("tr[data-md-row]");
    if (tr && e.target.checked) {
      var other = tr.querySelector(e.target.closest(".md-best") ? ".md-worst input" : ".md-best input");
      if (other) other.checked = false;
    }
    paint();
  });
  paint();
});

// An answer option with a text field ("Other: ..."): typing chooses the option, and choosing it with the
// pointer puts the cursor into the field (not with the keyboard: arrow keys pass through radio buttons).
(function () {
  document.querySelectorAll(".p-option-text").forEach(function (box) {
    var pick = box.querySelector("input[type=radio], input[type=checkbox]"), text = box.querySelector(".p-other");
    if (!pick || !text) return;
    text.addEventListener("input", function () {
      if (text.value.trim() === "" || pick.checked) return;
      pick.checked = true;
      pick.dispatchEvent(new Event("change", { bubbles: true }));
    });
    box.addEventListener("click", function (e) {
      if (e.target === text || !e.detail) return;
      setTimeout(function () { if (pick.checked) text.focus(); }, 0);  // after the label has passed the click on
    });
  });
})();

// Single choice as a drop-down list: the text field of an option ("Other: ...") shows only while that
// option is chosen. Without this script all text fields stay visible; the server ignores the others.
document.querySelectorAll("[data-select]").forEach(function (box) {
  var select = box.querySelector("select"), fields = box.querySelectorAll("[data-select-text]");
  if (!select || !fields.length) return;
  function sync() {
    Array.prototype.forEach.call(fields, function (field) { field.hidden = field.dataset.selectText !== select.value; });
  }
  select.addEventListener("change", sync);
  sync();
});

// Single choice as a search field (data-search; items/choice.py): a text field in front of the drop-down list
// suggests the options that hold every typed word (ignoring case and accents; those that start with it first, at
// most 50). Picking one sets the list, which stays the field that is sent and is hidden. The options with a text
// field ("nicht in der Liste, und zwar") always come last; picking one carries what was typed into its field.
// Text that matches no option leaves the answer empty: leaving the field says so under it, with a button for each
// option with a text field that takes the text along, and "Next" asks once more before the page goes (X37).
// Combobox pattern of the WAI-ARIA Authoring Practices.
document.querySelectorAll("[data-search]").forEach(function (box) {
  var select = box.querySelector("select");
  if (!select) return;
  var MAX = 50, id = select.id, active = -1, shown = [];
  function plain(s) { return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/\s+/g, " ").trim(); }
  var options = Array.prototype.filter.call(select.options, function (o) { return o.value; }).map(function (o) {
    return { code: o.value, label: o.textContent.trim(), key: plain(o.textContent),
             other: !!box.querySelector('[data-select-text="' + o.value.replace(/"/g, '\\"') + '"]') };
  });
  var others = options.filter(function (o) { return o.other; });
  var input = document.createElement("input"), list = document.createElement("ul"), status = document.createElement("span");
  input.type = "text";
  input.dir = "auto";
  input.className = "p-search";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = uiT("Zum Suchen tippen", "Type to search");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  ["aria-label", "aria-required"].forEach(function (a) { if (select.hasAttribute(a)) input.setAttribute(a, select.getAttribute(a)); });
  list.className = "p-search-list";
  list.id = id + "-suggest";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  input.setAttribute("aria-controls", list.id);
  status.className = "p-sr-only";
  status.setAttribute("role", "status");
  select.id = id + "-list";  // the question's <label for> now names the search field
  input.id = id;
  select.hidden = true;
  select.tabIndex = -1;
  select.parentNode.insertBefore(input, select);
  select.parentNode.insertBefore(list, select);
  select.parentNode.insertBefore(status, select);
  // typed text without a pick: a visible note under the field (X37)
  var note = document.createElement("p");
  note.className = "p-search-note";
  note.id = id + "-note";
  note.hidden = true;
  select.parentNode.insertBefore(note, select);
  var current = options.filter(function (o) { return o.code === select.value; })[0];
  if (current) input.value = current.label;
  function set(code) {
    if (select.value === code) return;
    select.value = code;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }
  function mark(n) {
    var items = list.children;
    if (active >= 0 && items[active]) items[active].setAttribute("aria-selected", "false");
    active = n;
    if (n < 0 || !items[n]) { input.removeAttribute("aria-activedescendant"); return; }
    items[n].setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", items[n].id);
    items[n].scrollIntoView({ block: "nearest" });
  }
  function open() {
    var q = plain(input.value), words = q ? q.split(" ") : [];
    var hits = options.filter(function (o) {
      return !o.other && words.every(function (w) { return o.key.indexOf(w) >= 0; });
    });
    hits.sort(function (a, b) { return (b.key.indexOf(q) === 0) - (a.key.indexOf(q) === 0); });
    var n = hits.length;
    shown = hits.slice(0, MAX).concat(others);
    list.textContent = "";
    shown.forEach(function (o, k) {
      var li = document.createElement("li");
      li.id = list.id + "-" + k;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.textContent = o.label;
      if (o.other) li.className = "is-other";
      list.appendChild(li);
    });
    if (!shown.length || !q && !others.length) { close(); return; }
    active = -1;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    status.textContent = n ? (n === 1 ? uiT("1 Vorschlag", "1 suggestion") : uiT("{n} Vorschläge", "{n} suggestions", { n: n }))
                           : uiT("Nichts gefunden.", "Nothing found.");
  }
  function pick(k, given) {
    var o = given || shown[k];
    if (!o) return;
    var typed = input.value.trim();
    input.value = o.label;
    set(o.code);
    close();
    hint(false);
    if (o.other) {
      var field = box.querySelector('[data-select-text="' + o.code.replace(/"/g, '\\"') + '"]');
      if (field) {
        if (!field.value.trim() && typed && plain(typed) !== o.key) field.value = typed;
        field.focus();
      }
    }
  }
  function unpicked() { return !select.value && !!input.value.trim(); }
  function hint(show) {
    note.hidden = !show;
    if (!show) { input.removeAttribute("aria-describedby"); return; }
    note.textContent = "";
    var text = document.createElement("span");
    text.textContent = others.length
      ? uiT("„{text}“ ist kein Vorschlag der Liste. Bitte wählen Sie einen Vorschlag oder:", "“{text}” is not one of the suggestions. Please pick a suggestion or:", { text: input.value.trim() })
      : uiT("„{text}“ ist kein Vorschlag der Liste. Bitte wählen Sie einen Vorschlag.", "“{text}” is not one of the suggestions. Please pick a suggestion.", { text: input.value.trim() });
    note.appendChild(text);
    others.forEach(function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "p-search-other";
      b.textContent = o.label;
      b.addEventListener("click", function () { pick(shown.indexOf(o) >= 0 ? shown.indexOf(o) : -1, o); hint(false); });
      note.appendChild(document.createTextNode(" "));
      note.appendChild(b);
    });
    input.setAttribute("aria-describedby", note.id);
  }
  var asked = false;
  input.addEventListener("input", function () {
    var q = plain(input.value), same = options.filter(function (o) { return o.key === q; })[0];
    set(same ? same.code : "");
    if (!note.hidden && !unpicked()) hint(false);
    open();
  });
  input.addEventListener("focus", function () { if (!select.value && input.value) open(); });
  input.addEventListener("blur", function () {
    setTimeout(function () {
      if (document.activeElement === input) return;
      close();
      if (!note.contains(document.activeElement)) hint(unpicked());
    }, 150);
  });
  var form = box.closest("form");
  if (form) form.addEventListener("submit", function (e) {
    var by = e.submitter;
    if (by && (by.formNoValidate || (by.name === "nav" && by.value === "back"))) return;
    if (!unpicked() || asked) return;
    asked = true;  // once: a second "Next" sends the page as it is (the answer stays empty)
    e.preventDefault();
    hint(true);
    input.scrollIntoView({ block: "center" });
    input.focus();
  });
  input.addEventListener("keydown", function (e) {
    var n = list.children.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (list.hidden) open();
      if (list.children.length) mark((active + 1) % list.children.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (n && !list.hidden) mark(active <= 0 ? n - 1 : active - 1);
    } else if (e.key === "Enter" && !list.hidden) {
      e.preventDefault();  // a pick, not sending the page
      pick(active >= 0 ? active : 0);
    } else if (e.key === "Escape" && !list.hidden) {
      e.preventDefault();
      close();
    }
  });
  list.addEventListener("mousedown", function (e) { e.preventDefault(); });  // keeps the focus in the field
  list.addEventListener("click", function (e) {
    var li = e.target.closest("li");
    if (li) pick(Array.prototype.indexOf.call(list.children, li));
  });
});

// Video / audio stimulus (items/stimulus.py, data-media): an own control bar instead of the browser's, which keeps
// the rules of data-seek (back: back, but not beyond the furthest point reached; free; none: only play and pause;
// once: play once, no pause, no replay), and the playback log in the hidden field: [ms since the page was shown,
// event, values]. "seg" closes a stretch that played without a jump (from, to, 1 = while the window was in the
// background); it ends at every pause, jump, end, change of visibility and when the page is sent. The server works
// the figures out of the events (stimulus.figures) and checks them (stimulus.read_log).
document.querySelectorAll("[data-media]").forEach(function (box) {
  var el = box.querySelector("[data-media-el]"), bar = box.querySelector("[data-media-bar]");
  var field = box.querySelector("[data-media-log]");
  if (!el || !bar || !field) return;
  var rule = box.dataset.seek, dur = parseFloat(box.dataset.dur) || 0, t0 = performance.now();
  var play = bar.querySelector("[data-media-play]"), seek = bar.querySelector("[data-media-seek]");
  var time = bar.querySelector("[data-media-time]"), mute = bar.querySelector("[data-media-mute]");
  var cc = bar.querySelector("[data-media-cc]"), full = bar.querySelector("[data-media-full]");
  var status = box.querySelector("[data-media-status]");
  var log = [], MAX = 500, from = null, last = 0, furthest = 0, muted = null, started = false, jumping = null;
  el.controls = false;
  el.removeAttribute("controls");
  bar.hidden = false;
  if (rule === "none" || rule === "once") seek.disabled = true;
  function r(x) { return Math.round(x * 1000) / 1000; }
  function add(row) {
    if (log.length < MAX) log.push([Math.round(performance.now() - t0)].concat(row));
    field.value = JSON.stringify(log);
  }
  function close(at) {
    if (from === null) return;
    var to = Math.max(at, from);
    if (to - from > 0.05) add(["seg", r(from), r(to), document.hidden ? 1 : 0]);
    from = null;
  }
  function clock(s) { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + ("0" + s % 60).slice(-2); }
  function total() { return dur || (isFinite(el.duration) ? el.duration : 0); }
  function paint() {
    var playing = !el.paused && !el.ended;
    play.textContent = playing ? uiT("Pause", "Pause") : (el.ended ? uiT("Noch einmal", "Play again") : uiT("Abspielen", "Play"));
    play.disabled = rule === "once" && (playing || el.ended);
    if (rule === "once" && el.ended) play.hidden = true;
    time.textContent = clock(el.currentTime) + " / " + clock(total());
    if (document.activeElement !== seek) seek.value = el.currentTime;
    // the position read as minutes and seconds, "0:37 of 2:10", not as "37.4" (UI/UX audit 2026-10-16, 101)
    seek.setAttribute("aria-valuetext", uiT("{t} von {d}", "{t} of {d}", {t: clock(parseFloat(seek.value)), d: clock(total())}));
  }
  el.addEventListener("loadedmetadata", function () {
    if (!dur && isFinite(el.duration)) { dur = el.duration; seek.max = dur; }
    paint();
  });
  play.addEventListener("click", function () {
    if (el.paused || el.ended) {
      if (el.ended && rule === "once") return;
      var p = el.play();
      if (p && p.catch) p.catch(function () {});
    } else if (rule !== "once") el.pause();
  });
  el.addEventListener("play", function () {
    started = true;
    add(["play", r(el.currentTime)]);
    from = el.currentTime;
    var now = el.muted || el.volume === 0;
    if (now !== muted) { muted = now; add(["mute", now ? 1 : 0]); }
    paint();
  });
  el.addEventListener("pause", function () {
    if (el.ended) return;
    close(el.currentTime);
    add(["pause", r(el.currentTime)]);
    paint();
  });
  el.addEventListener("ended", function () {
    close(total() || el.currentTime);
    add(["end", r(total() || el.currentTime)]);
    furthest = total();
    status.textContent = uiT("Zu Ende.", "Ended.");
    paint();
  });
  el.addEventListener("timeupdate", function () {
    var t = el.currentTime;
    if (jumping === null && !el.seeking) {
      if (t - last >= 0 && t - last < 2) furthest = Math.max(furthest, t);
      last = t;
    }
    paint();
  });
  el.addEventListener("seeking", function () {
    var to = el.currentTime;
    if ((rule === "back" && to > furthest + 0.5) || rule === "none" || rule === "once") {
      if (Math.abs(to - last) > 0.3) { el.currentTime = last; return; }  // not allowed: back to where it was
    }
    if (Math.abs(to - last) < 0.05) return;
    close(last);
    add(["seek", r(last), r(to)]);
    last = to;
    if (!el.paused) from = to;
  });
  seek.addEventListener("input", function () {
    var to = parseFloat(seek.value);
    if (rule === "back" && to > furthest) { to = furthest; seek.value = to; }
    el.currentTime = to;
    paint();
  });
  el.addEventListener("ratechange", function () { add(["rate", el.playbackRate]); });
  el.addEventListener("volumechange", function () {
    var now = el.muted || el.volume === 0;
    if (started && now !== muted) { muted = now; add(["mute", now ? 1 : 0]); }
    mute.textContent = el.muted ? uiT("Ton an", "Unmute") : uiT("Ton aus", "Mute");
    mute.setAttribute("aria-pressed", el.muted ? "true" : "false");
  });
  mute.addEventListener("click", function () { el.muted = !el.muted; });
  el.addEventListener("waiting", function () { if (started) add(["stall", r(el.currentTime)]); });
  el.addEventListener("error", function () {
    add(["error", el.error ? el.error.code : 0]);
    box.querySelector("[data-media-error]").hidden = false;
    bar.hidden = true;
  });
  document.addEventListener("visibilitychange", function () {
    var playing = !el.paused && !el.ended;
    if (playing) {  // the stretch before the change keeps its own flag
      var hiddenNow = document.hidden;
      if (from !== null && el.currentTime - from > 0.05) add(["seg", r(from), r(el.currentTime), hiddenNow ? 0 : 1]);
      from = el.currentTime;
    }
    add([document.hidden ? "hide" : "show"]);
  });
  if (full) {
    full.addEventListener("click", function () {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (el.requestFullscreen) el.requestFullscreen();
      else if (el.webkitEnterFullscreen) el.webkitEnterFullscreen();  // iPhone: only the video itself
    });
    document.addEventListener("fullscreenchange", function () { add(["full", document.fullscreenElement === el ? 1 : 0]); });
  }
  var vtt = box.querySelector("template[data-media-vtt]");
  if (vtt && el.tagName === "VIDEO") {
    var track = document.createElement("track");
    track.kind = "captions";
    track.label = uiT("Untertitel", "Captions");
    track.srclang = document.documentElement.lang || "de";
    track.src = URL.createObjectURL(new Blob([vtt.content.textContent], { type: "text/vtt" }));
    el.appendChild(track);
    track.track.mode = "hidden";
    cc.addEventListener("click", function () {
      var on = track.track.mode !== "showing";
      track.track.mode = on ? "showing" : "hidden";
      cc.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  // the page goes: the stretch that is playing ends here; if the page stays (a check of the browser), it goes on
  var form = box.closest("form");
  if (form) form.addEventListener("submit", function () {
    if (!el.paused && !el.ended) { close(el.currentTime); from = el.currentTime; }
    field.value = JSON.stringify(log);
  }, true);
  field.value = JSON.stringify(log);
  paint();
});

// Drill-down (items/drill_down.py): each list below the first comes with all its entries in groups (optgroup
// data-parent = the entry above). The script keeps only the group of the current choice above, locks a list until
// the one above is chosen, hides a list where the branch above ends and empties the lists below a changed choice.
document.querySelectorAll("[data-drill]").forEach(function (box) {
  var levels = Array.prototype.slice.call(box.querySelectorAll("[data-drill-level]"));
  var selects = levels.map(function (l) { return l.querySelector("select"); });
  // what the page came with (after an error: the chain sent), read before the groups leave the lists (X36)
  var start = selects.map(function (s) { return s.value; });
  var groups = selects.map(function (s) {
    var out = {};
    Array.prototype.forEach.call(s.querySelectorAll("optgroup[data-parent]"), function (g) {
      out[g.dataset.parent] = Array.prototype.slice.call(g.querySelectorAll("option"));
      g.remove();
    });
    return out;
  });
  function from(n, first) {
    for (var k = Math.max(n, 1); k < selects.length; k++) {
      var s = selects[k], keep = first ? start[k] : s.value, gone = levels[k - 1].hidden;
      var above = gone ? "" : selects[k - 1].value, entries = groups[k][above] || [];
      while (s.options.length > 1) s.remove(1);
      entries.forEach(function (o) { s.appendChild(o); });
      s.value = entries.some(function (o) { return o.value === keep; }) ? keep : "";
      s.disabled = !above;
      levels[k].hidden = gone || (!!above && !entries.length);  // the branch above ends here
    }
  }
  selects.forEach(function (s, n) { s.addEventListener("change", function () { from(n + 1); }); });
  from(1, true);
});

// Personal code (items/personal_code.py, [data-code]): letters show in upper case as they are typed; the tick
// "weiß ich nicht" of a part empties and locks its field, and unticking opens it again. The server reads a ticked part
// as "don't know" whatever the field holds, so the page works without this script too.
document.querySelectorAll("[data-code]").forEach(function (box) {
  box.querySelectorAll(".p-code-row").forEach(function (row) {
    var input = row.querySelector("input[type=text]"), dk = row.querySelector("[data-code-dk]");
    function sync() {
      input.disabled = dk.checked;
      if (dk.checked) input.value = "";
    }
    input.addEventListener("input", function () {
      if (input.dataset.codeKind === "letter") input.value = input.value.toLocaleUpperCase();
    });
    dk.addEventListener("change", function () {
      sync();
      if (!dk.checked) input.focus();
    });
    sync();
  });
});

// Federal states as a map to click on: a click on a state (its area, its name, or the larger circle around a
// small state) ticks the radio button of that state, as if the button had been clicked, so required checks and
// automatic continue work as with the list. From 600 px on the buttons of the states are hidden from view but stay
// reachable: the keyboard moves through them and the map shows where the focus is. Below 600 px they stay in view
// as the choice and the map only shows it (participant.css; UI audit 2026-10-12, U38).
document.querySelectorAll("[data-state-pick]").forEach(function (box) {
  var map = box.querySelector("[data-state-map]"), chosen = box.querySelector("[data-state-chosen]");
  if (!map) return;
  function radio(code) { return box.querySelector('input[type=radio][value="' + code + '"]'); }
  function shapes(code) { return map.querySelectorAll('[data-code="' + code + '"]'); }
  function mark(name, code) {
    map.querySelectorAll("." + name).forEach(function (el) { el.classList.remove(name); });
    if (code !== null) Array.prototype.forEach.call(shapes(code), function (el) { el.classList.add(name); });
  }
  function paint() {
    var on = box.querySelector("input[type=radio]:checked"), state = on && on.closest("[data-state-option]");
    mark("is-chosen", state ? on.value : null);
    chosen.hidden = !state;
    if (state) chosen.querySelector("[data-state-name]").textContent = state.textContent.trim();
  }
  map.addEventListener("click", function (e) {
    var el = e.target.closest("[data-code]"), pick = el && radio(el.dataset.code);
    if (!pick || pick.checked) return;
    pick.checked = true;
    pick.dispatchEvent(new Event("change", { bubbles: true }));
  });
  map.addEventListener("pointerover", function (e) {
    var el = e.target.closest("[data-code]");
    mark("is-hover", el && e.pointerType === "mouse" ? el.dataset.code : null);
  });
  map.addEventListener("pointerleave", function () { mark("is-hover", null); });
  box.addEventListener("change", paint);
  box.addEventListener("focusin", function (e) {
    mark("is-focus", e.target.closest("[data-state-option]") ? e.target.value : null);
  });
  box.addEventListener("focusout", function () { mark("is-focus", null); });
  map.hidden = false;
  box.classList.add("has-map");
  paint();
});

// Side-by-side matrix (items/side_by_side.py): a table once the element is as wide as its columns need
// (data-sbs-min, worked out by the server), cards below that. Without this script the rows stay cards.
(function () {
  var boxes = document.querySelectorAll("[data-sbs]");
  if (!boxes.length) return;
  function fit(box) {
    box.classList.toggle("p-sbs-table", box.clientWidth >= (parseInt(box.getAttribute("data-sbs-min"), 10) || 0));
  }
  boxes.forEach(fit);
  // a column with a condition (data-sbs-when, E10): off until the column it depends on has one of the answers, in
  // the order of the columns, so that a chain of conditions falls with its first link; the server checks the same.
  // Before that column has any answer the cell waits (dimmed, no "entfällt"): nothing is ruled out yet (X34).
  function value(row, cid) {  // the answer of a cell; "off" for a cell switched off, null for one without answer
    var cell = row.querySelector('[data-sbs-col="' + CSS.escape(String(cid)) + '"]');
    if (!cell || cell.classList.contains("p-sbs-cell-off")) return "off";
    if (cell.classList.contains("p-sbs-cell-wait")) return null;
    var picked = cell.querySelector("input[type=radio]:checked") || cell.querySelector("select");
    return picked && picked.value ? parseInt(picked.value, 10) : null;
  }
  function decide(row) {
    var any = false;
    row.querySelectorAll("[data-sbs-col]").forEach(function (cell) {
      if (!cell.dataset.sbsWhen) return;
      var when = JSON.parse(cell.dataset.sbsWhen), got = value(row, when.col);
      var wait = got === null, off = !wait && (when.codes || []).indexOf(got) < 0;
      cell.classList.toggle("p-sbs-cell-off", off);
      cell.classList.toggle("p-sbs-cell-wait", wait);
      cell.querySelectorAll("input, select").forEach(function (f) { f.disabled = off || wait; });
      any = any || off;
    });
    var note = row.querySelector(".p-sbs-skipped");
    if (note) note.hidden = !any;
  }
  boxes.forEach(function (box) {
    box.querySelectorAll("[data-sbs-row]").forEach(function (row) {
      decide(row);
      row.addEventListener("change", function () { decide(row); });
    });
  });
  if (window.ResizeObserver) {
    var watch = new ResizeObserver(function (entries) { entries.forEach(function (e) { fit(e.target); }); });
    boxes.forEach(function (b) { watch.observe(b); });
  } else {
    window.addEventListener("resize", function () { boxes.forEach(fit); });
  }
})();

// Automatic continue: once every radio group on the page has a value after a pointer click,
// submit with "Next" after a short pause (the choice stays visible and can still be changed).
// Keyboard changes (arrow keys) never trigger it.
(function () {
  var form = document.querySelector("form[data-auto-advance]");
  if (!form) return;
  // the page on screen: in a block loaded at once (form[data-block]) its section, else the form
  function here() { return form.querySelector(".p-bpage:not([hidden])") || form; }
  function nextButton() { return here().querySelector('button[name="nav"][value="next"]:not(.p-default-submit)'); }
  if (!nextButton()) return;
  var note = document.querySelector("[data-auto-note]");
  if (note && form.querySelector("input[type=radio]")) note.hidden = false;
  var pointer = false, timer = null;
  form.addEventListener("pointerdown", function () { pointer = true; });
  form.addEventListener("keydown", function () { pointer = false; });
  function complete() {
    var groups = {};
    here().querySelectorAll("input[type=radio]").forEach(function (r) { groups[r.name] = groups[r.name] || r.checked; });
    return Object.keys(groups).length > 0 && Object.keys(groups).every(function (n) { return groups[n]; });
  }
  form.addEventListener("change", function (e) {
    if (e.target.type !== "radio" || !pointer) return;
    clearTimeout(timer);
    if (!complete()) return;
    form.classList.add("auto-advancing");
    timer = setTimeout(function () {
      var next = nextButton();
      if (!next) return;
      if (form.requestSubmit) form.requestSubmit(next); else next.click();
      form.classList.remove("auto-advancing");
    }, 450);
  });
})();

// Coarse technical information (only if the survey records it): window width, orientation, touch.
// The server turns these into classes and never stores exact values or the user-agent string.
// A theme that follows the device (light or dark): the form says which one was on screen, so the answer
// carries the look it was given under (export column "scheme")
document.querySelectorAll("form[data-scheme]").forEach(function (form) {
  if (!window.matchMedia) return;
  var input = document.createElement("input");
  input.type = "hidden"; input.name = "_scheme";
  form.appendChild(input);
  form.addEventListener("submit", function () {
    input.value = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
});

// Leaving the page and pasting (only if the survey counts them, and the consent says so): how often the window or
// tab was left while this page was open, and how often text was pasted into a text field. Counts only: never what
// was pasted or where the person went. Leaving and coming back is one time, however the browser reports it.
document.querySelectorAll("form[data-focus]").forEach(function (form) {
  function field(name) {
    var input = document.createElement("input");
    input.type = "hidden"; input.name = name; input.value = "0";
    form.appendChild(input);
    return input;
  }
  var blur = field("_fp_blur"), paste = field("_fp_paste"), away = false;
  // with data-focus-inputs: the length of what was pasted, and the input steps in text fields (a key, a word of the
  // phone keyboard, dictation; not pasting or deleting). Numbers only: never the text
  var counts = form.hasAttribute("data-focus-inputs"), pchars = counts && field("_fp_pchars"), inputs = counts && field("_fp_inputs");
  function textField(t) {
    return t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && (!t.type || t.type === "text")) || t.isContentEditable);
  }
  function left() {
    // focus inside an embedded frame of the page (a map, a video) is not leaving
    if (away || (document.visibilityState === "visible" && document.activeElement && document.activeElement.tagName === "IFRAME")) return;
    away = true;
    blur.value = String(Number(blur.value) + 1);
  }
  function back() { away = false; }
  window.addEventListener("blur", left);
  window.addEventListener("focus", back);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") left(); else back();
  });
  form.addEventListener("paste", function (e) {
    if (textField(e.target)) {
      paste.value = String(Number(paste.value) + 1);
      if (counts && e.clipboardData) pchars.value = String(Number(pchars.value) + (e.clipboardData.getData("text") || "").length);
    }
  });
  if (counts) form.addEventListener("input", function (e) {
    var kind = e.inputType || "";
    if (!e.isTrusted) return;  // a script's own event (inputs restored after a reload, keepPage): no input step
    if (textField(e.target) && !/^(insertFromPaste|insertFromDrop|delete|history)/.test(kind)) {
      inputs.value = String(Number(inputs.value) + 1);
    }
  });
});

document.querySelectorAll("form[data-paradata]").forEach(function (form) {
  function add(name, value) {
    var input = document.createElement("input");
    input.type = "hidden"; input.name = name; input.value = value;
    form.appendChild(input);
  }
  add("_pd_vw", String(window.innerWidth || document.documentElement.clientWidth || ""));
  add("_pd_or", window.innerHeight >= window.innerWidth ? "portrait" : "landscape");
  add("_pd_touch", ("ontouchstart" in window || navigator.maxTouchPoints > 0) ? "1" : "0");
});

// Ranking: drag (mouse) or ↑/↓ (touch, keyboard); hidden inputs follow the visible order
(function () {
  document.querySelectorAll("[data-rank]").forEach(function (list) {
    var id = list.dataset.rank;
    var touched = document.querySelector('[data-rank-touched="' + id + '"]');
    var ok = document.querySelector('[data-rank-ok="' + id + '"]');
    var live = document.querySelector('[data-rank-said="' + id + '"]');
    function said(li) {  // "Kosten: jetzt Platz 2" (WCAG 4.1.3)
      if (!live) return;
      var label = li.querySelector(".p-rank-label").textContent.trim();
      live.textContent = live.dataset.text.replace("%s", label).replace("%s", li.querySelector(".p-rank-pos").textContent);
    }
    function changed() {
      list.querySelectorAll(".p-rank-item").forEach(function (li, n) { li.querySelector(".p-rank-pos").textContent = n + 1; });
      touched.value = "1";
      if (ok) ok.classList.add("is-done");
      var item = list.closest(".p-item");
      if (item) item.classList.remove("has-error", "has-nudge");
    }
    list.addEventListener("click", function (e) {
      var li = e.target.closest(".p-rank-item");
      if (!li) return;
      if (e.target.closest(".p-rank-up") && li.previousElementSibling) {
        list.insertBefore(li, li.previousElementSibling); changed(); e.target.closest("button").focus(); said(li);
      } else if (e.target.closest(".p-rank-down") && li.nextElementSibling) {
        list.insertBefore(li.nextElementSibling, li); changed(); e.target.closest("button").focus(); said(li);
      }
    });
    var dragged = null;
    list.addEventListener("dragstart", function (e) {
      dragged = e.target.closest(".p-rank-item");
      if (!dragged) return;
      dragged.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragged.dataset.code);
    });
    list.addEventListener("dragover", function (e) {
      if (!dragged) return;
      e.preventDefault();
      var over = e.target.closest(".p-rank-item");
      if (!over || over === dragged) return;
      var box = over.getBoundingClientRect();
      list.insertBefore(dragged, e.clientY < box.top + box.height / 2 ? over : over.nextElementSibling);
    });
    list.addEventListener("dragend", function () {
      if (dragged) { dragged.classList.remove("is-dragging"); dragged = null; changed(); }
    });
    if (ok) ok.addEventListener("click", function () { touched.value = "1"; ok.classList.add("is-done"); });
  });
})();

// Assignment task: one element at a time into one of two boxes; decision and latency (ms).
// A discarded run never resumes its reaction-time clock. Pending paint/timer callbacks belong to one run only.
function interruptibleTask(root, stage, task, reset) {
  var generation = 0, running = false;
  var abort = document.createElement("button");
  abort.type = "button"; abort.className = "p-button p-button-secondary";
  abort.textContent = uiT("Aufgabe abbrechen", "Stop task");
  task.appendChild(abort);
  var note = document.createElement("p");
  note.setAttribute("role", "status"); note.tabIndex = -1;
  function stop() {
    if (!running) return;
    running = false; generation++; reset();
    task.hidden = true; stage.hidden = false;
    note.textContent = uiT("Die unterbrochene Aufgabe wird nicht ausgewertet. Ein Neustart beginnt von vorn.", "The interrupted task will not be evaluated. Restarting begins from the start.");
    stage.prepend(note); note.focus();
    root.dispatchEvent(new CustomEvent("item:abort", { bubbles: true }));
  }
  abort.addEventListener("click", stop);
  // only a hidden tab stops the task; a lost focus does not (a system notification, a click into a frame: UI/UX
  // audit 2026-10-17, W-A05)
  document.addEventListener("visibilitychange", function () { if (document.hidden) stop(); });
  return {
    start: function () { generation++; running = true; note.remove(); },
    done: function () { running = false; generation++; },
    painted: function (fn) {
      var run = generation, started = false;
      function go() { if (running && run === generation && !started) { started = true; fn(); } }
      requestAnimationFrame(function () { requestAnimationFrame(go); }); setTimeout(go, 50);
    },
    later: function (fn, delay) { var run = generation; setTimeout(function () { if (running && run === generation) fn(); }, delay); }
  };
}

// Page navigation: "Next" appears only when every task on the page is complete; while a task runs,
// the whole navigation is hidden.
(function () {
  var roots = Array.prototype.slice.call(document.querySelectorAll("[data-assign]"));
  if (!roots.length) return;
  var autoNext = !!document.querySelector("form[data-auto-next]");
  var nav = autoNext ? null : document.querySelector(".p-nav");  // auto-next pages have no visible navigation
  function complete(root) {
    try { return JSON.parse(root.querySelector("[data-assign-answer]").value || "[]").length ===
                 JSON.parse(root.querySelector("[data-assign-elements]").textContent).length; } catch (e) { return false; }
  }
  // "Start" stays in the task area (data-assign-start), never in the navigation row: two main buttons side by side
  // pushed "Next" off narrow screens (UI/UX audit 2026-10-05, V32). While an optional task has not been done, "Next"
  // shows as the secondary button.
  function syncNav() {
    if (!nav) return;
    var running = roots.some(function (r) { return !r.querySelector("[data-assign-task]").hidden; });
    nav.hidden = running;
    var next = nav.querySelector(".p-next");
    // "Next" waits for required tasks only; an optional one can be left out
    var open = roots.some(function (r) { return r.dataset.required === "1" && !complete(r); });
    if (next) {
      next.hidden = open;
      next.classList.toggle("p-button-secondary", !roots.every(complete));
    }
  }
  roots.forEach(function (root) {
    var ids = JSON.parse(root.querySelector("[data-assign-elements]").textContent);
    var answer = root.querySelector("[data-assign-answer]");
    var stage = root.querySelector("[data-assign-stage]");
    var task = root.querySelector("[data-assign-task]");
    var word = root.querySelector("[data-assign-word]");
    var progress = root.querySelector("[data-assign-progress]");
    var keys = root.dataset.keys === "1";
    var results = [], i = 0, shownAt = 0, active = false;
    var interruption = interruptibleTask(root, stage, task, function () {
      active = false; results = []; answer.value = ""; task.hidden = true; syncNav();
    });
    var swap = {}; try { swap = JSON.parse(root.querySelector("[data-assign-swap]").textContent) || {}; } catch (e) {}
    var cats = JSON.parse(root.dataset.cats), labels = JSON.parse(root.dataset.labels);
    var left = root.querySelector('[data-assign-side="left"]'), right = root.querySelector('[data-assign-side="right"]');
    function label(id) { return root.querySelector('[data-assign-label="' + id + '"]').textContent; }
    function place(id) {
      // which category sits on which side for this element; the choice still carries the category code
      var a = swap[id] ? 1 : 0, b = 1 - a;
      left.dataset.assignChoice = cats[a]; left.querySelector("[data-assign-cat-label]").textContent = labels[a];
      right.dataset.assignChoice = cats[b]; right.querySelector("[data-assign-cat-label]").textContent = labels[b];
    }
    if (ids.length) place(ids[0]);
    function show() {
      place(ids[i]);
      word.textContent = label(ids[i]);
      progress.textContent = (i + 1) + " / " + ids.length;
      // start the clock when the element is painted (next frame); fall back to a timer if frames are paused
      var started = false;
      function go() { if (!started) { started = true; shownAt = performance.now(); active = true; } }
      interruption.painted(go);
    }
    function choose(code) {
      if (!active) return;
      active = false;
      results.push({ s: ids[i], c: code, rt: Math.round(performance.now() - shownAt) });
      answer.value = JSON.stringify(results);
      i += 1;
      if (i < ids.length) { word.textContent = ""; interruption.later(show, 250); return; }
      interruption.done();
      var had = root.contains(document.activeElement) || document.activeElement === document.body;
      task.hidden = true;
      stage.hidden = false;
      stage.innerHTML = '<p class="p-assign-done" tabindex="-1">✓ ' + uiT("Alle Begriffe zugeordnet. Es kann weitergehen.", "All terms sorted. You can continue.") + "</p>";
      if (had) stage.firstChild.focus();  // the task is gone: the focus to the line that says so (D7)
      var item = root.closest(".p-item");
      if (item) item.classList.remove("has-error", "has-nudge");
      syncNav();
      root.dispatchEvent(new CustomEvent("item:complete", { bubbles: true }));
    }
    function start() {
      interruption.start();
      results = []; i = 0; answer.value = "";
      stage.hidden = true; task.hidden = false;
      syncNav();
      show();
      // "Start" is gone (accessibility concept D7): the focus into the task, where E and I work, or on the left box
      if (keys) task.focus(); else left.focus();
    }
    root._start = start;
    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-assign-start]") || e.target.closest("[data-assign-restart]")) start();
      var box = e.target.closest("[data-assign-choice]");
      if (box) choose(box.dataset.assignChoice);
    });
    if (keys) document.addEventListener("keydown", function (e) {
      if (task.hidden || e.repeat || !task.contains(document.activeElement) || (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]"))) return;
      var k = e.key.toLowerCase();
      if (k === "e") { e.preventDefault(); choose(left.dataset.assignChoice); }
      if (k === "i") { e.preventDefault(); choose(right.dataset.assignChoice); }
    });
  });
  syncNav();
})();

// Pages without navigation buttons (item option "auto_next"): a pointer choice on every radio group,
// or an item that reports "item:complete" (e.g. the assignment task), submits the page with "Next".
(function () {
  var form = document.querySelector("form[data-auto-next]");
  if (!form) return;
  var next = form.querySelector('button[name="nav"][value="next"]:not(.p-default-submit)');
  if (!next) return;
  var nav = form.querySelector("[data-auto-next-nav]");
  var timer = null, manual = false;
  // said beforehand, not only done (WCAG 3.2.2, accessibility concept D7): after a choice, or after a task
  var note = document.querySelector("[data-auto-note]");
  if (note && !form.querySelector("input[type=radio]")) {
    if (form.querySelector("[data-assign-answer]")) note.textContent = note.dataset.taskText; else note = null;
  }
  if (note) note.hidden = false;
  function submit(delay) {
    if (manual) return;
    clearTimeout(timer);
    form.classList.add("auto-advancing");
    timer = setTimeout(function () { if (form.requestSubmit) form.requestSubmit(next); else next.click(); }, delay);
  }
  // The buttons come back, and the page waits for them, when someone returns to an answered page
  // (nothing would change, so nothing would continue) or uses the keyboard (no pointer choice to wait for).
  function showNav() {
    manual = true;
    clearTimeout(timer);
    form.classList.remove("auto-advancing");
    if (nav) nav.hidden = false;
    if (note) note.hidden = true;  // with the buttons back, nothing goes on by itself
  }
  function radiosComplete() {
    var groups = {};
    form.querySelectorAll("input[type=radio]").forEach(function (r) { groups[r.name] = groups[r.name] || r.checked; });
    return Object.keys(groups).length > 0 && Object.keys(groups).every(function (n) { return groups[n]; });
  }
  function assignComplete() {
    var answers = form.querySelectorAll("[data-assign-answer]");
    return answers.length > 0 && Array.prototype.every.call(answers, function (a) { return a.value !== ""; });
  }
  if (radiosComplete() || assignComplete()) showNav();
  var keyboardMove = false;
  document.addEventListener("keydown", function (e) {  // on the document: the first Tab starts outside the form
    keyboardMove = e.key.indexOf("Arrow") === 0;
    if (keyboardMove || e.key === "Tab" || e.key === " " || e.key === "Enter") showNav();
  });
  form.addEventListener("pointerdown", function () { keyboardMove = false; });
  form.addEventListener("change", function (e) {
    // arrow keys move through radio buttons without choosing; space or a click chooses
    if (e.target.type === "radio" && !keyboardMove && radiosComplete()) submit(350);
  });
  form.addEventListener("item:complete", function () { submit(600); });
  form.addEventListener("item:abort", showNav);
})();

// Constant sum. Moving a slider keeps the total right: the difference is taken from the others, first from
// those not set yet (keeping their proportions), then from those already set. A number that is typed stays
// as typed and never changes a value the participant has set; only options still at the starting split take
// up the difference. When the total is off, the line below says how much is left or too much (the server
// checks the total). Values are kept as decimals internally and shown as whole numbers.
(function () {
  document.querySelectorAll("[data-csum]").forEach(function (box) {
    var total = parseInt(box.dataset.total, 10);
    var ranges = Array.prototype.slice.call(box.querySelectorAll("[data-csum-range]"));
    var nums = Array.prototype.slice.call(box.querySelectorAll("[data-csum-num]"));
    var sumOut = box.querySelector("[data-csum-sum] strong");
    var restOut = box.querySelector("[data-csum-rest]");
    var touchedInput = box.querySelector("[data-csum-touched]");
    var ok = box.querySelector("[data-csum-ok]");
    var n = nums.length;
    var vals = nums.map(function (x) { return parseFloat(x.value) || 0; });
    var set = nums.map(function () { return box.hasAttribute("data-answered"); });  // set by the participant
    ranges.forEach(function (r) { r.hidden = false; });
    if (ok) ok.hidden = false;
    box.classList.add("is-live");

    function shown() {
      // whole numbers adding up to total (largest remainder)
      var floor = vals.map(Math.floor), rest = total - floor.reduce(function (a, b) { return a + b; }, 0);
      vals.map(function (v, i) { return [v - floor[i], i]; }).sort(function (a, b) { return b[0] - a[0]; })
        .slice(0, rest).forEach(function (p) { floor[p[1]] += 1; });
      return floor;
    }
    function whole() { return vals.every(function (v) { return Math.abs(v - Math.round(v)) < 1e-9; }); }
    function render() {
      // typed numbers are whole already and may not add up; slider values are spread to whole numbers that do
      var ints = whole() ? vals.map(Math.round) : shown();
      ints.forEach(function (v, i) {
        ranges[i].value = v; nums[i].value = v;
        ranges[i].style.setProperty("--pct", (Math.min(v, total) / total * 100) + "%");
      });
      var sum = ints.reduce(function (a, b) { return a + b; }, 0);
      if (sumOut) sumOut.textContent = sum;
      if (restOut) {
        restOut.hidden = sum === total;
        restOut.textContent = sum < total ? restOut.dataset.left.replace("%s", total - sum) : restOut.dataset.over.replace("%s", sum - total);
      }
    }
    // what a screen reader hears: only what is left (or that all is distributed), 600 ms after the last change; read at
    // every step of a slider it never stopped (UI/UX audit 2026-10-16, 100)
    var sayLine = box.querySelector("[data-csum-say]"), sayTimer = null;
    function say() {
      if (!sayLine) return;
      clearTimeout(sayTimer);
      sayTimer = setTimeout(function () {
        sayLine.textContent = restOut && !restOut.hidden ? restOut.textContent : sayLine.dataset.done;
      }, 600);
    }
    function markTouched() {
      touchedInput.value = "1";
      if (ok) ok.classList.add("is-done");
      var item = box.closest(".p-item");
      if (item) item.classList.remove("has-error", "has-nudge");
      say();
    }
    function typed(i, target) {
      vals = whole() ? vals.map(Math.round) : shown();  // freeze what is shown
      vals[i] = Math.max(0, Math.min(total, Math.round(target)));
      set[i] = true;
      // options the participant has not set yet still hold the starting split: they take up the difference.
      // Values the participant set (typed or moved) are never changed by typing.
      var open = vals.map(function (_, j) { return j; }).filter(function (j) { return !set[j]; });
      var need = total - vals.reduce(function (a, b) { return a + b; }, 0);
      if (open.length) {
        var have = open.reduce(function (a, j) { return a + vals[j]; }, 0);
        if (need < 0) {
          var take = Math.min(-need, have);
          if (have > 0) open.forEach(function (j) { vals[j] -= vals[j] / have * take; });
        } else {
          open.forEach(function (j) { vals[j] += have > 0 ? vals[j] / have * need : need / open.length; });
        }
      }
      render();
      markTouched();
    }
    function change(i, target) {
      target = Math.max(0, Math.min(total, Math.round(target)));
      vals[i] = target;
      set[i] = true;
      // what the others must take (> 0) or give (< 0) so that the total is right again, also after typing
      var need = total - vals.reduce(function (a, b) { return a + b; }, 0);
      var others = vals.map(function (_, j) { return j; }).filter(function (j) { return j !== i; });
      var groups = [others.filter(function (j) { return !set[j]; }), others.filter(function (j) { return set[j]; })];
      groups.forEach(function (g) {
        if (!g.length || Math.abs(need) < 1e-9) return;
        var have = g.reduce(function (a, j) { return a + vals[j]; }, 0);
        if (need < 0) {  // take proportionally, at most what the group has
          var take = Math.min(-need, have);
          if (have > 0) g.forEach(function (j) { vals[j] -= vals[j] / have * take; });
          need += take;
        } else {         // give proportionally (evenly if the group has nothing)
          g.forEach(function (j) { vals[j] += have > 0 ? vals[j] / have * need : need / g.length; });
          need = 0;
        }
      });
      vals[i] += need;   // only if nothing could absorb the difference (single option)
      render();
      markTouched();
    }
    ranges.forEach(function (r, i) { r.addEventListener("input", function () { change(i, parseFloat(r.value)); }); });
    nums.forEach(function (x, i) {
      x.addEventListener("change", function () { typed(i, parseFloat(x.value) || 0); });
      x.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); typed(i, parseFloat(x.value) || 0); } });
    });
    if (ok) ok.addEventListener("click", markTouched);
    render();
  });
})();

// Preview bar on a phone: the tools open from "Werkzeuge" (X47); without the script they stay in the bar
(function () {
  var more = document.querySelector("[data-preview-more]"), bar = more && more.closest(".p-preview-bar");
  if (!bar) return;
  bar.classList.add("has-more");
  more.addEventListener("click", function () {
    var open = bar.classList.toggle("is-more");
    more.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();

// Preview bar: close the "jump to block" list on outside click or Escape
(function () {
  var jump = document.querySelector(".p-jump");
  if (!jump) return;
  document.addEventListener("click", function (e) { if (jump.open && !jump.contains(e.target)) jump.open = false; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && jump.open) { jump.open = false; jump.querySelector("summary").focus(); } });
})();

// Privacy page: "back" returns to the survey page as it was (keeps typed answers) when possible
(function () {
  var back = document.querySelector("[data-privacy-back]");
  if (!back) return;
  back.addEventListener("click", function (e) {
    var ref = document.referrer, here = location.origin;
    if (history.length > 1 && ref && ref.indexOf(here) === 0) { e.preventDefault(); history.back(); }
  });
})();

// Van Westendorp price questions (items/psm.py): the same checks as the server, while typing.
// Each price: a number (comma or point, thousands separators allowed), not negative, within
// min/max, at most `decimals` places. Order TC <= C <= E <= TE: shown live; with consistency
// "required" the page is not sent while it is violated. "warn" is left to the server (one nudge).
(function () {
  // point: the language writes numbers like English (cfg.point), so only "," separates thousands; else only "."
  function parse(raw, decimals, point) {
    var v = String(raw || "").replace(/[\s  '’€$£]|CHF|EUR|USD|GBP/gi, "").replace("−", "-").replace("–", "-");
    if (!v) return null;
    if (v.indexOf(",") >= 0 && v.indexOf(".") >= 0) {
      var dec = v.lastIndexOf(",") > v.lastIndexOf(".") ? "," : ".", group = dec === "," ? "." : ",";
      // the thousands in groups of three: "1.299,50", not "1,5.3"
      if (!new RegExp("^-?\\d{1,3}(\\" + group + "\\d{3})+\\" + dec + "\\d*$").test(v)) return NaN;
      v = v.split(group).join("").replace(dec, ".");
    } else if ((v.match(/,/g) || []).length > 1 || (v.match(/\./g) || []).length > 1) {
      v = v.replace(/[.,]/g, "");
    } else {
      var sep = v.indexOf(",") >= 0 ? "," : (v.indexOf(".") >= 0 ? "." : "");
      if (sep) {
        var parts = v.split(sep), own = sep === (point ? "," : ".");
        var head = parts[0].replace("-", "");  // no thousands group after a leading zero: "0,999" is 0.999
        v = own && parts[1].length === 3 && decimals < 3 && head && head.charAt(0) !== "0" ? parts[0] + parts[1] : parts[0] + "." + parts[1];
      }
    }
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(v)) return NaN;
    return parseFloat(v);
  }
  function places(n) { var t = n.toFixed(10).replace(/0+$/, ""); return t.indexOf(".") >= 0 ? t.split(".")[1].length : 0; }
  function fmt(s, vars) { return s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] !== undefined ? vars[k] : m; }); }

  document.querySelectorAll("[data-psm]").forEach(function (box) {
    var cfg = JSON.parse(box.querySelector("[data-psm-config]").textContent);
    var fields = {};
    box.querySelectorAll("[data-psm-field]").forEach(function (f) { fields[f.dataset.psmField] = f; });
    var orderOut = box.querySelector("[data-psm-order]");
    var form = box.closest("form");

    function check(key) {
      var f = fields[key], name = cfg.names[key], v = parse(f.value, cfg.decimals, cfg.point), msg = "";
      if (v !== null) {
        if (isNaN(v)) msg = fmt(cfg.m.number, { name: name });
        else if (v < 0) msg = fmt(cfg.m.negative, { name: name });
        else if (places(v) > cfg.decimals) msg = fmt(cfg.decimals ? cfg.m.decimals : cfg.m.decimals0, { name: name, n: cfg.decimals });
        else if (cfg.min !== null && v < cfg.min) msg = fmt(cfg.m.min, { name: name, lo: cfg.lo });
        else if (cfg.max !== null && v > cfg.max) msg = fmt(cfg.m.max, { name: name, hi: cfg.hi });
      }
      box.querySelector('[data-psm-msg="' + key + '"]').textContent = msg;
      f.classList.toggle("p-invalid", !!msg);
      f.setAttribute("aria-invalid", msg ? "true" : "false");
      return msg ? undefined : v;
    }
    function orderProblem(values) {
      var given = cfg.keys.filter(function (k) { return values[k] !== null && values[k] !== undefined; });
      for (var i = 1; i < given.length; i++) {
        var a = given[i - 1], b = given[i];
        if (values[b] < values[a]) {
          var show = function (k) { return fields[k].value.trim() + (cfg.unit ? "\u00a0" + cfg.unit : ""); };
          return fmt(cfg.m.order, { lo_name: cfg.names[a], lo: show(a), hi_name: cfg.names[b], hi: show(b) });
        }
      }
      return "";
    }
    function all(showFieldErrors) {
      var values = {}, bad = false;
      cfg.keys.forEach(function (k) {
        var v = showFieldErrors || fields[k].value.trim() === "" || fields[k].classList.contains("p-invalid") ? check(k) : parse(fields[k].value, cfg.decimals, cfg.point);
        if (v === undefined || (typeof v === "number" && isNaN(v))) { bad = true; values[k] = null; } else values[k] = v;
      });
      var problem = cfg.consistency === "off" ? "" : orderProblem(values);
      orderOut.textContent = problem ? problem + (cfg.consistency === "required" ? cfg.m.order_fix : "") : "";
      orderOut.hidden = !problem;
      // Newton-Miller-Smith questions name the participant's own "cheap" and "expensive" price
      box.querySelectorAll("[data-psm-price]").forEach(function (span) {
        var k = span.dataset.psmPrice, v = values[k];
        span.textContent = typeof v === "number" && !isNaN(v) && fields[k].value.trim()
          ? fields[k].value.trim() + (cfg.unit ? "\u00a0" + cfg.unit : "") : span.dataset.fallback;
      });
      return { bad: bad, problem: problem };
    }
    Object.keys(fields).forEach(function (k) {
      fields[k].addEventListener("change", function () { check(k); all(false); });
      fields[k].addEventListener("input", function () { if (fields[k].classList.contains("p-invalid")) check(k); all(false); });
    });
    if (form) form.addEventListener("submit", function (e) {
      var by = e.submitter;
      if (by && (by.formNoValidate || (by.name === "nav" && by.value === "back"))) return;
      var state = all(true);
      if (state.bad || (state.problem && cfg.consistency === "required")) {
        e.preventDefault();
        var first = box.querySelector(".p-invalid") || fields[cfg.keys[0]];
        if (first) first.focus();
        box.scrollIntoView({ block: "center" });
      }
    });
    all(false);
  });
})();

// 2D positioning (items/position2d.py): tap an element in the tray, then tap in the field; drag or use the
// arrow keys to move a placed element; "×" takes it back. Values 0..100 per axis (0 = left / bottom).
(function () {
  document.querySelectorAll("[data-pos]").forEach(function (box) {
    var field = box.querySelector("[data-pos-field]");
    var selected = null, markers = {};
    function chip(code) { return box.querySelector('[data-pos-chip="' + code + '"]'); }
    function input(code) { return box.querySelector('[data-pos-value="' + code + '"]'); }
    function skip(code) { return box.querySelector('[data-pos-skip="' + code + '"]'); }
    function select(code) {
      selected = code;
      box.querySelectorAll("[data-pos-chip]").forEach(function (c) {
        c.classList.toggle("is-selected", c.dataset.posChip === code);
        c.setAttribute("aria-pressed", c.dataset.posChip === code ? "true" : "false");  // chosen, for screen readers (R53)
      });
      Object.keys(markers).forEach(function (k) { markers[k].classList.toggle("is-selected", k === code); });
    }
    function set(code, x, y) {
      x = Math.max(0, Math.min(100, Math.round(x * 10) / 10));
      y = Math.max(0, Math.min(100, Math.round(y * 10) / 10));
      var m = markers[code];
      if (!m) {
        m = document.createElement("span");
        // a group that takes the focus (arrows move it) with its × beside the name, no button inside a button (R53)
        m.className = "p-pos-marker"; m.tabIndex = 0; m.setAttribute("role", "group");
        m.innerHTML = '<span class="p-pos-dot" aria-hidden="true"></span><span class="p-pos-name" aria-hidden="true"></span><button type="button" class="p-pos-remove">×</button>';
        m.querySelector(".p-pos-remove").setAttribute("aria-label", uiT("zurücklegen", "put back"));
        m.querySelector(".p-pos-name").textContent = chip(code).textContent;
        m.querySelector(".p-pos-remove").addEventListener("click", function (e) { e.stopPropagation(); unset(code); });
        m.addEventListener("pointerdown", function (e) { if (e.target.closest(".p-pos-remove")) return; drag(e, code); });
        m.addEventListener("keydown", function (e) {
          var d = e.shiftKey ? 10 : 2, v = input(code).value.split(",").map(Number);
          var moves = {ArrowLeft: [-d, 0], ArrowRight: [d, 0], ArrowUp: [0, d], ArrowDown: [0, -d]};
          if (moves[e.key]) { e.preventDefault(); set(code, v[0] + moves[e.key][0], v[1] + moves[e.key][1]); }
          if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); unset(code); }
        });
        field.appendChild(m); markers[code] = m;
      }
      m.style.left = x + "%"; m.style.top = (100 - y) + "%";
      m.setAttribute("aria-label", chip(code).textContent.trim() + ": " + Math.round(x) + " / " + Math.round(y));
      m.classList.toggle("is-right", x > 70);
      input(code).value = x + "," + y;
      chip(code).classList.add("is-placed");
      var s = skip(code); if (s) s.checked = false;
      box.dispatchEvent(new Event("change", {bubbles: true}));
    }
    function unset(code) {
      if (markers[code]) { markers[code].remove(); delete markers[code]; }
      input(code).value = "";
      chip(code).classList.remove("is-placed");
      if (selected === code) select(null);
    }
    function point(e) {
      var r = field.getBoundingClientRect();
      return [(e.clientX - r.left) / r.width * 100, 100 - (e.clientY - r.top) / r.height * 100];
    }
    function drag(e, code) {
      e.preventDefault(); select(code);
      var m = markers[code]; m.setPointerCapture(e.pointerId); m.classList.add("is-dragging");
      function move(ev) { var p = point(ev); set(code, p[0], p[1]); }
      function up() { m.classList.remove("is-dragging"); m.removeEventListener("pointermove", move); m.removeEventListener("pointerup", up); }
      m.addEventListener("pointermove", move); m.addEventListener("pointerup", up);
    }
    box.querySelectorAll("[data-pos-chip]").forEach(function (c) {
      c.addEventListener("click", function (e) {
        // from the keyboard (Enter or Space: no pointer position) a chip goes to the middle of the field at once
        // and the focus to it, so the arrow keys move it (WCAG 2.1.1; UI audit 2026-10-04, R53)
        if (e.detail === 0) {
          var code = c.dataset.posChip;
          if (!markers[code]) set(code, 50, 50);
          select(code); markers[code].focus();
          return;
        }
        select(selected === c.dataset.posChip ? null : c.dataset.posChip);
      });
      if (c.dataset.x) set(c.dataset.posChip, parseFloat(c.dataset.x), parseFloat(c.dataset.y));
    });
    box.querySelectorAll("[data-pos-skip]").forEach(function (s) {
      s.addEventListener("change", function () { if (s.checked) unset(s.dataset.posSkip); });
    });
    field.addEventListener("click", function (e) {
      if (!selected || e.target.closest(".p-pos-marker")) return;
      var p = point(e), code = selected;
      set(code, p[0], p[1]);
      // continue with the next element that is not placed yet
      var next = Array.prototype.find.call(box.querySelectorAll("[data-pos-chip]"), function (c) {
        return !c.classList.contains("is-placed") && !(skip(c.dataset.posChip) || {}).checked;
      });
      select(next ? next.dataset.posChip : null);
    });
    var first = box.querySelector("[data-pos-chip]:not(.is-placed)");
    if (first) select(first.dataset.posChip);
  });
})();

// Free associations (items/association.py): two steps on one page. First only the words; the page's "Next"
// then shows each own word with its rating instead of sending the page; "Next" again sends it.
(function () {
  var roots = Array.prototype.slice.call(document.querySelectorAll("[data-assoc]"));
  if (!roots.length) return;
  function words(root) {
    return Array.prototype.map.call(root.querySelectorAll("[data-assoc-word]"), function (w) { return w.value.trim(); });
  }
  function show(root, phase) {
    root.dataset.phase = phase;
    if (phase === "rate") {
      words(root).forEach(function (text, n) {
        var k = n + 1, row = root.querySelector('[data-assoc-rate="' + k + '"]');
        row.hidden = !text;
        root.querySelector('[data-assoc-show="' + k + '"]').textContent = text;
        if (!text) row.querySelectorAll("input").forEach(function (r) { r.checked = false; });
      });
    }
  }
  roots.forEach(function (root) {
    root.querySelector("[data-assoc-edit]").hidden = false;
    root.querySelector("[data-assoc-edit]").addEventListener("click", function () {
      show(root, "words");
      var first = root.querySelector("[data-assoc-word]"); if (first) first.focus();
    });
    if (root.dataset.phase !== "rate") show(root, "words");
    else show(root, "rate");
  });
  var form = roots[0].closest("form");
  if (!form) return;
  form.addEventListener("submit", function (e) {
    var by = e.submitter;
    if (!by || by.name !== "nav" || by.value !== "next") return;  // "Back" and the consent buttons pass
    var pending = roots.filter(function (r) { return r.dataset.phase === "words" && words(r).some(Boolean); });
    if (!pending.length) return;  // no words: let the server decide (optional or a reminder)
    e.preventDefault();
    pending.forEach(function (r) { show(r, "rate"); });
    pending[0].scrollIntoView({block: "start", behavior: (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth")});
    // the second step said and focused (accessibility concept D7): the words the focus was on are hidden now
    var status = pending[0].querySelector("[data-assoc-status]"), legend = pending[0].querySelector(".p-assoc-legend");
    if (status) status.textContent = uiT("Schritt 2 von 2: Bewerten Sie jetzt Ihre Begriffe.", "Step 2 of 2: now rate your words.");
    if (legend) legend.focus({ preventScroll: true });
  });
})();

// Date lists (items/date_time.py): days the chosen month does not have cannot be picked (30 February, 29 February
// outside a leap year); a day picked before that no longer exists is taken back. The server checks the same.
document.querySelectorAll("[data-date]").forEach(function (box) {
  var day = box.querySelector("[data-date-part=d]"), month = box.querySelector("[data-date-part=m]");
  var year = box.querySelector("[data-date-part=y]");
  if (!day || !month) return;
  function sync() {
    var m = parseInt(month.value, 10), y = year ? parseInt(year.value, 10) : NaN;
    // without a year: a leap year, so 29 February stays until the year says otherwise
    var last = m ? new Date(y || 2000, m, 0).getDate() : 31;
    Array.prototype.forEach.call(day.options, function (option) {
      option.disabled = option.hidden = Number(option.value) > last;
    });
    if (Number(day.value) > last) day.value = "";
  }
  [month, year].forEach(function (list) { if (list) list.addEventListener("change", sync); });
  sync();
});

// Enter in a one-line answer field: on a page with several questions it goes to the next question instead of
// sending the page (which would skip the questions below); after the last one it goes to "Next". On a page
// with a single question Enter sends the page, as before.
(function () {
  var form = document.querySelector(".p-container form[method=post]");
  if (!form) return;
  function fields(item) {
    return Array.prototype.filter.call(item.querySelectorAll("input:not([type=hidden]), textarea, select, button[type=button]"),
      function (el) { return !el.disabled && el.offsetParent !== null; });
  }
  var items = Array.prototype.filter.call(form.querySelectorAll(".p-item"), function (item) { return fields(item).length > 0; });
  if (items.length < 2) return;
  form.addEventListener("keydown", function (e) {
    var el = e.target;
    if (e.key !== "Enter" || e.defaultPrevented || el.tagName !== "INPUT") return;
    if (["text", "email", "number", "tel", "search", "url"].indexOf(el.type) < 0) return;
    var here = items.indexOf(el.closest(".p-item"));
    if (here < 0) return;
    e.preventDefault();
    var next = items[here + 1] ? fields(items[here + 1])[0] : form.querySelector('button[name="nav"][value="next"]:not(.p-default-submit)');
    if (next) next.focus();
  });
})();

// Language switch on a page: send the page along, so the server keeps what was typed (like "Back")
// before it shows the page in the other language. Without a form (end pages) the link works as it is.
document.querySelectorAll("[data-set-lang]").forEach(function (link) {
  link.addEventListener("click", function (e) {
    var form = document.querySelector(".p-container form[method=post]");
    if (!form || link.hasAttribute("aria-current")) return;
    e.preventDefault();
    var input = document.createElement("input");
    input.type = "hidden"; input.name = "set_lang"; input.value = link.dataset.setLang;
    form.appendChild(input);
    form.submit();  // no checks: nothing is decided here
  });
});
// the same from the language field (from four languages on, participant/_lang_select.html; UI audit 2026-10-12, U44):
// a page with a form is sent along, any other page opens in the chosen language. A choice with the mouse or finger
// goes at once; with the arrow keys every step is a change, so there the page waits for Enter or the button "Auswählen"
// (WCAG 3.2.2, UI/UX audit 2026-10-16, 78)
document.querySelectorAll("[data-lang-select]").forEach(function (select) {
  var pointer = false, button = select.form.querySelector("[data-lang-go]");
  function go() {
    var form = select.hasAttribute("data-send-page") && document.querySelector(".p-container form[method=post]");
    if (!form) { select.form.submit(); return; }
    var input = document.createElement("input");
    input.type = "hidden"; input.name = "set_lang"; input.value = select.value;
    form.appendChild(input);
    form.submit();
  }
  select.addEventListener("pointerdown", function () { pointer = true; });
  select.addEventListener("keydown", function (e) {
    pointer = false;
    if (e.key === "Enter") { e.preventDefault(); go(); }
  });
  select.addEventListener("change", function () {
    if (pointer) { go(); return; }
    if (button) button.hidden = false;
  });
  if (button) button.addEventListener("click", function (e) { e.preventDefault(); go(); });
});

// Minimum and maximum time of a page (app/pagetime.py): the seconds left stand next to "Next". Until the
// minimum is over, "Next" is held ("Back" and the other language are not); when the maximum is over, the page
// is sent as it is. The server has the same clock and decides; here the person sees what it will say.
// Non-essential limits offer up to ten extensions; ordinary pages persist them without submitting answers.
function holdPage(root) {
  var box = root.querySelector("[data-hold]");
  var form = box && box.closest("form");
  if (!form) return null;
  var block = root !== document;  // a page of a block loaded at once: its own buttons, its own clock
  var scope = block ? root : form;
  var stopped = false;
  var next = scope.querySelector('button[name="nav"][value="next"]:not(.p-default-submit)');
  var min = parseInt(box.dataset.min, 10) || 0, max = parseInt(box.dataset.max, 10) || 0;
  var start = Date.now(), up = false, timer = null, warned = false;
  var warn = scope.querySelector("[data-hold-warn]");
  var more = scope.querySelector("[data-hold-more]"), moreField = scope.querySelector("[data-hold-more-field]");
  var extra = parseInt(box.dataset.more, 10) || 0;
  var note = root.querySelector("[data-hold-note]");  // the fixed time, said beforehand
  if (note) note.hidden = false;
  var pending = false, paused = false;
  var limit = parseInt(box.dataset.moreLimit, 10) || 10;
  var taken = function () { return parseInt(moreField && moreField.value, 10) || 0; };
  var offered = function () { return more && moreField && extra && taken() < limit; };
  if (more) more.addEventListener("click", function () {
    if (stopped || !offered() || pending) return;
    if (block) {
      // Preloaded pages already send their individual extension counts in _block; keep their offline operation.
      moreField.value = String((parseInt(moreField.value, 10) || 0) + 1);
      max += extra; more.hidden = !offered(); warned = false;
      if (warn) warn.textContent = (box.dataset.moreDoneText || "").split("{n}").join(extra);
      tick(); return;
    }
    pending = true; paused = true; more.disabled = true;
    var count = (parseInt(moreField.value, 10) || 0) + 1;
    var body = new FormData();
    ["csrf_token", "pos"].forEach(function (name) {
      var input = form.querySelector('[name="' + name + '"]');
      if (input) body.append(name, input.value);
    });
    body.append("nav", "time_more"); body.append("time_more", String(count));
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    fetch(form.action || location.href, { method: "POST", body: body, credentials: "same-origin", signal: controller.signal })
      .then(function (response) { if (!response.ok) throw new Error("timer"); return response.text(); })
      .then(function (html) {
        var page = new DOMParser().parseFromString(html, "text/html");
        var saved = page.querySelector('[name="time_more"]'), clock = page.querySelector("[data-hold]");
        if (!saved || Number(saved.value) !== count || !clock) throw new Error("timer");
        moreField.value = String(count);
        max = (Date.now() - start) / 1000 + Number(clock.dataset.max);
        more.hidden = !offered(); warned = false; paused = false;
        if (warn) warn.textContent = (box.dataset.moreDoneText || "").split("{n}").join(extra);
      }).catch(function () {
        if (warn) warn.textContent = uiT("Bitte versuchen Sie es noch einmal.", "Please try again.");
      }).finally(function () { clearTimeout(timeout); pending = false; more.disabled = false; tick(); });
  });
  function left(total) { return Math.ceil(total - (Date.now() - start) / 1000); }
  function say(text, n) { box.textContent = text.split("{n}").join(n); box.hidden = false; }
  form.addEventListener("submit", function (e) {
    var by = e.submitter;
    if (stopped) return;
    if (up) {
      // a check of the page stopped it (e.g. the price questions): the time is over all the same
      if (e.defaultPrevented) setTimeout(function () { form.submit(); }, 0);
      return;
    }
    if (min && left(min) > 0 && !(by && by.name === "nav" && by.value === "back")) e.preventDefault();
  });
  function timeUp() {
    up = true;
    clearInterval(timer);
    if (block) {  // the block goes on to its next page (or is sent at its end) with what is answered
      root.dispatchEvent(new CustomEvent("p:timeup", { bubbles: true }));
      return;
    }
    [["time_up", "1"], ["nav", "next"]].forEach(function (pair) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = pair[0]; input.value = pair[1];
      form.appendChild(input);
    });
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }
  function tick() {
    var wait = min ? left(min) : 0;
    if (next) next.disabled = wait > 0;
    if (wait > 0 && !max) { say(box.dataset.minText, wait); return; }
    if (!max) { box.hidden = true; clearInterval(timer); return; }
    if (paused) return;
    if (left(max) <= 0) { timeUp(); return; }
    if (offered() && more.hidden) more.hidden = false;  // also at once on a page with 20 s or less
    if (!warned && warn && max > 20 && left(max) <= 21) {
      warned = true;
      warn.textContent = offered() ? box.dataset.warnMoreText.split("{n}").join(extra) : box.dataset.warnText;
    }
    say(wait > 0 ? box.dataset.minText : box.dataset.maxText, wait > 0 ? wait : left(max));
  }
  timer = setInterval(tick, 250);
  tick();
  return function stop() { stopped = true; clearInterval(timer); if (next) next.disabled = false; };
}
(function () {
  var blockForm = document.querySelector(".p-container form[data-block]");
  if (!blockForm) {
    if (holdPage(document)) {
      // back from the browser history: the server shows the page it is at, with its own clock
      window.addEventListener("pageshow", function (e) { if (e.persisted) location.reload(); });
    }
    return;
  }
  var stop = holdPage(blockForm.querySelector(".p-bpage:not([hidden])") || blockForm);
  document.addEventListener("p:page", function (e) {
    if (stop) stop();
    stop = holdPage(e.detail.page);  // each page of the block from its full time, as when it is shown by itself
  });
  window.addEventListener("pageshow", function (e) { if (e.persisted) location.reload(); });
})();

// A simple block loaded at once (app/blockload.py; form[data-block]): every page of the block stands in the form as a
// section (.p-bpage), one shown at a time. "Next" and "Back" inside the block switch here, without the server. Each
// page left with "Next" is sent in the background to be stored (data-block-url: JSON with the CSRF token, keepalive);
// the answer brings the page's errors and notes, and the page stays until they are seen to. Without a connection the
// page goes into a queue kept in the tab (sessionStorage, gone when the tab closes) and is sent again later, and the
// person goes on. The last page sends the whole form with the time measured on each page (field _block, decision 4a),
// and the server checks every page once more and goes back to the first page that is not complete.
var blockPages = null;
(function () {
  var form = document.querySelector(".p-container form[data-block]");
  var pages = form ? Array.prototype.slice.call(form.querySelectorAll(".p-bpage")) : [];
  if (!pages.length) return;
  var url = form.getAttribute("data-block-url"), start = form.getAttribute("data-block-start");
  var csrf = (form.querySelector("input[name=csrf_token]") || {}).value || "";
  var posField = form.querySelector("input[name=pos]");
  var say = form.querySelector("[data-bpage-live]"), note = form.querySelector("[data-block-note]");
  var QUEUE = "blockq:" + (form.getAttribute("data-keep") || start);
  var store = null;
  try { store = window.sessionStorage; store.getItem(QUEUE); } catch (e) { store = null; }
  var meta = {}, current = pages.filter(function (p) { return !p.hidden; })[0] || pages[0], since = Date.now();
  function key(p) { return p.getAttribute("data-bpage"); }
  function of(p) { return meta[key(p)] || (meta[key(p)] = { ms: 0, seq: 0 }); }
  function clock() { var now = Date.now(); of(current).ms += now - since; since = now; }
  // {name: [values]} of the fields of one page
  function fields(p) {
    var out = {};
    p.querySelectorAll("input, select, textarea").forEach(function (el) {
      var t = (el.type || "").toLowerCase();
      if (!el.name || el.disabled || /^(file|submit|button|reset|image)$/.test(t)) return;
      if ((t === "radio" || t === "checkbox") && !el.checked) return;
      var list = out[el.name] || (out[el.name] = []);
      if (el.tagName === "SELECT" && el.multiple) {
        Array.prototype.forEach.call(el.options, function (o) { if (o.selected) list.push(o.value); });
      } else list.push(el.value);
    });
    return out;
  }
  function queued() { try { return JSON.parse(store.getItem(QUEUE) || "[]") || []; } catch (e) { return []; } }
  function keep(list) {
    if (!store) return;
    try { if (list.length) store.setItem(QUEUE, JSON.stringify(list)); else store.removeItem(QUEUE); } catch (e) { /* full */ }
  }
  function enqueue(body) { keep(queued().filter(function (b) { return b.page !== body.page; }).concat([body])); }
  // one page to the server: resolves with its answer, rejects without a connection (or after 8 s, or a server error)
  function send(body) {
    return new Promise(function (resolve, reject) {
      if (!window.fetch || navigator.onLine === false) { reject(); return; }
      var ctrl = window.AbortController ? new AbortController() : null;
      var timer = setTimeout(function () { if (ctrl) ctrl.abort(); reject(); }, 8000);
      fetch(url, { method: "POST", credentials: "same-origin", keepalive: true, signal: ctrl ? ctrl.signal : undefined,
                   headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) })
        .then(function (r) {
          clearTimeout(timer);
          if (r.status === 409) {  // the server is elsewhere (another tab): its page
            // a page of another participation or an older version of the block never fits: out of the queue, or the
            // reload would send it again and again (audit 2026-10-23, DS-01)
            r.json().then(function (j) {
              if (j && j.gone) keep(queued().filter(function (b) { return !(b.page === body.page && b.seq === body.seq); }));
            }, function () {}).then(function () { location.reload(); });
            return;
          }
          if (!r.ok) { reject(); return; }
          return r.json().then(resolve, reject);
        }, function () { clearTimeout(timer); reject(); });
    });
  }
  // one block POST at a time: "Back", "Next" and the queue wait for each other, so two pages never reach the server
  // at once (a version conflict there reloads the whole block) nor in another order than they were sent (audit data
  // storage 2026-10-21, A4)
  var line = Promise.resolve();
  function serial(body) {
    var out = line.then(function () { return send(body); });
    line = out.then(function () {
      // stored: an older copy of the same page still waiting in the queue must not overwrite it later
      keep(queued().filter(function (b) { return !(b.page === body.page && b.seq <= body.seq); }));
    }, function () { /* the next one goes all the same */ });
    return out;
  }
  var flushing = false;
  function flush() {
    var list = queued();
    if (flushing || !list.length || navigator.onLine === false) return;
    flushing = true;
    serial(list[0]).then(function () {
      flushing = false;
      flush();
    }, function () { flushing = false; });
  }
  window.addEventListener("online", flush);
  setInterval(flush, 15000);
  // leaving the tab: what is still queued goes with keepalive, the request outlives the page
  window.addEventListener("pagehide", function () {
    queued().forEach(function (body) {
      try {
        fetch(url, { method: "POST", credentials: "same-origin", keepalive: true,
                     headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });
      } catch (e) { /* sent again from the queue */ }
    });
  });
  flush();
  // the server's errors and notes on a page, as a page shown again would have them (_item.html)
  function mark(p, answer) {
    p.querySelectorAll("[data-block-message]").forEach(function (el) { el.remove(); });
    p.querySelectorAll(".has-error, .has-nudge").forEach(function (el) { el.classList.remove("has-error", "has-nudge"); });
    var first = null;
    [["errors", "has-error", "p-error", "alert"], ["nudges", "has-nudge", "p-nudge", "status"]].forEach(function (k) {
      Object.keys(answer[k[0]] || {}).forEach(function (id) {
        var item = p.querySelector("#i" + id);
        if (!item) return;
        item.classList.add(k[1]);
        var msg = document.createElement("p");
        msg.className = k[2]; msg.setAttribute("role", k[3]); msg.id = "msg" + id;
        msg.setAttribute("data-item-message", ""); msg.setAttribute("data-block-message", "");
        msg.textContent = answer[k[0]][id];
        item.appendChild(msg);
        item.querySelectorAll("input:not([type=hidden]), textarea, select").forEach(function (input) {
          input.setAttribute("aria-describedby", msg.id);
          if (k[0] === "errors") input.setAttribute("aria-invalid", "true");
        });
        first = first || item;
      });
    });
    var summary = messageSummary(p, p.querySelectorAll(".has-error, .has-nudge"));
    if (summary) {
      summary.scrollIntoView({ block: "start" });
      try { summary.focus({ preventScroll: true }); } catch (e) { summary.focus(); }
    } else if (first) {
      first.scrollIntoView({ block: "center" });
      if (first.classList.contains("has-error")) focusGap(first, first);
    }
  }
  function show(target) {
    clock();
    current.hidden = true;
    target.hidden = false;
    current = target;
    since = Date.now();
    var n = parseInt(target.getAttribute("data-page"), 10), count = parseInt(target.getAttribute("data-page-count"), 10);
    var bar = document.querySelector(".p-progress");
    if (bar && n && count) {
      bar.setAttribute("aria-valuenow", String(Math.min(n, count)));  // the page, as the text says (UI/UX audit 2026-10-16, 96)
      bar.setAttribute("aria-valuetext", target.getAttribute("data-announce"));
      var fill = bar.querySelector("span");
      if (fill) fill.style.width = (Math.round((n - 1) / count * 1000) / 10) + "%";
    }
    if (target.getAttribute("data-title")) document.title = target.getAttribute("data-title");
    // the new page starts at its heading, and a screen reader hears that the page changed (BITV, concept D)
    window.scrollTo(0, 0);
    var heading = target.querySelector("h1");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      try { heading.focus({ preventScroll: true }); } catch (e) { heading.focus(); }
    }
    if (say) { say.textContent = ""; setTimeout(function () { say.textContent = target.getAttribute("data-announce"); }, 50); }
    if (note) note.textContent = "";
    document.dispatchEvent(new CustomEvent("p:page", { detail: { page: target } }));
  }
  function body(p, extra) {
    clock();
    var m = of(p);
    m.seq = Math.max(Date.now(), m.seq + 1);  // a counter that also grows over a reload of the tab
    var out = { start: start, page: key(p), seq: m.seq, ms: m.ms, form: fields(p), ack: m.ack ? 1 : 0, up: m.up ? 1 : 0,
                pos: posField ? posField.value : undefined };  // the participation and page the block was shown for (DS-01)
    Object.keys(extra || {}).forEach(function (k) { out[k] = extra[k]; });
    return out;
  }
  var busy = false;
  function forward(p) {
    if (busy) return;
    var i = pages.indexOf(p), b = body(p);
    if (p.hasAttribute("data-nudged")) b.ack = 1;
    busy = true;
    form.setAttribute("aria-busy", "true");
    serial(b).then(function (answer) {
      busy = false;
      form.removeAttribute("aria-busy");
      var errors = answer && Object.keys(answer.errors || {}).length, nudges = answer && Object.keys(answer.nudges || {}).length;
      if ((errors || (nudges && !b.ack)) && !b.up) {
        if (nudges && !errors) { of(p).ack = true; p.setAttribute("data-nudged", ""); }
        mark(p, answer || {});
        return;
      }
      mark(p, {});
      show(pages[i + 1]);
    }, function () {
      busy = false;
      form.removeAttribute("aria-busy");
      enqueue(b);  // kept in the tab and sent later; the server checks the page again at the end of the block
      show(pages[i + 1]);
      if (note) note.textContent = note.getAttribute("data-offline");
    });
  }
  form.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    var by = e.submitter, i = pages.indexOf(current);
    var back = by && by.name === "nav" && by.value === "back";
    var lang = by && by.name === "set_lang";
    var held = current.querySelector(".p-next");
    if (!back && !lang && held && held.disabled) { e.preventDefault(); return; }  // the page's minimum time runs
    if (back && i > 0) {
      e.preventDefault();
      var b = body(current, { back: 1 });
      // without a connection kept in the queue like "Next", replacing an older copy of the page there (A4)
      serial(b).catch(function () { enqueue(b); });
      show(pages[i - 1]);
      return;
    }
    if (!back && !lang && i < pages.length - 1) {
      e.preventDefault();
      forward(current);
      return;
    }
    // to the server: the last page, "Back" from the first one, the language; the whole form with every page's time
    clock();
    var sent = {};
    pages.forEach(function (p) {
      var m = of(p), more = p.querySelector("[data-hold-more-field], input[name=time_more]");
      sent[key(p)] = { ms: m.ms, up: m.up ? 1 : 0, ack: (m.ack || p.hasAttribute("data-nudged")) ? 1 : 0,
                       more: more ? parseInt(more.value, 10) || 0 : 0 };
    });
    var field = form.querySelector("input[name=_block]") || document.createElement("input");
    field.type = "hidden"; field.name = "_block";
    field.value = JSON.stringify({ start: start, pages: sent });
    form.appendChild(field);
    keep([]);  // the whole block goes with this request
  });
  // the timer of a page ran out (pagetime): on with what is answered, nothing is asked for
  form.addEventListener("p:timeup", function (e) {
    var p = e.target.closest ? e.target.closest(".p-bpage") : current;
    of(p || current).up = true;
    var i = pages.indexOf(current);
    if (i < pages.length - 1) forward(current);
    else if (form.requestSubmit) form.requestSubmit(current.querySelector(".p-next") || undefined); else form.submit();
  });
  blockPages = { form: form, current: function () { return current; } };
})();

// One page, one submit: a second click on "Next", or an automatic continue that is still pending,
// must not send the page again (the server would take it for the following page). Registered last,
// so checks that stop a submit (e.g. the price questions) have already run.
// The page is still sent as a plain form (not by fetch): the server answers with the next page, the same page with
// its errors or notes, or a redirect to a panel's completion link on another site, and the browser follows each of
// them as without the script. What the script adds is what a dropped connection needs: "Sending …" on the
// button, no sending at all while the device is offline, and after a long wait the button back with a note, so the
// page can be sent again (the inputs stay in the tab, keepPage above; a page sent twice is caught by the server).
(function () {
  var form = document.querySelector(".p-container form[method=post]");
  if (!form) return;
  var note = form.querySelector("[data-send-note]");
  var sent = false, slow = null, button = null, label = null;
  function say(text, urgent) {
    if (!note) return;
    note.textContent = "";
    if (!text) return;
    var p = document.createElement("p");
    p.className = "p-tempo-note";
    if (urgent) p.setAttribute("role", "alert");
    p.textContent = text;
    note.appendChild(p);
  }
  // "Sending …": the dots of the text dance (participant.css .p-dots, after 100 ms) so a wait looks like work in progress
  function showSending(el, text) {
    var m = /^(.*?)\s*(…|\.\.\.)$/.exec(text);
    if (!m) { el.textContent = text; return; }
    el.textContent = m[1] + " ";
    var dots = document.createElement("span");
    dots.className = "p-dots";
    dots.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 3; i++) {
      var dot = document.createElement("span");
      dot.textContent = ".";
      dots.appendChild(dot);
    }
    el.appendChild(dots);
  }
  function release(text) {
    sent = false;
    clearTimeout(slow);
    form.removeAttribute("aria-busy");
    if (button && label !== null) { button.innerHTML = label; button.removeAttribute("aria-disabled"); button.classList.remove("is-sending"); }
    button = label = null;
    say(text, !!text);
  }
  form.addEventListener("submit", function (e) {
    if (e.defaultPrevented) return;
    if (sent) { e.preventDefault(); return; }
    if (keepPage) keepPage.save();
    if (note && navigator.onLine === false) {  // the browser knows there is no network: the page stays as it is
      e.preventDefault();
      say(note.getAttribute("data-offline"), true);
      return;
    }
    sent = true;
    form.setAttribute("aria-busy", "true");
    // the server may answer with the next page itself instead of a redirect to it (one request instead of two); the
    // page that comes then turns its history entry into a GET (data-replace, below)
    if (!form.querySelector("input[name=_direct]")) {
      var direct = document.createElement("input");
      direct.type = "hidden"; direct.name = "_direct"; direct.value = "1";
      form.appendChild(direct);
    }
    if (!note) return;
    say("");
    var by = e.submitter;  // Enter sends with the unseen first button: it stands for "Next"
    if (by && by.classList.contains("p-default-submit")) by = form.querySelector(".p-next");
    if (by && by.classList.contains("p-button") && !by.hidden) {
      button = by; label = by.innerHTML;
      showSending(by, note.getAttribute("data-sending"));
      by.setAttribute("aria-disabled", "true");  // not disabled: the browser would leave out its name and value
      by.classList.add("is-sending");
    }
    // a file (a voice message) takes longer on a slow line; the browser goes on sending all the same, the note only
    // offers to send again
    var files = Array.prototype.some.call(form.querySelectorAll("input[type=file]"), function (f) { return f.files && f.files.length; });
    slow = setTimeout(function () { release(note.getAttribute("data-slow")); }, files ? 90000 : 25000);
  });
  // back from the browser history: the page may be sent again (the server checks which page it was)
  window.addEventListener("pageshow", function (e) { if (e.persisted) release(""); });
})();

// keepPage (above): item scripts follow the restored fields (live sums, piping, conditions), the person is told, and
// from now on every change is kept in the tab
(function () {
  if (!keepPage) return;
  var form = keepPage.form, note = form.querySelector("[data-send-note]");
  keepPage.restored.forEach(function (el) {
    if (el.type === "hidden") return;  // their scripts read them when they start
    if (el.type !== "radio" && el.type !== "checkbox" && el.tagName !== "SELECT") el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  if (keepPage.restored.length && note && !note.textContent) {
    var p = document.createElement("p");
    p.className = "p-keep-note";
    p.textContent = note.getAttribute("data-restored");
    note.appendChild(p);
  }
  var later = null;
  function soon() { clearTimeout(later); later = setTimeout(keepPage.save, 250); }
  ["input", "change", "click", "keyup", "pointerup", "drop"].forEach(function (type) { form.addEventListener(type, soon, true); });
  setInterval(keepPage.save, 3000);  // hidden fields that scripts fill fire no event
  window.addEventListener("pagehide", keepPage.save);
  keepPage.save();
})();

// Cloze text (items/cloze.py): Enter in a gap goes to the next gap; after the last one as in any text field.
(function () {
  document.querySelectorAll(".p-cloze").forEach(function (box) {
    var gaps = Array.prototype.slice.call(box.querySelectorAll(".p-cloze-gap"));
    gaps.forEach(function (gap, i) {
      gap.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" || !gaps[i + 1]) return;
        e.preventDefault();
        gaps[i + 1].focus();
      });
    });
  });
})();

// A page the server could not store (participant/error.html): the button repeats the request with its form.
(function () {
  document.querySelectorAll("[data-resend]").forEach(function (btn) {
    btn.addEventListener("click", function () { window.location.reload(); });
  });
})();

// An external section (participant/external.html): the page sends itself after a short pause; the button stays
// for those who are faster, and for browsers without scripts.
(function () {
  var form = document.querySelector("form[data-auto-leave]");
  if (!form) return;
  var sent = false;
  form.addEventListener("submit", function () { sent = true; });
  setTimeout(function () { if (!sent) form.submit(); }, 1000 * (parseInt(form.getAttribute("data-auto-leave"), 10) || 2));
})();

// Answers of the same page in a text ({{code}} of a question on this page, app/piping.py): the server leaves a
// span with what it knows ([data-pipe], the stored answer or the text for an empty one) and what is needed to
// read the answer off the page; here it is kept current while the person answers.
(function () {
  var spans = Array.prototype.slice.call(document.querySelectorAll("[data-pipe]"));
  if (!spans.length) return;
  var form = spans[0].closest("form") || document;
  var LIMIT = 200;
  function cut(text) {
    text = String(text || "").replace(/\s+/g, " ").trim();
    return text.length <= LIMIT ? text : text.slice(0, LIMIT - 1).trim() + "…";
  }
  function fields(name) {
    return Array.prototype.slice.call(form.querySelectorAll('[name="' + name + '"]'));
  }
  function label(input) {
    // what somebody typed next to the option ("Anderes: …") is its wording
    var box = input.closest("label"), other = box ? box.querySelector(".p-other") : null;
    if (other && other.value.trim()) return cut(other.value);
    var text = box ? box.querySelector("span") : null;
    return text ? cut(text.textContent) : "";
  }
  function chosen(item) {
    var out = [];
    fields("i" + item).forEach(function (f) {
      if (f.tagName === "SELECT") {
        if (!f.value) return;
        var typed = form.querySelector('[data-select-text="' + f.value.replace(/"/g, '\\"') + '"]');
        out.push(typed && typed.value.trim() ? cut(typed.value) : cut(f.options[f.selectedIndex].textContent));
      } else if ((f.type === "radio" || f.type === "checkbox") && f.checked) out.push(label(f));
    });
    return out.filter(Boolean);
  }
  function number(span, raw) {
    if (raw === "" || raw === null || isNaN(parseFloat(raw))) return "";
    var places = parseInt(span.dataset.pipePlaces || "", 10), n = parseFloat(raw);
    var text = isNaN(places) ? String(n) : roundHalfUp(n, places).toFixed(places);
    if (span.dataset.pipeComma) text = text.replace(".", ",");
    return (span.dataset.pipePre || "") + text + (span.dataset.pipeSuf || "");
  }
  function value(span) {
    var kind = span.dataset.pipeKind;
    if (kind === "choice") return chosen(span.dataset.pipeItem)[0] || "";
    if (kind === "multi") {
      var all = chosen(span.dataset.pipeItem);
      if (all.length < 2) return all.join("");
      return all.slice(0, -1).join(", ") + " " + (span.dataset.pipeAnd || "und") + " " + all[all.length - 1];
    }
    if (kind === "text") { var f = fields("i" + span.dataset.pipeItem)[0]; return f ? cut(f.value) : ""; }
    if (kind === "value") {
      var found = fields(span.dataset.pipeField), raw = "";
      found.forEach(function (x) {
        if (x.type === "radio") { if (x.checked) raw = x.value; } else if (x.type === "hidden" && x.value !== "") raw = x.value;
      });
      return number(span, raw);
    }
    return "";
  }
  function update() {
    spans.forEach(function (span) {
      var text = value(span);
      span.textContent = text || span.dataset.fallback || "";
    });
  }
  // after the handlers of the fields themselves (a slider writes its value into a hidden field)
  function later() { setTimeout(update, 0); }
  form.addEventListener("input", later);
  form.addEventListener("change", later);
  form.addEventListener("click", later);
})();

// Copy buttons (the deletion code on the end page and the privacy page): shown only where the browser can copy;
// the label says "Copied" for a moment. Only its own buttons (.p-copy):
// End page with a completion link that moves on by itself (end.html): the seconds count down beside "Next" and
// "Stop" holds it, so nobody is sent away before they are done reading (WCAG 2.2.1).
(function () {
  var box = document.querySelector("[data-forward-after]");
  if (!box) return;
  var link = box.querySelector("[data-forward]"), note = box.querySelector("[data-forward-note]");
  var stop = box.querySelector("[data-forward-stop]"), live = box.querySelector("[data-forward-live]");
  var end = Date.now() + (parseInt(box.dataset.forwardAfter, 10) || 0) * 1000;
  var timer = setInterval(function () {
    var n = Math.ceil((end - Date.now()) / 1000);
    if (n <= 0) { clearInterval(timer); location.href = link.href; return; }
    note.textContent = note.dataset.text.split("{n}").join(n);
  }, 250);
  stop.hidden = false;
  stop.addEventListener("click", function () {
    clearInterval(timer);
    note.textContent = note.dataset.stopped;
    if (live) live.textContent = note.dataset.stopped;
    stop.hidden = true;
    link.focus();
  });
})();

// app.js copies the field named in data-copy="#field".
(function () {
  if (!navigator.clipboard) return;
  document.querySelectorAll(".p-copy[data-copy]").forEach(function (button) {
    var label = button.textContent;
    button.hidden = false;
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(button.dataset.copy).then(function () {
        button.textContent = button.dataset.copied;
        setTimeout(function () { button.textContent = label; }, 2000);
      });
    });
  });
})();

// Small things without inline code (the pages' CSP allows no inline script; the researchers' pages have them from
// app.js): a select or field with data-submit-on-change saves its form at once (requestSubmit, so a
// question on the form, data-confirm, is asked; on the document, so it also holds for parts drawn anew); a field
// with a link to copy selects all of it when clicked or entered (data-select-all); a button prints the page
// (data-print); a form or link that only shows how something looks does nothing (data-inert: theme sample, the
// element's preview in the editor)
// A number as its value (data-submit-on-change="700") waits that many milliseconds after the last change, so several
// boxes can be ticked before the page loads once (results, "Nur zeigen"; UI/UX audit 2026-10-07, W62)
// The same helpers stand in app.js for the researchers' pages: whichever script comes first binds them, once
// (window.qSmallHelpers; code review 2026-10-07, F9)
if (!window.qSmallHelpers) {
  window.qSmallHelpers = true;
  document.addEventListener("change", function (e) {
    var el = e.target, form = el.form;
    if (!el.matches || !el.matches("select[data-submit-on-change], input[data-submit-on-change]") || !form) return;
    var send = function () { if (form.requestSubmit) form.requestSubmit(); else form.submit(); };
    var wait = parseInt(el.getAttribute("data-submit-on-change"), 10);
    if (!(wait > 0)) { send(); return; }
    clearTimeout(form._submitLater);
    form._submitLater = setTimeout(send, wait);
  });
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-select-all], [data-print], a[data-inert]");
    if (!el) return;
    if (el.hasAttribute("data-print")) window.print();
    else if (el.hasAttribute("data-inert")) e.preventDefault();
    else el.select();
  });
  document.addEventListener("focusin", function (e) { if (e.target.matches && e.target.matches("[data-select-all]")) e.target.select(); });
  document.addEventListener("submit", function (e) { if (e.target.matches("form[data-inert]")) e.preventDefault(); });
}

// A box "none of these" (data-exclusive) unticks the other boxes of its group, and they untick it (ACBC no-go
// question, MaxDiff anchor, options of a multiple choice that exclude the others)
document.addEventListener("change", function (e) {
  var el = e.target, box = el.closest && el.closest(".p-options, .p-image-options");
  if (!box || !el.checked || !el.matches("input[type=checkbox]") || !box.querySelector("[data-exclusive]")) return;
  var alone = el.hasAttribute("data-exclusive");
  box.querySelectorAll("input[type=checkbox]").forEach(function (i) {
    if (i !== el && (alone || i.hasAttribute("data-exclusive"))) i.checked = false;
  });
});

// ACBC build-your-own: the price of the chosen levels, base price plus the surcharges, rounded to the price step
document.querySelectorAll("[data-acbc-byo]").forEach(function (box) {
  var total = box.querySelector("[data-acbc-total]"), info = box.querySelector(".acbc-byo-total");
  if (!total || !info) return;
  function update() {
    var sum = parseFloat(info.dataset.base) || 0, step = parseFloat(info.dataset.step) || 1;
    box.querySelectorAll("input[type=radio]:checked").forEach(function (r) { sum += parseFloat(r.dataset.price) || 0; });
    sum = Math.round(sum / step) * step;
    // the separators of the language as acbc.format_price writes them: a decimal comma and the currency after the
    // number in every language but those that write numbers like English (fr read "€1,299" as 1,30 €; Z-5)
    var comma = !pointLang(info.dataset.lang), cur = info.dataset.currency;
    var text = sum.toLocaleString(comma ? "de-DE" : "en-GB",
      { minimumFractionDigits: step < 1 || sum % 1 ? 2 : 0, maximumFractionDigits: 2 });
    total.textContent = comma || cur.length > 1 ? text + " " + cur : cur + text;
  }
  box.addEventListener("change", update);
  update();
});

// The preview bar: "Prüfen" and the IP region reload the test run with the new setting (?validate=, ?geo=);
// data-top: from inside the frame of the mobile view, the whole page
document.addEventListener("change", function (e) {
  var el = e.target.closest && e.target.closest("[data-preview-param]");
  if (!el) return;
  var value = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
  (el.hasAttribute("data-top") ? window.top : window).location.href =
    "?lang=" + encodeURIComponent(el.dataset.lang) + "&" + el.dataset.previewParam + "=" + encodeURIComponent(value);
});

// The mobile view of the preview (participant/preview_device.html): screen size and orientation (remembered in this
// browser; the frame adds 12 px on each side), the phone scaled down to fit the window, and the bar kept in sync
// with the page shown inside the phone
(function () {
  var device = document.querySelector("[data-device]");
  var frame = document.querySelector("[data-device-frame]");
  if (!device || !frame) return;
  var sizeSel = document.querySelector("[data-device-size]"), rotate = document.querySelector("[data-device-rotate]");
  var caption = document.querySelector("[data-device-caption]"), orient = document.querySelector("[data-device-orientation]");
  var landscape = false, W = 390, H = 844, actual = false;
  var actualBtn = document.querySelector("[data-device-actual]"), actualText = actualBtn ? actualBtn.textContent : "";
  try {
    var saved = JSON.parse(localStorage.getItem("preview-device") || "{}");
    if (saved.size && sizeSel.querySelector('option[value="' + saved.size + '"]')) sizeSel.value = saved.size;
    landscape = !!saved.landscape;
  } catch (e) { /* storage unavailable */ }
  function apply() {
    var wh = sizeSel.value.split("x").map(Number);
    W = landscape ? wh[1] : wh[0]; H = landscape ? wh[0] : wh[1];
    frame.style.width = W + "px"; frame.style.height = H + "px";
    device.style.width = (W + 24) + "px"; device.style.height = (H + 24) + "px";
    device.classList.toggle("is-landscape", landscape);
    // the words in the researcher's language, from the button (preview_device.html)
    var portrait = rotate.dataset.portrait || "Hochformat", wide = rotate.dataset.landscape || "Querformat";
    caption.dataset.size = W + " × " + H + " px · " + (landscape ? wide : portrait);
    orient.textContent = landscape ? portrait : wide;
    try { localStorage.setItem("preview-device", JSON.stringify({ size: sizeSel.value, landscape: landscape })); } catch (e) { /* ignore */ }
    fit();
  }
  // Scale the phone down when the window is too small (the layout inside keeps the chosen size); the caption says so,
  // and "Originalgröße" shows it unscaled (X48)
  function fit() {
    var dw = W + 24, dh = H + 24;
    var avail = window.innerHeight - 36 - 110, availW = window.innerWidth - 32 - (window.innerWidth > 900 ? 300 : 0);
    var fitted = Math.min(1, avail / dh, availW / dw), s = actual ? 1 : fitted;
    var pct = Math.round(fitted * 100);
    caption.textContent = caption.dataset.size + (s < 1 && caption.dataset.scaled ? " · " + caption.dataset.scaled.replace("{p}", pct) : "");
    if (actualBtn) {
      actualBtn.hidden = fitted >= 1;
      actualBtn.textContent = actual ? actualBtn.dataset.fit : actualText;
    }
    device.style.transform = "scale(" + s + ")";
    device.style.marginBottom = (dh * s - dh) + "px";
    device.style.marginLeft = device.style.marginRight = ((dw * s - dw) / 2) + "px";
  }
  sizeSel.addEventListener("change", apply);
  if (actualBtn) actualBtn.addEventListener("click", function () { actual = !actual; fit(); });
  rotate.addEventListener("click", function () { landscape = !landscape; apply(); });
  window.addEventListener("resize", fit);
  apply();
  var label = document.querySelector("[data-pager-label]"), bar = document.querySelector(".p-preview-bar");
  var prev = document.querySelector("[data-prev]"), next = document.querySelector("[data-next]");
  frame.addEventListener("load", function () {
    var main = frame.contentDocument && frame.contentDocument.querySelector("main[data-page]");
    if (!main || !label) return;
    var n = main.dataset.pageCount, p = main.dataset.page, lang = device.dataset.lang;
    // another page inside the phone: reload this page once so the block list and the comments match it
    if (p !== "end" && p !== device.dataset.page && document.querySelector("[data-comments]")) { location.replace(location.pathname + "?lang=" + lang); return; }
    var text = label.querySelector("summary") ? label.querySelector("summary").firstChild : label;  // keep the dropdown
    if (p === "end") { text.textContent = bar.dataset.end + " "; p = parseInt(n, 10) + 1; }
    else { text.textContent = bar.dataset.page.replace("{p}", p).replace("{n}", n) + " · " + main.dataset.block + " "; p = parseInt(p, 10); }
    prev.href = "?lang=" + lang + "&page=" + (p - 1); prev.classList.toggle("off", p <= 1);
    next.href = "?lang=" + lang + "&page=" + (p + 1); next.classList.toggle("off", p >= parseInt(n, 10));
  });
})();

// An entry of a diary study ends: the end page carries data-app-done, and the Askurious app (its WebView,
// askurious-android EntryActivity) is told so it can close and refresh the study (app/diary_api.py)
(function () {
  if (!document.querySelector("[data-app-done]") || !window.AskuriousApp || !window.AskuriousApp.done) return;
  try { window.AskuriousApp.done(); } catch (e) { /* not in the app */ }
})();

// A page of a diary study kept in the app for answering without a connection (app/diary_api.py, offline_form):
// "Next" hands the filled form to the app (AskuriousApp.offlineSubmit, askurious-android EntryActivity), which sends
// it once it has a connection; nothing goes to the server from here
(function () {
  var form = document.querySelector("form[data-app-offline]");
  if (!form || !window.AskuriousApp || !window.AskuriousApp.offlineSubmit) return;
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var fields = {};
    new FormData(form, e.submitter || undefined).forEach(function (value, name) {
      if (typeof value !== "string") return;
      (fields[name] = fields[name] || []).push(value);
    });
    try { window.AskuriousApp.offlineSubmit(JSON.stringify(fields)); } catch (err) { /* not in the app */ }
  });
})();
