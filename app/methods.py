"""Method section for a paper: the facts of a survey (field time, sample and exclusions, constructs,
demographics) as a fixed draft, optionally reworded by the AI. Only these aggregated facts go to the AI,
never individual answers."""
import re
from datetime import datetime, timedelta

from app import brand
from app import constructs as constructs_mod
from app import export, units
from app.choice_model import T_UNTIL
from app.latent_class import MIN_CLASS, MIN_SHARE
from app.extensions import db
from app.lang import de_en
from app.models import Response
from app.timeutil import to_local

SITE = "Askurious.de"
# own questions that clearly ask for age or gender, by their variable name (see facts)
AGE_CODE = re.compile(r"^(age|alter)(_?jahre|_?years)?$", re.I)
GENDER_CODE = re.compile(r"gender|geschlecht|^sex$", re.I)


def _de(x, places=2):
    from app.units import fixed

    return fixed(x, places).replace(".", ",")


def field_period(survey):
    """(first start, end, still running): pauses and reopenings in between are left out. The end is the
    last close, or for a running survey the planned field end (or None)."""
    starts = [datetime.fromisoformat(e["at"]) for e in survey.status_log or [] if e.get("event") == "start"]
    start = min(starts) if starts else survey.opened_at
    running = survey.status == survey.RUNNING
    # a planned field end at midnight belongs to the day before it (timeutil.day_end_utc, FUNC-06)
    end = (survey.field_end - timedelta(microseconds=1) if survey.field_end else None) if running else survey.closed_at
    if not running and end and survey.field_end and end >= survey.field_end:
        # closed by its field end: lifecycle closes at the next run after midnight (or at the field end itself for a
        # paused one), which is already the next local day; the field time ended with the day before
        end = survey.field_end - timedelta(microseconds=1)
    return start, end, running


def _look(survey, completed):  # completed: the paradata of the completed participations
    """The look of the survey pages for the page (German) and for the AI (English), see theme.describe."""
    from app import theme

    look = theme.of(survey)
    shown = {}
    for paradata in completed:
        key = (paradata or {}).get("_scheme")
        if key:
            shown[key] = shown.get(key, 0) + 1
    # without colour values and font (V31); those stay in the README of a deposit and the pre-registration
    return {"de": theme.describe(look, "de", shown, brief=True), "en": theme.describe(look, "en", shown, brief=True)}


def _plain_look(survey):
    """True for the standard look: nothing worth a sentence in a method section."""
    from app import theme

    return theme.clean(survey.theme) is None


def _census(survey):
    """The quotas of the assistant "Bevölkerung Deutschland" (app/census.py), if the survey has them: the
    population they stand for and where the shares come from."""
    from app import census

    found = census.facts(survey)
    if found is None:
        return None
    day = ".".join(reversed(found["date"].split("-")))
    lo, hi = found.get("lo") or 18, found.get("hi")
    regions = found.get("regions") if found.get("regions") in census.REGIONS else "states"
    return {"date": day, "table": found["table"], "span_de": census.span_label(lo, hi),
            "regions_de": census.REGIONS[regions][1], "postal": found.get("state") == "postal_code", "location": found.get("state") == "geolocation",
            "span": f"aged {lo} and older" if hi is None else f"aged {lo} to {hi}"}


CHOICE_TYPES = ("conjoint", "acbc", "maxdiff", "paired", "cva", "aca")


def _choice(survey, table):
    """The choice experiments (CBC, ACBC, MaxDiff): design, the persons of the final sample with answers, and a
    stored hierarchical Bayes estimate. What the results page estimates is fixed by the type, so it is described
    from the configuration (the models themselves are not computed here)."""
    from app.choice_model import responses_for
    from app.timeutil import to_local as local

    out = []
    for item in export._items(survey):
        if item.type not in CHOICE_TYPES:
            continue
        c = item.config or {}
        answered = [r for r in responses_for(item, table) if (r.answers or {}).get(str(item.id))]
        entry = {"code": item.code, "type": item.type, "n": len(answered)}
        if item.type == "aca":  # adaptive conjoint: self-explication and pairs per person (app/aca_analysis.py)
            entry.update(levels=item.kind.level_counts(item), pairs=item.kind.n_pairs(item), cal=item.kind.n_cal(item),
                         rated=len(item.kind.rated(item)), robust=False, hb=None, holdouts=0, segments=None)
            entry["text_de"] = _choice_de(entry)
            out.append(entry)
            continue
        if item.type == "cva":  # ratings, a regression per person (app/cva_analysis.py), HB and segments by regression
            from app import cva_hb

            hb_res = cva_hb.current(item)
            entry.update(levels=item.kind.level_counts(item), form=item.kind.form(item), points=c.get("points"),
                         slider=bool(c.get("slider")), tasks=item.kind.n_tasks(item), versions=c.get("versions") or 1,
                         robust=False, holdouts=item.kind.holdout_count(item), segments=_segments(item),
                         hb={"n": len(hb_res["pids"]), "burn": hb_res["settings"]["burn"],
                             "draws": hb_res["settings"]["draws"], "converged": hb_res.get("diag_ok"),
                             "rhat_max": hb_res.get("rhat_max"), "ess_min": hb_res.get("ess_min"),
                             "kept": hb_res.get("kept")} if hb_res and hb_res.get("pids") else None)
            entry["text_de"] = _choice_de(entry)
            out.append(entry)
            continue
        if item.type == "maxdiff":
            entry.update(statements=len(c.get("statements") or []), set_size=c.get("set_size"), sets=c.get("sets"),
                         versions=c.get("versions") or 1, anchor=bool(c.get("anchor")))
            params = len(c.get("statements") or []) - 1 + bool(c.get("anchor"))
        elif item.type == "paired":
            entry.update(options=item.kind.n_items(item), pairs=item.kind.n_pairs(item), versions=c.get("versions") or 1,
                         answer=c.get("answer", "choice"), steps=c.get("steps", 5), complete=item.kind.complete(item))
            # utilities but the reference, the position effect, the thresholds of "same" and the weaker steps
            params = item.kind.n_items(item) + len({abs(x) for x in item.kind.categories(item)} - {max(item.kind.categories(item))})
        else:
            levels = item.kind.level_counts(item)
            price = item.kind.price_attribute(item) if item.type == "conjoint" else None
            entry.update(levels=levels, price=price is not None or bool((c.get("price") or {}).get("on")))
            params = _parameters(item, levels, price)
            if item.type == "conjoint":
                entry.update(tasks=c.get("tasks"), alternatives=c.get("alternatives"), versions=c.get("versions") or 1,
                             none=bool(c.get("none")), dual=_dual(c))
            else:
                entry.update(screens=c.get("screens"), per_screen=c.get("per_screen"), tour_max=c.get("tour_max"))
        entry["robust"] = len(answered) > params  # as choice_model: the sandwich needs more persons than parameters
        module = export.hb_module(item)
        hb = module.load(item) if module else None
        entry["hb"] = ({"n": len(hb.get("pids") or []), "burn": hb["settings"]["burn"], "draws": hb["settings"]["draws"],
                        "date": f"{local(datetime.fromisoformat(hb['created'])):%d.%m.%Y}",
                        "rhat_max": hb.get("rhat_max"), "ess_min": hb.get("ess_min"), "kept": hb.get("kept"),
                        "converged": hb.get("diag_ok")}
                       if hb and hb.get("pids") and hb.get("settings") and hb.get("created") else None)
        entry["holdouts"] = item.kind.holdout_count(item) if hasattr(item.kind, "holdout_count") else 0
        entry["segments"] = _segments(item)
        entry["text_de"] = _choice_de(entry)
        out.append(entry)
    return out


def _dual(config):
    """Dual response of a CBC, read from its config as items/conjoint.py does (never together with "none")."""
    return bool((config or {}).get("dual")) and not (config or {}).get("none")


def _parameters(item, levels, price):
    """Number of parameters of the aggregate logit as the results page codes it (choice_model.Coding): L - 1 per
    attribute in effects coding; a CBC price with numbers as levels enters linearly with one parameter; the constant
    only with "Keine davon" (CBC) and always for the threshold (ACBC), plus the ACBC summed price."""
    if item.type == "conjoint":
        numbers = item.kind.level_numbers(item, price) if price is not None else None
        linear = bool(numbers) and len(set(numbers)) > 1  # as cbc_analysis: a price without spread is no column
        return (sum(n - 1 for a, n in enumerate(levels) if not (linear and a == price)) + linear
                + bool((item.config or {}).get("none") or _dual(item.config)))
    return sum(n - 1 for n in levels) + 1 + bool(((item.config or {}).get("price") or {}).get("on"))


def _cva_segments_de(s):
    """The sentence on the segments of a ratings-based conjoint (app/lc_regression.py), "" without them."""
    if not s:
        return ""
    text = (f" Segmente wurden mit einer Latent-Class-Regression bestimmt (DeSarbo & Cron, 1988; n = {s['n']}, 1 bis "
            f"{s['max']} Segmente); ")
    if s["K"] == 1:
        return text + ("die Lösung mit einem Segment passte am besten." if s.get("suggested") == 1 else
                       f"gezeigt wird die Lösung mit einem Segment, abweichend vom BIC-Vorschlag von {s.get('suggested')} "
                       "Segmenten (Schwarz, 1978).")
    fit = f"BIC = {units.number(s['bic'], 1)}" if s.get("bic") is not None else ""
    if s.get("entropy") is not None:
        fit += (", " if fit else "") + f"Entropie R² = {units.number(s['entropy'], 2)}"
    return text + f"gewählt wurden {s['K']} Segmente" + (f" ({fit})" if fit else "") + "."


