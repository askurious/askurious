"""Results of the choice-based conjoint (CBC): counts per level and an aggregate logit.

Counts over all answered tasks of the persons in the data pool: per level how often it was shown in
an alternative, how often that alternative was chosen, and the choice share (chosen / shown); with
the "none" option the share of tasks answered with it, with dual response the share of follow-up questions
answered "no". Counts are descriptive; in a balanced design
they approximate the utilities, but they do not control for the other levels in a task.

The model (app/choice_model.py) is one conditional logit over all tasks: the alternatives of a task,
plus the "none" alternative with its own constant if offered. Dual response (Brazell et al., 2006) is coded as
the exact likelihood of the two questions: "would choose it" gives one choice among the options and "none" (the
constant); "would not" gives two, the forced choice among the options and "none" chosen among the options and
"none"; without an answer to the follow-up question only the forced choice. If no follow-up question was answered at
all, the model has no constant. An attribute marked "price (numbers)" enters linearly with the number in its level
labels, which gives a willingness to pay per level.

Input is the wide export table (export.rows), so the data pool and speeder filter of the results
page apply; the choices and design versions are read from the responses of the persons in it (by pid).
"""
from app.lang import de_en
from app.choice_model import Coding, ChoiceSet, charts, once, price_scale, responses_for, summarise


def tasks_of(item, response):
    """[(task, [profiles of the alternatives], choice, again)] of one response; choice = 1..J, 0 = none; again: with
    dual response 1 (would really choose it) or 0, else None. A level None in a profile is an attribute not shown
    (partial profiles); an alternative None at the end of a task was not offered there (tasks with fewer concepts,
    adaptive designs) and is left out, so the choice still counts from the first alternative."""
    kind = item.kind
    state = ((response.layout or {}).get("cj") or {}).get(str(item.id))
    answer = (response.answers or {}).get(str(item.id))
    if not state or "version" not in state or not isinstance(answer, dict) or not kind.design_current(item):
        return []
    if not kind.version_known(item, state["version"]):
        return []  # a version the current design does not have (counted in skipped())
    v = state["version"]
    dual = kind.dual(item)
    with_none = bool(item.config.get("none"))
    alternatives = item.config.get("alternatives", 0)
    out = []
    for t in range(item.config.get("tasks", 0)):
        choice = answer.get(f"t{t}")
        if choice is None or (dual and choice == 0):
            continue
        # "Keine davon" switched off after answers with it, or a choice the design does not have: left out, not read
        # as another alternative (code review 2026-10-15, C5)
        if isinstance(choice, float) and choice.is_integer():
            choice = int(choice)
        if isinstance(choice, bool) or not isinstance(choice, int) or not (0 if with_none else 1) <= choice <= alternatives:
            continue
        profiles = [kind.profile(item, v, t, a) for a in range(item.config["alternatives"])]
        while profiles and profiles[-1] is None:  # not offered in this task
            profiles.pop()
        if choice and choice > len(profiles):
            continue
        again = answer.get(f"dt{t}") if dual else None
        out.append((t, profiles, choice, again if again in (0, 1) else None))
    return out


def skipped(item, responses):
    """Persons with answers whose design version the current design does not have (left out)."""
    if not item.kind.design_current(item):
        return 0
    n = 0
    for r in responses:
        state = ((r.layout or {}).get("cj") or {}).get(str(item.id)) or {}
        if "version" in state and isinstance((r.answers or {}).get(str(item.id)), dict)                 and not item.kind.version_known(item, state["version"]):
            n += 1
    return n


def counts(item, responses, lang):
    kind = item.kind
    attrs = kind.attributes(item, lang)
    cells = [[{"shown": 0, "chosen": 0} for _ in a["levels"]] for a in attrs]
    persons = tasks = none = asked = declined = 0
    for r in responses:
        done = tasks_of(item, r)
        if not done:
            continue
        persons += 1
        for _, profiles, choice, again in done:
            tasks += 1
            none += choice == 0
            asked += again is not None
            declined += again == 0
            for alt, profile in enumerate(profiles, start=1):
                for a, lv in enumerate(profile):
                    if lv is None:  # not shown (partial profiles)
                        continue
                    cells[a][lv]["shown"] += 1
                    cells[a][lv]["chosen"] += choice == alt
    rows = [{"code": a["code"], "label": a["label"],
             "levels": [{**cells[n][lv], "label": label, "share": cells[n][lv]["chosen"] / cells[n][lv]["shown"]
                         if cells[n][lv]["shown"] else None} for lv, label in enumerate(a["level_labels"])]}
            for n, a in enumerate(attrs)]
    return {"persons": persons, "tasks": tasks, "none_offered": bool(item.config.get("none")),
            "skipped": skipped(item, responses), "none": none, "none_share": none / tasks if tasks else None,
            "dual": kind.dual(item), "asked": asked, "declined": declined,
            "declined_share": declined / asked if asked else None, "attributes": rows}


