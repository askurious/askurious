"""The results of a survey as tables for programs: one ZIP of CSV files in long format beside the PDF report
(app/results_pdf.py), from the button "Tabellen (ZIP)" on the export page (Ralf, 2026-11-26: "wie finden wir einen
Weg, alles aus dem Ergebnisbericht zu exportieren?").

The data basis is that of the report (results_pdf.collect: the saved data pool, marked participations left out,
unweighted, the results the results page computed and kept); nothing is estimated anew. Two kinds of files:

- The core files are written from the kept results themselves: unrounded, a decimal point whatever the language, fixed
  file and column names (German, as the columns of the data set), codes beside their labels. FILES lists them.
- The folder "wie_angezeigt" holds every table of the results page as it stands there (results_pdf.page_tree, as the
  report takes them over): rounded and in the language of the person downloading, but complete, also for what has no
  core file yet (hypotheses, structural equation model, panel models, market simulator, rarer kinds of question).

Aggregates only: no row per person, no free texts (the values per person of HB and latent class models are columns of
the data set, app/export.py model_columns). README.txt names the data basis and every file, tabellen.csv lists the
files with their rows."""
import csv
import io
import re
import zipfile

from app.items.base import plain

NAME = "ergebnistabellen.zip"
SHOWN = "wie_angezeigt"