def _segments(item):
    """The stored latent class result with the solution the results page shows (and the export uses), or None."""
    from app import segments

    lc = segments._stored(item)
    if not lc or not lc.get("solutions"):
        return None
    K = segments.shown_k(item, lc)
    sol = segments.solution(lc, K)
    return {"K": K, "suggested": lc.get("suggested"), "max": max(s["K"] for s in lc["solutions"]),
            "n": len(lc.get("pids") or []), "bic": sol.get("bic"), "entropy": sol.get("entropy"),
            "covariates": sorted({m["question"] for m in lc.get("covariates") or []})}


def _location(survey, table):
    """The elements "Standort": what was stored and how many of the final sample shared their position."""
    from app.choice_model import responses_for
    from app.items.geolocation import SHARED

    stored = {"exact": "die genaue Position", "100m": "die auf etwa 100 m gerundete Position",
              "1km": "die auf etwa 1 km gerundete Position", "plz": "nur Postleitzahl und Bundesland",
              "state": "nur das Bundesland"}

    out = []
    for item in export._items(survey):
        if item.type != "geolocation":
            continue
        ways = [((r.answers or {}).get(str(item.id)) or {}).get("way") for r in responses_for(item, table)]
        ways = [w for w in ways if w]
        out.append({"code": item.code, "store": stored.get((item.config or {}).get("store") or "exact"),
                    "store_key": (item.config or {}).get("store") or "exact", "n": len(ways),
                    "shared": sum(w in SHARED for w in ways), "ip": (item.config or {}).get("fallback") == "ip"})
    return out


def _voice(survey, table):
    """The voice messages (concept "Lernhilfen: vierte Runde", G7): how many of the final sample spoke, how many typed
    instead (only when typing was offered)."""
    from app.choice_model import responses_for

    out = []
    for item in export._items(survey):
        if item.type != "voice":
            continue
        answers = [(r.answers or {}).get(str(item.id)) for r in responses_for(item, table)]
        answers = [a for a in answers if isinstance(a, dict) and (a.get("audio") or a.get("text"))]
        out.append({"code": item.code, "n": len(answers), "spoke": sum(1 for a in answers if a.get("audio")),
                    "typing": item.kind.text_fallback(item)})
    return out


def voice_de(v):
    """German sentence on a voice message for the draft."""
    return (f"Die Frage „{v['code']}“ wurde als Sprachnachricht im Browser beantwortet"
            + (", wahlweise auch getippt" if v["typing"] else "") + f"; {v['spoke']} von {v['n']} Personen sprachen ihre "
            f"Antwort ein" + (f", {v['n'] - v['spoke']} tippten sie." if v["typing"] else "."))


def voice_en(v):
    return (f"Voice message \"{v['code']}\": answered by speaking in the browser" + (", typing offered" if v["typing"] else "")
            + f"; {v['spoke']} of {v['n']} spoke their answer" + (f", {v['n'] - v['spoke']} typed it" if v["typing"] else ""))


TASK_TYPES = ("iat", "discounting")  # tasks with a procedure of their own (app/items/iat.py, …)


def _tasks(survey, table):
    """The IAT and delay discounting elements: one sentence each (German and English) on procedure and scoring, from
    the answers of the final sample, and the sources of the sentence (math audit 2026-10-05, N-8)."""
    from app.choice_model import responses_for

    out = []
    for item in export._items(survey):
        if item.type not in TASK_TYPES:
            continue
        answers = [a for r in responses_for(item, table) if (a := (r.answers or {}).get(str(item.id)))]
        out.append({"code": item.code, "type": item.type, "text_de": item.kind.method_text(item, answers, "de"),
                    "text_en": item.kind.method_text(item, answers, "en"), "sources": item.kind.method_sources(item)})
    return out


def task_sources(tasks):
    """Source groups of the sentences on the experimental tasks, each group once."""
    groups = []
    for t in tasks:
        groups += [g for g in t["sources"] if g not in groups]
    return groups


def choice_sources(choice):
    """Source groups (title, [full citations]) for the choice experiments named in the text."""
    from app import choice_model, conjoint_design, hb, hb_convergence, maxdiff_analysis

    types = {c["type"] for c in choice}
    groups = []
    if types & {"conjoint", "acbc"}:
        groups.append((de_en("Wahlmodelle: konditionales Logit und clusterrobuste Standardfehler",
                             "Choice models: conditional logit and cluster-robust standard errors"),
                       [choice_model.MCFADDEN, choice_model.CAMERON]))
    if "conjoint" in types:
        groups.append((de_en("Versuchsplan des CBC: Information des Logit-Modells", "CBC experimental design: information of the logit model"),
                       [conjoint_design.HUBER_ZWERINA]))
    if any(c.get("dual") for c in choice):
        from app import learning

        groups.append((de_en("CBC: Nachfrage nach der Wahl (Dual Response)", "CBC: follow-up question after the choice (dual response)"),
                       [learning.BRAZELL]))
    if "maxdiff" in types:
        groups.append((de_en("MaxDiff: Best-Worst-Logit", "MaxDiff: best-worst logit"), [maxdiff_analysis.MARLEY, maxdiff_analysis.LOUVIERE]))
    if "paired" in types:
        from app import paired_analysis as pa

        groups.append((de_en("Paarvergleich: Bradley-Terry-Modell und Positionseffekt", "Paired comparison: Bradley-Terry "
                             "model and position effect"), [pa.BRADLEY_TERRY, pa.DAVIDSON_BEAVER]))
        if any(c["type"] == "paired" and c["answer"] != "choice" for c in choice):
            groups.append((de_en("Paarvergleich: „gleich“ und Stärke", "Paired comparison: “same” and strength"),
                           [pa.DAVIDSON, pa.AGRESTI]))
        if any(c["type"] == "paired" and c["complete"] for c in choice):
            groups.append((de_en("Paarvergleich: Konsistenz", "Paired comparison: consistency"), [pa.KENDALL]))
    if "aca" in types:
        from app import aca_design

        groups.append((de_en("Adaptive Conjoint (ACA)", "Adaptive conjoint (ACA)"),
                       [aca_design.JOHNSON, aca_design.GREEN_KRIEGER_AGARWAL, aca_design.PINNELL]))
    if "cva" in types:
        from app import cva_design

        groups.append((de_en("Bewertungs-Conjoint: Regression je Person", "Ratings-based conjoint: regression per person"),
                       [cva_design.GREEN_SRINIVASAN]))
        if any(c["type"] == "cva" and c.get("hb") for c in choice):
            from app import cva_hb

            groups.append((de_en("Bewertungs-Conjoint: Hierarchical-Bayes-Regression", "Ratings-based conjoint: hierarchical "
                                 "Bayes regression"), [cva_hb.LENK, hb_convergence.VEHTARI]))
    if any(c["hb"] for c in choice):
        groups.append(("Hierarchical Bayes", [hb.ALLENBY, hb.ROSSI]))
    if any(c["hb"] and c["hb"].get("rhat_max") is not None for c in choice):
        groups.append((de_en("Konvergenz der HB-Kette: split-R̂ und effektive Stichprobengröße; Geweke-artiger Vergleich der "
                             "Hälften", "Convergence of the HB chain: split R̂ and effective sample size; Geweke-like "
                             "comparison of the halves"), list(hb_convergence.SOURCES) + [hb.GEWEKE]))
    if any(c.get("segments") and c["type"] == "cva" for c in choice):
        from app import latent_class, lc_regression

        groups.append((de_en("Segmente: Latent-Class-Regression und Wahl der Segmentzahl",
                             "Segments: latent class regression and choice of the number of segments"),
                       [lc_regression.DESARBO, latent_class.SCHWARZ]))
    if any(c.get("segments") and c["type"] != "cva" for c in choice):
        from app import latent_class

        groups.append((de_en("Segmente: Latent-Class-Logit und Wahl der Segmentzahl",
                             "Segments: latent class logit and choice of the number of segments"),
                       [latent_class.KAMAKURA, latent_class.SCHWARZ]
                       + ([latent_class.DAYTON] if any((c["segments"] or {}).get("covariates") for c in choice) else [])))
    if any(c["robust"] for c in choice) and not types & {"conjoint", "acbc"}:
        groups.append((de_en("Clusterrobuste Standardfehler", "Cluster-robust standard errors"), [choice_model.CAMERON]))
    return groups