def choice_sets(item, responses, price_in_levels=False):
    """The choice sets of all persons, the coding, the number of persons and the prices shown. price_in_levels: the
    price attribute with a part-worth per level like the others (for the test of its linearity)."""
    kind = item.kind
    pa = kind.price_attribute(item)
    numbers = kind.level_numbers(item, pa) if pa is not None else None
    scale = price_scale(numbers) if numbers and not price_in_levels else None
    linear = (pa,) if scale else ()
    with_none = bool(item.config.get("none"))
    dual = kind.dual(item)
    done_of = [tasks_of(item, r) for r in responses]
    # dual response without a single answered follow-up question: no set holds "none", the constant is not estimated
    # (it would only be set by the ridge bound and give "none" an arbitrary share in the simulator; audit 2026-10-06, W-2)
    answered = dual and any(again is not None for done in done_of for *_, again in done)
    coding = Coding(kind.level_counts(item), scale, asc=with_none or answered, linear=linear,
                    price_center=sum(numbers) / len(numbers) if scale else 0.0)
    sets, persons, prices = [], 0, []
    for p, done in enumerate(done_of):
        if not done:
            continue
        persons += 1
        for _, profiles, choice, again in done:
            X = []
            for profile in profiles:
                price = numbers[profile[pa]] if scale and profile[pa] is not None else None
                if price is not None:
                    prices.append(price)
                X.append(coding.row(profile, price))
            if with_none:
                X.append(coding.asc_row())
            if dual and again is not None:
                # "would choose it" is one choice among the options and
                # "none"; "would not" is the forced choice and the choice of "none" among the options and "none".
                # This is the exact likelihood of the random utility model behind the two questions (audit
                # 2026-10-06, W-1); a choice between the chosen option and "none" alone made the constant too small
                full = X + [coding.asc_row()]
                if again == 1:
                    sets.append(ChoiceSet(full, choice - 1, person=p))
                else:
                    sets.append(ChoiceSet(X, choice - 1, person=p))
                    sets.append(ChoiceSet(full, len(X), person=p))
                continue
            sets.append(ChoiceSet(X, len(profiles) if choice == 0 else choice - 1, person=p))
    return sets, coding, persons, prices


def table_sets(item, table, responses=None):
    """choice_sets() of the persons in the export table, built once per table (model and HB status)."""
    return once(table, ("cbc", item.id),
                lambda: choice_sets(item, responses_for(item, table) if responses is None else responses))


def estimate(item, responses, lang, built=None):
    sets, coding, persons, prices = built or choice_sets(item, responses)
    if not sets:
        return None
    kind = item.kind
    pa = kind.price_attribute(item)
    result = summarise(sets, coding, kind.attributes(item, lang), persons, prices, constant="„Keine davon“")
    result["center"] = "levels"  # the price is centred on the mean of its levels, not of the prices shown (C-3)
    if kind.dual(item) and coding.asc is None:
        result["warnings"].append(de_en("Die Nachfrage (Dual Response) wurde nie beantwortet: Ein Anteil „Keine“ ist "
                                        "nicht bestimmbar. Das Modell hat deshalb keine Konstante, und der "
                                        "Marktsimulator bietet „Keine davon“ nicht an.",
                                        "The follow-up question (dual response) was never answered: no share of "
                                        "“none” can be determined. The model therefore has no constant, and the market "
                                        "simulator does not offer “None of these”."))
    if pa is not None and coding.price is not None and result["price"]:
        # the linear price as zero-centred part-worths of its shown levels: beta x (price - mean price)
        numbers = kind.level_numbers(item, pa)
        mean = sum(numbers) / len(numbers)
        b, se = result["price"]["beta"], result["price"]["se"]
        labels = kind.attributes(item, lang)[pa]["level_labels"]
        result["price_levels"] = {"label": kind.attributes(item, lang)[pa]["label"], "levels": [
            {"label": label, "beta": b * (p - mean), "se": abs(p - mean) * se if se is not None else None}
            for label, p in zip(labels, numbers)]}
        result["charts"] = charts(result)
    if pa is not None and coding.price is not None and result["price"]:
        result["price_linear"] = price_linearity(item, responses, result, persons)
    if pa is not None and coding.price is None:
        result["warnings"].append(de_en("Das Preis-Merkmal geht wie die übrigen Merkmale mit Stufen ein: Seine Stufen sind "
                                        "keine unterschiedlichen Zahlen.",
                                        "The price attribute enters with levels like the other attributes: its levels are "
                                        "not distinct numbers."))
    return result


def price_linearity(item, responses, linear, persons):
    """Likelihood ratio test of the linear price against a part-worth per price level (the linear model is nested:
    the part-worths on a straight line), df = levels - 2 (audit 2026-10-05, M-6): a price between two levels in the
    market simulator is only as good as the straight line. {"chi2", "df", "p"} or None with fewer than three distinct
    price levels, without the persons' answers or when one of the models needed the ridge bound (its likelihood would
    not be the maximum)."""
    from app import group_tests as gt
    from app.choice_model import fit

    numbers = item.kind.level_numbers(item, item.kind.price_attribute(item))
    if not responses or linear.get("ridge") or not linear.get("converged") or len(set(numbers)) < 3 \
            or len(set(numbers)) != len(numbers):
        return None
    sets, coding, _, _ = choice_sets(item, responses, price_in_levels=True)
    if not sets:
        return None
    _, _, ll, converged, _ = fit(sets, coding.k)
    if not converged:
        return None
    chi2 = max(2 * (ll - linear["loglik"]), 0.0)
    df = len(numbers) - 2
    return {"chi2": chi2, "df": df, "p": gt.chi2_sf(chi2, df)}


def results(item, table, lang):
    responses = responses_for(item, table)
    return {"counts": counts(item, responses, lang),
            "model": estimate(item, responses, lang, table_sets(item, table, responses))}