# file -> (columns, German description, English description); the order is the order in README and tabellen.csv
FILES = {
    "haeufigkeiten.csv": (
        ("frage", "variable", "durchgang", "label", "art", "code", "antwort", "n", "anteil", "ci_low", "ci_high",
         "n_gueltig", "fehlend"),
        "Je Variable und Antwort: Anzahl, Anteil an den gültigen Antworten (0 bis 1) und 95-%-Intervall nach Wilson. "
        "art: nominal, ordinal oder mehrfach (Mehrfachauswahl: Anteil der Personen, die die Option sahen). fehlend = 1: "
        "als fehlend gewerteter Code, ohne Anteil.",
        "Per variable and answer: count, share of the valid answers (0 to 1) and 95% Wilson interval. art: nominal, "
        "ordinal or mehrfach (multiple choice: share of the people who saw the option). fehlend = 1: a code counted as "
        "missing, without a share."),
    "kennwerte.csv": (
        ("frage", "variable", "label", "einheit", "n", "m", "sd", "median", "min", "max", "ci_low", "ci_high", "gini"),
        "Je metrischer Variable: n, Mittelwert, Standardabweichung, Median, Minimum, Maximum, 95-%-Intervall des "
        "Mittelwerts, Gini-Koeffizient (nur bei echtem Nullpunkt).",
        "Per metric variable: n, mean, standard deviation, median, minimum, maximum, 95% interval of the mean, Gini "
        "coefficient (only with a true zero)."),
    "skalen.csv": (
        ("frage", "skala", "items", "n", "alpha", "alpha_ci_low", "alpha_ci_high", "omega", "omega_ci_low",
         "omega_ci_high", "spearman_brown", "m", "sd", "m_ci_low", "m_ci_high"),
        "Je Skala und Subskala: Zahl der Aussagen, n, Cronbachs Alpha (Intervall nach Feldt), McDonalds Omega "
        "(Bootstrap-Intervall, falls berechnet), Spearman-Brown bei zwei Aussagen, M und SD des Skalenmittelwerts.",
        "Per scale and subscale: number of items, n, Cronbach's alpha (Feldt interval), McDonald's omega (bootstrap "
        "interval where computed), Spearman-Brown for two items, M and SD of the scale mean."),
    "skalen_items.csv": (
        ("frage", "skala", "variable", "text", "umgepolt", "n", "m", "sd", "trennschaerfe", "alpha_ohne", "ladung"),
        "Je Aussage einer Skala: M, SD, korrigierte Trennschärfe, Alpha ohne die Aussage, standardisierte Ladung.",
        "Per item of a scale: M, SD, corrected item-total correlation, alpha without the item, standardised loading."),
    "wahlmodell_kennwerte.csv": (
        ("frage", "modell", "kennwert", "wert"),
        "Je Wahlexperiment und Modell (logit, hb, wechselwirkungen) die Kennwerte des Modells: Personen, Entscheidungen, "
        "Pseudo-R², bei HB Ketten, Ziehungen, R-hat, ESS, Geweke, RLH.",
        "Per choice experiment and model (logit, hb, wechselwirkungen) the model's figures: people, decisions, pseudo "
        "R², for HB chains, draws, R-hat, ESS, Geweke, RLH."),
    "wahlmodell_logit.csv": (
        ("frage", "art", "merkmal_code", "merkmal", "stufe_nr", "stufe", "beta", "se", "wichtigkeit", "zahlungsbereitschaft",
         "zb_low", "zb_high", "begrenzt"),
        "Aggregiertes Logit-Modell (CBC, ACBC): art = teilnutzen (effektkodiert je Stufe, mit Standardfehler und, wo "
        "bestimmbar, Zahlungsbereitschaft mit 95-%-Intervall nach Fieller), wichtigkeit (je Merkmal, 0 bis 1), preis "
        "(Koeffizient je Währungseinheit), konstante.",
        "Aggregate logit model (CBC, ACBC): art = teilnutzen (effects-coded per level, with standard error and, where "
        "determinable, willingness to pay with its 95% Fieller interval), wichtigkeit (per attribute, 0 to 1), preis "
        "(coefficient per currency unit), konstante."),
    "wahlmodell_hb.csv": (
        ("frage", "skala", "art", "merkmal_code", "merkmal", "stufe_nr", "stufe", "mittel", "ci_low", "ci_high", "sd", "q1",
         "median", "q3", "bevorzugt", "begrenzt"),
        "Hierarchical Bayes (CBC, ACBC), Mittel über die Personen: art = teilnutzen (je Stufe, nullzentriert je Merkmal), "
        "wichtigkeit (je Merkmal, 0 bis 1), preis (Koeffizient je Währungseinheit), konstante, wechselwirkung (für alle "
        "gleich). ci: 95-%-Posterior-Intervall des Mittels; sd, q1, median, q3 über die Personen. skala = normiert: je "
        "Person umskaliert, wie die Ergebnisseite es zeigt.",
        "Hierarchical Bayes (CBC, ACBC), means over the people: art = teilnutzen (per level, zero-centred per "
        "attribute), wichtigkeit (per attribute, 0 to 1), preis (coefficient per currency unit), konstante, "
        "wechselwirkung (the same for everyone). ci: 95% posterior interval of the mean; sd, q1, median, q3 over the "
        "people. skala = normiert: rescaled per person as the results page shows it."),
    "wechselwirkungen.csv": (
        ("frage", "merkmalspaar", "p_einfach", "p_hb", "p_holm", "zuwachs_r2", "delta_ll", "status", "ergebnis"),
        "Suche nach Wechselwirkungen zwischen Merkmalen: je Paar die p-Werte (einfach, nach HB, Holm), der Zuwachs von "
        "McFaddens R² in Prozentpunkten, die Verbesserung der Log-Likelihood zurückgehaltener Wahlen und das Ergebnis.",
        "Search for interactions between attributes: per pair the p values (simple, after HB, Holm), the gain in "
        "McFadden's R² in percentage points, the improvement of the log-likelihood of held-out choices and the result."),
    "segmente_guete.csv": (
        ("frage", "k", "gezeigt", "vorgeschlagen", "loglik", "parameter", "bic", "aic3", "caic", "entropie",
         "kleinstes_segment", "laeufe", "gefunden", "konvergiert"),
        "Latent Class: je geschätzter Zahl von Segmenten (k) die Gütemaße. gezeigt = 1: die Lösung der Ergebnisseite, "
        "auf die sich die übrigen Segment-Dateien beziehen.",
        "Latent class: per estimated number of segments (k) the fit criteria. gezeigt = 1: the solution of the "
        "results page, which the other segment files describe."),
    "segmente.csv": (
        ("frage", "segment", "name", "anteil", "ci_low", "ci_high", "personen", "sicher", "rlh"),
        "Je Segment der gezeigten Lösung: Anteil (0 bis 1) mit Intervall, Personen mit diesem wahrscheinlichsten Segment, "
        "davon klar zugeordnet, RLH.",
        "Per segment of the shown solution: share (0 to 1) with interval, people with this most likely segment, those "
        "clearly assigned, RLH."),
    "segmente_parameter.csv": (
        ("frage", "segment", "name", "art", "merkmal_code", "merkmal", "stufe_nr", "stufe", "wert", "se", "score",
         "zahlungsbereitschaft", "zb_low", "zb_high", "begrenzt"),
        "Je Segment die Parameter: art = teilnutzen (je Stufe), wichtigkeit (je Merkmal, 0 bis 1), preis, konstante, "
        "nutzen (MaxDiff je Aussage, mit Wahrscheinlichkeits-Score), hb_mittel (Mittel der individuellen HB-Werte je "
        "Segment).",
        "Per segment the parameters: art = teilnutzen (per level), wichtigkeit (per attribute, 0 to 1), preis, "
        "konstante, nutzen (MaxDiff per statement, with probability score), hb_mittel (mean of the individual HB values "
        "per segment)."),
    "segmente_kovariaten.csv": (
        ("frage", "kovariate", "segment", "referenz", "koeffizient", "se", "odds_ratio", "p"),
        "Kovariaten der Segmentanteile: Logit-Koeffizient je Segment gegen das Referenzsegment, Standardfehler, Odds "
        "Ratio, p (Wald).",
        "Covariates of the segment shares: logit coefficient per segment against the reference segment, standard "
        "error, odds ratio, p (Wald)."),
    "segmente_profile.csv": (
        ("frage", "profilfrage", "variable", "label", "art", "antwort", "segment", "name", "wert", "sd", "personen",
         "test_f", "test_df1", "test_df2", "test_p"),
        "Profile der Segmente über andere Fragen: art = anteil (je Antwort, 0 bis 1) oder mittel; der Wald-Test (BCH) "
        "prüft, ob die Segmente gleich antworten, und steht in jeder Zeile seines Blocks.",
        "Profiles of the segments over other questions: art = anteil (per answer, 0 to 1) or mittel; the Wald test "
        "(BCH) checks whether the segments answer alike and is repeated in every row of its block."),
}


