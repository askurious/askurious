"""Demographic standard questions of the RatSWD for the construct toolbox (concept "Demografie-Standards", 2026-10-15).

The items are the minimal versions and basic items of the "Standardfragenkatalog zur Erhebung soziodemographischer
und krisenbezogener Variablen" (Rat für Sozial- und Wirtschaftsdaten, 2023; CC BY 4.0), developed with the
KonsortSWD sub-project on harmonised variables and pretested. Wording, answer codes and filters as in the catalogue:
"Weiß nicht" is -1 and "Keine Angabe" -2 there, both count as missing. Household net income has no minimal version;
the shortest form is the closed question with unfolding brackets HNE_3 to HNE_6 (after NEPS).

Each item is a construct of its own, so it can be left out or moved like any other question; their wording, codes
and metadata are in the construct catalogue (app/construct_catalog.py, tag "quelle:RatSWD/KonsortSWD"), which
app/constructs.py seeds. They start with status "review" until the wording is checked against the PDF (as gdms). The English
texts are working translations for Askurious, not part of the catalogue.

They are standard questions, not scales: they stay out of the pooled analysis of the constructs (licence.md section
4; concept decision 7a). NOT_POOLED names them; app/constructs.pooled() asks it."""

from app import construct_catalog as _catalog


ERW_EMPLOYED = ("1", "2")                           # main job, full or part time
ERW_NOT_EMPLOYED = ("3", "4", "5", "6", "7", "8", "9")  # filter of ERW_2
# filter of BER_1 and STE_1: their wording also asks those no longer employed about their former main job ("Wenn Sie
# nicht mehr erwerbstätig sind …", "haben oder hatten"), the catalogue filters "heute oder früher erwerbstätig". The
# bundle has no question on earlier employment, so all but pupils/students (3) and volunteers (8) get them: retired
# (4), permanently unable to work (5), housework and care (6), unemployed (7) and other reasons (9); pre-test 2026-10-20
ERW_EVER_EMPLOYED = ("1", "2", "4", "5", "6", "7", "9")


KEYS = [c["key"] for c in _catalog.entries() if "RatSWD/KonsortSWD" in (c.get("tags") or {}).get("quelle", [])]
NOT_POOLED = set(KEYS)


# ---- the bundle "Demografie kurz (RatSWD)" ----
# One click inserts a block with the basic items; each part can be left out (concept decision 5b). Offered in the
# editor (constructs), by the setup wizard and by AInstein (wizard.add_demographics).
BUNDLE_NAME = ("Demografie kurz (RatSWD)", "Short demographics (RatSWD)")
BUNDLE = [  # (part, German, English, construct keys): in the order of the block
    ("alt", "Geburtsmonat und -jahr (ALT_1)", "Month and year of birth (ALT_1)", ("ratswd_alt1",)),
    ("ges", "Geschlecht (GES_1)", "Sex (GES_1)", ("ratswd_ges1",)),
    ("bil1", "Schulabschluss (BIL_1)", "School-leaving qualification (BIL_1)", ("ratswd_bil1",)),
    ("bil4", "Ausbildungsabschluss (BIL_4)", "Vocational qualification (BIL_4)", ("ratswd_bil4",)),
    ("erw1", "Erwerbssituation (ERW_1)", "Employment situation (ERW_1)", ("ratswd_erw1",)),
    ("erw2", "Nebenerwerbstätigkeit (ERW_2)", "Side job (ERW_2)", ("ratswd_erw2",)),
    ("ber1", "Berufliche Tätigkeit (BER_1)", "Occupation (BER_1)", ("ratswd_ber1",)),
    ("ste1", "Stellung im Beruf (STE_1)", "Occupational status (STE_1)", ("ratswd_ste1",)),
    ("sta1", "Deutsche Staatsangehörigkeit (STA_1)", "German citizenship (STA_1)", ("ratswd_sta1",)),
    ("geb1", "In Deutschland geboren (GEB_1)", "Born in Germany (GEB_1)", ("ratswd_geb1",)),
    ("geb35", "Geburtsland der Eltern (GEB_3, GEB_5)", "Parents' country of birth (GEB_3, GEB_5)",
     ("ratswd_geb3", "ratswd_geb5")),
    ("hhg1", "Haushaltsgröße (HHG_1)", "Household size (HHG_1)", ("ratswd_hhg1",)),
    ("fam1", "Familienstand (FAM_1)", "Marital status (FAM_1)", ("ratswd_fam1",)),
    ("hne", "Haushaltsnettoeinkommen (HNE_3 bis HNE_6)", "Household net income (HNE_3 to HNE_6)",
     ("ratswd_hne3", "ratswd_hne4", "ratswd_hne5", "ratswd_hne6")),
]
PARTS = [part for part, *_ in BUNDLE]
# the catalogue's filters: construct key -> (the construct whose answer decides, its answer codes); such a question
# goes on a page after that one (a condition reads only answers of earlier pages, app/item_condition.py)
FILTERS = {"ratswd_erw2": ("ratswd_erw1", ERW_NOT_EMPLOYED), "ratswd_ber1": ("ratswd_erw1", ERW_EVER_EMPLOYED),
           "ratswd_ste1": ("ratswd_erw1", ERW_EVER_EMPLOYED), "ratswd_hne4": ("ratswd_hne3", ("1",)),
           "ratswd_hne5": ("ratswd_hne3", ("2",)), "ratswd_hne6": ("ratswd_hne3", ("3",))}


