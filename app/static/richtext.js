// Formatted texts without typing marks: an editing field that sits on top of a textarea, shows its text the
// way participants see it and writes every change back as the marks the server has always read
// (**bold**, *italic*, `code`, H~2~O, x^2^, <u>, <small>, <sub>, <sup>, <br>; in long texts blank line =
// paragraph, "- " = list item, a line that is bold as a whole = heading). The textarea stays the form field,
// so saving, the live preview, translations and exports know nothing of the editor; a text nobody types in
// is sent back unchanged.
//
// Part 1 (also loaded by the tests under Node) turns the text into a model and back and holds what the
// buttons do to a model. Part 2 is the field in the page.

(function (root) {
  "use strict";

  // ---- the text as a model -------------------------------------------------------------------------------
  // [{type: "p" | "h" | "li", atoms: [...]}]; in "inline" mode (question texts) there is one block. Atoms:
  // {k: "t", s: text, f}, {k: "br"}, {k: "v", name, alt, f} (placeholder {{name}}, or {{name|alt}} with the text
  // for an empty value), {k: "g", n, f} (gap of a cloze text with n spaces), {k: "cA", f} and {k: "cF", f}
  // (start and end of the selection). f names the formats as "b i u small code sub sup" (those that apply, in
  // this order).

  // The patterns of app/items/base.py, to the letter: what the server takes for bold is bold here.
  // Python's \s and \w, which differ from the ones of JavaScript:
  var WS = "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  var WORD = "\\p{L}\\p{N}_";
  var CODE = /`([^`]+)`/g;
  var BOLD = /\*\*([^\n]+?)\*\*/g;
  var ITALIC = new RegExp("(?<![*" + WORD + "])\\*(?![" + WS + "*])([^\\n]+?)(?<![" + WS + "*])\\*(?![*" + WORD + "])", "gu");
  // _italic_, never inside a word or an address, path or placeholder (_UNDER_OPEN and _UNDER_CLOSE)
  var UNDER = new RegExp("(?<![" + WORD + "/\\\\.:@{}=?#%+~^`$|&\\-])_(?![" + WS + "_])([^\\n]+?)(?<![" + WS + "_])_(?![" + WORD + "/\\\\@{}=#%+~^`$|])", "gu");
  var SUB = new RegExp("(?<!~)~([^~" + WS + "]+)~(?!~)", "g");
  var SUP = new RegExp("\\^([^\\^" + WS + "]+)\\^", "g");
  var TAG = /&lt;(\/?)(b|strong|i|em|small|u|sub|sup|code)&gt;/gi;
  var BR = new RegExp("&lt;br[" + WS + "]*/?&gt;", "gi");
  var PLACEHOLDER = new RegExp("\\{\\{[" + WS + "]*([A-Za-z_][A-Za-z0-9_]*)[" + WS + "]*(?:\\|([^{}|]*))?\\}\\}", "g");
  var GAP = /\[( *)\]/g;
  var HEADING = /^\*\*([^\n]+)\*\*$/;
  var BLANK_LINE = new RegExp("\\n[" + WS + "]*\\n");
  var LEFT = new RegExp("^[" + WS + "]+"), RIGHT = new RegExp("[" + WS + "]+$"), SPACE = new RegExp("[" + WS + "]+", "g");
  var FORMATS = ["b", "i", "u", "small", "code", "sub", "sup"];
  var CANON = { strong: "b", em: "i" };
  var CARET_A = "\uE000", CARET_F = "\uE001";  // the selection inside a text, while it goes through parse()

  function trim(s) { return s.replace(LEFT, "").replace(RIGHT, ""); }
  function key(list) { return FORMATS.filter(function (f) { return list.indexOf(f) >= 0; }).join(" "); }
  function formats(f) { return f ? f.split(" ") : []; }
  function has(f, name) { return formats(f).indexOf(name) >= 0; }
  function setFormat(f, name, on) {
    var list = formats(f).filter(function (x) { return x !== name && !(on && (name === "sub" || name === "sup") && (x === "sub" || x === "sup")); });
    return key(on ? list.concat(name) : list);
  }
  function escape(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/'/g, "&#39;").replace(/"/g, "&#34;");
  }
  function unescape(s) {
    return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;/g, "'").replace(/&#34;/g, '"').replace(/&amp;/g, "&");
  }
  function isCaret(a) { return a.k === "cA" || a.k === "cF"; }
  // The text of a placeholder for an empty value: one line, without what would end the placeholder
  function altText(s) { return trim(String(s || "").replace(/[{}|\x00-\x1f​]/g, " ").replace(SPACE, " ")); }
  function ref(a) { return a.name + (a.alt ? "|" + a.alt : ""); }
  function hasContent(atoms) {
    return atoms.some(function (a) { return a.k === "v" || a.k === "g" || (a.k === "t" && trim(a.s) !== ""); });
  }

  // What the server makes of one stretch of text (_inline after fill), as atoms. The tags it writes are read
  // the way a browser reads them: a format holds from where it opens to where it closes, also when the
  // marks overlap.
  function inlineAtoms(raw, ctx, lineBreaks) {
    var html = escape(raw).replace(PLACEHOLDER, function (m, name, alt) {
      ctx.vars.push({ name: name, alt: altText(unescape(alt || "")) });
      return "\x01" + (ctx.vars.length - 1) + "\x01";
    });
    if (lineBreaks) html = html.replace(/\n/g, "<br>");
    var codes = [];
    html = html.replace(CODE, function (m, c) { codes.push(c); return "\x00" + (codes.length - 1) + "\x00"; });
    html = html.replace(BOLD, "<strong>$1</strong>").replace(ITALIC, "<em>$1</em>").replace(UNDER, "<em>$1</em>");
    html = html.replace(SUB, "<sub>$1</sub>").replace(SUP, "<sup>$1</sup>").replace(BR, "<br>");
    var stack = [], out = [], pos = 0, m;
    TAG.lastIndex = 0;
    while ((m = TAG.exec(html))) {
      out.push(html.slice(pos, m.index));
      var tag = m[2].toLowerCase();
      if (!m[1]) { stack.push(tag); out.push("<" + tag + ">"); }
      else if (stack.indexOf(tag) >= 0) {
        while (stack.length) { var t = stack.pop(); out.push("</" + t + ">"); if (t === tag) break; }
      }
      pos = m.index + m[0].length;
    }
    out.push(html.slice(pos));
    while (stack.length) out.push("</" + stack.pop() + ">");
    return tokens(out.join(""), codes, ctx, []);
  }

  function tokens(html, codes, ctx, base) {
    var token = /<(\/?)(strong|em|b|i|small|u|sub|sup|code)>|<br>|\x00(\d+)\x00|\x01(\d+)\x01|\x02(\d+)\x03/g;
    var atoms = [], open = base.slice(), pos = 0, m;
    function text(s) { if (s) atoms.push({ k: "t", s: unescape(s), f: key(open) }); }
    while ((m = token.exec(html))) {
      text(html.slice(pos, m.index));
      pos = m.index + m[0].length;
      if (m[0] === "<br>") atoms.push({ k: "br" });
      else if (m[3] !== undefined) atoms = atoms.concat(tokens(codes[+m[3]], codes, ctx, open.concat("code")));
      else if (m[4] !== undefined) {
        var v = ctx.vars[+m[4]], atom = { k: "v", name: v.name, f: key(open) };
        if (v.alt) atom.alt = v.alt;
        atoms.push(atom);
      }
      else if (m[5] !== undefined) atoms.push({ k: "g", n: ctx.gaps[+m[5]], f: key(open) });
      else {
        var name = CANON[m[2]] || m[2];
        if (!m[1]) open.push(name);
        else {
          var at = open.lastIndexOf(name);
          if (at >= base.length) {
            open.splice(at, 1);
            // closing a format also closes a sub or sup opened inside it (they are no formatting elements)
            if (name !== "sub" && name !== "sup") open = open.filter(function (f, i) { return i < at || (f !== "sub" && f !== "sup"); });
          }
        }
      }
    }
    text(html.slice(pos));
    return atoms;
  }

  // mode "inline": inline_rich (question texts); mode "block": rich (paragraphs, lists, headings).
  // opts.gaps: [ ] is a gap (cloze text).
  function parse(text, mode, opts) {
    var ctx = { vars: [], gaps: [] };
    text = String(text || "").replace(/\r\n?/g, "\n").replace(/[\x00-\x03]/g, "");
    if (opts && opts.gaps) text = text.replace(GAP, function (m, spaces) { ctx.gaps.push(spaces.length); return "\x02" + (ctx.gaps.length - 1) + "\x03"; });
    if (mode !== "block") return [{ type: "p", atoms: inlineAtoms(text, ctx, true) }];
    var blocks = [];
    trim(text).split(BLANK_LINE).forEach(function (part) {
      var lines = part.split("\n").filter(function (l) { return trim(l); });
      if (!lines.length) return;
      var heading = HEADING.exec(trim(lines[0]));
      if (heading) {
        blocks.push({ type: "h", atoms: inlineAtoms(heading[1], ctx, false).map(function (a) { return a.f ? Object.assign({}, a, { f: setFormat(a.f, "b", false) }) : a; }) });
        lines = lines.slice(1);
        if (!lines.length) return;
      }
      if (lines.every(function (l) { return l.replace(LEFT, "").slice(0, 2) === "- "; })) {
        lines.forEach(function (l) {
          var atoms = inlineAtoms(trim(l), ctx, false), first = atoms[0];
          if (first && first.k === "t" && first.s.slice(0, 2) === "- ") {
            if (first.s.length > 2) atoms[0] = { k: "t", s: first.s.slice(2), f: first.f };
            else atoms.shift();
          }
          blocks.push({ type: "li", atoms: atoms });
        });
      } else {
        var atoms = [];
        lines.forEach(function (l, i) { atoms = atoms.concat(i ? [{ k: "br" }] : [], inlineAtoms(trim(l), ctx, false)); });
        blocks.push({ type: "p", atoms: atoms });
      }
    });
    return blocks;
  }

  // ---- comparing models ----------------------------------------------------------------------------------

  function tidy(atoms) {
    var out = [];
    atoms.forEach(function (a) {
      var last = out[out.length - 1];
      if (a.k === "t" && !a.s) return;
      if (a.k === "t" && last && last.k === "t" && last.f === a.f) out[out.length - 1] = { k: "t", s: last.s + a.s, f: a.f };
      else out.push(a);
    });
    return out;
  }
  function strip(blocks) {
    return blocks.map(function (b) { return { type: b.type, atoms: b.atoms.filter(function (a) { return !isCaret(a); }) }; });
  }
  function splitLines(atoms) {
    var lines = [[]];
    atoms.forEach(function (a) { if (a.k === "br") lines.push([]); else lines[lines.length - 1].push(a); });
    return lines;
  }
  function trimLine(line) {
    line = line.slice();
    while (line.length && line[0].k === "t") {
      var s = line[0].s.replace(LEFT, "");
      if (s) { line[0] = { k: "t", s: s, f: line[0].f }; break; }
      line.shift();
    }
    while (line.length && line[line.length - 1].k === "t") {
      var e = line[line.length - 1].s.replace(RIGHT, "");
      if (e) { line[line.length - 1] = { k: "t", s: e, f: line[line.length - 1].f }; break; }
      line.pop();
    }
    return line;
  }
  function flat(a) { return a.k === "t" ? ["t", a.s, a.f] : a.k === "v" ? ["v", ref(a), a.f] : a.k === "g" ? ["g", a.n, a.f] : ["br"]; }

  // The model without what makes no difference to a reader: in long texts the server drops spaces at the
  // ends of lines, empty lines at the ends of a block and empty blocks, and a heading is bold anyway.
  function norm(blocks, mode) {
    blocks = strip(blocks);
    if (mode !== "block") {
      var all = [];
      blocks.forEach(function (b, i) { all = all.concat(i ? [{ k: "br" }] : [], b.atoms); });
      return [["p", tidy(all).map(flat)]];
    }
    var out = [];
    blocks.forEach(function (b) {
      var lines = splitLines(tidy(b.atoms)).map(trimLine);
      while (lines.length && !lines[0].length) lines.shift();
      while (lines.length && !lines[lines.length - 1].length) lines.pop();
      if (!lines.length) return;
      var atoms = [];
      lines.forEach(function (l, i) { atoms = atoms.concat(i ? [{ k: "br" }] : [], l); });
      if (b.type === "h") atoms = atoms.map(function (a) { return a.f ? Object.assign({}, a, { f: setFormat(a.f, "b", false) }) : a; });
      out.push([b.type, tidy(atoms).map(flat)]);
    });
    return out;
  }
  function same(a, b, mode) { return JSON.stringify(norm(a, mode)) === JSON.stringify(norm(b, mode)); }

  // What two models have in common when only spaces and line breaks differ: while somebody types, a space at
  // the end of a line or a paragraph that is still empty must not count as a difference.
  function sig(blocks, mode) {
    var out = [];
    norm(blocks, mode).forEach(function (b) {
      var atoms = [];
      b[1].forEach(function (a) {
        if (a[0] === "br") return;
        var s = a[0] === "t" ? a[1].replace(SPACE, "") : a[1], last = atoms[atoms.length - 1];
        if (a[0] === "t" && !s) return;
        if (a[0] === "t" && last && last[0] === "t" && last[2] === a[2]) last[1] += s;
        else atoms.push([a[0], s, a[2]]);
      });
      if (atoms.length) out.push([b[0], atoms]);
    });
    return JSON.stringify(out);
  }

  // ---- the model as text ---------------------------------------------------------------------------------

  var MARKS = { b: "**", i: "*", sub: "~", sup: "^", code: "`" };

  // level 0 writes marks wherever there is one; each further level writes one more format as tags, for the
  // places where the server would not read the mark (italic inside a word, a space inside ~ ~ ...)
  function emit(atoms, level, lineBreak) {
    var mark = { b: level < 3, i: level < 1, sub: level < 2, sup: level < 2, code: level < 4 };
    var out = "", stack = [];
    function open(f) { return mark[f] ? MARKS[f] : "<" + f + ">"; }
    function close(f) { return mark[f] ? MARKS[f] : "</" + f + ">"; }
    function span(f, from) {  // over how many atoms the format goes on: the longest opens first
      var n = from;
      while (n < atoms.length && atoms[n].k !== "br" && has(atoms[n].f, f)) n++;
      return n - from;
    }
    atoms = tidy(atoms);
    atoms.forEach(function (a, i) {
      var wanted = a.k === "br" ? [] : formats(a.f), keep = 0;
      while (keep < stack.length && wanted.indexOf(stack[keep]) >= 0) keep++;
      // nothing may open inside `code`: there the server leaves every mark as it is
      var code = stack.indexOf("code");
      if (code >= 0 && code < keep && wanted.some(function (f) { return stack.slice(0, keep).indexOf(f) < 0; })) keep = code;
      while (stack.length > keep) out += close(stack.pop());
      wanted.filter(function (f) { return stack.indexOf(f) < 0; })
        .sort(function (x, y) { return (x === "code") - (y === "code") || span(y, i) - span(x, i); })
        .forEach(function (f) { stack.push(f); out += open(f); });
      out += a.k === "t" ? a.s : a.k === "br" ? lineBreak : a.k === "v" ? "{{" + ref(a) + "}}"
        : a.k === "g" ? "[" + new Array(a.n + 1).join(" ") + "]" : a.k === "cA" ? CARET_A : CARET_F;
    });
    while (stack.length) out += close(stack.pop());
    return out;
  }

  // level: one number, or in long texts one per block (blocks are read one by one)
  function build(blocks, mode, levels) {
    var level = levels;
    if (mode !== "block") {
      var all = [];
      blocks.forEach(function (b, i) { all = all.concat(i ? [{ k: "br" }] : [], b.atoms); });
      return emit(all, level, "\n");
    }
    var out = "", before = null;
    blocks.forEach(function (b, n) {
      if (!hasContent(b.atoms)) return;
      var text;
      if (typeof levels !== "number") level = levels[n];
      if (b.type === "h") {
        text = "**" + emit(b.atoms.map(function (a) { return a.f ? Object.assign({}, a, { f: setFormat(a.f, "b", false) }) : a; }), level, "<br>") + "**";
      } else if (b.type === "li") {
        text = "- " + emit(b.atoms, level, "<br>");
      } else {
        // a line break inside a paragraph is a new line; an empty line would start a new paragraph, so it
        // is written as <br> at the end of the line before
        var lines = splitLines(b.atoms);
        while (lines.length && !hasContent(lines[0])) lines.shift();
        while (lines.length && !hasContent(lines[lines.length - 1])) lines.pop();
        text = "";
        lines.forEach(function (line, i) { text += hasContent(line) ? (i ? "\n" : "") + emit(line, level, "") : "<br>"; });
      }
      out += (before === null ? "" : before === "li" && b.type === "li" ? "\n" : "\n\n") + text;
      before = b.type;
    });
    return out;
  }

  // The text for a model: the shortest spelling the server reads as exactly this model. `exact` is false
  // when there is none, which means the text holds typed marks (somebody wrote **word** into the field);
  // then the field shows what these marks do (see withCarets).
  function serialize(blocks, mode, opts) {
    var clean = strip(blocks), text = "", level;
    function fits(part, levels) { text = build(part, mode, levels); return same(parse(text, mode, opts), part, mode); }
    if (mode === "block") {
      var levels = clean.map(function (b) {
        for (level = 0; level < 4; level++) if (fits([b], level)) break;
        return level;
      });
      var whole = fits(clean, levels);
      return { text: text, level: levels, exact: whole };
    }
    for (level = 0; level < 4; level++) if (fits(clean, level)) break;
    var exact = fits(clean, level);
    return { text: text, level: level, exact: exact };
  }

  function extractCarets(blocks) {
    return blocks.map(function (b) {
      var atoms = [];
      b.atoms.forEach(function (a) {
        if (a.k !== "t") { atoms.push(a); return; }
        a.s.split(/([\uE000\uE001])/).forEach(function (piece) {
          if (piece === CARET_A || piece === CARET_F) atoms.push({ k: piece === CARET_A ? "cA" : "cF", f: a.f });
          else if (piece) atoms.push({ k: "t", s: piece, f: a.f });
        });
      });
      return { type: b.type, atoms: atoms };
    });
  }
  function carets(blocks) {
    var found = {};
    blocks.forEach(function (b, bi) { b.atoms.forEach(function (a, ai) { if (isCaret(a)) found[a.k] = [bi, ai]; }); });
    return found.cA && found.cF ? found : null;
  }
  function caretsAtEnd(blocks) {
    blocks = strip(blocks);
    if (!blocks.length) blocks.push({ type: "p", atoms: [] });
    blocks[blocks.length - 1].atoms.push({ k: "cA", f: "" }, { k: "cF", f: "" });
    return blocks;
  }
  // The model the server reads from the text of `blocks`, with the selection where it was (at the end if it
  // cannot be told, e.g. inside an empty paragraph)
  function withCarets(blocks, mode, opts, level) {
    var read = parse(build(strip(blocks), mode, level), mode, opts);
    var marked = extractCarets(parse(build(blocks, mode, level), mode, opts));
    return carets(marked) && sig(marked, mode) === sig(read, mode) ? marked : caretsAtEnd(read);
  }

  // ---- what the buttons and keys do ----------------------------------------------------------------------

  function selection(blocks) {
    var found = carets(blocks);
    if (!found) return null;
    var a = found.cA, f = found.cF, forward = a[0] < f[0] || (a[0] === f[0] && a[1] < f[1]);
    return { start: forward ? a : f, end: forward ? f : a };
  }
  function ensureCarets(blocks) {
    if (carets(blocks)) return blocks;
    var at = caretsAtEnd(blocks);
    blocks.length = 0;
    at.forEach(function (b) { blocks.push(b); });
    return blocks;
  }
  // Removes what is selected; returns [block, atom] of the two selection marks, which then stand together
  function collapse(blocks) {
    var sel = selection(ensureCarets(blocks)), s = sel.start, e = sel.end;
    var first = blocks[s[0]], f = first.atoms[s[1]].f || "";
    first.atoms = first.atoms.slice(0, s[1]).concat([{ k: "cA", f: f }, { k: "cF", f: f }], blocks[e[0]].atoms.slice(e[1] + 1));
    blocks.splice(s[0] + 1, e[0] - s[0]);
    return [s[0], s[1]];
  }
  function insert(blocks, atoms) {
    var at = collapse(blocks), block = blocks[at[0]], f = block.atoms[at[1]].f;
    atoms = atoms.map(function (a) { return a.k === "br" || a.f !== undefined ? a : Object.assign({ f: f }, a); });
    block.atoms.splice.apply(block.atoms, [at[1], 0].concat(atoms));
  }
  // Text from the keyboard buffer or a dropped text: line breaks stay; in long texts an empty line starts a
  // new paragraph
  function insertText(blocks, text, mode) {
    text = String(text || "").replace(/\r\n?/g, "\n").replace(/[\x00-\x03\u200b\uE000\uE001]/g, "");
    (mode === "block" ? text.split(/\n{2,}/) : [text]).forEach(function (part, i) {
      if (i) splitBlock(blocks);
      var atoms = [];
      part.split("\n").forEach(function (line, n) { if (n) atoms.push({ k: "br" }); if (line) atoms.push({ k: "t", s: line }); });
      insert(blocks, atoms);
    });
  }
  // Enter in a long text: a new paragraph or list item; in an empty list item the list ends
  function splitBlock(blocks) {
    var at = collapse(blocks), block = blocks[at[0]];
    if (block.type === "li" && !hasContent(block.atoms)) { block.type = "p"; return; }
    var right = block.atoms.slice(at[1]);
    block.atoms = block.atoms.slice(0, at[1]);
    blocks.splice(at[0] + 1, 0, { type: block.type === "h" ? "p" : block.type, atoms: right });
  }
  function toggleFormat(blocks, name) {
    var sel = selection(ensureCarets(blocks)), s = sel.start, e = sel.end, picked = [];
    if (s[0] === e[0] && e[1] === s[1] + 1) {  // nothing selected: the format of what is typed next
      var on = !has(blocks[s[0]].atoms[s[1]].f, name);
      [s, e].forEach(function (p) { var a = blocks[p[0]].atoms[p[1]]; a.f = setFormat(a.f, name, on); });
      return;
    }
    blocks.forEach(function (b, bi) {
      b.atoms.forEach(function (a, ai) {
        var after = bi > s[0] || (bi === s[0] && ai > s[1]), before = bi < e[0] || (bi === e[0] && ai < e[1]);
        if (after && before && (a.k === "t" || a.k === "v" || a.k === "g")) picked.push(a);
      });
    });
    var all = picked.length && picked.every(function (a) { return has(a.f, name); });
    picked.forEach(function (a) { a.f = setFormat(a.f, name, !all); });
  }
  // "li" or "h" for the blocks the selection touches, back to a paragraph if they all are that already
  function toggleType(blocks, type) {
    var sel = selection(ensureCarets(blocks)), touched = blocks.slice(sel.start[0], sel.end[0] + 1);
    var all = touched.every(function (b) { return b.type === type; });
    touched.forEach(function (b) {
      b.type = all ? "p" : type;
      if (b.type === "h") b.atoms = b.atoms.map(function (a) { return a.k === "br" ? { k: "t", s: " ", f: "" } : a; });
    });
  }

  var core = {
    parse: parse, serialize: serialize, norm: norm, sig: sig, same: same, strip: strip, tidy: tidy,
    withCarets: withCarets, extractCarets: extractCarets, carets: carets, caretsAtEnd: caretsAtEnd,
    insert: insert, insertText: insertText, splitBlock: splitBlock, toggleFormat: toggleFormat, toggleType: toggleType,
    formats: formats, setFormat: setFormat, hasContent: hasContent, altText: altText,
  };
  if (typeof module === "object" && module.exports) module.exports = core;
  else root.QRichText = core;
})(typeof self !== "undefined" ? self : this);