class Tables:
    """The rows of every core file, in the order they are found."""

    def __init__(self):
        self.rows = {name: [] for name in FILES}

    def add(self, file, /, **values):
        self.rows[file].append(values)


def _cell(value):
    """A value as the files write it: empty for nothing, 1/0 for yes/no, a decimal point, no rounding."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return repr(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):  # a numpy number
        try:
            return _cell(value.item())
        except (AttributeError, ValueError):
            pass
    return str(value)


def _text(value):
    return " ".join(plain(str(value)).split()) if value not in (None, "") else ""


def _pair(pair):
    return (pair[0], pair[1]) if pair and len(pair) >= 2 else (None, None)


def csv_bytes(columns, rows, separator="comma"):
    """One file: UTF-8 with a byte order mark (Excel), the chosen separator, values as _cell writes them."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";" if separator == "semicolon" else ",", lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in columns] if isinstance(r, dict) else [_cell(x) for x in r])
    return ("﻿" + out.getvalue()).encode("utf-8")


# --- the core files -----------------------------------------------------------------------------------------------

def _frequencies(t, e):
    code = e["item"].code
    for c in e.get("columns") or []:
        if c.get("type") not in ("freq", "ord") or not c.get("n"):
            continue
        base = {"frage": code, "variable": c.get("code"), "label": _text(c.get("label") or e.get("question")),
                "art": "ordinal" if c["type"] == "ord" else "nominal", "n_gueltig": c.get("n")}
        for r in c.get("rows") or []:
            lo, hi = _pair(r.get("ci"))
            t.add("haeufigkeiten.csv", **base, code=r.get("value"), antwort=_text(r.get("label")), n=r.get("count"),
                  anteil=r.get("share"), ci_low=lo, ci_high=hi, fehlend=False)
        for r in c.get("missing") or []:
            t.add("haeufigkeiten.csv", **base, code=r.get("value"), antwort=_text(r.get("label")), n=r.get("count"),
                  fehlend=True)
    for m in e.get("multi") or []:
        if not m.get("n"):
            continue
        for r in m.get("rows") or []:
            lo, hi = _pair(r.get("ci"))
            t.add("haeufigkeiten.csv", frage=code, variable=f"{code}_{r.get('value')}" + (f"_{m['pass']}" if m.get("pass") else ""),
                  durchgang=m.get("pass"), label=_text(e.get("question")), art="mehrfach", code=1,
                  antwort=_text(r.get("label")), n=r.get("count"), anteil=r.get("share"), ci_low=lo, ci_high=hi,
                  n_gueltig=r.get("n"), fehlend=False)