def _choice_de(c):
    """German sentences on one choice experiment for the draft."""
    name = f"„{c['code']}“"
    if c["type"] == "aca":
        levels = "/".join(str(n) for n in c["levels"])
        return (f"{name} war ein Adaptive Conjoint (ACA; Johnson, 1987) mit {len(c['levels'])} Merkmalen ({levels} Stufen): "
                f"Die Personen bewerteten die Stufen von {c['rated']} Merkmalen und die Wichtigkeit aller Merkmale "
                f"(Selbstauskunft) und danach {c['pairs']} Paaraufgaben aus Teilangeboten, die nach jeder Antwort so gewählt "
                "wurden, dass beide Seiten nach der bisherigen Schätzung ähnlich gut waren"
                + (f", am Ende {c['cal']} Kalibrierungsangebote mit Kaufwahrscheinlichkeit" if c["cal"] else "")
                + ". Die Teilnutzen je Person kombinieren Selbstauskunft und Regression der Paare (Green et al., 1991)"
                + (" und sind über die Kalibrierung in Logit-Einheiten skaliert" if c["cal"] else "")
                + f"; berichtet sind ihre Mittel über n = {c['n']} Personen mit t-Intervallen. ACA unterschätzt die "
                "Wichtigkeit des Preises (Pinnell, 1994).")
    if c["type"] == "cva":
        levels = "/".join(str(n) for n in c["levels"])
        lo, hi = (0, 100) if c.get("slider") else ((0, 10) if c.get("points") == 11 else (1, c.get("points")))
        form = {"pairs": f"je zwei Angebote auf einer {c['points']}-stufigen Skala zu vergleichen",
                "ranking": "alle auf einer Seite in eine Reihenfolge zu bringen"}.get(
            c["form"], f"jedes Angebot einzeln auf einer Skala von {lo} bis {hi} zu bewerten")
        return (f"{name} war eine Bewertungs-Conjoint (Conjoint Value Analysis) mit {len(c['levels'])} Merkmalen ({levels} "
                f"Stufen); jede Person hatte {c['tasks']} Angebote aus einem D-optimalen Versuchsplan für das lineare Modell "
                f"({c['versions']} {'Version' if c['versions'] == 1 else 'Versionen'}) {form}. Ausgewertet wurde je Person eine "
                + ("Regression der umgekehrten Ränge" if c["form"] == "ranking" else "Regression der Antworten")
                + " auf die Stufen (kleinste Quadrate, Effektkodierung; Green & Srinivasan, 1990); "
                f"berichtet sind die Mittel der Teilnutzen und Wichtigkeiten über n = {c['n']} Personen mit t-Intervallen "
                "und das R² je Person."
                + (f" Dazu kamen {c['holdouts']} feste Holdout-{'Aufgabe' if c['holdouts'] == 1 else 'Aufgaben'}, die nicht "
                   "in die Regression eingingen und die Vorhersage prüfen." if c.get("holdouts") else "")
                + _cva_segments_de(c.get("segments")) + (
                    f" Zusätzlich wurden die Teilnutzen je Person mit einer Hierarchical-Bayes-Regression geschätzt (Lenk "
                    f"et al., 1996; n = {c['hb']['n']}, {c['hb']['burn']} Burn-in-Iterationen, {c['hb']['draws']} "
                    "Iterationen)" + ("; die Konvergenzregel (R̂ < 1,01, ESS ≥ 400; Vehtari et al., 2021) war erfüllt."
                                      if c["hb"].get("converged") else
                                      "; die Konvergenzregel (R̂ < 1,01, ESS ≥ 400) war nicht erfüllt, die Werte sind nur "
                                      "als Richtung zu lesen.") if c.get("hb") else ""))
    if c["type"] == "paired":
        form = {"choice": "die bessere von zwei Optionen zu wählen",
                "tie": "die bessere von zwei Optionen zu wählen oder „gleich“ anzugeben",
                "strength": f"auf einer {c['steps']}-stufigen Skala anzugeben, welche von zwei Optionen wie deutlich besser ist"}[c["answer"]]
        model = {"choice": "ein Bradley-Terry-Modell (Bradley & Terry, 1952)",
                 "tie": "ein Bradley-Terry-Modell mit Gleichstand (Davidson, 1970)",
                 "strength": "ein Bradley-Terry-Modell benachbarter Kategorien (Agresti, 1992)"}[c["answer"]]
        text = (f"{name} war ein Paarvergleich mit {c['options']} Optionen; jede Person sah "
                + ("alle " if c["complete"] else "")
                + f"{c['pairs']} Paare ({c['versions']} {'Version' if c['versions'] == 1 else 'Versionen'}, links und rechts "
                f"ausbalanciert) und hatte {form}. Ausgewertet wurde {model} mit einem Parameter für die zuerst gezeigte "
                f"Seite (Davidson & Beaver, 1977) über die Urteile von n = {c['n']} Personen; berichtet sind Werte mit Summe 100"
                + ("; die Konsistenz je Person über zirkuläre Triaden (Kendall & Babington Smith, 1940)" if c["complete"] else ""))
    elif c["type"] == "maxdiff":
        text = (f"{name} war ein MaxDiff (Best-Worst Scaling, Fall 1) mit {c['statements']} Aussagen in {c['sets']} "
                f"Sets zu je {c['set_size']} Aussagen ({c['versions']} {'Version' if c['versions'] == 1 else 'Versionen'})"
                + (", gefolgt von einer direkten Ankerfrage" if c["anchor"] else "") + ". Ausgewertet wurde ein "
                "sequenzielles Best-Worst-Logit-Modell (Marley & Louviere, 2005; Louviere et al., 2015) über die Wahlen von "
                f"n = {c['n']} Personen; berichtet sind Wahrscheinlichkeits-Scores: die Wahrscheinlichkeit, in einem Set "
                f"mit {c['set_size'] - 1 if c.get('set_size') else 'a − 1'} durchschnittlichen Aussagen als wichtigste gewählt "
                "zu werden, auf Summe 100 skaliert")
    else:
        levels = "/".join(str(n) for n in c["levels"])
        if c["type"] == "conjoint":
            text = (f"{name} war ein Choice-Based Conjoint (CBC) mit {len(c['levels'])} Merkmalen ({levels} Stufen), "
                    f"{c['tasks']} Auswahlaufgaben zu je {c['alternatives']} Alternativen"
                    + (" und der Option „Keine davon“" if c["none"] else "")
                    + (" und nach jeder Wahl der Nachfrage, ob die gewählte Alternative wirklich gewählt würde (Dual "
                       "Response; Brazell et al., 2006)" if c.get("dual") else "")
                    + f" in {c['versions']} {'Version' if c['versions'] == 1 else 'Versionen'}; der Versuchsplan maximierte die "
                    "Information des Logit-Modells (Huber & Zwerina, 1996). Ausgewertet wurde ein aggregiertes konditionales "
                    f"Logit-Modell (McFadden, 1974) über die Wahlen von n = {c['n']} Personen"
                    # as cbc_analysis.choice_sets (audit 2026-10-06, W-1)
                    + ("; die Nachfrage ging so in das Modell ein: „würde wählen“ als eine Wahl unter den "
                       "Optionen und „Keine“, „würde nicht wählen“ als die erzwungene Wahl und dazu „Keine“ als Wahl "
                       "unter den Optionen und „Keine“" if c.get("dual") else ""))
        else:
            text = (f"{name} war ein adaptives Choice-Based Conjoint (ACBC) mit {len(c['levels'])} Merkmalen ({levels} "
                    f"Stufen): ein selbst zusammengestelltes Wunschprofil, {c['screens']} Screening-Seiten mit je "
                    f"{c['per_screen']} Konzepten und ein Turnier über bis zu {c['tour_max']} Konzepte. Ausgewertet wurde ein "
                    "aggregiertes konditionales Logit-Modell (McFadden, 1974) mit den Screening-Urteilen als Wahl gegen eine "
                    "Schwelle und den Turnieraufgaben als Wahl unter den Konzepten, über die Antworten von "
                    f"n = {c['n']} Personen")
        text += ", Merkmale in Effektkodierung" + (", der Preis linear" if c["price"] else "")
        # fixed choices of Askurious, named in the draft (audit data storage and calculations 2026-10-21, D8)
        text += "; wo Stufen nie oder immer gewählt wurden, begrenzte eine schwache Ridge-Strafe (λ = 0,1) die Schätzung"
    if c["robust"]:
        text += ", Standardfehler clusterrobust je Person (Cameron & Miller, 2015)"
        if c["n"] < T_UNTIL:  # as choice_model.critical(): few clusters, t quantile
            text += f", 95-%-Intervalle mit dem t-Quantil für {c['n'] - 1} Freiheitsgrade"
    text += "."
    if c["hb"]:
        h = c["hb"]
        text += (f" Individuelle Nutzen wurden zusätzlich per Hierarchical Bayes geschätzt (Allenby & Rossi, 1998; "
                 f"n = {h['n']}, {h['burn']} Burn-in-Iterationen, {h['draws']} Iterationen, Stand {h['date']}).")
        text += (" Priori: Populationsmittel normalverteilt mit Standardabweichung 5 (Logit-Skala), Kovarianz zwischen "
                 "Personen invers-Wishart mit k + 5 Freiheitsgraden bei k Parametern und der Priori-Kovarianz als "
                 "Erwartungswert; bei kleinen Stichproben zieht sie die Streuung schwach bestimmter Parameter spürbar "
                 "zu diesem Wert.")
        if h.get("rhat_max") is not None and h.get("ess_min") is not None:
            text += (f" Die Konvergenz der Kette wurde mit dem rang-normalisierten split-R̂ und der effektiven "
                     f"Stichprobengröße (ESS) je Populationsmittel geprüft (Vehtari et al., 2021; Geyer, 1992): "
                     f"R̂ max. {units.number(h['rhat_max'], 3)}, ESS min. {units.number(h['ess_min'])} von {h['kept']} "
                     f"Ziehungen" + (" (Regel R̂ < 1,01 und ESS ≥ 400 erfüllt)." if h.get("converged")
                                     else "; die Regel R̂ < 1,01 und ESS ≥ 400 war nicht erfüllt, die HB-Werte sind "
                                          "daher nur als Richtung zu lesen."))
    if c.get("holdouts"):
        unit = "Sets" if c["type"] == "maxdiff" else ("Aufgabe" if c["holdouts"] == 1 else "Aufgaben")
        text += f" Dazu kamen {c['holdouts']} Holdout-{unit}, die nicht in die Schätzung eingingen und die Vorhersage prüfen."
    s = c.get("segments")
    if s:
        text += (f" Segmente wurden mit einem Latent-Class-Logit-Modell bestimmt (Kamakura & Russell, 1989; n = {s['n']}, "
                 f"1 bis {s['max']} Segmente; jedes Segment mindestens {round(100 * MIN_SHARE)} % der Personen oder "
                 f"{MIN_CLASS} Personen, je nachdem, was mehr ist); ")
        if s["K"] == 1 and s.get("suggested") == 1:
            text += "die Lösung mit einem Segment passte am besten, die Vorlieben ließen sich nicht in Gruppen trennen."
        elif s["K"] == 1:  # the researcher's choice, not the BIC's (audit 2026-10-05, N-5)
            text += ("gezeigt wird die Lösung mit einem Segment"
                     + (f", abweichend vom BIC-Vorschlag von {s['suggested']} Segmenten (Schwarz, 1978)."
                        if s.get("suggested") else "."))
        else:
            fit = f"BIC = {units.number(s['bic'], 1)}" if s.get("bic") is not None else ""
            if s.get("entropy") is not None:
                fit += (", " if fit else "") + f"Entropie R² = {units.number(s['entropy'], 2)}"
            text += (f"gewählt wurden {s['K']} Segmente" + (f" ({fit})" if fit else "")
                     + (", wie nach dem BIC vorgeschlagen (Schwarz, 1978)." if s["K"] == s.get("suggested")
                        else f", abweichend vom BIC-Vorschlag von {s.get('suggested')} Segmenten (Schwarz, 1978)."))
        if s["covariates"]:
            text += (" Die Segmentanteile hingen von Kovariaten ab (Dayton & Macready, 1988): "
                     + ", ".join(f"„{q}“" for q in s["covariates"]) + ".")
    return text


