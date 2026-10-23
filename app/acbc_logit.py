"""Aggregate logit model for the adaptive conjoint (ACBC), see app/choice_model.py.

Choice sets per person:
  screening   every rated concept is a choice between the concept and a threshold alternative
              ("not possible"): P(possible) = exp(V) / (exp(V) + exp(threshold))
  tournament  every decided task is a choice among its two or three concepts
The BYO step and the rule questions are not part of the model (the rules act through the concepts
that were shown). With the summed price the concept's price enters linearly; the levels then carry
only their utility beyond their price component.
"""
from app.choice_model import Coding, ChoiceSet, price_scale, summarise


def choice_sets(item, answers):
    """The choice sets of all persons, the coding, the number of persons and all prices shown."""
    kind = item.kind
    prices, plans = [], []
    for answer in answers:
        if not kind.byo_profile(item, answer) or not answer.get("s"):
            continue
        plan = kind.plan(item, answer)
        plans.append((answer, plan))
        prices += [x["price"] for x in plan.concepts if x.get("price") is not None]
    scale = price_scale(prices) if kind.price_on(item) else None
    coding = Coding(kind.level_counts(item), scale, asc=True, price_center=sum(prices) / len(prices) if scale else 0.0)
    sets, persons, judged = [], 0, []
    for p, (answer, plan) in enumerate(plans):
        s = answer.get("s") or {}
        used = False
        for i, concept in enumerate(plan.concepts):
            if concept.get("auto") or s.get(str(i)) is None:
                continue
            X = [coding.row(concept["p"], concept.get("price")), coding.asc_row()]
            sets.append(ChoiceSet(X, 0 if s[str(i)] == 1 else 1, person=p))
            judged.append(concept.get("price"))
            used = True
        for _, group, chosen in kind.tournament(item, answer, plan)[0]:
            if chosen is None:
                continue
            X = [coding.row(plan.concepts[c]["p"], plan.concepts[c].get("price")) for c in group]
            sets.append(ChoiceSet(X, group.index(chosen), person=p))
            judged += [plan.concepts[c].get("price") for c in group]
            used = True
        persons += used
    # the price range for the importance: only prices of concepts that entered the model
    return sets, coding, persons, [x for x in judged if x is not None]


def estimate(item, answers, lang):
    """The aggregate model for the results page (dict), or None without any choice data."""
    sets, coding, persons, prices = choice_sets(item, answers)
    if not sets:
        return None
    return summarise(sets, coding, item.kind.attributes(item, lang), persons, prices,
                     price_wanted=item.kind.price_on(item), constant="Die Schwelle „kommt nicht infrage“")