def _numbers(t, e):
    for c in e.get("columns") or []:
        if c.get("type") != "num" or not c.get("n"):
            continue
        lo, hi = _pair(c.get("ci"))
        t.add("kennwerte.csv", frage=e["item"].code, variable=c.get("code"), label=_text(c.get("label") or e.get("question")),
              einheit=c.get("unit"), n=c.get("n"), m=c.get("mean"), sd=c.get("sd"), median=c.get("median"),
              min=c.get("min"), max=c.get("max"), ci_low=lo, ci_high=hi, gini=c.get("gini"))


def _scales(t, e):
    code = e["item"].code
    found = ([("", e["scale"])] if e.get("scale") else []) + [(s.get("name") or "", s) for s in e.get("subscales") or []]
    for name, s in found:
        a_lo, a_hi = _pair(s.get("alpha_ci"))
        o_lo, o_hi = _pair(s.get("omega_ci"))
        m_lo, m_hi = _pair(s.get("mean_ci"))
        t.add("skalen.csv", frage=code, skala=_text(name), items=s.get("k"), n=s.get("n"), alpha=s.get("alpha"),
              alpha_ci_low=a_lo, alpha_ci_high=a_hi, omega=s.get("omega"), omega_ci_low=o_lo, omega_ci_high=o_hi,
              spearman_brown=s.get("sb"), m=s.get("mean"), sd=s.get("sd"), m_ci_low=m_lo, m_ci_high=m_hi)
        for r in s.get("item_stats") or []:
            t.add("skalen_items.csv", frage=code, skala=_text(name), variable=r.get("code"), text=_text(r.get("text")),
                  umgepolt=bool(r.get("reverse")), n=r.get("n"), m=r.get("mean"), sd=r.get("sd"),
                  trennschaerfe=r.get("rit"), alpha_ohne=r.get("alpha_without"), ladung=r.get("loading"))


def _figures(t, code, model, pairs):
    for name, value in pairs:
        if value is not None:
            t.add("wahlmodell_kennwerte.csv", frage=code, modell=model, kennwert=name, wert=value)


def _logit(t, code, m):
    if not m or not m.get("attributes"):
        return
    _figures(t, code, "logit", [("personen", m.get("persons")), ("entscheidungen", m.get("sets")),
                                ("pseudo_r2", m.get("rho2")), ("loglik", m.get("loglik")),
                                ("konvergiert", None if m.get("converged") is None else bool(m["converged"]))])
    for a in m["attributes"]:
        base = {"frage": code, "merkmal_code": a.get("code"), "merkmal": _text(a.get("label"))}
        for n, lv in enumerate(a.get("levels") or [], start=1):
            t.add("wahlmodell_logit.csv", **base, art="teilnutzen", stufe_nr=n, stufe=_text(lv.get("label")),
                  beta=lv.get("beta"), se=lv.get("se"), zahlungsbereitschaft=lv.get("wtp"), zb_low=lv.get("wtp_lo"),
                  zb_high=lv.get("wtp_hi"), begrenzt=bool(lv.get("separated")))
        if a.get("importance") is not None:
            t.add("wahlmodell_logit.csv", **base, art="wichtigkeit", wichtigkeit=a["importance"])
    price = m.get("price")
    if isinstance(price, dict):
        t.add("wahlmodell_logit.csv", frage=code, art="preis", merkmal=_text(price.get("label")) or "Preis",
              beta=price.get("beta"), se=price.get("se"), wichtigkeit=price.get("importance"))
    if m.get("asc") is not None:
        t.add("wahlmodell_logit.csv", frage=code, art="konstante", beta=m["asc"], se=m.get("asc_se"))


def _hb_levels(t, code, hb, scale):
    for a in hb.get("attributes") or []:
        base = {"frage": code, "skala": scale, "merkmal_code": a.get("code"), "merkmal": _text(a.get("label"))}
        for n, lv in enumerate(a.get("levels") or [], start=1):
            t.add("wahlmodell_hb.csv", **base, art="teilnutzen", stufe_nr=n, stufe=_text(lv.get("label")),
                  mittel=lv.get("mean"), ci_low=lv.get("lo"), ci_high=lv.get("hi"), sd=lv.get("sd"), q1=lv.get("q1"),
                  median=lv.get("median"), q3=lv.get("q3"), bevorzugt=lv.get("best"), begrenzt=bool(lv.get("separated")))
    price = hb.get("price")
    if price and price.get("mean") is not None:
        t.add("wahlmodell_hb.csv", frage=code, skala=scale, art="preis", merkmal=_text(price.get("label")) or "Preis",
              mittel=price.get("mean"), ci_low=price.get("lo"), ci_high=price.get("hi"), sd=price.get("sd"),
              q1=price.get("q1"), median=price.get("median"), q3=price.get("q3"))
    asc = hb.get("asc")
    if asc and asc.get("mean") is not None:
        t.add("wahlmodell_hb.csv", frage=code, skala=scale, art="konstante", mittel=asc.get("mean"), ci_low=asc.get("lo"),
              ci_high=asc.get("hi"), sd=asc.get("sd"), q1=asc.get("q1"), median=asc.get("median"), q3=asc.get("q3"))