def location_de(loc):
    """German sentence on an element "Standort" for the draft."""
    share = f"{round(100 * loc['shared'] / loc['n'])} %" if loc["n"] else "–"
    return (f"Den Standort erfragte das Element „{loc['code']}“ über den Browser, erst nach einem Tipp der Person und mit "
            f"gleichwertiger Möglichkeit, ihn nicht anzugeben; gespeichert wurde {loc['store']}. {loc['shared']} von "
            f"{loc['n']} Personen ({share}) teilten ihre Position"
            + ("; für die übrigen wurde das Bundesland aus der IP-Adresse geschätzt." if loc["ip"] else "."))


def location_en(loc):
    return (f"Location element \"{loc['code']}\": browser geolocation after a tap, refusal offered as an equal answer; stored: "
            f"{loc['store_key']}; {loc['shared']} of {loc['n']} shared their position"
            + ("; for the others the federal state was estimated from the IP address" if loc["ip"] else ""))


def _choice_en(c):
    """One line on a choice experiment for the AI facts."""
    if c["type"] == "aca":
        return (f"Adaptive conjoint (ACA; Johnson, 1987) \"{c['code']}\": {len(c['levels'])} attributes "
                f"({'/'.join(str(n) for n in c['levels'])} levels), self-explicated level ratings ({c['rated']} attributes) "
                f"and importances, {c['pairs']} adaptive pairs of partial offers"
                + (f", {c['cal']} calibration concepts" if c["cal"] else "")
                + f"; part-worths per person combining self-explication and pairs regression (Green et al., 1991), means over "
                f"n = {c['n']} people with t intervals; ACA underestimates price importance (Pinnell, 1994)")
    if c["type"] == "cva":
        form = (f"pairs of offers on a {c['points']}-point graded scale" if c["form"] == "pairs"
                else "all offers on one page put in order (OLS on the reversed ranks)" if c["form"] == "ranking"
                else ("one offer at a time on a slider 0-100" if c.get("slider") else f"one offer at a time on a {c['points']}-point scale"))
        return (f"Ratings-based conjoint (CVA) \"{c['code']}\": {len(c['levels'])} attributes "
                f"({'/'.join(str(n) for n in c['levels'])} levels), {c['tasks']} tasks per person, {form}, D-optimal design "
                f"for the linear model, {c['versions']} {'version' if c['versions'] == 1 else 'versions'}; OLS regression per "
                f"person (effects coding; Green & Srinivasan, 1990), means of part-worths and importances over n = {c['n']} "
                "people with t intervals, R2 per person"
                + (f"; {c['holdouts']} holdout task(s), not used in the estimation" if c.get("holdouts") else "")
                + (f"; segments by a latent class regression (DeSarbo & Cron, 1988), n = {c['segments']['n']}, 1 to "
                   f"{c['segments']['max']} segments, {c['segments']['K']} shown (BIC suggestion: "
                   f"{c['segments'].get('suggested')}; Schwarz, 1978)" if c.get("segments") else "")
                + (f"; also part-worths per person by hierarchical Bayes regression (Lenk et al., 1996), n = {c['hb']['n']}, "
                   f"{c['hb']['burn']} burn-in and {c['hb']['draws']} iterations, convergence rule "
                   f"{'met' if c['hb'].get('converged') else 'not met'}" if c.get("hb") else ""))
    if c["type"] == "paired":
        form = {"choice": "choose the better one", "tie": "choose the better one or “same”",
                "strength": f"{c['steps']}-step strength scale"}[c["answer"]]
        line = (f"Paired comparison \"{c['code']}\": {c['options']} options, {c['pairs']} pairs per person"
                + (" (all pairs)" if c["complete"] else "") + f", {c['versions']} {'version' if c['versions'] == 1 else 'versions'}, "
                f"{form}; estimated by a Bradley-Terry model (adjacent categories, Agresti, 1992) with a position effect, "
                f"n = {c['n']}; values sum to 100")
    elif c["type"] == "maxdiff":
        line = (f"Choice experiment \"{c['code']}\": MaxDiff (best-worst scaling, case 1), {c['statements']} statements, "
                f"{c['sets']} sets of {c['set_size']}, {c['versions']} {'version' if c['versions'] == 1 else 'versions'}" + (", direct anchoring question" if c["anchor"] else "")
                + f"; estimated by a sequential best-worst logit (Marley & Louviere, 2005), n = {c['n']}; probability scores"
                ": probability of being chosen as best in a set with "
                + (f"{c['set_size'] - 1}" if c.get("set_size") else "a - 1") + " average statements, rescaled to sum 100")
    else:
        levels = "/".join(str(n) for n in c["levels"])
        if c["type"] == "conjoint":
            line = (f"Choice experiment \"{c['code']}\": choice-based conjoint (CBC), {len(c['levels'])} attributes ({levels} "
                    f"levels), {c['tasks']} tasks of {c['alternatives']} alternatives" + (" plus a none option" if c["none"] else "")
                    + (" with a dual-response follow-up question (Brazell et al., 2006)" if c.get("dual") else "")
                    + f", {c['versions']} {'version' if c['versions'] == 1 else 'versions'}, experimental design maximising the logit information (Huber & Zwerina, 1996)")
        else:
            line = (f"Choice experiment \"{c['code']}\": adaptive CBC (ACBC), {len(c['levels'])} attributes ({levels} levels), "
                    f"build-your-own profile, {c['screens']} screening pages of {c['per_screen']} concepts, tournament of up "
                    f"to {c['tour_max']} concepts; screening as choices against a threshold, tournament tasks as choices")
        line += ("; estimated by an aggregate conditional logit (McFadden, 1974), effects coding"
                 + (", linear price" if c["price"] else "") + f", n = {c['n']}"
                 + "; levels never or always chosen bounded by a weak ridge penalty (lambda 0.1)")
        if c["type"] == "conjoint" and c.get("dual"):  # as cbc_analysis.choice_sets (audit 2026-10-06, W-1)
            line += ("; dual response coded as \"would choose it\" as one choice among the options "
                     "and none, \"would not\" as the forced choice plus none chosen among the options and none")
    if c["robust"]:
        line += ", cluster-robust standard errors by person (Cameron & Miller, 2015)"
        if c["n"] < T_UNTIL:
            line += f", 95% intervals with the t quantile for {c['n'] - 1} degrees of freedom"
    if c["hb"]:
        h = c["hb"]
        line += (f"; also individual utilities by hierarchical Bayes (Allenby & Rossi, 1998), n = {h['n']}, "
                 f"{h['burn']} burn-in and {h['draws']} iterations; prior: population means normal with sd 5 (logit "
                 "scale), covariance inverse Wishart with k + 5 degrees of freedom and the prior covariance as its mean")
        if h.get("rhat_max") is not None and h.get("ess_min") is not None:
            line += (f"; convergence: rank-normalised split R-hat max {h['rhat_max']:.3f}, bulk ESS min "
                     f"{h['ess_min']:.0f} of {h['kept']} draws (Vehtari et al., 2021; Geyer, 1992), rule R-hat < 1.01 and "
                     f"ESS >= 400 {'met' if h.get('converged') else 'not met'}")
    if c.get("holdouts"):
        line += f"; {c['holdouts']} holdout task(s), not used in the estimation"
    s = c.get("segments")
    if s:
        line += (f"; segments by a latent class logit (Kamakura & Russell, 1989), n = {s['n']}, 1 to {s['max']} segments "
                 f"(each at least {round(100 * MIN_SHARE)}% of the people or {MIN_CLASS} people, whichever is more), "
                 f"{s['K']} shown (BIC suggestion: {s.get('suggested')}; Schwarz, 1978)"
                 + (f", BIC {s['bic']:.1f}" if s.get("bic") is not None and s["K"] > 1 else "")
                 + (f", entropy R2 {s['entropy']:.2f}" if s.get("entropy") is not None and s["K"] > 1 else "")
                 + (f", covariates of the segment shares (Dayton & Macready, 1988): {', '.join(s['covariates'])}"
                    if s["covariates"] else ""))
    return line


