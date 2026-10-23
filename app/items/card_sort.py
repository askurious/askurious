"""Card sorting: participants sort cards (terms, short statements) into groups.

Three ways (config "mode"): closed (into the groups the researcher gives), open (into groups the participants
make and name themselves) and hybrid (the given groups, and own ones besides). Optionally the cards within a group
are put in order as well ("rank"). In the tradition of the sorting
techniques (Rugg & McGeorge, 1997; Coxon, 1999); the results page shows how often two cards ended up together.

Interaction (participant.js, [data-sort]): tap a card, then a group (works on phones and with the keyboard); with a
mouse also drag. "Neue Gruppe" opens an own group with a name field. The page sends the whole sort as JSON in the
field i<id>: {"s": {group: [card codes]}, "own": {group: name}}. Given groups are named by their code, own groups
by OWN_BASE + n (101, 102, ... in the order they were made), which is also their code in the export.

Export: <code>_<card> = the code of the card's group (empty = not sorted), with "rank" also <code>_<card>_rank
(1 = top of its group), with own groups <code>_own<n> = the name of own group n."""
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import r_flag, r_id, r_int, r_one, r_rows, r_text  # rules for survey files, see base.py
from app.items.base import ID_RE, AnswerError, in_lang, load_json, new_id, plain, prepared, typed_text, whole
from app.items.choice import LAYOUT_RULE, OPTION_RULES, _Choice
from app.lang import Both, de_en
from app.ui_i18n import tr

MODES = ("closed", "open", "hybrid")
OWN_BASE = 100  # own group n has the code OWN_BASE + n
MAX_OWN = 30
MAX_NAME = 60  # characters of the name of an own group
MAX_GROUPS = 30
GROUP_RULES = r_rows(MAX_GROUPS, id=r_id, code=r_text(64, 1))
RUGG = ("Rugg, G., & McGeorge, P. (1997). The sorting techniques: A tutorial paper on card sorts, picture sorts and "
        "item sorts. Expert Systems, 14(2), 80–93. https://doi.org/10.1111/1468-0394.00045")
COXON = "Coxon, A. P. M. (1999). Sorting data: Collection and analysis. Sage. https://doi.org/10.4135/9781412983440"


def own_code(n):
    return str(OWN_BASE + n)