def _hb(t, code, hb):
    if not hb:
        return
    conv, settings, rlh = hb.get("convergence") or {}, hb.get("settings") or {}, hb.get("rlh") or {}
    _figures(t, code, "hb", [
        ("personen", hb.get("persons")), ("entscheidungen", hb.get("sets")), ("ketten", conv.get("chains")),
        ("burn_in", settings.get("burn")), ("ziehungen", settings.get("draws")), ("behalten", hb.get("kept")),
        ("akzeptanz", hb.get("acceptance")), ("konvergiert", None if hb.get("converged") is None else bool(hb["converged"])),
        ("rhat_max", conv.get("rhat_max")), ("ess_min", conv.get("ess_min")), ("ess_loglik", conv.get("ess_ll")),
        ("rhat_sigma_max", conv.get("rhat_sigma_max")), ("ess_sigma_min", conv.get("ess_sigma_min")),
        ("geweke_loglik_z", conv.get("geweke_ll")), ("geweke_loglik_grenze", conv.get("geweke_ll_limit")),
        ("rlh_mittel", rlh.get("mean")), ("rlh_median", rlh.get("median")), ("rlh_zufall", rlh.get("chance")),
        ("rlh_niedrig_anteil", rlh.get("low_share"))])
    _hb_levels(t, code, hb, "roh")
    for a in list(hb.get("attributes") or []) + ([{**hb["price"], "code": None, "label": hb["price"].get("label") or "Preis"}]
                                                 if hb.get("price") else []):
        if a.get("importance") is None:
            continue
        q = a.get("importance_q") or [None, None, None]
        t.add("wahlmodell_hb.csv", frage=code, skala="roh", art="wichtigkeit", merkmal_code=a.get("code"),
              merkmal=_text(a.get("label")), mittel=a["importance"], ci_low=a.get("imp_lo"), ci_high=a.get("imp_hi"),
              sd=a.get("importance_sd"), q1=q[0], median=q[1], q3=q[2])
    for row in hb.get("interactions") or []:
        lo, hi = row.get("lo"), row.get("hi")
        for i, la in enumerate(row.get("a_levels") or []):
            for j, lb in enumerate(row.get("b_levels") or []):
                t.add("wahlmodell_hb.csv", frage=code, skala="roh", art="wechselwirkung", merkmal=_text(row.get("label")),
                      stufe_nr=f"{i + 1}x{j + 1}", stufe=f"{_text(la)} × {_text(lb)}", mittel=row["cells"][i][j],
                      ci_low=lo[i][j] if lo else None, ci_high=hi[i][j] if hi else None)
    if hb.get("diffs"):
        _hb_levels(t, code, hb["diffs"], "normiert")


def _search(t, code, res):
    if not res or not res.get("rows"):
        return
    conf = res.get("confirm") or {}
    _figures(t, code, "wechselwirkungen", [("personen", res.get("persons")), ("auffaellige_paare", res.get("flagged")),
                                           ("bestaetigung", conf.get("mode")), ("aufteilungen", conf.get("splits"))])
    for r in res["rows"]:
        simple, after, c = r.get("simple") or {}, r.get("hb") or {}, r.get("confirm") or {}
        t.add("wechselwirkungen.csv", frage=code, merkmalspaar=_text(r.get("label")),
              p_einfach=simple.get("p") if simple.get("estimable") else None,
              p_hb=after.get("p") if after.get("estimable") else None, p_holm=r.get("p_holm"), zuwachs_r2=r.get("gain"),
              delta_ll=c.get("mean"), status=r.get("status"), ergebnis=_text(r.get("status_label")))


