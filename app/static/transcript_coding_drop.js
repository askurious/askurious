// What a drop of one category onto another may do in the category system (list and map, app/static/transcript_coding.js):
// "unter B hängen" and "mit B zusammenführen", each with a short reason when the rules do not allow it. The same rules
// as the server (app/transcript_coding.py move, app/transcript_coding_review.py may_merge), which checks again.
// data: {cats: {id: {id, kind, level, anc, parent, move}}, team, fixed, max, may_main} (transcript_coding.drop_data).
(function (root) {
  function t(de, en, values) {
    var text = typeof root.de_en === "function" ? root.de_en(de, en) : de;
    Object.keys(values || {}).forEach(function (k) { text = text.split("{" + k + "}").join(values[k]); });
    return text;
  }
  function below(a, b) { return b.id === a.id || (b.anc || []).indexOf(a.id) >= 0; }  // b is a or stands below a
  function height(data, a) {
    var h = 1;
    Object.keys(data.cats).forEach(function (k) {
      var c = data.cats[k];
      if ((c.anc || []).indexOf(a.id) >= 0) h = Math.max(h, c.level - a.level + 1);
    });
    return h;
  }
  function hang(data, a, b) {
    if (!a.move) return { ok: false, why: t("Hauptkategorien hängen nur die Forschenden um.", "Only the researchers move main categories.") };
    if (b.kind === "candidate") return { ok: false, why: t("Unter einen Kandidaten kommt nichts.", "Nothing goes under a candidate.") };
    if (below(a, b)) return { ok: false, why: t("Nicht unter sich selbst oder etwas darunter.", "Not under itself or something below it.") };
    if (b.id === a.parent) return { ok: false, why: t("Steht schon dort.", "Already stands there.") };
    if (b.level + height(data, a) > data.max) {
      return { ok: false, why: t("Dann stünde etwas tiefer als {n} Ebenen.", "Something would then stand deeper than {n} levels.", { n: data.max }) };
    }
    return { ok: true, why: "" };
  }
  function merge(data, a, b) {
    if (a.id === b.id) return { ok: false, why: "" };
    if (below(a, b)) return { ok: false, why: t("Nicht mit etwas darunter.", "Not with something below it.") };
    var subs = a.kind === "sub" && b.kind === "sub";
    var mains = b.kind === "main" && (a.kind === "main" || a.kind === "candidate");
    if (!subs && !mains) return { ok: false, why: t("Nur Kategorien derselben Ebene (Unter- mit Unterkategorie, Haupt- mit Hauptkategorie).", "Only categories of the same level (sub with sub, main with main).") };
    if (mains && !data.team) return { ok: false, why: t("Hauptkategorien führen nur die Forschenden zusammen.", "Only the researchers merge main categories.") };
    if (b.level + height(data, a) - 1 > data.max) {
      return { ok: false, why: t("Dann stünde etwas tiefer als {n} Ebenen.", "Something would then stand deeper than {n} levels.", { n: data.max }) };
    }
    return { ok: true, why: "" };
  }
  function siblings(a, b) {  // reordering: the same one above, the same kind of level
    return a.id !== b.id && a.parent === b.parent && (a.kind === "sub") === (b.kind === "sub");
  }
  var api = { hang: hang, merge: merge, siblings: siblings, height: height };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.tcDrop = api;
})(typeof window !== "undefined" ? window : this);
