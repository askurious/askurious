"""Questions with real answers in a survey opened for editing again ("Zur Bearbeitung öffnen"): what their stored
answers mean stays. The variable name, the code and reversal of every option, statement or row with an id, the
number of scale points or the range, the reversal and the settings that say how an answer is read ("weiß nicht",
the mode; audit 2026-10-10, N-3; the further settings the export reads, audit 2026-10-12, N-1 to N-13, M-2, M-3)
(ItemType.answer_shape) keep their values; options and statements may be added, texts change (data audit
2026-10-05, T-32). Not in a Q sort and a ranking, where a new row changes the task (closed_lists; 2026-10-12, M-4,
M-7). An option of a multiple choice or an area of a hot spot added now carries the version from which it is shown,
and the participations before get -77 in its column (stamp_added; 2026-10-12, N-5).

One rule for every way a question changes: the element editor (also the optional options of a construct), undo and
redo (app/undo.py), options carried from another question (app/carry.py) and the quota assistant (app/census.py).
Each takes the shape before its change and asks `refused` afterwards (or `refused_shape` with a state it has not
put into the question yet). Linked elements keep a stricter rule of their own: a survey with real participations
takes no other Fassung, and a frozen element changes no use (app/linked.py). A survey file is always read in as a
new survey (app/transfer.py), and the toolbox inserts new questions only.
"""
from types import SimpleNamespace

from app.items.base import shape_changes
from app.lang import de_en

MESSAGE = ("„{code}“ hat schon echte Antworten: {what} bleiben, wie sie sind, damit die gespeicherten Antworten "
           "ihre Bedeutung behalten. {outcome} Zum Umbauen die Befragung duplizieren.")
MESSAGE_EN = ("“{code}” already has real answers: {what} stay as they are, so that the stored answers keep their "
              "meaning. {outcome} To rebuild it, duplicate the survey.")
SAVED_TEXTS = "Diese Änderung wurde nicht gespeichert (Texte schon); Optionen und Aussagen lassen sich ergänzen."
SAVED_TEXTS_EN = "This change was not saved (the texts were); options and statements can be added."
# Q sort and ranking: nothing may be added (ItemType.closed_lists; audit 2026-10-12, M-4, M-7)
SAVED_TEXTS_CLOSED = "Diese Änderung wurde nicht gespeichert (Texte schon)."
SAVED_TEXTS_CLOSED_EN = "This change was not saved (the texts were)."


class _Shown:
    """A text in German and English that reads in the language of the page wherever it is shown (str() or a
    template): for NOTE, which the editor hands to its page as it is."""

    def __init__(self, de, en):
        self.de, self.en = de, en

    def __str__(self):
        return de_en(self.de, self.en)

    __html__ = __str__


NOTE = _Shown("Diese Frage hat schon echte Antworten: Variablenname, die vorhandenen Optionen, Aussagen oder Zeilen "
              "mit ihren Codes, Stufenzahl und Umpolung sowie Einstellungen wie „weiß nicht“ oder der Modus bleiben, "
              "wie sie sind, damit die gespeicherten Antworten ihre Bedeutung behalten. Texte lassen sich ändern, "
              "Optionen und Aussagen ergänzen. Zum Umbauen die Befragung duplizieren.",
              "This question already has real answers: the variable name, the existing options, statements or rows "
              "with their codes, the number of points and the reversal as well as settings such as “don’t know” or "
              "the mode stay as they are, so that the stored answers keep their meaning. Texts can be changed, options "
              "and statements added. To rebuild it, duplicate the survey.")


NOTE_CLOSED = _Shown("Diese Frage hat schon echte Antworten: Variablenname, die Aussagen oder Optionen mit ihren Codes "
                     "(auch kein Ergänzen: Es änderte die Aufgabe selbst) und die Einstellungen, nach denen die Antworten "
                     "gelesen werden, bleiben, wie sie sind, damit die gespeicherten Antworten ihre Bedeutung behalten. "
                     "Texte lassen sich ändern. Zum Umbauen die Befragung duplizieren.",
                     "This question already has real answers: the variable name, the statements or options with their "
                     "codes (no adding either: it would change the task itself) and the settings the answers are read "
                     "by stay as they are, so that the stored answers keep their meaning. Texts can be changed. To "
                     "rebuild it, duplicate the survey.")


def note(item):
    """The note over the element editor of a question whose answers are fixed (NOTE, or NOTE_CLOSED where nothing may
    be added)."""
    return NOTE_CLOSED if item.kind.closed_lists else NOTE


def fixed(survey, item):
    """The question has real answers and the survey is a draft again: its answer shape stays. Before the first
    start there are no real answers; in a running or closed survey the structure is fixed anyway."""
    return (survey.is_editable and (survey.qversion or 1) > 1 and (item.kind.collects_answer or item.kind.computed)
            and item.kind.has_real_answers(item))


def answered(survey):
    """The ids of the questions whose answers are fixed (fixed): deleting one of them asks first, since its answers
    then leave the data set, the results and the exports (audit 2026-10-18, FUNC-05). Empty before a reopening."""
    if not survey.is_editable or (survey.qversion or 1) <= 1:
        return set()
    return {i.id for b in survey.blocks for i in b.items if fixed(survey, i)}


