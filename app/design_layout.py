"""The study design lays out the questionnaire (user test 2026-10-27, point 2: "Experiment mit Zufallszuteilung"
did nothing to the questionnaire).

When a draft's study plan changes its design to one that needs a structure in the flow, and the flow has none of
that kind yet, Askurious puts it there once:

- "experiment": two blocks "Bedingung A" and "Bedingung B" in a randomisation that shows each person one of them;
- "within" (Messwiederholung): the same two blocks, each person gets both in random order;
- "vignettes": a vignette loop (factors, app/vignettes.py) around a block "Vignette".

The group goes before the first empty block after the questions so far (the "Block 1" of a new survey, which then
takes the questions after the condition), else at the end of the regular part. Nothing is laid out after the start,
when the design is set back or changed again, or when the flow already has a randomisation (or a loop): the
researchers then built it themselves.
"""
import copy

from app import flow
from app.lang import de_en
from app.models import Block

KINDS = {"experiment": "rand", "within": "rand", "vignettes": "loop"}


def _title(survey, de, en):
    """A block title in every language of the survey: German for German, English for the rest."""
    return {lang: de if lang == "de" else en for lang in (survey.languages or ["de"])}


def _new_block(survey, de, en):
    block = Block(position=len(survey.blocks), titles=_title(survey, de, en))
    survey.blocks.append(block)
    return block


def _first_empty(survey, root):
    """The node id of the first empty block at the top level of the flow that is not the consent, else None."""
    consent = survey.consent_item
    by_id = {b.id: b for b in survey.blocks}
    for child in root["children"]:
        if child["t"] == "split":
            return None
        if child["t"] == "block":
            block = by_id.get(child["block"])
            if block is not None and not block.items and (consent is None or block.id != consent.block_id):
                return flow.node_id(child)
    return None


def lay_out(survey, before, after):
    """Put the structure of the design `after` into the flow of `survey` (see the module); `before` is the design
    the plan had. Returns a sentence saying what was laid out, or None. Flushes nothing itself: call inside the
    request that saves the plan."""
    from app.extensions import db

    kind = KINDS.get(after)
    if survey.status != "draft" or kind is None or before == after or not survey.id:
        return None
    root = flow.normalize(survey)
    if any(n["t"] == kind for n, _, _ in flow.walk(root)):
        return None
    if kind == "rand":
        blocks = [_new_block(survey, "Bedingung A", "Condition A"), _new_block(survey, "Bedingung B", "Condition B")]
        db.session.flush()
        root = flow.normalize(survey)
        empty = _first_empty(survey, root)  # before the new blocks took their place at the end
        node = flow.add_node(survey, "rand")
        node_id = node["id"]
        both = after == "within"
        tree = copy.deepcopy(flow.tree(survey))  # a new value, so that the JSON column is stored
        stored, _ = flow.find(tree, node_id)
        stored.update({"name": de_en("Versuchsbedingung", "Condition"), "k": 2 if both else 1, "shuffle": both})
        survey.flow = tree
        for block in blocks:
            flow.place(survey, f"k{block.id}", node_id, "into")
        if empty:
            flow.place(survey, node_id, empty, "before")
        if both:
            return de_en("Im Fragebogen angelegt: die Blöcke „Bedingung A“ und „Bedingung B“, die jede Person in "
                         "zufälliger Reihenfolge sieht (Ablauf: Randomisierung „Versuchsbedingung“).",
                         "Laid out in the questionnaire: the blocks “Condition A” and “Condition B”, which every "
                         "person sees in random order (flow: randomisation “Condition”).")
        return de_en("Im Fragebogen angelegt: die Blöcke „Bedingung A“ und „Bedingung B“, von denen jede Person "
                     "zufällig einen sieht (Ablauf: Randomisierung „Versuchsbedingung“).",
                     "Laid out in the questionnaire: the blocks “Condition A” and “Condition B”, of which every "
                     "person sees one at random (flow: randomisation “Condition”).")
    from app import vignettes

    block = _new_block(survey, "Vignette", "Vignette")
    db.session.flush()
    root = flow.normalize(survey)
    empty = _first_empty(survey, root)
    n = sum(1 for x, _, _ in flow.walk(root) if x["t"] == "loop") + 1
    node = vignettes.new_node(n)
    split_at = next((i for i, c in enumerate(root["children"]) if c["t"] == "split"), len(root["children"]))
    root["children"].insert(split_at, node)
    survey.flow = root
    flow.place(survey, f"k{block.id}", node["id"], "into")
    if empty:
        flow.place(survey, node["id"], empty, "before")
    return de_en("Im Fragebogen angelegt: der Block „Vignette“ in einer Vignetten-Schleife; Merkmale und Stufen "
                 "legen Sie im Ablauf fest.",
                 "Laid out in the questionnaire: the block “Vignette” in a vignette loop; set the factors and "
                 "levels in the flow.")