def facts(survey, speeder=None, sources=("real",)):
    """The facts of the methods section. sources: the participations counted, the data pool of the results page on
    the page "Methodenteil" (UI/UX audit 2026-10-05, V29: one methods section on the pool of the results, dummies
    included while a draft has only them); "pool" in the result names them.

    Kept on the server like the results (app/results_store.py, file "methods"), under a fingerprint of all they
    are made from (facts_key); the same fingerprint, the kept facts (performance audit 2026-10-09b, P2: 4 to 13 s
    on every view before)."""
    from app import results_store

    sources = tuple(sources) or ("real",)
    speeder = export.threshold(survey, speeder)
    key = facts_key(survey, speeder, sources)
    kept = results_store.load(survey, key, name="methods")
    if kept is not None:
        return kept[0]
    f = _facts(survey, speeder, sources)
    results_store.save(survey, key, f, name="methods", basis=lambda: facts_key(survey, speeder, sources))
    return f


def facts_key(survey, speeder, sources):
    """The fingerprint of the facts: the results fingerprint over every participation of the sources (also
    unfinished ones, they are counted) with the weighting, every stored setting of the survey (field period, status
    log, look, study plan, declines, ...), the constructs used as stored, and the stored estimates (HB, latent
    classes) of its questions by file time and size."""
    import hashlib
    import json
    import os

    from flask import current_app
    from sqlalchemy import inspect

    from app import results_store

    pool = {"sources": list(sources), "all": True, "drop": True, "speeder": speeder, "speed_real": True,
            "weighted": True}
    row = {a.key: getattr(survey, a.key) for a in inspect(survey).mapper.column_attrs}
    used = [{a.key: getattr(c, a.key) for a in inspect(c).mapper.column_attrs} | {"items": [i.id for i in items]}
            for c, items in export.used_constructs(survey)]
    stored = []
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid)  # see maxdiff_hb.folder
    try:
        for name in sorted(os.listdir(folder)):
            if not name.endswith((".bin", ".tmp")):  # the kept results themselves are no source of the facts
                st = os.stat(os.path.join(folder, name))
                stored.append([name, st.st_size, st.st_mtime_ns])
    except OSError:
        pass
    basis = [results_store.fingerprint(survey, pool), row, used, stored]
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def _facts(survey, speeder, sources):
    """facts() computed."""
    from app import results

    # a real participation from the preview does not count; test runs and dummies are stored as previews
    not_preview = db.or_(Response.source != "real", Response.is_preview.is_(False))
    # counted in the database; of the completed ones only what the page reads of them (their paradata)
    by_status = dict(db.session.execute(db.select(Response.status, db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.source.in_(sources), not_preview).group_by(Response.status)).all())
    n_real = sum(by_status.values())
    completed = list(db.session.scalars(db.select(Response.paradata).filter(
        Response.survey_id == survey.id, Response.source.in_(sources), not_preview,
        Response.status == Response.COMPLETED)))  # their paradata
    screenout = by_status.get(Response.SCREENOUT, 0)
    quotafull = by_status.get(Response.QUOTAFULL, 0)
    excluded = by_status.get(Response.EXCLUDED, 0)  # an end "Ausschluss" in the flow
    declined = survey.declined_count or 0
    # The page shows case numbers, demographics and the scales of the constructs: only those questions are
    # summarised (the whole results page was computed here, with every conjoint and MaxDiff model), and the
    # table of answers is built once for this and for the subscales below.
    constructs = export.used_constructs(survey)
    needed = {i.id for i in export._items(survey)
              if constructs_mod.toolbox_key(i) or AGE_CODE.match(i.code or "")
              or GENDER_CODE.search(i.code or "")}
    pool = export.rows(survey, False, sources, speeder, True, speed_sources=sources, with_hb=False,
                       with_quality=False)
    res = results.compute(survey, sources, False, True, speeder, True, only=needed, table=pool)  # completed, speeders dropped
    # the paragraph on exclusions of the Markdown and Word draft (methods_text.exclusions) from the same pool, so the
    # draft reads the kept facts instead of building the table and quality.summary anew (audit 2026-10-10, Q-Rest):
    # the completed participations, the speeders and all marked ones, as quality.summary counts them
    exclusions = {"n": len(pool), "speeders": res["speeders"], "marked": res["marked"]}
    from app import quotas as quota_mod

    quota_facts = [{"name": e["quota"].name, "kind": ("counted at the quota calculation" if e["quota"].count_at == "check"
                                                     else f"counted at completion, {e['quota'].mode}"),
                    "kind_de": ("sofort an der Quotenberechnung gezählt" if e["quota"].count_at == "check"
                                else f"bei Abschluss gezählt, {e['mode']}"),
                    "cells": [(c["name"], c["target"], c["done"]) for c in e["cells"]],
                    "target": e["sum"]["target"], "done": e["sum"]["done"], "refused": e["sum"]["refused"],
                    "marked": e["sum"]["marked"], "freed": e["sum"]["freed"]}
                   # reached: without what quality control marks, as the final sample
                   # counted in the same pool as the rest of the page, not only the real participations
                   for e in quota_mod.report(survey, marked=quota_mod.marked_done(pool, sources), sources=sources)
                   ] if survey.quotas else []
    start, end, running = field_period(survey)
    # while the survey runs, a participation still open may be someone filling it in right now: not a dropout
    in_progress = by_status.get(Response.ACTIVE, 0) if running else 0
    median = export.median_seconds(survey, sources=sources)
    f = {"title": survey.title, "pool": list(sources), "start": start, "end": end, "running": running,
         "days": (to_local(end).date() - to_local(start).date()).days + 1 if start and end else None,
         "quotas": quota_facts, "census": _census(survey),
         "started": n_real + declined, "declined": declined, "screenout": screenout, "quotafull": quotafull,
         "excluded": excluded,
         "incomplete": n_real - len(completed) - screenout - quotafull - excluded - in_progress,
         "in_progress": in_progress, "completed": len(completed),
         "speeder_pct": speeder, "speeders": res["speeders"], "final": res["n_rows"],
         "marked_other": res["marked"] - res["speeders"], "quality": _quality_rule(survey),
         "look": _look(survey, completed), "look_plain": _plain_look(survey),
         "median_min": median / 60 if median else None, "cutoff_min": median * speeder / 100 / 60 if median else None, "constructs": [], "age": None, "gender": None,
         # the relative speed index (Leiner 2019) as the speeder rule: its limit, else None
         "rsi": export.rsi_limit(survey) if export.speeder_rule(survey) == "rsi" else None,
         "paths_differ": export.paths_differ(survey), "exclusions": exclusions}
    from app import tempo

    if tempo.active(survey):  # completed participations that saw the note at least once
        f["tempo"] = {"shown": sum(1 for p in completed if ((p or {}).get("_tempo") or {}).get("hint")),
                      "once": tempo.dose(survey) == "once", "ms": tempo.MS_PER_WORD}
    from app import weights

    weighting = weights.compute(survey, pool)  # the weights of the completed, unmarked participations (app/weights.py)
    f["weighting"] = weights.method_sentence(survey, weighting)
    f["weighting_en"] = weights.method_sentence_en(survey, weighting)
    entries = {e["item"].id: e for e in res["items"]}
    table = pool.keep(lambda r: r.get("qc_marked") != 1)  # the final sample, as in res
    f["choice"] = _choice(survey, table)
    f["location"] = _location(survey, table)
    f["voice"] = _voice(survey, table)
    f["tasks"] = _tasks(survey, table)
    from app import coding  # categorised open answers

    f["coding"] = coding.methods_sentences(survey)
    from app import vignettes  # factorial surveys

    f["vignettes"] = [t for t in (vignettes.methods_text(survey, n) for n in vignettes.nodes(survey)) if t]
    f["vignettes_en"] = [t for t in (vignettes.methods_text_en(survey, n) for n in vignettes.nodes(survey)) if t]
    for c, items in constructs:
        # about the person or the participation, no scales; the RatSWD standard questions neither (app/ratswd.py)
        if c.key in ("age", "gender", "sorgfalt") or c.key in constructs_mod.ratswd.NOT_POOLED:
            continue
        e = next((entries[i.id] for i in items if i.id in entries), None)
        scale = (e or {}).get("scale") or {}
        if not scale and e and len(e["columns"]) == 1 and e["columns"][0].get("type") == "num":
            col = e["columns"][0]  # an instrument of one item (R-1): the answer is the score, there is no alpha
            scale = {"k": 1, "mean": col.get("mean"), "sd": col.get("sd")}
        config = items[0].config or {}
        f["constructs"].append({"name": c.name, "citation": c.citation, "authors": c.authors, "year": c.year,
                                "items": scale.get("k") or len(config.get("statements") or config.get("pairs") or []) or None,
                                "points": config.get("points"),
                                "alpha": scale.get("alpha"), "omega": scale.get("omega"), "sb": scale.get("sb"),
                                "omega_small": bool(scale.get("omega_small")),
                                "mean": scale.get("mean"), "sd": scale.get("sd"),
                                "modified": bool((config.get("construct") or {}).get("unlocked")),
                                "subscales": subscales(e)})
    # demographics: the standard questions from the construct toolbox first, else own questions that clearly ask
    # for age or gender (by their variable name); without either the page says so and the AI leaves it out
    age_code, gender_code = AGE_CODE, GENDER_CODE
    for own in (False, True):
        for e in res["items"]:
            key = constructs_mod.toolbox_key(e["item"])
            col = (e["columns"] or [None])[0]
            if not col or e.get("passes") or e["item"].type == "date_time":
                continue  # a date (e.g. the year of birth under the code "alter") is no age
            code = e["item"].code or ""
            is_age = key == "age" or (own and not key and age_code.match(code))
            is_gender = key in ("gender", "ratswd_ges1") or (own and not key and gender_code.search(code))
            if f["age"] is None and is_age and col.get("type") == "num" and col.get("n"):
                f["age"] = {k: col.get(k) for k in ("n", "mean", "sd", "min", "max")}
            if f["gender"] is None and is_gender and col.get("type") == "freq":
                f["gender"] = [(r["label"] or r["value"], r["count"]) for r in col["rows"] if r["count"]] or None
    # are there questions for age and gender at all? The page tells "no answers yet" from "no questions" (V30)
    from app import census

    found = census.questions(survey)
    f["has_age"] = found["age"] is not None or any(AGE_CODE.match(i.code or "") for i in export._items(survey))
    f["has_gender"] = found["gender"] is not None or any(GENDER_CODE.search(i.code or "") for i in export._items(survey))
    f["plan"] = plan_facts(survey)
    if getattr(survey, "sampling", None) is not None:  # a diary study: design, persons, compliance, Android only
        from app import diary

        f["diary"] = diary.method_facts(survey)
    return f