@register
class CardSort(_Choice):
    key = "card_sort"
    read_keys = ("mode", "max_own", "rank")  # own groups and ranks of earlier answers (audit 2026-10-10, N-3)
    code_answer = False  # not a code to compare with: a condition only asks "answered or not" (X17)
    label = Both("Kartensortierung", "Card sorting")
    short = Both("Karten auf Stapel sortieren", "Sort cards into piles")
    description = Both("Karten auf Stapel sortieren: vorgegebene, selbst benannte oder beides; auf Wunsch mit Reihenfolge je Stapel",
                       "Sort cards into piles: given ones, ones the participants name, or both; optionally in order within each pile")
    order = 26
    fact_sheet = FactSheet(
        "tasks",
        keywords=["Card sorting", "Card sort", "Sortieren", "Gruppieren", "Grouping", "Kategorien", "Categories"],
        can=[("Karten auf Stapel sortieren: vorgegebene (geschlossen), selbst angelegte und benannte (offen) oder beides.",
              "Sort cards into piles: given ones (closed), ones the participants make and name (open) or both."),
             ("Auf Wunsch zusätzlich eine Reihenfolge der Karten in jedem Stapel.",
              "Optionally also an order of the cards within each pile."),
             ("Antippen oder ziehen, am Handy und mit der Tastatur bedienbar.",
              "Tap or drag, usable on a phone and with the keyboard."),
             ("Auswertung: Verteilung je Karte, wie oft zwei Karten zusammen lagen, Namen eigener Stapel.",
              "Analysis: distribution per card, how often two cards ended up together, names of own piles.")],
        good_for=[("Wie Menschen Begriffe ordnen: mentale Modelle, Kategorien, Menüs und Navigation",
                   "How people order terms: mental models, categories, menus and navigation"),
                  ("Zuordnung vieler Begriffe zu wenigen festen Kategorien",
                   "Assigning many terms to a few fixed categories")],
        not_for=[("Zustimmung zu Aussagen auf einer Skala (dann Likert-Skala oder Q-Sort)",
                  "Agreement with statements on a scale (then Likert scale or Q sort)"),
                 ("Schnelle Zuordnung mit Reaktionszeit (dann Zuordnungsaufgabe)",
                  "Quick sorting with reaction time (then sorting task)")],
        designs=["perception", "cross"],
        examples=[("Offene Sortierung: Auf welche Stapel legen Sie diese 20 Funktionen einer App?",
                   "Open sort: onto which piles would you put these 20 features of an app?")],
    )
    uses_answers = False  # no options carried from an earlier question here
    codes_in_columns = True
    levels = ("nominal",)
    result_kind = "categorical"
    import_rules = {"options": OPTION_RULES, "layout": LAYOUT_RULE, "groups": GROUP_RULES, "mode": r_one(*MODES),
                    "max_own": r_int(1, MAX_OWN), "rank": r_flag, "randomize": r_flag}

    def source_groups(self, item):
        return [(de_en("Sortierverfahren", "Sorting techniques"), [RUGG, COXON])]

    def references(self, item):
        return [RUGG, COXON]

    def default_config(self):
        return {**super().default_config(), "groups": [{"id": new_id(), "code": str(i)} for i in (1, 2)],
                "mode": "closed", "max_own": 8, "rank": False, "randomize": True}

    def default_texts(self, lang):
        return {**super().default_texts(lang), "groups": {}}

    # --- editor -----------------------------------------------------------------------------------

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        config, texts = dict(item.config), dict(item.texts or {})
        lt = dict(texts.get(lang, {}))
        labels = dict(lt.get("groups", {}))
        ids, codes, names = form.getlist("group_id"), form.getlist("group_code"), form.getlist("group_label")
        if structural:
            groups = []
            for gid, code, name in zip(ids, codes, names):
                if not (code.strip() or name.strip()) or len(groups) >= MAX_GROUPS:
                    continue
                gid = gid if ID_RE.match(gid or "") else new_id()  # ids from the form are part of selectors and field names
                groups.append({"id": gid, "code": code.strip()[:64] or str(len(groups) + 1)})
                labels[gid] = name.strip()[:200]
            config["groups"] = groups
        else:
            known = {g["id"] for g in config.get("groups", [])}
            for gid, name in zip(ids, names):
                if gid in known:
                    labels[gid] = name.strip()[:200]
        lt["groups"] = labels
        texts[lang] = lt
        item.config, item.texts = config, texts

    def _update_extra_config(self, config, form):
        config["mode"] = form.get("mode") if form.get("mode") in MODES else "closed"
        n = form.get("max_own", "")
        config["max_own"] = max(1, min(MAX_OWN, int(n))) if n.isascii() and n.isdigit() else config.get("max_own", 8)
        config["rank"] = bool(form.get("rank"))
        config["randomize"] = bool(form.get("randomize"))
        config.pop("layout", None)

    def validate(self, item):
        problems = [p.replace("Antwortoptionen", "Karten").replace("Antwortoption", "Karte").replace("Antwortcodes", "Codes der Karten")
                    .replace("answer options", "cards").replace("answer option", "card").replace("answer codes", "card codes")
                    for p in super().validate(item)]
        c = item.config
        groups, mode = self.given(item), c.get("mode", "closed")
        codes = [g["code"] for g in groups]
        if mode == "closed" and len(groups) < 2:
            problems.append(de_en("mindestens zwei Stapel", "at least two piles"))
        if mode == "hybrid" and not groups:
            problems.append(de_en("mindestens ein vorgegebener Stapel (sonst „offen“ wählen)",
                                  "at least one given pile (or choose “open”)"))
        if len(set(codes)) != len(codes):
            problems.append(de_en("Codes der Stapel sind nicht eindeutig", "pile codes are not unique"))
        labels = item.text("groups") or {}
        if any(not labels.get(g["id"]) for g in groups):
            problems.append(de_en("Stapel ohne Bezeichnung", "pile without a label"))
        if mode != "closed":
            taken = [code for code in codes if code in {own_code(n) for n in range(1, MAX_OWN + 1)}]
            if taken:
                problems.append(de_en("Die Codes {codes} sind für eigene Stapel reserviert (101, 102 …)",
                                      "the codes {codes} are reserved for own piles (101, 102 …)", codes=", ".join(taken)))
        return problems

    def given(self, item):
        """The groups the researcher gives (none in an open sort)."""
        return [] if (item.config or {}).get("mode") == "open" else list((item.config or {}).get("groups", []))

    def groups(self, item, lang):
        return [{**g, "label": (item.t(lang).get("groups") or {}).get(g["id"], "")} for g in self.given(item)]

    def own_allowed(self, item):
        """How many own groups a person may make (0 in a closed sort)."""
        c = item.config or {}
        return 0 if c.get("mode", "closed") == "closed" else int(c.get("max_own") or 8)

    def ordered_options(self, item, lang, start_order=None):
        opts = self.options(item, lang)
        if start_order:
            rank = {oid: n for n, oid in enumerate(start_order)}
            return sorted(opts, key=lambda o: rank.get(o["id"], len(rank)))
        return opts

    def order_codes(self, item):
        if not item.config.get("randomize", True):  # the order of the stack
            return None
        return {o["id"]: f"{item.code}_{o['code']}" for o in item.config.get("options", [])}

    # --- participant side ---------------------------------------------------------------------------

    def _read(self, item, form):
        """The sort a page sent, cleaned: ({group: [card codes]}, {own group: name}). Unknown groups and cards are
        dropped, a card counts in its first group only, own groups without cards are left out and the others
        numbered 101, 102, ... in the order they were made."""
        data = load_json(form.get(f"i{item.id}"), {})
        sorts = data.get("s") if isinstance(data, dict) else None
        names = data.get("own") if isinstance(data, dict) else None
        sorts = sorts if isinstance(sorts, dict) else {}
        names = names if isinstance(names, dict) else {}
        cards = {o["code"] for o in item.config.get("options", [])}
        given = {g["code"] for g in self.given(item)}
        allowed = self.own_allowed(item)
        seen, out, own = set(), {}, {}
        # own groups: every number key that is not a given group (the page numbers them 101 ...; an older page or
        # a given group with a high code may have pushed them further), in the order of their numbers
        own_keys = sorted((k for k in sorts if k not in given and whole(k) is not None and len(k) <= 3), key=int)
        for key in [g["code"] for g in self.given(item)] + own_keys:
            values = sorts.get(key)
            values = [c for c in values if isinstance(c, str)] if isinstance(values, list) else []
            picked = [c for c in dict.fromkeys(values) if c in cards and c not in seen]
            if not picked:
                continue
            if key not in given:
                if len(own) >= allowed:
                    continue
                key_new = own_code(len(own) + 1)
                own[key_new] = typed_text(names.get(key), MAX_NAME)
                key = key_new
            seen.update(picked)
            out[key] = picked
        return out, own

    def parse_answer(self, item, form, lang="de", meta=None):
        sorts, own = self._read(item, form)
        sorted_ = {c for cards in sorts.values() for c in cards}
        if item.required and any(o["code"] not in sorted_ for o in item.config.get("options", [])):
            raise AnswerError(tr(lang, "Bitte legen Sie jede Karte auf einen Stapel.", "Please put every card on a pile."))
        if item.required and any(not name for name in own.values()):
            raise AnswerError(tr(lang, "Bitte geben Sie jedem eigenen Stapel einen Namen.", "Please give each of your own piles a name."))
        if not sorts:
            return None
        return {"s": sorts, **({"own": own} if own else {})}

    def display_value(self, item, form, meta=None):
        sorts, own = self._read(item, form)
        return {"s": sorts, "own": own} if sorts else None

    def is_missing(self, item, answer):
        if not isinstance(answer, dict) or not answer.get("s"):
            return True
        sorted_ = {c for cards in answer["s"].values() for c in cards}
        return any(o["code"] not in sorted_ for o in item.config.get("options", []))

    def soft_warning(self, item, answer, lang):
        """"Hinweis": an own group without a name is asked about once."""
        if item.nudge and isinstance(answer, dict) and any(not n for n in (answer.get("own") or {}).values()):
            return tr(lang, "Einer Ihrer Stapel hat noch keinen Namen. Sie können ihn eintragen oder trotzdem weitergehen.",
                      "One of your piles has no name yet. You can enter it or continue anyway.")
        return None

    # --- export ------------------------------------------------------------------------------------

    def open_columns(self, item):
        return [c for c, _ in self.export_columns(item) if self._column_kind(item, c) == "own"]  # names of own groups

    def export_columns(self, item):
        labels = item.text("options") or {}
        cols = []
        for o in item.config.get("options", []):
            name = plain(labels.get(o["id"], o["code"]))
            cols.append((f"{item.code}_{o['code']}", f"„{name}“: Stapel"))
            if item.config.get("rank"):
                cols.append((f"{item.code}_{o['code']}_rank", f"„{name}“: Platz im Stapel (1 = oben)"))
        cols += [(f"{item.code}_own{n}", f"Name des eigenen Stapels {n} (Code {own_code(n)})")
                 for n in range(1, self.own_allowed(item) + 1)]
        return cols

    def export_values(self, item, answer):
        sorts = (answer or {}).get("s") or {}
        where = {c: (g, n) for g, cards in sorts.items() for n, c in enumerate(cards, start=1)}
        out = {}
        for o in item.config.get("options", []):
            found = where.get(o["code"])
            out[f"{item.code}_{o['code']}"] = found[0] if found else None
            if item.config.get("rank"):
                out[f"{item.code}_{o['code']}_rank"] = found[1] if found else None
        names = (answer or {}).get("own") or {}
        for n in range(1, self.own_allowed(item) + 1):
            key = own_code(n)
            out[f"{item.code}_own{n}"] = (names.get(key) or None) if key in sorts else None
        return out

    def _column_kind(self, item, column):
        """"card", "rank" or "own" for an export column of this item, else None: looked up in the columns the
        export makes, not read from the ending (a card coded "a_rank" is a card, not the rank of card "a")."""
        return prepared(item, "card_sort_kinds", lambda: self._column_kinds(item)).get(column)

    def _column_kinds(self, item):
        kinds = {}
        for o in item.config.get("options", []):
            kinds.setdefault(f"{item.code}_{o['code']}", "card")
            if item.config.get("rank"):
                kinds.setdefault(f"{item.code}_{o['code']}_rank", "rank")
        for n in range(1, self.own_allowed(item) + 1):
            kinds.setdefault(f"{item.code}_own{n}", "own")
        return kinds

    def column_level(self, item, column):
        return "ordinal" if self._column_kind(item, column) == "rank" else "nominal"

    def lists_texts(self, item, column):
        return self._column_kind(item, column) == "own"

    def result_column(self, item, column):
        """Results page: the cards and names in the table of the sort (results()), not one by one."""
        return None, "", None

    def codebook_info(self, item, column, lang, ui=None):
        kind = self._column_kind(item, column)
        if kind == "rank":
            return "Rang", in_lang(ui, "1 bis {n}", "1 to {n}", n=len(item.config.get("options", []))), [
                in_lang(ui, "Platz der Karte in ihrem Stapel, 1 = oben; leer = nicht einsortiert",
                        "place of the card in its pile, 1 = top; empty = not sorted")]
        if kind == "own":
            return "Text", "", [in_lang(ui, "höchstens {n} Zeichen; leer = Stapel nicht angelegt", "at most {n} characters; empty = pile not made", n=MAX_NAME)]
        if kind == "card":
            values = "; ".join(f"{g['code']} = {plain(g['label'])}" for g in self.groups(item, lang))
            notes = [in_lang(ui, "leer = nicht einsortiert", "empty = not sorted")]
            if self.own_allowed(item):
                # one pair per code, so that the value labels of .sps/.sav/.rds/.omv name each (audit 2026-10-10, N-6)
                own = "; ".join(f"{own_code(i)} = " + in_lang(ui, "eigener Stapel {i}", "own pile {i}", i=i)
                                for i in range(1, self.own_allowed(item) + 1))
                values = f"{values}; {own}" if values else own
                notes.append(in_lang(ui, "Name des eigenen Stapels in Spalte {col}", "name of the own pile in column {col}",
                                     col=f"{item.code}_own1 …"))
                notes.append(in_lang(ui, "die Nummern eigener Stapel gelten nur innerhalb einer Person",
                                     "the numbers of own piles hold only within one person"))
            return "Auswahl", values, notes
        return None

    # --- results -----------------------------------------------------------------------------------

    def results(self, item, table, lang, passes=()):
        """Results page: per card the share of persons who put it into each given group (and into an own one),
        how often two cards ended up in the same group (share of the persons who sorted both), and the names of
        own groups with how often each was given. In a loop every pass counts as one sort."""
        from collections import Counter

        cards = self.options(item, lang)
        groups = self.groups(item, lang)
        own_n = self.own_allowed(item)
        suffixes = [f"_{n}" for n in passes] or [""]
        sorts = []
        for row in table:
            for s in suffixes:
                found = {o["code"]: row.get(f"{item.code}_{o['code']}{s}") for o in cards}
                found = {k: str(v) for k, v in found.items() if v not in (None, "")}
                if found:
                    names = [row.get(f"{item.code}_own{n}{s}") for n in range(1, own_n + 1)]
                    sorts.append((found, [str(x).strip() for x in names if x not in (None, "")]))
        given = [g["code"] for g in groups]
        rows = []
        for o in cards:
            placed = [found[o["code"]] for found, _ in sorts if o["code"] in found]
            counts = Counter(placed)
            n = len(placed)
            shares = [counts.get(code, 0) / n if n else None for code in given]
            own = sum(v for k, v in counts.items() if k not in given)
            top = max(shares + ([own / n] if own_n and n else []), default=None, key=lambda v: v or 0)
            rows.append({"label": plain(o["label"]) or o["code"], "code": o["code"], "n": n, "shares": shares,
                         "own": own / n if n and own_n else None, "top": top})
        pairs = []
        for i, a in enumerate(cards):
            line = []
            for j, b in enumerate(cards):
                if i == j:
                    line.append(None)
                    continue
                both = [f for f, _ in sorts if a["code"] in f and b["code"] in f]
                line.append(sum(1 for f in both if f[a["code"]] == f[b["code"]]) / len(both) if both else None)
            pairs.append(line)
        names = Counter(" ".join(n.lower().split()) for _, ns in sorts for n in ns)
        return {"persons": len(sorts), "groups": [plain(g["label"]) or g["code"] for g in groups], "own": bool(own_n),
                "rows": rows, "labels": [r["label"] for r in rows], "pairs": pairs,
                "names": names.most_common(30)}

    # --- dummy data, duration ------------------------------------------------------------------------

    def dummy_fields(self, item, rng, meta, answers=None):
        import json

        codes = [o["code"] for o in item.config.get("options", [])]
        rng.shuffle(codes)
        given = [g["code"] for g in self.given(item)]
        own = [own_code(n) for n in range(1, min(self.own_allowed(item), 4) + 1)]
        keys = given + own
        if not codes or not keys:
            return {}
        sorts = {}
        for c in codes:
            sorts.setdefault(rng.choice(keys), []).append(c)
        names = {k: f"Dummy-Stapel {k}" for k in sorts if k in own}
        return {f"i{item.id}": json.dumps({"s": sorts, "own": names})}

    def seconds(self, item, read):
        n = len(item.config.get("options", []))
        return read + 4 * n + (8 * min(self.own_allowed(item), 4) if self.own_allowed(item) else 0) + (2 * n if item.config.get("rank") else 0)
