"""Counting analysis of the adaptive conjoint (ACBC) for the results page.

Per attribute level over all persons of the chosen data pool:
  byo      share of persons who put the level into their preferred option (BYO)
  screen   how often it was shown in the screening, and the share of those concepts marked
           "possible" (concepts rejected automatically because of a rule are not counted as shown)
  nogo     how often it was offered as unacceptable, and the share confirmed
  must     how often it was asked as a must-have, and the share confirmed
  win      share of persons whose final choice has the level (tournament winner, the only possible
           concept, or the BYO profile if nothing was possible; see ACBC.winner)
Plus the number of persons, possible and automatically rejected concepts per person, where the
winner came from, and with a summed price the price limits and prices.

These are descriptive counts, not utilities: a high screening share can also come from the BYO
neighbourhood. Choice models (MNL, hierarchical Bayes) use the long-format export.

Input is the wide export table (export.rows), so the data pool and speeder filter of the results
page apply; the answers themselves are read from the responses of the persons in it (by pid).
"""
import statistics


def _share(k, n):
    return k / n if n else None


def _describe(values):
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": statistics.median(xs),
            "min": min(xs), "max": max(xs)}


def answers_for(item, table):
    """The stored ACBC answers of the persons in the table (in table order)."""
    from app.choice_model import responses_for

    answers = [(r.answers or {}).get(str(item.id)) for r in responses_for(item, table)]
    return [a for a in answers if isinstance(a, dict) and a.get("concepts")]


def analyse(item, answers, lang):
    kind = item.kind
    attrs = kind.attributes(item, lang)
    cells = [[{"byo": 0, "shown": 0, "accepted": 0, "nogo_offered": 0, "nogo": 0, "must_asked": 0, "must": 0,
               "wins": 0} for _ in a["levels"]] for a in attrs]
    persons = screened = winners = ruled = 0
    possible, auto, tour = [], [], []
    sources = {"tour": 0, "single": 0, "byo": 0}
    caps, cap_asked, cap_none, byo_prices, win_prices = [], 0, 0, [], []
    for answer in answers:
        byo = kind.byo_profile(item, answer)
        if not byo:
            continue
        persons += 1
        for a, lv in enumerate(byo):
            cells[a][lv]["byo"] += 1
        if answer.get("byo_price") is not None:
            byo_prices.append(answer["byo_price"])
        s = answer.get("s") or {}
        if not s:
            continue
        screened += 1
        plan = kind.plan(item, answer)
        for i, concept in enumerate(plan.concepts):
            if concept.get("auto") or s.get(str(i)) is None:
                continue
            for a, lv in enumerate(concept["p"]):
                cells[a][lv]["shown"] += 1
                cells[a][lv]["accepted"] += s[str(i)] == 1
        stored = answer.get("rules") or {}
        for key, q in plan.questions.items():
            if stored.get(key) is None:
                continue  # not answered yet, or left open
            if q["phase"] == "nogo":
                chosen = {kind.parse_ref(item, x) for x in stored[key] or []}
                for a, lv in q["options"]:
                    cells[a][lv]["nogo_offered"] += 1
                    cells[a][lv]["nogo"] += (a, lv) in chosen
            elif q["phase"] == "must":
                a, lv = q["level"]
                cells[a][lv]["must_asked"] += 1
                cells[a][lv]["must"] += bool((stored[key] or {}).get("yes"))
            elif q["phase"] == "price":
                cap_asked += 1
                cap = (stored[key] or {}).get("cap")
                if cap is None:
                    cap_none += 1
                else:
                    caps.append(cap)
        # a confirmed rule (unacceptable level, must-have, price limit) acts only through the concepts shown after it,
        # it is no observation of its own in the models (acbc_logit; audit data storage and calculations 2026-10-21, D3)
        ruled += bool(plan.nogo or plan.must or plan.cap is not None)
        possible.append(len(kind.possible(item, answer, plan)))
        auto.append(sum(1 for x in plan.concepts if x.get("auto")))
        rounds, _, _ = kind.tournament(item, answer, plan)
        tour.append(sum(1 for _, _, chosen in rounds if chosen is not None))
        win, source = kind.winner(item, answer)
        if win:
            winners += 1
            sources[source] += 1
            for a, lv in enumerate(win["p"]):
                cells[a][lv]["wins"] += 1
            if win.get("price") is not None:
                win_prices.append(win["price"])
    rows = []
    for a, attr in enumerate(attrs):
        levels = []
        for lv, label in enumerate(attr["level_labels"]):
            c = cells[a][lv]
            levels.append({**c, "label": label, "byo_share": _share(c["byo"], persons),
                           "accept_rate": _share(c["accepted"], c["shown"]),
                           "nogo_rate": _share(c["nogo"], c["nogo_offered"]),
                           "must_rate": _share(c["must"], c["must_asked"]),
                           "win_share": _share(c["wins"], winners)})
        rows.append({"code": attr["code"], "label": attr["label"], "levels": levels})
    price = None
    if kind.price_on(item):
        price = {"asked": cap_asked, "no_limit": cap_none, "caps": _describe(caps),
                 "byo": _describe(byo_prices), "win": _describe(win_prices)}
    return {"persons": persons, "screened": screened, "winners": winners, "sources": sources, "ruled": ruled,
            "possible": _describe(possible), "auto": _describe(auto), "tour": _describe(tour),
            "rules": kind.rules_on(item), "attributes": rows, "price": price}