def plan_facts(survey):
    """The study plan for the methods section (V29): research question, hypotheses with the planned test, target
    sample, recruitment and the exclusion rules set in advance; None while nothing is planned."""
    from app import methods_text, study_plan

    plan = study_plan.plan_of(survey)
    hyps = [{"text": h.get("text") or "", "test": (h.get("analysis") or study_plan.suggest(survey, h)) or "",
             "explorative": bool(h.get("after_start"))} for h in plan["hypotheses"] if h.get("text")]
    for h in hyps:
        h["test_de"] = study_plan.ANALYSES.get(h["test"], h["test"])
        h["test_en"] = methods_text.ANALYSES_EN.get(h["test"], h["test"])
    out = {"question": (getattr(survey, "research_question", "") or "").strip(), "hypotheses": hyps,
           "target": plan["sample"]["target"], "recruitment": plan["sample"]["recruitment"],
           "recruitment_de": study_plan.RECRUITMENT.get(plan["sample"]["recruitment"], ""),
           "recruitment_en": methods_text.RECRUITMENT_EN.get(plan["sample"]["recruitment"], ""),
           "rules": plan["rules"]}
    # the sample in the line of the sample model, as every page shows it (UI/UX audit 2026-10-07, S3)
    from app import sample_model

    m = sample_model.of(survey)
    if m and m["basis"] != "end":
        out.update(model=m, model_de=sample_model.text(m, "de"), model_en=sample_model.text(m, "en"))
    return out if (out["question"] or hyps or out["target"] or out.get("model") or out["recruitment"] or out["rules"]) else None


def _quality_rule(survey):
    """The marking rule of the page "Qualitätskontrolle" besides speeding, for the sentences on the sample; None if
    nothing this questionnaire can show is counted."""
    from app import quality

    ctx = quality.context(survey, export._items(survey))
    keys = [k for k, *_ in quality.available(ctx) if k in ctx["rules"]["flags"]]
    if not keys or ctx["rules"]["min"] > len(keys):
        return None
    de = {"straight": "in mindestens der Hälfte der Matrizen durchgehend dieselbe Antwort (bei mindestens zwei Matrizen)",
          "missing": "mindestens die Hälfte der gezeigten Fragen ohne Antwortpflicht unbeantwortet (bei mindestens vier solchen Fragen)",
          "attention": "mindestens eine nicht bestandene Aufmerksamkeitsprüfung",
          "self": "die eigene Angabe, die Antworten sollten nicht ausgewertet werden",
          "bot": "ein ausgefülltes, für Menschen unsichtbares Formularfeld (Hinweis auf ein Skript)",
          "same": "eine längere offene Antwort, die wortgleich oder fast gleich auch bei einer anderen Teilnahme steht",
          "tempo": "mindestens die Hälfte der geprüften Seiten schneller abgeschickt, als die Schwelle des Tempo-Hinweises erlaubt",
          "ai": "das Befolgen eines für Menschen unsichtbaren Hinweises an KI-Systeme (Prompt-Injektion als Köder; Höhne, Claassen & Wolf, 2025)",
          "paste": "mindestens 30 in Textfelder eingefügte Zeichen",
          "typed": "Text in offenen Antworten, der weder getippt noch eingefügt wurde (kaum Eingaben für die Zahl der Zeichen)"}
    en = {"straight": "the same answer throughout in at least half of the matrix questions (six rows or more; at least two such matrices)",
          "missing": "at least half of the non-mandatory questions shown left unanswered (at least four such questions)",
          "attention": "at least one failed attention check (instructed response item)",
          "self": "the participant's own statement that the answers should not be used",
          "bot": "a form field invisible to people that came back filled in (honeypot; points to a script)",
          "same": "a longer open-ended answer given word for word or nearly so by another participation as well",
          "tempo": "at least half of the checked pages sent faster than the threshold of the speed prompt",
          "ai": "following a note to AI systems that people do not see (a prompt injection used as bait; Höhne, Claassen & Wolf, 2025)",
          "paste": "at least 30 characters pasted into text fields",
          "typed": "text in open-ended answers that was neither typed nor pasted (few input steps for the number of characters)"}
    return {"min": ctx["rules"]["min"], "texts": [de[k] for k in keys], "en": [en[k] for k in keys]}


def subscales(entry):
    """[{"name", "k", "alpha", "omega", "omega_small", "sb", "mean", "sd"}] per subscale of a construct item, as the results page
    computed them for the same sample (results.compute: Likert and semantic differential, metric only, attention
    checks left out, the mean over the statements shown); [] without subscales. A single alpha over the whole item
    would mix them. Omega from three items on, Spearman-Brown for two (app/scales.py)."""
    return [{key: s.get(key) for key in ("name", "k", "alpha", "omega", "omega_small", "sb", "mean", "sd")}
            for s in (entry or {}).get("subscales") or []]


def _alpha(a):
    """A negative alpha (negative covariance, e.g. in small samples) is not a reliability; say so instead."""
    return f"α = {_de(a)}" if a >= 0 else "α negativ, nicht interpretierbar"


def _reliability(k, alpha, omega, sb, lead="", small=False):
    """The reliability of a scale for the draft: Spearman-Brown for two items (alpha says little there, Eisinga et
    al., 2013), else alpha and from three items omega; small: omega from fewer cases than scales.omega_small wants
    (the fit can end in a local optimum there), said next to it."""
    if k == 2:
        if sb is None:
            return []
        # a negative coefficient (negative correlation, mostly a statement not reversed) is no reliability: the
        # same sentence as for a negative alpha instead of e.g. "-6,80"
        return [f"Spearman-Brown-Koeffizient = {_de(sb)} (bei zwei Items statt α)" if sb >= 0
                else "Spearman-Brown-Koeffizient negativ, nicht interpretierbar (bei zwei Items statt α)"]
    return ([lead + _alpha(alpha)] if alpha is not None else []) + (
        [f"{'McDonalds ' if lead else ''}ω = {_de(omega)}" + (" (kleine Stichprobe, Schätzung instabil)" if small else "")]
        if omega is not None else [])


def _date(dt):
    return f"{to_local(dt):%d.%m.%Y}" if dt else "…"