# what deleting a question with real answers costs, for the dialog that asks first and for the refusal without it
DELETE_ASK = _Shown("Diese Frage hat schon echte Antworten. Gelöscht fehlen sie in Datensatz, Ergebnissen und Exporten; "
                    "gespeichert bleiben sie, und die Frage bleibt im Papierkorb, bis Sie sie dort endgültig löschen "
                    "oder wiederherstellen. Die Sicherung vom Öffnen zur Bearbeitung (Reiter „Export“) enthält sie "
                    "weiter.",
                    "This question already has real answers. Deleted, they are missing from the data set, the results "
                    "and the exports; they stay stored, and the question stays in the bin until you delete it there "
                    "for good or restore it. The backup made when the survey was opened for editing (tab “Export”) "
                    "still holds them.")
DELETE_BLOCK_ASK = _Shown("Fragen dieses Blocks haben schon echte Antworten. Gelöscht fehlen sie in Datensatz, "
                          "Ergebnissen und Exporten; gespeichert bleiben sie, und die Fragen bleiben im Papierkorb, bis "
                          "Sie sie dort endgültig löschen oder wiederherstellen.",
                          "Questions of this block already have real answers. Deleted, they are missing from the data "
                          "set, the results and the exports; they stay stored, and the questions stay in the bin until "
                          "you delete them there for good or restore them.")
CONFIRM_FIELD = "answers_confirmed"  # sent by the dialog's form: the person has read DELETE_ASK


def shape(item):
    """The answer shape of `item` as it is now (taken before a change)."""
    return item.kind.answer_shape(item)


def shape_of(item, code, config):
    """The answer shape `item` would have with this variable name and config (a state not yet put into it)."""
    from app import items

    return item.kind.answer_shape(SimpleNamespace(code=code, config=config or {}, type=item.type, block=item.block,
                                                  id=item.id, texts=item.texts,
                                                  cfg=items.with_defaults(item.type, config)))


def refused_shape(survey, item, before, after):
    """What of the answer shape `before` the change to `after` would alter, in words ("die Stufenzahl ..."), if
    the question's answers are fixed; [] if the change may be made. The database is asked only when the shape
    changes."""
    changed = shape_changes(before, after)
    if changed and item.type == "calc":
        # The editor has already changed the inputs; the old inputs decide whether the formula was used.
        formula = before.get("read", {}).get("formula")
        if formula is not None:
            old = SimpleNamespace(id=item.id, type=item.type, block=item.block,
                                  config=dict(zip(item.kind.formula_keys, formula)), kind=item.kind)
            return changed if fixed(survey, old) else []
    return changed if changed and fixed(survey, item) else []


def refused(survey, item, before):
    """refused_shape for a change already made to `item`."""
    return refused_shape(survey, item, before, shape(item))


def message(item, changed, outcome=None):
    """The sentence for the person, in the language of the page: what stays, then what happened instead
    (`outcome`; by default that the texts were saved)."""
    if outcome is None:
        outcome = (de_en(SAVED_TEXTS_CLOSED, SAVED_TEXTS_CLOSED_EN) if item.kind.closed_lists
                   else de_en(SAVED_TEXTS, SAVED_TEXTS_EN))
    return de_en(MESSAGE, MESSAGE_EN, code=item.code, what=de_en(" und ", " and ").join(changed), outcome=outcome)


# --- rows added in a survey opened for editing again (audit 2026-10-12, N-5) ----------------------------------------

def stamp_added(survey, item, config_before):
    """Rows of the item's since_lists (options of a multiple choice, areas of a hot spot) that are new against
    `config_before` get "since": the questionnaire version from which they are shown. The participations of earlier
    versions never saw them; their columns say -77 (not shown), not 0 (not ticked). Only in a survey opened for editing
    again (before the first start every participation is a test run of the version it shows)."""
    version = survey.qversion or 1
    if version <= 1 or not item.kind.since_lists:
        return
    config = dict(item.config or {})
    changed = False
    for key in item.kind.since_lists:
        known = {e.get("id") for e in (config_before or {}).get(key) or [] if isinstance(e, dict)}
        rows = []
        for e in config.get(key) or []:
            if isinstance(e, dict) and e.get("id") not in known and not e.get("since"):
                e, changed = {**e, "since": version}, True
            rows.append(e)
        config[key] = rows
    if changed:
        item.config = config


def without_since(config):
    """`config` without the "since" of its rows: a copy of the questionnaire starts at version 1 again
    (app/duplicate.py)."""
    out = dict(config or {})
    for key, value in out.items():
        if isinstance(value, list) and any(isinstance(e, dict) and "since" in e for e in value):
            out[key] = [{k: v for k, v in e.items() if k != "since"} if isinstance(e, dict) else e for e in value]
    return out


def freeze_on_reopen(survey):
    """A survey with real answers is opened for editing again: settings only worked out while reading are written
    down (ItemType.freeze_answer_shape, the grid of a Q sort; audit 2026-10-12, M-4)."""
    from app.items.base import ItemType

    for block in survey.blocks:
        for item in block.items:
            kind = item.kind
            if (kind is not None and type(kind).freeze_answer_shape is not ItemType.freeze_answer_shape
                    and kind.has_real_answers(item)):
                kind.freeze_answer_shape(item)