def _segments(t, code, lc):
    res = (lc or {}).get("result")
    if not res:
        return
    for s in res.get("solutions") or []:
        t.add("segmente_guete.csv", frage=code, k=s.get("K"), gezeigt=s.get("K") == res.get("K"),
              vorgeschlagen=s.get("K") == res.get("suggested"), loglik=s.get("ll"), parameter=s.get("params"),
              bic=s.get("bic"), aic3=s.get("aic3"), caic=s.get("caic"), entropie=s.get("entropy"),
              kleinstes_segment=s.get("smallest"), laeufe=s.get("runs"), gefunden=s.get("found"),
              konvergiert=None if s.get("converged") is None else bool(s["converged"]))
    names = [_text(s.get("name")) for s in res.get("segments") or []]
    for s in res.get("segments") or []:
        t.add("segmente.csv", frage=code, segment=s["index"] + 1, name=_text(s.get("name")), anteil=s.get("share"),
              ci_low=s.get("lo"), ci_high=s.get("hi"), personen=s.get("persons"), sicher=s.get("sure"), rlh=s.get("rlh"))

    def per_segment(values):
        return [(c, names[c] if c < len(names) else "", v) for c, v in enumerate(values or [])]

    for a in res.get("attributes") or []:
        base = {"frage": code, "merkmal_code": a.get("code"), "merkmal": _text(a.get("label"))}
        for n, lv in enumerate(a.get("levels") or [], start=1):
            for c, name, v in per_segment(lv.get("values")):
                t.add("segmente_parameter.csv", **base, segment=c + 1, name=name, art="teilnutzen", stufe_nr=n,
                      stufe=_text(lv.get("label")), wert=v.get("beta"), se=v.get("se"),
                      zahlungsbereitschaft=v.get("wtp"), zb_low=v.get("wtp_lo"), zb_high=v.get("wtp_hi"),
                      begrenzt=bool(v.get("separated")))
        for c, name, v in per_segment(a.get("importance")):
            t.add("segmente_parameter.csv", **base, segment=c + 1, name=name, art="wichtigkeit", wert=v)
    price = res.get("price")
    if price:
        label = _text(price.get("label")) or "Preis"
        ses, imps = price.get("se") or [], price.get("importance") or []
        for c, name, v in per_segment(price.get("beta")):
            t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="preis", merkmal=label, wert=v,
                  se=ses[c] if c < len(ses) else None)
            if c < len(imps) and imps[c] is not None:
                t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="wichtigkeit", merkmal=label,
                      wert=imps[c])
    for c, name, v in per_segment(res.get("asc")):
        t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="konstante", wert=v.get("beta"),
              se=v.get("se"))
    for c, name, v in per_segment(res.get("constants")):  # ratings-based conjoint: the constant of the regression
        t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="konstante", wert=v)
    for s in res.get("statements") or []:  # MaxDiff
        for c, name, v in per_segment(s.get("values")):
            t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="nutzen",
                  stufe_nr=(s.get("index") or 0) + 1, stufe=_text(s.get("label")), wert=v.get("util"), se=v.get("se"),
                  score=v.get("score"), begrenzt=bool(v.get("separated")))
    means = lc.get("hb_means")
    if means:
        for row in means.get("rows") or []:
            levels = row.get("levels") or [{"label": row.get("label"), "values": row.get("values")}]
            for n, lv in enumerate(levels, start=1):
                for c, name, v in per_segment(lv.get("values")):
                    t.add("segmente_parameter.csv", frage=code, segment=c + 1, name=name, art="hb_mittel",
                          merkmal=_text(row.get("label")) if row.get("levels") else "", stufe_nr=n if row.get("levels") else None,
                          stufe=_text(lv.get("label")), wert=v)
    cov = res.get("covariates")
    if cov:
        for row in cov.get("rows") or []:
            for name, cell in zip(cov.get("against") or [], row.get("cells") or []):
                t.add("segmente_kovariaten.csv", frage=code, kovariate=_text(row.get("label")), segment=_text(name),
                      referenz=_text(cov.get("reference")), koeffizient=cell.get("g"), se=cell.get("se"),
                      odds_ratio=cell.get("or"), p=cell.get("p"))
    for q in lc.get("profile") or []:
        for b in q.get("blocks") or []:
            test = b.get("test") if isinstance(b.get("test"), dict) and not b["test"].get("small") else {}
            base = {"frage": code, "profilfrage": _text(q.get("label")), "variable": b.get("code"),
                    "label": _text(b.get("label")), "test_f": test.get("F"), "test_df1": test.get("df"),
                    "test_df2": test.get("df2"), "test_p": test.get("p")}
            if b.get("kind") == "shares":
                for r in b.get("rows") or []:
                    for c, name, v in per_segment(r.get("shares")):
                        t.add("segmente_profile.csv", **base, art="anteil", antwort=_text(r.get("label")), segment=c + 1,
                              name=name, wert=v, personen=r.get("count"))
            else:
                for c, name, v in per_segment(b.get("per")):
                    t.add("segmente_profile.csv", **base, art="mittel", segment=c + 1, name=name, wert=v.get("mean"),
                          sd=v.get("sd"))