def bundle_parts():
    """[(part, label)] of the bundle in the language of the page."""
    from app.lang import english

    return [(part, en if english() else de) for part, de, en, _keys in BUNDLE]


def bundle_keys(parts):
    """The construct keys of the chosen parts, in the order of the block: first the questions everyone gets, then
    those that follow from an answer on the first page."""
    keys = [k for part, _de, _en, ks in BUNDLE if part in parts for k in ks]
    first = [k for k in keys if FILTERS.get(k, (None,))[0] not in keys]
    return first + [k for k in keys if k not in first]


def add_bundle(survey, parts, block):
    """Fill `block` of `survey` with the questions of the chosen parts (app.ratswd.PARTS), each built from its
    construct like a question added in the editor, a page break before those with a filter, and their conditions.
    The block must belong to the survey already (its questions get their ids on the flush here). Returns the
    questions."""
    from app import constructs, duplicate, item_texts, items
    from app.models import Item

    keys = bundle_keys(parts)
    from app import edit_guard

    used = edit_guard.taken_codes(survey)  # read under the survey's row

    def free(base, first=""):
        """base if free, else base_2, base_3 … (a page break: _pb1, _pb2 …, as the editor names them)."""
        code, n = base + first, 1
        while code in used:
            n += 1
            code = f"{base}{n}" if first else f"{base}_{n}"
        used.add(code)
        return code

    rows, made, broke = list(block.items), {}, False
    for key in keys:
        if not broke and FILTERS.get(key, (None,))[0] in made:  # the first question that follows from an answer
            kind = items.get("page_break")
            rows.append(Item(type="page_break", answer_mode="optional", code=free("_pb", "1"),
                             config=kind.default_config(), texts=item_texts.new(kind, survey.languages,
                                                                                survey.default_language)))
            broke = True
        kind_key, base, config, texts = constructs.build(key, survey.languages)
        code = free(base)
        if code != base:
            duplicate.number_rows(kind_key, config, code)
        item = made[key] = Item(type=kind_key, answer_mode="nudge", code=code, config=config, texts=texts)
        rows.append(item)
    block.items = rows
    for position, item in enumerate(rows):
        item.position = position
    from app.extensions import db

    db.session.flush()
    for key, item in made.items():
        source, codes = FILTERS.get(key, (None, ()))
        if source in made:
            item.config = {**item.config, "when": {"item": made[source].id, "op": "in", "value": ", ".join(codes)}}
    return list(made.values())