def draft(f):
    """The method section from the facts alone (German), also the fallback without the AI."""
    parts = []
    period = (f"Die Datenerhebung erfolgte im Zeitraum vom {_date(f['start'])} bis {_date(f['end'])}"
              + (f" ({f['days']} Tage)" if f["days"] else "") + f" als Onlineumfrage über {brand.apply(SITE)}.")
    if f["running"]:
        period += " (Die Befragung läuft noch; das Ende ist das geplante Feldende.)"
    parts.append(period)
    if f.get("diary"):
        parts.append(f["diary"]["de"])
    sample = [f"Insgesamt begannen {f['started']} Personen die Befragung."]
    out = []
    if f["declined"]:
        out.append(f"{f['declined']} stimmten der Teilnahme nicht zu")
    if f["screenout"]:
        out.append(f"{f['screenout']} wurden durch Filterfragen ausgeschlossen (Screen-out)")
    if f.get("quotafull"):
        out.append(f"{f['quotafull']} wurden abgewiesen, weil ihre Quote bereits erfüllt war")
    if f.get("excluded"):
        out.append(f"{f['excluded']} wurden während der Befragung ausgeschlossen (Ende „Ausschluss“ im Ablauf, "
                   "z. B. nach nicht bestandenen Aufmerksamkeitsprüfungen)")
    if f["incomplete"]:
        out.append(f"{f['incomplete']} beendeten die Befragung nicht")
    if f.get("in_progress"):
        out.append(f"{f['in_progress']} füllten die Befragung zum Zeitpunkt des Berichts noch aus")
    if out:
        sample.append(", ".join(out[:-1]) + (" und " if len(out) > 1 else "") + out[-1] + ".")
    sample.append(f"{f['completed']} Personen schlossen die Befragung ab.")
    for q in f.get("quotas") or []:
        sample.append(f"Die Stichprobe war nach „{q['name']}“ quotiert ({len(q['cells'])} "
                      f"{'Teilquote' if len(q['cells']) == 1 else 'Teilquoten'}, {q['kind_de']}): Soll insgesamt {q['target']}, "
                      f"erreicht {q['done']}"
                      + (f" (ohne {q['marked']} von der Qualitätskontrolle markierte Abschlüsse"
                         + (f"; {q['freed']} davon wurden beim Abschluss markiert und gaben ihren Quotenplatz frei"
                            if q.get("freed") else "") + ")" if q.get("marked") else "")
                      + ".")
    if f.get("census"):
        c = f["census"]
        sample.append(f"Die Sollzahlen nach Alter, Geschlecht und Bundesland entsprechen der Verteilung der Bevölkerung "
                      f"{c['span_de']} in Deutschland am {c['date']} (Statistisches Bundesamt, Bevölkerungsfortschreibung auf "
                      f"Grundlage des Zensus 2022, Tabelle {c['table']}). Die amtliche Statistik weist dabei nur männlich und "
                      "weiblich aus; wer „divers“ oder „keine Angabe“ wählte, wurde nicht nach Geschlecht quotiert."
                      + (" Das Bundesland ergab sich aus der angegebenen Postleitzahl." if c.get("postal") else "")
                      + (" Das Bundesland ergab sich aus dem geteilten Standort des Geräts." if c.get("location") else "")
                      + (f" Die Bundesländer waren für die Quoten zu Regionen zusammengefasst ({c['regions_de']})."
                         if c.get("regions_de") else ""))
    if f.get("weighting"):
        sample.append(f["weighting"])
    if f["median_min"]:
        sample.append(f"Die mediane Bearbeitungsdauer betrug {_de(f['median_min'], 1)} Minuten.")
    if f.get("tempo"):
        t = f["tempo"]
        sample.append(f"Wer eine Seite schneller abschickte, als sich ihre Fragen lesen lassen ({t['ms']} ms je Wort), "
                      f"wurde {'einmal' if t['once'] else 'jedes Mal'} gebeten, die Antworten zu prüfen "
                      f"(vgl. Conrad et al., 2017); diesen Hinweis sahen {t['shown']} der {f['completed']} Personen.")
    if f.get("rsi"):
        sample.append(f"Als Speeder wurden Teilnahmen mit einem Relative Speed Index über {_de(f['rsi'], 1)} ausgeschlossen "
                      f"(n = {f['speeders']}; Leiner, 2019): Je Seite wurde die Median-Zeit der Seite durch die eigene Zeit "
                      f"geteilt, dieser Faktor auf höchstens 3 begrenzt und über die Seiten gemittelt. Gezählt wurden Seiten "
                      f"mit mindestens einer Frage und ohne Zeitvorgabe, die mindestens {export.RSI_MIN_N} Personen abschickten."
                      + (" Wer auf einer Seite nur einen Teil der Fragen, Aussagen oder Optionen sah, wurde mit den Personen "
                         "verglichen, die dieselbe Fassung sahen, sofern es davon genug gab." if f.get("paths_differ") else ""))
    elif f["speeder_pct"]:  # a live vote has no speeder rule (export.threshold)
        sample.append(f"Als Speeder wurden Teilnahmen mit einer Bearbeitungszeit unter {f['speeder_pct']} % des Medians"
                      + (f", also unter {_de(f['cutoff_min'], 1)} Minuten," if f["cutoff_min"] else "")
                      + f" ausgeschlossen (n = {f['speeders']}). Die Grenze ist eine eigene Festlegung; zur Bearbeitungszeit "
                      "als Hinweis auf sinnlose Daten vgl. Leiner (2019), der die Geschwindigkeit je Seite relativ zum "
                      "Median der Seite misst.")
    if f.get("quality"):
        q = f["quality"]
        sample.append(("Weitere Teilnahmen wurden ausgeschlossen, wenn sie " if q["min"] == 1 else
                       f"Weitere Teilnahmen wurden ausgeschlossen, wenn sie mindestens {q['min']} der folgenden "
                       "Auffälligkeiten zeigten: ") + (" oder ".join(q["texts"]) if q["min"] == 1 else "; ".join(q["texts"]))
                      + (" zeigten" if q["min"] == 1 else "") + f" (vgl. Curran, 2016; n = {f['marked_other']}).")
    sample.append(f"Die bereinigte Stichprobe umfasst N = {f['final']} Personen.")
    if f["age"] and f["age"].get("n"):
        a = f["age"]
        # one person has no standard deviation: the bracket is built from what there is
        inside = ([f"SD = {_de(a['sd'], 1)}"] if a.get("sd") is not None else []) + [f"{a['min']:g} bis {a['max']:g} Jahre"]
        sample.append(f"Die Teilnehmenden waren im Mittel {_de(a['mean'], 1)} Jahre alt ({', '.join(inside)}).")
    if f["gender"]:
        sample.append("Geschlecht: " + ", ".join(f"{label} {n}" for label, n in f["gender"]) + ".")
    parts.append(" ".join(sample))
    if f["constructs"]:
        lines = []
        for c in f["constructs"]:
            s = f"{c['name']}" + (f" ({c['authors']}, {c['year']})" if c["authors"] and c["year"] else "")
            details = []
            if c["items"]:
                details.append(f"{c['items']} Items")
            if c["points"]:
                details.append(f"{c['points']}-stufige Skala")
            if c["subscales"]:  # reliability and scores per subscale, never over the mixed item
                for sub in c["subscales"]:
                    vals = [f"{sub['k']} Items"] + _reliability(sub["k"], sub["alpha"], sub["omega"], sub.get("sb"), small=sub.get("omega_small")) \
                        + ([f"M = {_de(sub['mean'])}" + (f", SD = {_de(sub['sd'])}" if sub["sd"] is not None else "")]
                           if sub["mean"] is not None else [])
                    details.append(f"Subskala {sub['name']} ({', '.join(vals)})")
            else:
                details += _reliability(c["items"], c["alpha"], c.get("omega"), c.get("sb"), lead="Cronbachs ",
                                        small=c.get("omega_small"))
                if c["mean"] is not None:
                    details.append(f"M = {_de(c['mean'])}" + (f", SD = {_de(c['sd'])}" if c["sd"] is not None else ""))
            if c["modified"]:
                details.append("gegenüber der validierten Fassung angepasst")
            lines.append(s + (f": {'; '.join(details)}" if details else ""))
        parts.append("Erhoben wurden unter anderem folgende Konstrukte: " + ". ".join(lines) + ".")
    if f.get("choice"):
        parts.append(" ".join(_choice_de(c) for c in f["choice"]))
    if f.get("location"):
        parts.append(" ".join(location_de(loc) for loc in f["location"]))
    if f.get("voice"):
        parts.append(" ".join(voice_de(v) for v in f["voice"]))
    if f.get("tasks"):
        parts.append(" ".join(t["text_de"] for t in f["tasks"]))
    if f.get("vignettes"):
        parts.append(" ".join(f["vignettes"]))
    return "\n\n".join(parts)  # the domain's name only in SITE above, never in the researcher's wording (M1)


LENGTHS = {"short": "kurz", "medium": "mittel", "long": "lang"}
LENGTHS_EN = {"short": "short", "medium": "medium", "long": "long"}


def lengths():
    """LENGTHS in the language of the page (the keys stay)."""
    from app.lang import english

    return LENGTHS_EN if english() else LENGTHS

INSTRUCTIONS = """You write the method section of an empirical paper (data collection, sample, instruments) for the
online survey described by the facts below. The survey ran on the platform Askurious.de.

Language: {language}. Style: APA 7, past tense, neutral and precise, plain prose without headings, lists or markdown,
no introduction and no closing remarks.
{conventions}

Use ONLY the facts given. Never invent numbers, dates, sources, items, procedures, software or analyses; leave out
what is not given (e.g. no demographics if none are listed). Report numbers exactly as given (rounding to the
precision shown is fine). Cite instruments as author-year in the text; do not add a reference list.

Start with this sentence, with the facts filled in, close to verbatim:
"{opening}"

Content by length ({length}):
- short (about 80 to 120 words, one paragraph): field time and platform, final N with the speeder rule in one
  clause, demographics if given, and the instruments by name and source with their reliability.
- medium (about 150 to 250 words, two or three paragraphs): as short, plus the full sample flow (started, not
  consented, screened out, not finished, completed, speeders with the rule given: below the given percentage of
  the median completion time with median and cut-off in minutes, or the relative speed index above its limit,
  final N) and for each instrument the number of items and
  the response scale.
- long (about 300 to 450 words, three or four paragraphs): as medium, plus means and standard deviations of the
  scales, every subscale separately, and whether an instrument was adapted from its validated version.
Reliability: when subscales are given, report alpha per subscale only, never one alpha for the whole instrument; say
that alpha is of limited meaning for two-item scales and report the Spearman-Brown coefficient given for them instead
(Eisinga, te Grotenhuis & Pelzer, 2013); a negative alpha is reported as not interpretable. Where
McDonald's omega is given, report it next to alpha ("α = .., ω = .."). Choice experiments (if given): in medium
and long, their design and estimation as given, with the sources named there. If the
survey is still running, say that the reported end is the planned end of the field time."""

LANG = {
    "de": {"language": "German", "opening": "Die Datenerhebung erfolgte im Zeitraum vom {start} bis {end} ({days} Tage) als Onlineumfrage über Askurious.de.",
           "conventions": "German conventions: decimal comma, dates as DD.MM.YYYY, \"N =\", \"M =\", \"SD =\", \"α =\", \"Teilnehmende\"."},
    "en": {"language": "English", "opening": "Data were collected from {start} to {end} ({days} days) as an online survey on Askurious.de.",
           "conventions": "English conventions: decimal point, dates as in APA (e.g. September 30, 2026), \"N =\", \"M =\", \"SD =\", \"α =\", \"participants\"."},
}