def core(data):
    """Tables with the rows of every core file, from the kept results of results.compute()."""
    t = Tables()
    for e in data.get("items") or []:
        code = e["item"].code
        _frequencies(t, e)
        _numbers(t, e)
        _scales(t, e)
        for kind in ("cbc", "acbc"):
            found = e.get(kind) or {}
            _logit(t, code, found.get("model"))
            _hb(t, code, (found.get("hb") or {}).get("result"))
            _search(t, code, (found.get("ww") or {}).get("result"))
        for kind in ("cbc", "acbc", "maxdiff", "cva"):
            _segments(t, code, (e.get(kind) or {}).get("lc"))
    return t


# --- the tables of the results page as they stand there ------------------------------------------------------------

def _slug(text, most=48):
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                  .replace("ß", "ss")).strip("-")
    return text[:most].rstrip("-") or "tabelle"


def shown(root):
    """[(title, section id, head, rows)] of every table of the results page (an lxml tree, results_pdf.page_tree), in
    the page's order, the cells as plain text."""
    from app import results_pdf as pdf

    out = []
    for table in root.xpath("//table"):
        if table.xpath("ancestor::table"):
            continue
        head = [pdf._text(th) for th in table.xpath("./thead/tr[1]/th|./thead/tr[1]/td")]
        rows = [[pdf._text(td) for td in tr.xpath("./th|./td")] for tr in table.xpath("./tbody/tr|./tr")]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            continue
        section = table.xpath("ancestor::section[@id][1]/@id")
        out.append((pdf._heading_of(table), section[0] if section else "", head, rows))
    return out


# --- the ZIP -------------------------------------------------------------------------------------------------------

