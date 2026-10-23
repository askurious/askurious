"""Assignment task: participants sort each element (shown one at a time, in random order) into
one of two boxes (e.g. "gut" / "schlecht"). Records the decision and the latency in milliseconds
from the element's appearance to the click or key press (E = left, I = right). A start button
keeps instruction reading out of the first latency.
Sides ("sides"): the two categories swap sides at random per participant (default, stable for that person),
per element, or stay fixed; the answer always records the category, never the side.

Answer: [{"s": element id, "c": category code, "rt": ms}] in presentation order.
Export per element: <code>_<element> = category code, <code>_<element>_rt = ms; <code>_order."""
import hashlib

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import CODE_RE, AnswerError, ItemType, load_json, read_cfg
from app.items.base import form_ids, new_id as _nid
from app.lang import Both, de_en
from app.ui_i18n import tr

MAX_RT = 10 * 60 * 1000  # latencies above 10 minutes are implausible and discarded
SIDES = ("person", "item", "fixed")


@register
class Assign(ItemType):
    key = "assign"
    read_keys = ("sides",)  # the sides are drawn anew for the export (_swap): fixed once real answers exist (N-8)
    import_rules = {"categories": r_rows(2, 2, id=r_one("L", "R"), code=r_text(64, 1)),
                    "elements": r_rows(300, id=r_id, code=r_text(64, 1)), "randomize": r_flag, "keys": r_flag,
                    "sides": r_one(*SIDES)}
    label = Both("Zuordnungsaufgabe", "Matching task")
    short = Both("Begriffe zwei Kategorien zuordnen", "Sort terms into two categories")
    description = Both("Begriffe nacheinander einer von zwei Kategorien zuordnen (z. B. gut/schlecht); misst Entscheidung und Reaktionszeit",
                       "Sort terms one after another into one of two categories (e.g. good/bad); measures decision and reaction time")
    order = 27
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Kategorisieren", "Categorise", "Reaktionszeit", "Reaction time", "Swipe", "Wischen"],
        can=[("Begriffe einer von zwei Kategorien zuordnen, mit Reaktionszeit.",
              "Sort terms into one of two categories, with reaction time."),
             ("Die Seiten der Kategorien wechseln je Person, je Element oder bleiben fest.",
              "The sides of the categories switch per person, per element or stay fixed.")],
        good_for=[("Schnelle, spontane Urteile über viele Begriffe", "Quick, spontaneous judgements about many terms")],
        not_for=[("Implizite Einstellungen mit Kontrastblöcken (dann IAT)",
                  "Implicit attitudes with contrasting blocks (then IAT)")],
        designs=["within", "cross"],
        examples=[("Ordnen Sie jedes Wort so schnell wie möglich „gut“ oder „schlecht“ zu.",
                   "Sort each word into “good” or “bad” as fast as you can.")],
    )
    supports_auto_next = True  # the task is complete after the last element
    alone_on_page = True  # timed, navigation hidden until done
    auto_next_hint = Both("nach dem letzten Element", "after the last element")

    def default_config(self):
        return {"categories": [{"id": "L", "code": "1"}, {"id": "R", "code": "2"}],
                "elements": [{"id": _nid(), "code": "1"}, {"id": _nid(), "code": "2"}], "randomize": True, "keys": True,
                "sides": "person"}

    def default_texts(self, lang):
        return {"question": tr(lang, "Ordnen Sie jeden Begriff so schnell wie möglich einer Kategorie zu.", "Sort each term into a category as quickly as you can."),
                "categories": {"L": tr(lang, "gut", "good"), "R": tr(lang, "schlecht", "bad")}, "elements": {}}

    def update_from_form(self, item, form, lang, structural):
        config, texts = dict(item.config), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        lt["question"] = form.get("question", "").strip()
        cats = dict(lt.get("categories", {}))
        for cid in ("L", "R"):
            cats[cid] = form.get(f"cat_{cid}_label", cats.get(cid, "")).strip()[:60]
        lt["categories"] = cats
        etexts = dict(lt.get("elements", {}))
        ids, codes, labels = form_ids(form.getlist("el_id")), form.getlist("el_code"), form.getlist("el_label")
        if structural:
            elements = []
            for eid, code, label in zip(ids, codes, labels):
                if not (code.strip() or label.strip()):
                    continue
                eid = eid or _nid()
                elements.append({"id": eid, "code": code.strip() or str(len(elements) + 1)})
                etexts[eid] = label.strip()
            config["elements"] = elements
            config["categories"] = [{"id": cid, "code": (form.get(f"cat_{cid}_code") or str(n)).strip()}
                                    for n, cid in enumerate(("L", "R"), start=1)]
            config["randomize"] = bool(form.get("randomize"))
            config["keys"] = bool(form.get("keys"))
            config["sides"] = form.get("sides") if form.get("sides") in SIDES else "person"
        else:
            known = {e["id"] for e in config.get("elements", [])}
            for eid, label in zip(ids, labels):
                if eid in known:
                    etexts[eid] = label.strip()
        lt["elements"] = etexts
        texts[lang] = lt
        item.config, item.texts = config, texts

    def elements(self, item, lang, order=None):
        et = item.t(lang).get("elements", {})
        rows = [{**e, "label": et.get(e["id"], "")} for e in read_cfg(item).get("elements", [])]
        if order:
            rank = {eid: n for n, eid in enumerate(order)}
            rows.sort(key=lambda r: rank.get(r["id"], len(rank)))
        return rows

    def swapped(self, item, seed, element_ids):
        """{element id: True if the two categories swap sides for it}. Stable for one participant (seed)."""
        mode = read_cfg(item).get("sides", "person")
        if mode == "fixed" or seed is None:
            return {eid: False for eid in element_ids}

        def coin(*parts):
            return hashlib.sha256(":".join(map(str, (seed, item.id) + parts)).encode()).digest()[0] % 2 == 1

        person = coin("person")
        return {eid: coin(eid) if mode == "item" else person for eid in element_ids}

    def categories(self, item, lang):
        ct = item.t(lang).get("categories", {})
        return [{**c, "label": ct.get(c["id"], "")} for c in read_cfg(item).get("categories", [])]

    def validate(self, item):
        problems = []
        els = read_cfg(item).get("elements", [])
        if len(els) < 2:
            problems.append(de_en("mindestens zwei Elemente", "at least two elements"))
        codes = [e["code"] for e in els]
        if len(set(codes)) != len(codes):
            problems.append(de_en("Codes der Elemente sind nicht eindeutig", "codes of the elements are not unique"))
        if any(not CODE_RE.match(f"{item.code}_{c}") for c in codes):
            problems.append(de_en("Codes der Elemente: nur Buchstaben, Ziffern und _", "codes of the elements: only letters, digits and _"))
        if any(not (item.text("elements") or {}).get(e["id"]) for e in els):
            problems.append(de_en("Element ohne Text", "element without text"))
        cats = self.categories(item, None)
        if any(not c["label"] for c in cats):
            problems.append(de_en("beide Kategorien brauchen eine Bezeichnung", "both categories need a name"))
        if len({c["code"] for c in read_cfg(item).get("categories", [])}) != 2:
            problems.append(de_en("die beiden Kategorien brauchen verschiedene Codes", "the two categories need different codes"))
        return problems

    def _parse(self, item, raw):
        data = load_json(raw or "[]", None)
        if data is None:
            return None
        ids = {e["id"] for e in read_cfg(item).get("elements", [])}
        cats = {c["code"] for c in read_cfg(item).get("categories", [])}
        out, seen = [], set()
        for d in data if isinstance(data, list) else []:
            if (not isinstance(d, dict) or not isinstance(d.get("s"), str) or d["s"] not in ids or d["s"] in seen
                    or str(d.get("c")) not in cats):
                continue
            rt = d.get("rt")
            rt = int(rt) if isinstance(rt, (int, float)) and 0 <= rt <= MAX_RT else None
            seen.add(d["s"])
            out.append({"s": d["s"], "c": str(d["c"]), "rt": rt})
        return out

    def parse_answer(self, item, form, lang="de", meta=None):
        answer = self._parse(item, form.get(f"i{item.id}"))
        complete = answer is not None and len(answer) == len(read_cfg(item).get("elements", []))
        if item.required and not complete:
            raise AnswerError(tr(lang, "Bitte ordnen Sie alle Begriffe zu.", "Please sort all terms."))
        return answer or None

    def display_value(self, item, form, meta=None):
        return self._parse(item, form.get(f"i{item.id}"))

    def is_missing(self, item, answer):
        return not answer or len(answer) < len(read_cfg(item).get("elements", []))

    levels = ("nominal",)

    def column_level(self, item, column):
        return "metric" if column.endswith("_rt") else "nominal"  # _swap: 0/1  # reaction times in ms, categories, order

    def result_column(self, item, column):
        """Results page: assignments as frequencies, reaction times in ms, the order not at all."""
        if column.endswith(("_order", "_swap")):
            return None, "", None
        if column.endswith("_rt"):
            return "auto", "ms", None
        return "categorical", "", {c["code"]: c["label"] for c in self.categories(item, item.survey.default_language)}

    def export_columns(self, item):
        labels = item.text("elements") or {}
        cats = " | ".join(f"{c['code']} = {c['label']}" for c in self.categories(item, None))
        cols = []
        for e in read_cfg(item).get("elements", []):
            name = labels.get(e["id"], e["code"])
            cols += [(f"{item.code}_{e['code']}", f"Zuordnung „{name}“ ({cats})"),
                     (f"{item.code}_{e['code']}_rt", f"Reaktionszeit „{name}“ in ms")]
            if read_cfg(item).get("sides", "person") != "fixed":
                cols.append((f"{item.code}_{e['code']}_swap", f"Seiten bei „{name}“: 0 = wie im Editor, 1 = vertauscht"))
        cols.append((f"{item.code}_order", "Gezeigte Reihenfolge der Elemente (Codes, getrennt durch |)"))
        return cols

    def export_response_values(self, item, answer, response):
        """Which side each category was on (drawn from the same seed as the page, models.display_seed)."""
        from app.models import display_seed

        if read_cfg(item).get("sides", "person") == "fixed" or not answer:
            return {}  # not shown: no sides
        els = read_cfg(item).get("elements", [])
        swap = self.swapped(item, display_seed(response), [e["id"] for e in els])
        return {f"{item.code}_{e['code']}_swap": int(swap[e["id"]]) for e in els}

    def export_values(self, item, answer):
        by_id = {d["s"]: d for d in (answer or [])}
        code_of = {e["id"]: e["code"] for e in read_cfg(item).get("elements", [])}
        out = {}
        for e in read_cfg(item).get("elements", []):
            d = by_id.get(e["id"], {})
            out[f"{item.code}_{e['code']}"] = d.get("c")
            out[f"{item.code}_{e['code']}_rt"] = d.get("rt")
        out[f"{item.code}_order"] = "|".join(code_of[d["s"]] for d in (answer or []) if d["s"] in code_of) or None
        return out
