"""The minimum age as a screen-out (pre-test 2026-10-16: "Die Altersangabe schmeißt keine Teilnehmer unter der
vorgegebenen Altersgrenze raus").

"Mindestalter" in the key facts of the consent only adds the statement "Ich bin mindestens … Jahre alt" that people
tick; nothing compares an answer with it. Whoever wants younger people really ended needs a question for the age
and an end in the flow. This module builds that end at a click: behind the block of the age question (the construct
"Alter", or a question with the variable name age/alter) a condition "jünger als <Mindestalter>" with an end of the
kind "screenout" (Screen-out), both marked by MARK so that they can be found, follow a changed minimum age and be
taken out again.
"""
from app import flow
from app.lang import de_en

MARK = "min_age"


def age_item(survey):
    """The first question for the age in years in the order of the flow, or None."""
    from app.groupvars import _is_age

    for block in sorted(survey.blocks, key=lambda b: b.position):
        for item in sorted(block.items, key=lambda i: i.position):
            if item.type == "text_input" and _is_age(item):
                return item
    return None


def node(survey):
    """The condition this module built, or None."""
    return next((n for n, _, _ in flow.walk(flow.tree(survey)) if n["t"] == "branch" and n.get("by") == MARK), None)


def _label(age):
    return de_en("Jünger als {n}", "Younger than {n}", n=age)


def apply(survey, age):
    """Build (or move and update) the screen-out behind the age question; False without an age question or age."""
    import copy

    item = age_item(survey)
    if item is None or not age:
        return False
    root = copy.deepcopy(flow.normalize(survey))
    _drop(root)
    block, parent = flow.find(root, f"k{item.block_id}")
    if block is None:
        return False
    branch = {"t": "branch", "id": flow._nid("b"), "name": _label(age), "by": MARK,
              "cond": {"item": item.id, "op": "lt", "value": str(int(age))},
              "children": [{"t": "end", "id": flow._nid("e"), "text": {}, "kind": "screenout", "by": MARK}]}
    parent["children"].insert(parent["children"].index(block) + 1, branch)
    survey.flow = root
    flow.normalize(survey)
    return True


def follow(survey, age):
    """A changed minimum age: the screen-out built here takes the new value; without a minimum age it goes."""
    if node(survey) is None or not survey.is_editable:
        return
    if age:
        apply(survey, age)
    else:
        remove(survey)


def remove(survey):
    import copy

    root = copy.deepcopy(flow.tree(survey))
    if _drop(root):
        survey.flow = root
        flow.normalize(survey)


def _drop(root):
    """Take the marked condition (with its end) out of the tree; whatever else stands in it stays in its place."""
    for n, parent, _ in list(flow.walk(root)):
        if n["t"] == "branch" and n.get("by") == MARK and parent is not None:
            at = parent["children"].index(n)
            kept = [c for c in n.get("children", []) if not (c["t"] == "end" and c.get("by") == MARK)]
            parent["children"][at:at + 1] = kept
            return True
    return False