def _fmt_date(dt, lang):
    if not dt:
        return "…"
    d = to_local(dt)
    return f"{d:%d.%m.%Y}" if lang in ("de", "de-du") else f"{d:%B} {d.day}, {d:%Y}"


def _sb_en(sb, r):
    return "negative (not interpretable)" if sb < 0 else r(sb)


def _small_en(scale):
    return " (small sample: unstable estimate, say so)" if scale.get("omega_small") else ""


def facts_text(f):
    """All facts as a plain list for the AI (decimal points; the AI applies the language's conventions)."""
    # half up like the page and the German draft (units.fixed): never .84 here where they show .85
    r = lambda x, p=2: None if x is None else units.round_half_up(x, p)
    lines = [brand.apply("Platform: Askurious.de (online survey)"),
             f"Field time: first start {_fmt_date(f['start'], 'de')}, end {_fmt_date(f['end'], 'de')}"
             + (" (planned end, still running)" if f["running"] else "") + (f", {f['days']} days" if f["days"] else ""),
             f"Started: {f['started']}", f"Did not consent: {f['declined']}", f"Screened out: {f['screenout']}",
             f"Turned away because their quota was full: {f.get('quotafull', 0)}",
             *([f"Excluded during the survey by an exclusion end in the flow (e.g. after failed attention checks): "
                f"{f['excluded']}"] if f.get("excluded") else []),
             *[f"Quota \"{q['name']}\" ({q['kind']}): target {q['target']}, reached {q['done']}"
               + (f" (not counting {q['marked']} completions marked by quality control"
                  + (f"; {q['freed']} of them were marked at completion and gave their quota place back" if q.get("freed") else "")
                  + ")" if q.get("marked") else "")
               + "; cells (name: reached/target): "
               + "; ".join(f"{name}: {done}/{target}" for name, target, done in q["cells"][:40])
               for q in f.get("quotas") or []],
             *([f"Quota targets by age, gender and federal state follow the population of Germany {f['census']['span']} on "
                f"{f['census']['date']} (Statistisches Bundesamt, population update based on the 2022 census, table "
                f"{f['census']['table']}); official figures know male and female only, so participants answering \"divers\" or "
                "\"prefer not to disclose\" were not quota-controlled by gender"] if f.get("census") else []),
             f"Did not finish: {f['incomplete']}",
             *([f"Still filling it in at the time of the report: {f['in_progress']}"] if f.get("in_progress") else []),
             f"Completed: {f['completed']}",
             f"Median completion time: {r(f['median_min'], 1)} minutes" if f["median_min"] else "Median completion time: unknown",
             (f"Speeder rule: relative speed index above {r(f['rsi'], 1)} (Leiner, 2019: per page the median time of the "
              "page divided by the participant's time, each factor capped at 3, averaged over the pages; pages with at "
              f"least one question, without a time limit, sent by at least {export.RSI_MIN_N} participants"
              + ("; a page of which only part was shown compared with those who saw the same version, where there were "
                 "enough" if f.get("paths_differ") else "") + f"); excluded: {f['speeders']}"
              if f.get("rsi") else
              f"Speeder rule: completion time below {f['speeder_pct']} % of the median"
              + (f", i.e. below {r(f['cutoff_min'], 1)} minutes" if f["cutoff_min"] else "") + f" (own threshold; completion time as an indicator of meaningless data: Leiner, 2019, who relates the time "
              f"per page to the median of the page); excluded: {f['speeders']}"),
             *([f"Further exclusion rule (Curran, 2016, multiple hurdles): at least {f['quality']['min']} of: "
                + "; ".join(f["quality"]["en"]) + f"; excluded besides the speeders: {f['marked_other']}"] if f.get("quality") else []),
             f"Final sample: N = {f['final']}",
             *([f["weighting_en"]] if f.get("weighting_en") else [])]
    if f.get("tempo"):
        t = f["tempo"]
        lines.append(f"Speed prompt during the survey: pages sent faster than {t['ms']} ms per word of their question texts "
                     f"were shown once more with a note asking to check the answers, "
                     f"{'once per participation' if t['once'] else 'every time'} (Conrad et al., 2017); "
                     f"{t['shown']} of the {f['completed']} completed participations saw it")
    if not f.get("look_plain", True):
        lines.append(f"Look of the survey pages: {f['look']['en']} (mention only in the long version, in one sentence)")
    if f["age"] and f["age"].get("n"):
        a = f["age"]
        lines.append(f"Age (years, n = {a['n']}): M = {r(a['mean'], 1)}"
                     + (f", SD = {r(a['sd'], 1)}" if a.get("sd") is not None else "") + f", range {a['min']:g} to {a['max']:g}")
    if f["gender"]:
        lines.append("Gender: " + ", ".join(f"{label} {n}" for label, n in f["gender"]))
    if not (f["age"] and f["age"].get("n")) and not f["gender"]:
        lines.append("Demographics: none recorded in this survey (do not mention age or gender)")
    for c in f["constructs"]:
        line = f"Instrument: {c['name']}"
        if c["authors"] and c["year"]:
            line += f" ({c['authors']}, {c['year']})"
        parts = [f"{c['items']} items" if c["items"] else "", f"{c['points']}-point scale" if c["points"] else "",
                 "adapted from the validated version" if c["modified"] else ""]
        if c["subscales"]:
            for s in c["subscales"]:
                alpha, mean, sd, omega = s["alpha"], s["mean"], s["sd"], s["omega"]
                parts.append(f"subscale {s['name']}: {s['k']} items"
                             + (f", Spearman-Brown coefficient {_sb_en(s.get('sb'), r)} (two items: report it instead of alpha)"
                                if s["k"] == 2 and s.get("sb") is not None else "")
                             + ("" if alpha is None or s["k"] == 2 else f", alpha {'negative (not interpretable)' if alpha < 0 else r(alpha)}")
                             + ("" if omega is None else f", McDonald's omega {r(omega)}" + _small_en(s))
                             + (f", M = {r(mean)}" + (f", SD = {r(sd)}" if sd is not None else "") if mean is not None else ""))
        else:
            if c["items"] == 2 and c.get("sb") is not None:
                parts.append(f"Spearman-Brown coefficient {_sb_en(c['sb'], r)} (two items: report it instead of alpha)")
            elif c["alpha"] is not None:
                parts.append(f"alpha {'negative (not interpretable)' if c['alpha'] < 0 else r(c['alpha'])}")
            if c.get("omega") is not None:
                parts.append(f"McDonald's omega {r(c['omega'])}" + _small_en(c))
            if c["mean"] is not None:
                parts.append(f"M = {r(c['mean'])}" + (f", SD = {r(c['sd'])}" if c["sd"] is not None else ""))
        lines.append(line + ": " + "; ".join(p for p in parts if p) + (f". Full citation: {c['citation']}" if c["citation"] else ""))
    p = f.get("plan")
    if p:  # the study plan (V29): the hypotheses belong in the methods section, with the test planned for each
        if p["question"]:
            lines.append(f"Research question (as planned; quote or paraphrase): {p['question']}")
        if p.get("model_en"):
            lines.append(f"Planned sample (usable participations · completions · expected exclusions): {p['model_en']}")
        if p["target"]:  # the stored target, also beside the model of the power analysis (audit 2026-10-10, B-18)
            lines.append(f"Target sample size (completions, set in the research plan): {p['target']}")
        if p["recruitment_en"]:
            lines.append(f"Recruitment: {p['recruitment_en']}")
        if p["rules"]:
            lines.append(f"Exclusion rules set in advance (wording of the researcher, German): {p['rules']}")
        for i, h in enumerate(p["hypotheses"], 1):
            lines.append(f"Hypothesis H{i} (wording of the researcher, translate if needed): {h['text']}"
                         + (f"; planned test: {h['test_en']}" if h["test"] else "") + ("; exploratory, added after the start" if h["explorative"] else ""))
    if f.get("diary"):
        lines.append(f["diary"]["en"])
    lines += [_choice_en(c) for c in f.get("choice") or []]
    lines += [location_en(loc) for loc in f.get("location") or []]
    lines += [voice_en(v) for v in f.get("voice") or []]
    lines += [t["text_en"] for t in f.get("tasks") or []]
    lines += f.get("vignettes_en") or []
    return "\n".join(lines)  # hypotheses, rules and recruitment as the researcher wrote them (M1)


def ai_text(f, lang="de", length="medium"):
    from app import ai

    lang = lang if lang in LANG else "de"
    length = length if length in LENGTHS else "medium"
    L = LANG[lang]
    opening = L["opening"].format(start=_fmt_date(f["start"], lang), end=_fmt_date(f["end"], lang), days=f["days"] or "…")
    instructions = brand.apply(INSTRUCTIONS.format(language=L["language"], conventions=L["conventions"], opening=opening,
                                                   length=length))
    return ai.generate_text(instructions, purpose="methods", prompt="Facts (use exactly these):\n" + facts_text(f))


def reliability_sources(f):
    """The sources of the reliability figures on the page: alpha and omega, and Spearman-Brown where an instrument
    or a subscale has two items."""
    from app import scales

    two = any(c["items"] == 2 and not c["subscales"] or any(s["k"] == 2 for s in c["subscales"]) for c in f["constructs"])
    return [*scales.SOURCES, *([scales.EISINGA] if two else [])]