def _readme(survey, ui, data, pool, computed_at, files, n_shown, separator):
    from app import brand, timeutil

    en = ui == "en"

    def say(de, en_text, **values):
        return (en_text if en else de).format(**values)

    sources = {"real": say("echte Teilnahmen", "real participations"), "test": say("Testdurchläufe", "test runs"),
               "dummy": say("Dummy-Daten", "dummy data")}
    basis = ", ".join(sources.get(x, x) for x in pool["sources"])
    if pool.get("all"):
        basis += say(", auch nicht abgeschlossene", ", also those not completed")
    lines = [say("Ergebnistabellen: {t}", "Results tables: {t}", t=_text(survey.title)), "",
             say("Datengrundlage: N = {n} Teilnahmen ({b}).", "Data basis: N = {n} participations ({b}).",
                 n=data.get("n_rows", 0), b=basis)]
    if data.get("marked"):
        lines.append(say("Nicht enthalten: {m} von der Qualitätssicherung markierte Teilnahmen (davon {s} Speeder).",
                         "Not included: {m} participations marked by quality assurance ({s} of them speeders).",
                         m=data["marked"], s=data.get("speeders") or 0))
    lines += [say("Ungewichtete Ergebnisse, berechnet am {d}; dieselbe Datengrundlage und dieselben Ergebnisse wie der "
                  "Ergebnisbericht (PDF). Es wird nichts neu geschätzt: Eine fehlende oder veraltete Schätzung (HB, "
                  "Segmente) fehlt auch hier.",
                  "Unweighted results, computed on {d}; the same data basis and the same results as the results report "
                  "(PDF). Nothing is estimated anew: an estimate that is missing or out of date (HB, segments) is "
                  "missing here too.", d=timeutil.fmt(computed_at, lang=ui)),
              say("Erstellt mit {b}.", "Made with {b}.", b=brand.name()), "",
              say("Format: CSV, UTF-8, Trennzeichen {s}, Dezimalpunkt, ungerundet; leer = kein Wert; 1/0 = ja/nein. "
                  "Datei- und Spaltennamen sind fest und hängen nicht von der Sprache ab. Anteile und Wichtigkeiten "
                  "stehen als 0 bis 1. Die Spalte frage ist der Variablenname der Frage wie im Datensatz und im "
                  "Codebuch.",
                  "Format: CSV, UTF-8, separator {s}, decimal point, unrounded; empty = no value; 1/0 = yes/no. File "
                  "and column names are fixed and do not depend on the language. Shares and importances are written as "
                  "0 to 1. The column frage is the variable name of the question as in the data set and the codebook.",
                  s=say("Semikolon", "semicolon") if separator == "semicolon" else say("Komma", "comma")), "",
              say("Dateien", "Files"), "-------"]
    for name, n in files:
        columns, de, en_text = FILES[name]
        lines += [f"{name} ({n} {say('Zeilen', 'rows')})", "  " + say(de, en_text),
                  "  " + say("Spalten: ", "Columns: ") + ", ".join(columns), ""]
    if n_shown:
        lines += [f"{SHOWN}/ ({n_shown} {say('Dateien', 'files')})",
                  "  " + say("Jede Tabelle der Ergebnisseite, wie sie dort steht: gerundet und in der Sprache der "
                             "Oberfläche, Zahlen als Text. Vollständig, auch für Ergebnisse ohne eigene Datei oben "
                             "(Hypothesen, Strukturgleichungsmodell, Panelmodelle, Marktsimulator, seltene Fragetypen). "
                             "Für Berechnungen bitte die Dateien oben nehmen, wo es sie gibt.",
                             "Every table of the results page as it stands there: rounded and in the language of the "
                             "interface, numbers as text. Complete, also for results without a file of their own above "
                             "(hypotheses, structural equation model, panel models, market simulator, rarer kinds of "
                             "question). For computations please take the files above where they exist."), ""]
    lines += [say("tabellen.csv listet alle Dateien mit Zeilenzahl, Art (berechnet oder wie angezeigt) und Titel.",
                  "tabellen.csv lists all files with their number of rows, kind (berechnet = computed, wie angezeigt = "
                  "as shown) and title."),
              say("Werte je Person (HB-Teilnutzen, Wichtigkeiten, Segment) stehen als Spalten im Datensatz, nicht hier.",
                  "Values per person (HB part-worths, importances, segment) are columns of the data set, not here.")]
    return "\r\n".join(lines) + "\r\n"


def build(survey, ui=None, separator="comma", data=None, pool=None, computed_at=None, page=True):
    """The ZIP as bytes. ui: the language of README and of the tables as shown ("de"/"en"; None: the page's).
    page: also the tables of the results page (needs a request of a person, as the report)."""
    from app import results_pdf
    from app.items.base import reader

    if data is None:
        data, pool, computed_at = results_pdf.collect(survey)
    ui = reader(ui)
    tables = core(data)
    files = [(name, len(rows)) for name, rows in tables.rows.items() if rows]
    index = [{"datei": name, "zeilen": n, "art": "berechnet", "titel": "", "abschnitt": ""} for name, n in files]
    found = []
    root = results_pdf.page_tree(survey) if page else None
    if root is not None:
        for number, (title, section, head, rows) in enumerate(shown(root), start=1):
            name = f"{SHOWN}/{number:03d}-{_slug(section or title)}.csv"
            width = max([len(head)] + [len(r) for r in rows])
            body = [(r + [""] * width)[:width] for r in rows]
            found.append((name, (head + [""] * width)[:width] if head else None, body))
            index.append({"datei": name, "zeilen": len(rows), "art": "wie angezeigt", "titel": title, "abschnitt": section})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "﻿" + _readme(survey, ui, data, pool, computed_at, files, len(found), separator))
        z.writestr("tabellen.csv", csv_bytes(("datei", "zeilen", "art", "titel", "abschnitt"), index, separator))
        for name, _ in files:
            z.writestr(name, csv_bytes(FILES[name][0], tables.rows[name], separator))
        for name, head, body in found:
            lines = io.StringIO()
            w = csv.writer(lines, delimiter=";" if separator == "semicolon" else ",", lineterminator="\r\n")
            if head:
                w.writerow(head)
            w.writerows(body)
            z.writestr(name, ("﻿" + lines.getvalue()).encode("utf-8"))
    return out.getvalue()


def filename(survey):
    from app import export

    return export.filename(survey, "ergebnistabellen")[:-4] + ".zip"