// ---- the field in the page ---------------------------------------------------------------------------------
// Every <textarea data-rich="inline|block"> gets the field; data-rich-gaps: [ ] in the text are gaps.

(function () {
  "use strict";
  if (typeof document === "undefined" || typeof QRichText === "undefined") return;
  var core = QRichText;
  var ZW = "\u200b";  // lets the cursor stand in a format that has no text yet, and behind a placeholder
  var FORMAT_OF = { B: "b", STRONG: "b", I: "i", EM: "i", U: "u", SMALL: "small", CODE: "code", SUB: "sub", SUP: "sup" };
  var ELEMENT_OF = { b: "strong", i: "em", u: "u", small: "small", code: "code", sub: "sub", sup: "sup" };
  var BLOCK = /^(P|DIV|H[1-6]|LI|UL|OL|BLOCKQUOTE|PRE|SECTION|ARTICLE|TABLE|TBODY|TR|TD|TH)$/;
  var BUTTONS = [
    { format: "b", text: de_en("F", "B"), title: de_en("Fett (Strg+B)", "Bold (Ctrl+B)") },
    { format: "i", text: de_en("K", "I"), title: de_en("Kursiv (Strg+I)", "Italic (Ctrl+I)") },
    { format: "u", text: "U", title: de_en("Unterstrichen (Strg+U)", "Underlined (Ctrl+U)") },
    { format: "sub", text: "x", low: "2", title: de_en("Tiefgestellt", "Subscript") },
    { format: "sup", text: "x", high: "2", title: de_en("Hochgestellt", "Superscript") },
    { format: "small", text: de_en("klein", "small"), title: de_en("Kleiner Text", "Small text") },
    { type: "li", text: de_en("• Liste", "• List"), title: de_en("Aufzählung", "Bulleted list"), block: true },
    { type: "h", text: de_en("Überschrift", "Heading"), title: de_en("Zwischenüberschrift", "Subheading"), block: true },
    { gap: true, text: de_en("Lücke", "Gap"), title: de_en("Lücke einfügen (ein Klick auf eine Lücke ändert ihre Breite)", "Insert a gap (a click on a gap changes its width)"), gaps: true },
  ];
  var GAP_SIZES = [1, 3, 5];  // spaces between the brackets: small, medium, large (app/items/cloze.py)
  var editors = [];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }
  function gapSize(n) { return n <= 1 ? "s" : n <= 3 ? "m" : "l"; }

  // What can stand in a text of this page as a placeholder, where the page says so (the editor of an element):
  // <script type="application/json" data-rich-vars> with [{title, kind: "answer" | "loop" | "param", items:
  // [{name, text}]}]. Then a placeholder shows what it stands for instead of its name ("Antwort: Welche
  // Marke?"), one the page does not list is marked, and the buttons offer "Antwort einfügen".
  var groups = null, known = {}, looked = false;
  function vars() {
    if (looked) return groups;
    looked = true;
    var node = document.querySelector("script[data-rich-vars]");
    if (!node) return null;
    try { groups = JSON.parse(node.textContent); } catch (e) { groups = null; }
    (groups || []).forEach(function (g) { g.items.forEach(function (i) { known[i.name] = { text: i.text, kind: g.kind }; }); });
    return groups;
  }
  // where an image of a blog entry stands; the key is what follows the | (app/blog.py image_side)
  var IMAGE_SIDES = [
    { key: "", label: function () { return de_en("über die ganze Breite", "across the full width"); } },
    { key: "links", label: function () { return de_en("links, Text daneben", "left, text beside it"); } },
    { key: "rechts", label: function () { return de_en("rechts, Text daneben", "right, text beside it"); } },
  ];
  function short(s, n) { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1).trim() + "…" : s; }
  // The look of a placeholder: its words, and the text for an empty value behind them
  function dress(chip, name, alt) {
    vars();
    var info = known[name], words = "{{" + name + "}}";
    if (info) words = info.kind === "answer" ? de_en("Antwort: ", "Answer: ") + short(info.text || name, 36) : short(info.text || name, 36);
    var side = info && info.kind === "image" ? IMAGE_SIDES.filter(function (s) { return s.key === alt; })[0] : null;
    chip.textContent = words + (side ? " · " + side.label() : alt ? de_en(" · sonst „" + alt + "“", " · otherwise “" + alt + "”") : "");
    if (alt) chip.setAttribute("data-alt", alt); else chip.removeAttribute("data-alt");
    chip.classList.toggle("is-named", !!info);
    chip.classList.toggle("is-unknown", !!groups && !info);
    chip.title = info ? "{{" + name + "}}" + (info.kind === "answer" && info.text ? ": " + info.text : "")
      : groups ? "{{" + name + "}}: " + de_en("hier nicht einsetzbar (unbekannt, oder die Frage steht nicht auf einer früheren Seite)",
                                              "cannot be used here (unknown, or the question is not on an earlier page)") : "";
  }

  // --- model into the page (built from nodes: no text of a survey is ever set as HTML) ---
  function wrapped(node, f) {
    var list = core.formats(f);
    for (var i = list.length - 1; i >= 0; i--) { var outer = document.createElement(ELEMENT_OF[list[i]]); outer.appendChild(node); node = outer; }
    return node;
  }
  // pos.range: something is selected; then its start stands before the first selected letter and its end
  // behind the last one, whatever their formats (the buttons show the formats of the selected text)
  function fill(target, atoms, pos) {
    var waiting = [], lastText = null, lastF = null, afterAtom = false, starts = [];
    // Selection marks that found no text of their format to stand in get an empty text of their own:
    // next to formatted text the browser would otherwise put what is typed into that format
    function place(beforeFormat) {
      waiting.forEach(function (c) {
        var last = target.lastChild, plain = last && last.nodeType === 3;
        if (!c.f && plain) pos[c.k] = [last, last.length];
        else if (!c.f && !beforeFormat && (!last || last.tagName === "BR")) pos[c.k] = [target, target.childNodes.length];
        else {
          var holder = document.createTextNode(ZW);
          target.appendChild(wrapped(holder, c.f));
          pos[c.k] = [holder, 1];
        }
      });
      waiting = [];
    }
    atoms = core.tidy(atoms);
    atoms.forEach(function (a) {
      if (a.k === "cA" || a.k === "cF") {
        if (pos.range && !pos.started) { pos.started = true; starts.push(a); }
        else if (lastText && (pos.range || lastF === a.f)) pos[a.k] = [lastText, lastText.length];
        else waiting.push(a);
        return;
      }
      if (afterAtom && a.k !== "t") target.appendChild(document.createTextNode(ZW));
      afterAtom = false;
      if (a.k === "t") {
        var same = waiting.filter(function (c) { return c.f === a.f; });
        waiting = waiting.filter(function (c) { return c.f !== a.f; });
        place(true);
        lastText = document.createTextNode(a.s);
        lastF = a.f;
        target.appendChild(wrapped(lastText, a.f));
        same.concat(starts).forEach(function (c) { pos[c.k] = [lastText, 0]; });
        starts = [];
        return;
      }
      waiting = waiting.concat(starts);
      starts = [];
      place();
      lastText = null;
      if (a.k === "br") { target.appendChild(document.createElement("br")); return; }
      var chip = a.k === "v" ? el("span", "rt-var placeholder") : el("span", "rt-gap is-" + gapSize(a.n));
      chip.contentEditable = "false";
      chip.setAttribute(a.k === "v" ? "data-var" : "data-gap", a.k === "v" ? a.name : a.n);
      if (a.k === "v") dress(chip, a.name, a.alt);
      if (a.k === "g") chip.setAttribute("aria-label", de_en("Lücke", "Gap"));
      target.appendChild(wrapped(chip, a.f));
      afterAtom = true;
    });
    if (afterAtom) target.appendChild(document.createTextNode(ZW));
    waiting = waiting.concat(starts);
    place();
    var content = atoms.filter(function (a) { return a.k !== "cA" && a.k !== "cF"; });
    if (!content.length || content[content.length - 1].k === "br") target.appendChild(document.createElement("br"));
  }
  function draw(into, blocks, pos, mode) {
    var list = null;
    into.textContent = "";
    if (mode !== "block") {
      var all = [];
      blocks.forEach(function (b, i) { all = all.concat(i ? [{ k: "br" }] : [], b.atoms); });
      fill(into, all, pos);
      return;
    }
    (blocks.length ? blocks : [{ type: "p", atoms: [] }]).forEach(function (b) {
      var target = el(b.type === "h" ? "h3" : b.type === "li" ? "li" : "p");
      if (b.type === "li") { if (!list) { list = el("ul"); into.appendChild(list); } list.appendChild(target); }
      else { list = null; into.appendChild(target); }
      fill(target, b.atoms, pos);
    });
  }
  function setup(area) {
    if (area.richText) return;
    var mode = area.dataset.rich === "block" ? "block" : "inline", opts = { gaps: area.hasAttribute("data-rich-gaps") };
    var locked = area.readOnly || area.matches(":disabled"), fixedGaps = area.hasAttribute("data-rich-gaps-fixed");
    var wrap = el("div", "rt" + (area.dataset.richBar === "float" ? " rt-float" : "")), bar = el("div", "rt-bar"), ed = el("div", "rt-ed rt-" + mode);
    var label = area.closest("label"), hint = (label || area).nextElementSibling;
    if (!hint || !hint.classList.contains("format-hint")) hint = null;
    var source = false, composing = false, undo = [], redo = [], lastTyped = 0, valueAtFocus = area.value, buttons = [];

    function gapsIn(blocks) {
      return blocks.reduce(function (n, b) { return n + b.atoms.filter(function (a) { return a.k === "g"; }).length; }, 0);
    }

    // --- model from the page ---
    function read() {
      var sel = window.getSelection(), marks = [];
      if (sel.rangeCount && ed.contains(sel.anchorNode) && ed.contains(sel.focusNode)) {
        marks = [["cA", sel.anchorNode, sel.anchorOffset], ["cF", sel.focusNode, sel.focusOffset]];
      }
      var blocks = [], cur = null, type = "p", waiting = [], foreign = false, lineEnded = false;
      function need() {
        if (!cur) {
          cur = mode === "block" || !blocks.length ? { type: mode === "block" ? type : "p", atoms: [] } : blocks[0];
          if (blocks.indexOf(cur) < 0) blocks.push(cur);
          cur.atoms = cur.atoms.concat(waiting);
          waiting = [];
        }
        return cur;
      }
      function push(atom) {
        var block = need();
        if (lineEnded && core.hasContent(block.atoms) && atom.k !== "br") block.atoms.push({ k: "br" });
        lineEnded = false;
        block.atoms.push(atom);
      }
      function caretsAt(node, offset, f) {
        marks.forEach(function (mk) {
          if (mk[1] !== node || mk[2] !== offset) return;
          if (cur) cur.atoms.push({ k: mk[0], f: f }); else waiting.push({ k: mk[0], f: f });
        });
      }
      function text(s, f) {
        s.replace(/\u200b/g, "").split("\n").forEach(function (piece, i) {
          if (i) push({ k: "br" });
          if (piece) push({ k: "t", s: piece, f: f });
        });
      }
      function children(node, f) {
        for (var i = 0; i < node.childNodes.length; i++) { caretsAt(node, i, f); walk(node.childNodes[i], f); }
        caretsAt(node, node.childNodes.length, f);
      }
      function walk(node, f) {
        if (node.nodeType === 3) {
          var pos = 0, cuts = marks.filter(function (mk) { return mk[1] === node; }).sort(function (a, b) { return a[2] - b[2]; });
          cuts.concat([[null, node, node.data.length]]).forEach(function (mk) {
            text(node.data.slice(pos, mk[2]), f);
            pos = mk[2];
            if (mk[0]) need().atoms.push({ k: mk[0], f: f });
          });
          return;
        }
        if (node.nodeType !== 1) return;
        if (node.hasAttribute("data-var") || node.hasAttribute("data-gap")) {
          var alt = node.getAttribute("data-alt");
          push(node.hasAttribute("data-var") ? Object.assign({ k: "v", name: node.getAttribute("data-var"), f: f }, alt ? { alt: alt } : {})
            : { k: "g", n: +node.getAttribute("data-gap") || 0, f: f });
          marks.forEach(function (mk) { if (node.contains(mk[1])) need().atoms.push({ k: mk[0], f: f }); });
          return;
        }
        var tag = node.tagName;
        if (tag === "BR") { push({ k: "br" }); return; }
        if (BLOCK.test(tag)) {
          if (mode !== "block") { foreign = true; lineEnded = true; children(node, f); lineEnded = true; return; }
          if (tag !== "P" && tag !== "H3" && tag !== "UL" && tag !== "LI") foreign = true;
          if (tag === "UL" || tag === "OL") { cur = null; children(node, f); cur = null; return; }
          var count = blocks.length;
          cur = null;
          type = /^H\d$/.test(tag) ? "h" : tag === "LI" ? "li" : "p";
          children(node, f);
          if (blocks.length === count) need();  // a paragraph without text is still a paragraph
          cur = null;
          type = "p";
          return;
        }
        var format = FORMAT_OF[tag];
        if (!format && !(tag === "SPAN" && !node.attributes.length)) foreign = true;
        children(node, format ? core.setFormat(f, format, true) : f);
      }
      children(ed, "");
      if (waiting.length) {  // a selection that ends behind the last block
        cur = blocks[blocks.length - 1] || null;
        if (cur) cur.atoms = cur.atoms.concat(waiting); else need();
      }
      blocks.forEach(function (b) {  // the last <br> of a block only keeps its last line open
        for (var i = b.atoms.length - 1; i >= 0; i--) {
          if (b.atoms[i].k === "cA" || b.atoms[i].k === "cF") continue;
          if (b.atoms[i].k === "br") b.atoms.splice(i, 1);
          break;
        }
      });
      blocks.foreign = foreign;
      return blocks;
    }

    function render(blocks) {
      var pos = {}, sel = core.carets(blocks);
      pos.range = !!sel && !(sel.cA[0] === sel.cF[0] && Math.abs(sel.cA[1] - sel.cF[1]) === 1);
      draw(ed, blocks, pos, mode);
      ed.classList.toggle("is-empty", !blocks.some(function (b) { return b.atoms.some(function (a) { return a.k !== "cA" && a.k !== "cF"; }); }));
      if (pos.cA && pos.cF && document.activeElement === ed) {
        window.getSelection().setBaseAndExtent(pos.cA[0], pos.cA[1], pos.cF[0], pos.cF[1]);
        var at = pos.cF[0].nodeType === 1 ? pos.cF[0] : pos.cF[0].parentNode;
        if (at.scrollIntoView && at !== ed) at.scrollIntoView({ block: "nearest" });
      }
    }

    // --- model to the textarea ---
    function sync(text) {
      if (area.value === text) return;
      area.value = text;
      area.dispatchEvent(new Event("input", { bubbles: true }));  // live preview, "ungespeichert"
    }
    function remember(blocks, typing) {
      var now = Date.now(), state = JSON.stringify(blocks);
      if (typing && undo.length > 1 && now - lastTyped < 1000) undo[undo.length - 1] = state;  // one step per burst of typing
      else if (undo[undo.length - 1] !== state) undo.push(state);
      if (undo.length > 200) undo.shift();
      lastTyped = typing ? now : 0;
      redo = [];
    }
    // The model becomes the text of the textarea. Where the server reads that text differently (typed
    // marks such as **word** or "- " at the start of a line), the field shows what the server reads.
    function commit(blocks, redraw, typing) {
      var result = core.serialize(blocks, mode, opts);
      if (core.sig(core.parse(result.text, mode, opts), mode) !== core.sig(blocks, mode)) {
        blocks = core.withCarets(blocks, mode, opts, result.level);
        result = core.serialize(blocks, mode, opts);  // now without the typed marks: the shortest spelling
        redraw = true;
      }
      if (fixedGaps && gapsIn(blocks) !== gapCount) {  // the survey runs: no gap may come or go
        var before = JSON.parse(undo[undo.length - 1]);
        render(core.carets(before) ? before : core.caretsAtEnd(before));
        return;
      }
      if (redraw) render(blocks);
      else ed.classList.toggle("is-empty", !result.text);
      sync(result.text);
      remember(blocks, typing);
      showState();
    }
    function act(change) {  // a button or key: change the model, show it
      if (locked || source) return;
      ed.focus();
      var blocks = read();
      change(blocks);
      commit(blocks, true, false);
    }
    function back(forward) {  // undo, or redo: the fields keeps its own steps, as buttons redraw it
      if (forward ? !redo.length : undo.length < 2) return;
      if (forward) undo.push(redo.pop()); else redo.push(undo.pop());
      var blocks = JSON.parse(undo[undo.length - 1]);
      render(blocks);
      sync(core.serialize(blocks, mode, opts).text);
      showState();
    }

    // --- toolbar ---
    function showState() {
      if (locked) return;
      var sel = window.getSelection(), node = sel.rangeCount && ed.contains(sel.focusNode) ? sel.focusNode : null, on = {};
      for (; node && node !== ed; node = node.parentNode) {
        if (node.nodeType !== 1) continue;
        if (FORMAT_OF[node.tagName]) on[FORMAT_OF[node.tagName]] = true;
        if (node.tagName === "LI") on.li = true;
        if (node.tagName === "H3") on.h = true;
      }
      buttons.forEach(function (b) {
        b.node.setAttribute("aria-pressed", on[b.spec.format || b.spec.type] ? "true" : "false");
        b.node.disabled = source || (b.spec.gap && fixedGaps);
      });
    }
    function button(spec) {
      var node = el("button", "rt-btn" + (spec.format ? " rt-btn-" + spec.format : ""), spec.text);
      node.type = "button";
      node.title = spec.title;
      node.setAttribute("aria-label", spec.title);
      if (spec.format === "u") { node.textContent = ""; node.appendChild(el("u", "", spec.text)); }
      if (spec.low) node.appendChild(el("sub", "", spec.low));
      if (spec.high) node.appendChild(el("sup", "", spec.high));
      node.addEventListener("mousedown", function (e) { e.preventDefault(); });  // the selection stays in the text
      node.addEventListener("click", function () {
        if (spec.format) act(function (m) { core.toggleFormat(m, spec.format); });
        else if (spec.type) act(function (m) { core.toggleType(m, spec.type); });
        else if (spec.gap) act(function (m) { core.insert(m, [{ k: "g", n: GAP_SIZES[1] }]); });
      });
      bar.appendChild(node);
      buttons.push({ node: node, spec: spec });
    }
    // "Antwort einfügen": a list of what can stand in the text (answers to earlier questions, the values of a
    // loop, what the participation link brought), with a search field where the list is long. The choice goes
    // to where the cursor stood when the list was opened.
    var menu = null, pop = null;
    function closeMenu() { if (menu) { menu.remove(); menu = null; } }
    function insertButton(offered) {
      var answers = offered.some(function (g) { return g.kind === "answer"; });
      // the images of a blog entry (app/blog.py): {{bild<id>}}, kind "image"
      var images = offered.every(function (g) { return g.kind === "image"; });
      var node = el("button", "rt-btn rt-btn-insert", answers ? de_en("Antwort einfügen", "Insert answer")
        : images ? de_en("Bild einfügen", "Insert image") : de_en("Platzhalter einfügen", "Insert placeholder"));
      node.type = "button";
      node.title = answers ? de_en("Die Antwort auf eine frühere Frage in den Text setzen", "Put the answer to an earlier question into the text")
                 : images ? de_en("Ein hochgeladenes Bild an diese Stelle setzen", "Put an uploaded image at this place")
                           : de_en("Einen Wert in den Text setzen", "Put a value into the text");
      node.setAttribute("aria-haspopup", "true");
      node.addEventListener("mousedown", function (e) { e.preventDefault(); });
      node.addEventListener("click", function () {
        if (menu) { closeMenu(); return; }
        if (locked || source) return;
        closePop();
        var at = read(), total = offered.reduce(function (n, g) { return n + g.items.length; }, 0);
        menu = el("div", "rt-menu");
        menu.setAttribute("role", "menu");
        var search = el("input", "rt-menu-search"), list = el("div", "rt-menu-list");
        search.type = "search";
        search.placeholder = de_en("Suchen", "Search");
        search.setAttribute("aria-label", de_en("In der Liste suchen", "Search the list"));
        if (total > 8) menu.appendChild(search);
        function choose(name) {
          closeMenu();
          ed.focus();
          core.insert(at, [{ k: "v", name: name }]);
          commit(at, true, false);
        }
        function show() {
          var q = search.value.trim().toLowerCase();
          list.textContent = "";
          offered.forEach(function (g) {
            var hits = g.items.filter(function (i) { return !q || (i.text + " " + i.name).toLowerCase().indexOf(q) >= 0; });
            if (!hits.length) return;
            list.appendChild(el("div", "rt-menu-title", g.title));
            hits.forEach(function (i) {
              var row = el("button", "rt-menu-item");
              row.type = "button";
              row.setAttribute("role", "menuitem");
              row.appendChild(el("span", "rt-menu-text", short(i.text || i.name, 90)));
              if (i.text && i.text !== i.name) row.appendChild(el("span", "rt-menu-name", i.name));
              row.addEventListener("click", function () { choose(i.name); });
              list.appendChild(row);
            });
          });
          if (!list.firstChild) list.appendChild(el("div", "rt-menu-none", de_en("Nichts gefunden", "Nothing found")));
        }
        ["input", "change"].forEach(function (type) { search.addEventListener(type, function (e) { e.stopPropagation(); if (type === "input") show(); }); });
        menu.addEventListener("keydown", function (e) {
          if (e.key === "Escape") { e.preventDefault(); closeMenu(); ed.focus(); }
          else if (e.key === "Enter" && e.target === search) { e.preventDefault(); var first = list.querySelector(".rt-menu-item"); if (first) first.click(); }
        });
        show();
        menu.appendChild(list);
        wrap.appendChild(menu);
        menu.style.top = (bar.offsetTop + bar.offsetHeight + 2) + "px";
        menu.style.left = Math.max(0, Math.min(node.offsetLeft, wrap.clientWidth - menu.offsetWidth)) + "px";
        (total > 8 ? search : list.querySelector(".rt-menu-item")).focus();
      });
      bar.appendChild(node);
      document.addEventListener("mousedown", function (e) { if (menu && !menu.contains(e.target) && e.target !== node) closeMenu(); });
    }
    // A click on a placeholder: the text that stands there when the value is empty (the question was not
    // answered). It is written into the text as {{name|text}}.
    // emptied before it goes: its focusout would close it once more, in the middle of the removal, and the
    // field would not get the focus back
    function closePop() { if (pop) { var gone = pop; pop = null; gone.remove(); } }
    // An image of a blog entry instead (app/blog.py): where it stands, across the column or at the left or right
    // edge with the text flowing round it, written as {{bild12|links}} or {{bild12|rechts}}
    function openSides(chip) {
      closePop();
      var index = Array.prototype.indexOf.call(ed.querySelectorAll("[data-var]"), chip);
      pop = el("div", "rt-pop");
      pop.setAttribute("role", "group");
      pop.setAttribute("aria-label", de_en("Bild steht", "Image position"));
      pop.appendChild(el("span", "rt-pop-title", de_en("Bild steht", "Image position")));
      var now = chip.getAttribute("data-alt") || "", first = null;
      function leave() { closePop(); ed.focus(); }
      IMAGE_SIDES.forEach(function (side) {
        var b = el("button", "rt-btn", side.label());
        b.type = "button";
        b.setAttribute("aria-pressed", side.key === now ? "true" : "false");
        b.addEventListener("click", function () {
          var node = ed.querySelectorAll("[data-var]")[index];
          if (node) { dress(node, node.getAttribute("data-var"), side.key); commit(read(), false, true); }
          leave();
        });
        b.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); leave(); } });
        if (side.key === now || !first) first = b;
        pop.appendChild(b);
      });
      pop.addEventListener("focusout", function (e) { if (pop && !pop.contains(e.relatedTarget)) closePop(); });
      field.appendChild(pop);
      pop.style.top = (chip.offsetTop + chip.offsetHeight + 6) + "px";
      pop.style.left = Math.max(0, Math.min(chip.offsetLeft, field.clientWidth - pop.offsetWidth)) + "px";
      first.focus();
    }
    function openPop(chip) {
      closePop();
      var index = Array.prototype.indexOf.call(ed.querySelectorAll("[data-var]"), chip);
      function target() { return ed.querySelectorAll("[data-var]")[index]; }
      pop = el("div", "rt-pop");
      var input = el("input"), done = el("button", "rt-btn", de_en("Fertig", "Done"));
      input.type = "text";
      input.maxLength = 80;
      input.value = chip.getAttribute("data-alt") || "";
      input.placeholder = de_en("z. B. Ihre Angabe", "e.g. your answer");
      input.setAttribute("aria-label", de_en("Ersatztext, falls keine Antwort vorliegt", "Fallback text if there is no answer"));
      done.type = "button";
      pop.appendChild(el("span", "rt-pop-title", de_en("Ersatztext, falls keine Antwort vorliegt", "Fallback text if there is no answer")));
      pop.appendChild(input);
      pop.appendChild(done);
      ["input", "change"].forEach(function (type) {
        input.addEventListener(type, function (e) {
          e.stopPropagation();
          var node = target();
          if (!node || type !== "input") return;
          dress(node, node.getAttribute("data-var"), core.altText(input.value));
          commit(read(), false, true);
        });
      });
      function leave(focus) { closePop(); if (focus) ed.focus(); }
      input.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); leave(true); } });
      done.addEventListener("click", function () { leave(true); });
      pop.addEventListener("focusout", function (e) { if (pop && !pop.contains(e.relatedTarget)) closePop(); });
      field.appendChild(pop);
      pop.style.top = (chip.offsetTop + chip.offsetHeight + 6) + "px";
      pop.style.left = Math.max(0, Math.min(chip.offsetLeft, field.clientWidth - pop.offsetWidth)) + "px";
      input.focus();
      input.select();
    }
    function setSource(on) {
      closeMenu();
      closePop();
      source = on;
      area.hidden = !on;
      field.hidden = on;
      if (hint) hint.hidden = !on;
      sourceButton.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) area.focus();
      else { render(core.parse(area.value, mode, opts)); undo = [JSON.stringify(read())]; redo = []; ed.focus(); }
      showState();
    }

    // --- build ---
    ed.setAttribute("role", "textbox");
    ed.setAttribute("aria-multiline", "true");
    var title = label && (label.querySelector(".label-title") || label), name = area.getAttribute("aria-label") || "";
    if (!name && title) title.childNodes.forEach(function (n) { if (n.nodeType === 3) name += n.data; });  // without the hint
    if (name) ed.setAttribute("aria-label", name.replace(/\s+/g, " ").trim());
    var field = el("div", "rt-field");
    field.appendChild(ed);
    if (area.placeholder) {  // the text that applies while the field is empty, formatted like any other
      var shown = el("div", "rt-placeholder rt-" + mode);
      shown.setAttribute("aria-hidden", "true");
      draw(shown, core.parse(area.placeholder, mode, opts), {}, mode);
      field.appendChild(shown);
      ed.setAttribute("aria-placeholder", area.placeholder);
    }
    if (area.lang) ed.lang = area.lang;
    // as high as the textarea was; where the buttons stand over the field all the time, at least two lines
    ed.style.minHeight = (Math.max(+area.rows || 2, area.dataset.richBar === "float" ? 1 : 2) * 1.5 + 1.2) + "em";
    ed.spellcheck = true;
    render(core.parse(area.value, mode, opts));
    area.classList.add("rt-area");
    area.hidden = true;
    if (hint) hint.hidden = true;
    area.before(wrap);
    if (locked) {
      ed.classList.add("is-locked");
      wrap.appendChild(area);
      wrap.appendChild(field);
      area.richText = { active: function () { return false; }, refresh: function () { render(core.parse(area.value, mode, opts)); } };
      return;
    }
    ed.contentEditable = "true";
    if (area.hasAttribute("data-var-target")) ed.setAttribute("data-var-target", "");
    BUTTONS.forEach(function (spec) { if ((!spec.block || mode === "block") && (!spec.gaps || opts.gaps)) button(spec); });
    var offered = (vars() || []).filter(function (g) { return g.items.length; });
    if (offered.length) insertButton(offered);
    var sourceButton = el("button", "rt-btn rt-btn-source", de_en("Quelltext", "Source"));
    sourceButton.type = "button";
    sourceButton.title = de_en("Den Text mit seinen Formatierungszeichen zeigen", "Show the text with its formatting marks");
    sourceButton.setAttribute("aria-pressed", "false");
    sourceButton.addEventListener("click", function () { setSource(!source); });
    bar.appendChild(sourceButton);
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", de_en("Formatierung", "Formatting"));
    wrap.appendChild(bar);
    wrap.appendChild(area);  // under the buttons, where "Quelltext" shows it in place of the field
    wrap.appendChild(field);
    undo = [JSON.stringify(read())];
    var gapCount = gapsIn(read());
    if (!hint) {  // what "Quelltext" explains, where the page has no such line of its own
      hint = el("p", "muted small format-hint", (mode === "block" ? de_en("Leerzeile = neuer Absatz, „- “ = Aufzählung, eine ganz fette Zeile = Zwischenüberschrift, ",
                                                                          "blank line = new paragraph, “- ” = list item, a line bold as a whole = subheading, ") : "")
        + de_en("**fett**, *kursiv*, `Code`, H~2~O tief, x^2^ hoch; auch <u>, <small>, <sub>, <sup>, <br>",
                "**bold**, *italic*, `code`, H~2~O low, x^2^ high; also <u>, <small>, <sub>, <sup>, <br>"));
      hint.hidden = true;
      wrap.appendChild(hint);
    }
    if (label) {
      // a click on the field must not go to the textarea the label belongs to; one on the title goes here
      label.addEventListener("click", function (e) {
        if (source && !wrap.contains(e.target)) return;
        if (e.target.closest("button")) return;
        e.preventDefault();
        if (!wrap.contains(e.target)) ed.focus();
      });
    }

    // --- typing ---
    ed.addEventListener("input", function (e) {
      e.stopPropagation();  // the form hears of it from the textarea (sync)
      if (composing || e.isComposing) return;
      var blocks = read();
      commit(blocks, blocks.foreign, true);
    });
    ed.addEventListener("compositionstart", function () { composing = true; });
    ed.addEventListener("compositionend", function () { composing = false; var blocks = read(); commit(blocks, blocks.foreign, true); });
    ed.addEventListener("keydown", function (e) {
      if (e.isComposing) return;
      var mod = (e.ctrlKey || e.metaKey) && !e.altKey, k = e.key.toLowerCase();
      if (mod && !e.shiftKey && (k === "b" || k === "i" || k === "u")) {
        e.preventDefault();
        act(function (m) { core.toggleFormat(m, k); });
      } else if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); back(false); }
      else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); back(true); }
    });
    ed.addEventListener("beforeinput", function (e) {
      if (e.inputType === "historyUndo") { e.preventDefault(); back(false); }
      else if (e.inputType === "historyRedo") { e.preventDefault(); back(true); }
      else if (e.inputType === "insertParagraph" || e.inputType === "insertLineBreak") {
        // Enter: in a question text a line break; in a long text a new paragraph, with Shift a line break
        e.preventDefault();
        var paragraph = mode === "block" && e.inputType === "insertParagraph";
        act(function (m) { if (paragraph) core.splitBlock(m); else core.insert(m, [{ k: "br" }]); });
      } else if (/^format/.test(e.inputType)) {  // the browser's own menu: the same formats, through the model
        e.preventDefault();
        var f = { formatBold: "b", formatItalic: "i", formatUnderline: "u", formatSubscript: "sub", formatSuperscript: "sup" }[e.inputType];
        if (f) act(function (m) { core.toggleFormat(m, f); });
      }
    });
    // from Word or a web page only the text arrives
    ed.addEventListener("paste", function (e) {
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData("text/plain");
      if (text) act(function (m) { core.insertText(m, text, mode); });
    });
    ed.addEventListener("dragstart", function (e) { e.preventDefault(); });  // no moving of text by dragging: it would arrive as a copy
    ed.addEventListener("drop", function (e) {
      e.preventDefault();
      var text = e.dataTransfer ? e.dataTransfer.getData("text/plain") : "", range = null;
      if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(e.clientX, e.clientY);
      else if (document.caretPositionFromPoint) {
        var p = document.caretPositionFromPoint(e.clientX, e.clientY);
        if (p) { range = document.createRange(); range.setStart(p.offsetNode, p.offset); }
      }
      ed.focus();
      if (range && ed.contains(range.startContainer)) window.getSelection().setBaseAndExtent(range.startContainer, range.startOffset, range.startContainer, range.startOffset);
      if (text) act(function (m) { core.insertText(m, text, mode); });
    });
    ed.addEventListener("click", function (e) {
      var chip = e.target.closest && e.target.closest("[data-var]");
      // an image has no text for an empty value: it asks where it stands
      if (chip && !source) { vars(); if ((known[chip.getAttribute("data-var")] || {}).kind === "image") openSides(chip); else openPop(chip); return; }
      var gap = e.target.closest && e.target.closest("[data-gap]");
      if (!gap) return;
      var size = GAP_SIZES[(GAP_SIZES.indexOf(GAP_SIZES.filter(function (n) { return gapSize(n) === gapSize(+gap.getAttribute("data-gap")); })[0]) + 1) % GAP_SIZES.length];
      gap.setAttribute("data-gap", size);
      gap.className = "rt-gap is-" + gapSize(size);
      commit(read(), false, false);
    });
    ed.addEventListener("focus", function () { valueAtFocus = area.value; });
    ed.addEventListener("blur", function () {
      // what the field shows is what the text says, also in spaces and line breaks
      var shown = read(), stored = core.parse(area.value, mode, opts);
      if (shown.foreign || !core.same(shown, stored, mode)) render(stored);
      if (area.value !== valueAtFocus) area.dispatchEvent(new Event("change", { bubbles: true }));
    });

    editors.push({ ed: ed, showState: showState });
    area.richText = {
      active: function () { return !source; },
      insert: function (text) { act(function (m) { core.insertText(m, text, mode); }); },
      refresh: function () { if (!source) { render(core.parse(area.value, mode, opts)); undo = [JSON.stringify(read())]; redo = []; } },
    };
    showState();
  }

  // --- plain one-line fields: statements, answer options, scale labels ---
  // They take no formatting, but a placeholder in them shows as the same chip with a name as in the question
  // text ("Antwort: Welche Marke?"), with its text for an empty value. app.js turns a field into such a line
  // once it holds a placeholder; the input stays the form field (hidden) and gets every change, as typed.
  var PLACE_IN = /\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:\|[^{}|]*)?\}\}/;
  var PLACE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^{}|]*))?\}\}/g;
  function setupLine(input) {
    if (input.richText || input.readOnly || input.disabled || input.required) return;
    var ed = el("div", "rt-line"), wrap = el("div", "rt-line-wrap"), pop = null, valueAtFocus = input.value, caret = null;
    ed.contentEditable = "true";
    ed.spellcheck = true;
    ed.setAttribute("role", "textbox");
    ed.setAttribute("data-var-target", "");
    // read out as the input was: its own name, else the head of its column
    var cell = input.closest("td"), head = cell && cell.closest("table") && cell.closest("table").querySelector("thead tr");
    var name = input.getAttribute("aria-label") || (head && head.children[cell.cellIndex] ? head.children[cell.cellIndex].textContent.trim() : "");
    if (name) ed.setAttribute("aria-label", name);
    if (input.lang) ed.lang = input.lang;
    ed.chipInput = input;

    // the text of the field and where the cursor stands in it (as a position in that text)
    function serialize() {
      var sel = window.getSelection(), focus = sel.rangeCount && ed.contains(sel.focusNode) ? [sel.focusNode, sel.focusOffset] : null;
      var text = "", at = null;
      function walk(node) {
        if (node.nodeType === 3) {
          var data = node.data;
          if (focus && focus[0] === node) at = text.length + data.slice(0, focus[1]).replace(/​/g, "").replace(/\n/g, " ").length;
          text += data.replace(/​/g, "").replace(/\n/g, " ");
          return;
        }
        if (node.nodeType !== 1) return;
        if (node.hasAttribute("data-var")) {
          var alt = node.getAttribute("data-alt");
          text += "{{" + node.getAttribute("data-var") + (alt ? "|" + alt : "") + "}}";
          return;
        }
        for (var i = 0; i < node.childNodes.length; i++) {
          if (focus && focus[0] === node && focus[1] === i) at = text.length;
          walk(node.childNodes[i]);
        }
        if (focus && focus[0] === node && focus[1] === node.childNodes.length) at = text.length;
      }
      walk(ed);
      return { text: text, at: at };
    }
    // text and chips, built from nodes; the cursor at position `at` of the text
    function render(text, at) {
      ed.textContent = "";
      var pos = null, done = 0, m;
      function piece(s, start) {
        var node = document.createTextNode(s || ZW);
        ed.appendChild(node);
        if (at != null && pos === null && at >= start && at <= start + s.length) pos = [node, s ? at - start : 1];
      }
      PLACE.lastIndex = 0;
      while ((m = PLACE.exec(text))) {
        piece(text.slice(done, m.index), done);
        var chip = el("span", "rt-var placeholder");
        chip.contentEditable = "false";
        chip.setAttribute("data-var", m[1]);
        dress(chip, m[1], core.altText(m[2] || ""));
        ed.appendChild(chip);
        done = m.index + m[0].length;
        if (at != null && pos === null && at > m.index && at < done) at = done;  // never inside a placeholder
      }
      piece(text.slice(done), done);
      if (pos && document.activeElement === ed) window.getSelection().collapse(pos[0], pos[1]);
    }
    function sync(text) {
      if (input.value === text) return;
      input.value = text;
      input.dispatchEvent(new Event("input", { bubbles: true }));  // autosave, live preview, rows that are filled
    }
    function remember() { var s = serialize(); if (s.at != null) caret = s.at; }
    function put(text, at) {  // insert a text at a position, placeholders become chips
      var now = serialize().text, where = at == null ? now.length : Math.min(at, now.length);
      text = String(text).replace(/\s*[\r\n]+\s*/g, " ");
      var next = now.slice(0, where) + text + now.slice(where);
      ed.focus();
      render(next, where + text.length);
      caret = where + text.length;
      sync(next);
    }

    ed.addEventListener("input", function (e) {
      e.stopPropagation();  // the form hears of it from the input (sync)
      var s = serialize(), foreign = Array.prototype.some.call(ed.childNodes, function (n) {
        return n.nodeType === 1 && !n.hasAttribute("data-var");
      });
      var typed = Array.prototype.some.call(ed.childNodes, function (n) { return n.nodeType === 3 && PLACE_IN.test(n.data); });
      if (foreign || typed) render(s.text, s.at);  // a placeholder typed by hand, or what the browser put in
      caret = s.at;
      sync(s.text);
    });
    ed.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); return; }  // one line, as the input was
      if ((e.ctrlKey || e.metaKey) && !e.altKey && "biu".indexOf(e.key.toLowerCase()) >= 0) e.preventDefault();  // no formats here
    });
    ed.addEventListener("beforeinput", function (e) { if (/^format/.test(e.inputType)) e.preventDefault(); });
    ed.addEventListener("paste", function (e) {
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData("text/plain");
      if (text) put(text, serialize().at);
    });
    ed.addEventListener("dragstart", function (e) { e.preventDefault(); });  // moving text by dragging would copy it
    ed.addEventListener("drop", function (e) {  // a chip of the bar above the form, or a text
      e.preventDefault();
      var text = e.dataTransfer ? e.dataTransfer.getData("text/plain") : "", range = null;
      if (document.caretRangeFromPoint) range = document.caretRangeFromPoint(e.clientX, e.clientY);
      else if (document.caretPositionFromPoint) {
        var p = document.caretPositionFromPoint(e.clientX, e.clientY);
        if (p) { range = document.createRange(); range.setStart(p.offsetNode, p.offset); }
      }
      ed.focus();
      if (range && ed.contains(range.startContainer)) window.getSelection().collapse(range.startContainer, range.startOffset);
      if (text) put(text, serialize().at);
    });
    ["keyup", "mouseup", "focus"].forEach(function (type) { ed.addEventListener(type, remember); });
    ed.addEventListener("focus", function () { valueAtFocus = input.value; });
    ed.addEventListener("blur", function () {
      if (input.value !== valueAtFocus) input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // a click on a chip: the text that stands there when the value is empty, as in the question text
    // emptied before it goes: its focusout would close it once more, in the middle of the removal, and the
    // field would not get the focus back
    function closePop() { if (pop) { var gone = pop; pop = null; gone.remove(); } }
    ed.addEventListener("click", function (e) {
      var chip = e.target.closest && e.target.closest("[data-var]");
      if (!chip) return;
      closePop();
      var index = Array.prototype.indexOf.call(ed.querySelectorAll("[data-var]"), chip);
      pop = el("div", "rt-pop");
      var field = el("input"), done = el("button", "rt-btn", de_en("Fertig", "Done"));
      field.type = "text";
      field.maxLength = 80;
      field.value = chip.getAttribute("data-alt") || "";
      field.placeholder = de_en("z. B. Ihre Angabe", "e.g. your answer");
      field.setAttribute("aria-label", de_en("Ersatztext, falls keine Antwort vorliegt", "Fallback text if there is no answer"));
      done.type = "button";
      pop.appendChild(el("span", "rt-pop-title", de_en("Ersatztext, falls keine Antwort vorliegt", "Fallback text if there is no answer")));
      pop.appendChild(field);
      pop.appendChild(done);
      ["input", "change"].forEach(function (type) {
        field.addEventListener(type, function (ev) {
          ev.stopPropagation();
          var node = ed.querySelectorAll("[data-var]")[index];
          if (!node || type !== "input") return;
          dress(node, node.getAttribute("data-var"), core.altText(field.value));
          sync(serialize().text);
        });
      });
      function leave() {  // back in the field, behind the chip
        closePop();
        ed.focus();
        var node = ed.querySelectorAll("[data-var]")[index], after = node && node.nextSibling;
        if (after && after.nodeType === 3) window.getSelection().collapse(after, after.data.charAt(0) === ZW ? 1 : 0);
        remember();
      }
      field.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === "Escape") { ev.preventDefault(); leave(); } });
      done.addEventListener("click", leave);
      pop.addEventListener("focusout", function (ev) { if (pop && !pop.contains(ev.relatedTarget)) closePop(); });
      wrap.appendChild(pop);
      pop.style.top = (chip.offsetTop + chip.offsetHeight + 6) + "px";
      pop.style.left = Math.max(0, Math.min(chip.offsetLeft, wrap.clientWidth - pop.offsetWidth)) + "px";
      field.focus();
      field.select();
    });

    var typing = document.activeElement === input;  // typed into: the cursor goes on in the line, at its end
    input.hidden = true;
    input.before(wrap);
    wrap.appendChild(ed);
    wrap.appendChild(input);
    if (typing) ed.focus();
    render(input.value, typing ? input.value.length : null);
    if (typing) { valueAtFocus = null; caret = input.value.length; }  // changed in this visit: "change" when it is left
    input.richText = {
      active: function () { return true; },
      insert: function (text) { put(text, caret); },
      refresh: function () { render(input.value, null); },
      line: true,
    };
  }
  core.attachLine = setupLine;
  core.hasPlaceholder = function (text) { return PLACE_IN.test(text || ""); };

  document.addEventListener("selectionchange", function () {
    var node = window.getSelection().focusNode;
    editors.forEach(function (e) { if (node && e.ed.contains(node)) e.showState(); });
  });
  core.attach = setup;  // for a textarea that comes into the page later
  // A text that is only shown (data-rich-show="inline|block", e.g. the source next to a translation)
  function show(node) {
    var mode = node.getAttribute("data-rich-show") === "block" ? "block" : "inline";
    var text = node.textContent;
    node.classList.add("rt-shown");
    draw(node, core.parse(text, mode, { gaps: node.hasAttribute("data-rich-gaps") }), {}, mode);
  }
  function start() {
    document.querySelectorAll("textarea[data-rich]").forEach(setup);
    document.querySelectorAll("[data-rich-show]").forEach(show);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
