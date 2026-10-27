"""The survey as DDI Lifecycle 4.0 XML: study, data collection, questionnaire with its flow, questions, code lists,
variables and data file, for archives and catalogues; open to everyone (Ralf, 2026-10-27: no experimental feature).

DDI Lifecycle 4.0 is in public review since 2026-10-22 (DDI Alliance; comments until 2026-12-06): it keeps the content
of 3.3 and writes it as one flat ItemContainer of identifiable items that point at each other by URN
(urn:ddi:<agency>:<id>:<version>). Ralf chose to build on 4.0 directly (2026-10-27, option G of the DDI note in the
project library); the schema is checked by tests/exports/test_ddi.py against the XSD of the review package.

What goes in:
- StudyUnit: title in every survey language, the study number (UserID), the DOI of a Zenodo deposit, the team as
  creators with affiliation and ORCID (Individual), the publisher (the researchers' institution, publisher()), funding,
  the publications entered on the export page (OtherMaterial), the conditions of use from the licence (Archive),
  description as abstract, kind of data, unit of analysis; DataCollection with the field period and the mode of collection; Methodology with time
  method and sampling procedure, as far as the research plan names them (an open field stays out, nothing is guessed).
- Instrument: one Sequence for the survey, one per block (pages in order, "random order" as TypeOfSequence), a
  QuestionConstruct per question column, a StatementItem per element without answers (texts, images), an IfThenElse
  around an element shown only under a condition (the condition in words, as in the questionnaire).
- QuestionItem per answer column with the question text in every survey language and its response domain: codes
  (CodeList and Categories, missing codes marked as missing), numbers, dates or text.
- Variable per column of the codebook (also Askurious's own columns), with label, notes, the question it comes from
  and its representation; a PhysicalInstance names the data file of the CSV export.
Controlled vocabularies of the DDI Alliance (CESSDA Vocabulary Service, CC BY 4.0) carry the term, the vocabulary
URI and the term URI (CodeValue in 4.0).

No answers go in, only the questionnaire and the description of the columns. The download on the export page is
documentation for the team, like the Word questionnaire with codebook, and carries the full wording; the file in the
Zenodo and Coscine packages (public=True) describes the columns of their codebook.csv and leaves out the wording of
constructs without a general publication permission (export.withheld), as questionnaire.md there does.
"""
import re
import uuid
from urllib.parse import quote
import xml.etree.ElementTree as ET

from app import brand

NS = "ddi:instance:4_0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
DEFAULT_AGENCY = "de.askurious"
# the agency in every URN: a reverse domain name registered with the DDI Agency Registry; DDI_AGENCY in the
# configuration overrides it per installation
AGENCY_RE = re.compile(r"[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+")

CV = "http://rdf-vocabulary.ddialliance.org/cv/"
# (vocabulary, version): the versions current at the CESSDA Vocabulary Service on 2026-10-27
VOCABULARIES = {"ModeOfCollection": "5.0.1", "TimeMethod": "1.2.3", "SamplingProcedure": "2.0.2",
                "AnalysisUnit": "2.1.3", "KindOfData": "1.0.0", "TypeOfInstrument": "1.1.2"}
# term URIs of the vocabulary versions above (CESSDA Vocabulary Service, /v2/codes/<vocabulary>/<version>/en)
TERMS = {
    ("ModeOfCollection", "SelfAdministeredQuestionnaire.CAWI"): "557586d",
    ("ModeOfCollection", "SelfAdministeredQuestionnaire.Paper"): "6255fce",
    ("ModeOfCollection", "Interview"): "cb20d17",
    ("ModeOfCollection", "Interview.FaceToFace"): "9f669a4",
    ("ModeOfCollection", "Interview.Telephone"): "ae68e25",
    ("ModeOfCollection", "Interview.WebBased"): "1af2bfa",
    ("ModeOfCollection", "Experiment.WebBased"): "ed0d083",
    ("TimeMethod", "CrossSection"): "01558f3",
    ("TimeMethod", "Longitudinal.Panel"): "91403e2",
    ("TimeMethod", "Longitudinal.Panel.Interval"): "11cdacf",
    ("SamplingProcedure", "Nonprobability.Availability"): "90cbfe9",
    ("SamplingProcedure", "Nonprobability.RespondentAssisted"): "086e080",
    ("SamplingProcedure", "Nonprobability.Quota"): "f620230",
    ("SamplingProcedure", "Probability"): "0d2765b",
    ("SamplingProcedure", "TotalUniverseCompleteEnumeration"): "885b553",
    ("SamplingProcedure", "Nonprobability"): "4d8364f",
    ("SamplingProcedure", "Nonprobability.Purposive"): "45d8ec9",
    ("SamplingProcedure", "TheoreticalSampling"): "540b3b2",
    ("TimeMethod", "Longitudinal.TrendRepeatedCrossSection"): "8470cc4",
    ("SamplingProcedure", "Probability.SimpleRandom"): "38e8e88",
    ("SamplingProcedure", "Probability.SystematicRandom"): "f189f62",
    ("SamplingProcedure", "Probability.Stratified"): "55d038d",
    ("SamplingProcedure", "Probability.Cluster"): "1f6e5f3",
    ("SamplingProcedure", "Probability.Multistage"): "f91d8ca",
    ("AnalysisUnit", "Individual"): "2dd4472",
    ("KindOfData", "Quantitative"): "64f5c8c",
    ("KindOfData", "Qualitative"): "24f5e13",
    ("TypeOfInstrument", "Questionnaire.Structured"): "aa1571e",
    ("TypeOfInstrument", "Questionnaire.Semistructured"): "db1f13d",
}
COUNTRY_VOCABULARY = "https://vocabularies.cessda.eu/vocabulary/CountryNamesAndCodes/1.1.0"
# the interview guide's mode (app/interview.MODES); "gemischt" (mixed) stays the broad term
INTERVIEW_MODES = {"praesenz": "Interview.FaceToFace", "telefon": "Interview.Telephone", "video": "Interview.WebBased"}
# the research plan's recruitment (app/study_plan.RECRUITMENT) as a sampling procedure; an online panel only with its
# kind (an access panel is no probability sample, a probability panel is), a random sample in detail with its kind
SAMPLING = {"convenience": "Nonprobability.Availability", "snowball": "Nonprobability.RespondentAssisted",
            "quota": "Nonprobability.Quota", "random": "Probability", "census": "TotalUniverseCompleteEnumeration",
            "purposive": "Nonprobability.Purposive", "theoretical": "TheoreticalSampling"}
PANEL_SAMPLING = {"access": "Nonprobability", "probability": "Probability"}
RANDOM_SAMPLING = {"simple": "Probability.SimpleRandom", "systematic": "Probability.SystematicRandom",
                   "stratified": "Probability.Stratified", "cluster": "Probability.Cluster",
                   "multistage": "Probability.Multistage"}
# the research plan's design (app/study_plan.DESIGNS) as a time method; experiments and vignettes are cross-sections
TIME_METHOD = {"cross": "CrossSection", "experiment": "CrossSection", "vignettes": "CrossSection",
               "within": "Longitudinal.Panel", "panel": "Longitudinal.Panel",
               "trend": "Longitudinal.TrendRepeatedCrossSection"}


def agency():
    from flask import current_app, has_app_context

    value = (current_app.config.get("DDI_AGENCY") if has_app_context() else None) or DEFAULT_AGENCY
    return value if AGENCY_RE.fullmatch(value) else DEFAULT_AGENCY


class _Doc:
    """The ItemContainer under construction: items in order, each with a stable id from the survey and a key."""

    def __init__(self, survey):
        self.survey = survey
        self.agency = agency()
        self.version = str(getattr(survey, "qversion", None) or 1)
        self.base = f"{brand.public_base().rstrip('/')}/ddi/{survey.code}/"
        self.root = ET.Element(f"{{{NS}}}ItemContainer")
        self.items = []

    def ident(self, kind, key=""):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.base}{kind}/{key}"))

    def urn(self, ident):
        return f"urn:ddi:{self.agency}:{ident}:{self.version}"

    def item(self, kind, key=""):
        """A new identifiable item <kind> (appended later by add); returns (element, id)."""
        ident = self.ident(kind, key)
        el = ET.Element(_q(kind))
        for tag, value in (("URN", self.urn(ident)), ("Agency", self.agency), ("ID", ident), ("Version", self.version)):
            _sub(el, tag, value)
        return el, ident

    def ref(self, parent, tag, kind, ident):
        """<tag isReference="true"> pointing at an item of type `kind`."""
        el = _sub(parent, tag)
        el.set("isReference", "true")
        for t, value in (("URN", self.urn(ident)), ("Agency", self.agency), ("ID", ident), ("Version", self.version),
                         ("TypeOfObject", kind)):
            _sub(el, t, value)
        return el

    def inner(self, parent, tag, key=""):
        """An identifiable element inside an item (URN, Agency, ID, Version first)."""
        el = _sub(parent, tag)
        ident = self.ident(tag, key)
        for t, value in (("URN", self.urn(ident)), ("Agency", self.agency), ("ID", ident), ("Version", self.version)):
            _sub(el, t, value)
        return el


def _q(tag):
    return f"{{{NS}}}{tag}"


def _sub(parent, tag, text=None):
    el = ET.SubElement(parent, _q(tag))
    if text is not None:
        el.text = _clean(text)
    return el


def _clean(text):
    """Text as XML 1.0 allows it: no control characters (texts of the editor mark places with them)."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "", str(text))


def _lang(parent, tag, texts):
    """One <tag xml:lang> per language with a text; texts: {lang: text}."""
    for lang, text in texts.items():
        if text and str(text).strip():
            _sub(parent, tag, str(text).strip()).set(XML_LANG, lang)


def _code_value(parent, tag, vocabulary, term):
    """A controlled vocabulary term: the term, the vocabulary version's URI and the term's URI."""
    version = VOCABULARIES[vocabulary]
    el = _sub(parent, tag)
    _sub(el, "StringValue", term)
    _sub(el, "ControlledVocabularyURI", f"{CV}{vocabulary}/{version}")
    _sub(el, "ControlledVocabularyInstanceURI", f"{CV}{vocabulary}/{version}/{TERMS[(vocabulary, term)]}")
    return el


def _plain(text):
    from app.items.base import plain

    return plain(text or "").replace("\n", " ").strip()


# ---- what the survey says -------------------------------------------------------------------------------------------


def _languages(survey):
    return list(dict.fromkeys([survey.default_language, *(getattr(survey, "languages", None) or [])]))


def modes(survey):
    """Mode-of-collection terms of the survey (ModeOfCollection)."""
    from app import export

    if getattr(survey, "interview", None) is not None:  # the guide's interview mode (app/interview.MODES)
        from app import interview

        return [INTERVIEW_MODES.get(interview.state(survey).get("mode"), "Interview")]
    out = ["SelfAdministeredQuestionnaire.CAWI"]
    if getattr(survey, "id", None) is not None and export.has_paper(survey):
        out.append("SelfAdministeredQuestionnaire.Paper")
    if _plan(survey).get("design") in ("experiment", "vignettes"):
        out.append("Experiment.WebBased")
    return out


def _plan(survey):
    try:
        from app import study_plan

        return study_plan.plan_of(survey)
    except Exception:  # an old or broken plan must not stop the documentation  # noqa: BLE001
        return {}


def _method_text(survey):
    """What else the study says about its sample, in English words: the target and the expected exclusions and
    response of the research plan, the quotas, the weighting. "" if none of them is set."""
    sample = _plan(survey).get("sample") or {}
    parts = []
    if sample.get("target"):
        parts.append(f"Planned sample: {sample['target']} completed participations.")
    if sample.get("exclusions") is not None and sample.get("target"):
        parts.append(f"Expected exclusions by the quality rules: {sample['exclusions']} %.")
    if sample.get("recruitment") == "panel" and sample.get("response"):
        parts.append(f"Expected response rate of the panel: {sample['response']} %.")
    quotas = [q.name or q.code for q in (getattr(survey, "quotas", None) or [])]
    if quotas:
        parts.append("Quotas: " + "; ".join(quotas) + ".")
    try:
        from app import weights

        if weights.active(survey):
            parts.append("Weighting: " + weights.codebook_note(survey, "en") + ".")
    except Exception:  # noqa: BLE001 - the weighting note is a description; a broken setting must not stop the file
        pass
    return " ".join(parts)


def population_text(survey):
    """Who the survey speaks for: the population of the research plan; for an interview guide its target group
    with the more precise words after it ("Fachleute (Experteninterview): Pflegedienstleitungen in NRW")."""
    population = ((_plan(survey).get("sample") or {}).get("population") or "").strip()
    target = (getattr(survey, "interview", None) or {}).get("target") if getattr(survey, "interview", None) else ""
    if target:
        from app import interview

        name = interview.TARGETS.get(target, "")
        return f"{name}: {population}" if name and population else name or population
    return population


def time_method(survey):
    if getattr(survey, "sampling", None) is not None:  # a diary study: entries at intervals per person
        return "Longitudinal.Panel.Interval"
    if (getattr(survey, "waves", None) or {}).get("waves"):
        return "Longitudinal.Panel"
    from app import study_plan

    return TIME_METHOD.get(study_plan.design_key(_plan(survey).get("design") or ""))


def sampling(survey):
    sample = _plan(survey).get("sample") or {}
    how = sample.get("recruitment") or ""
    if how == "panel":
        return PANEL_SAMPLING.get(sample.get("panel_kind") or "")
    if how == "random":
        return RANDOM_SAMPLING.get(sample.get("random_kind") or "", "Probability")
    return SAMPLING.get(how)


# ---- what both DDI files (Lifecycle 4.0 here, Codebook 2.5 in app/ddi_codebook.py) say about the study -----------


def study_number(survey):
    """The study's own number: the survey's stable id, under the agency (agency())."""
    return survey.uid or survey.code


def doi(survey):
    """The DOI of the survey's Zenodo deposit (reserved or published), or ""; DataCite manages Zenodo's DOIs."""
    return ((getattr(survey, "zenodo", None) or {}).get("doi") or "").strip()


def owner(survey):
    """The member who created the survey, else the first in the author order; None without members."""
    members = list(survey.members)
    created = getattr(survey, "created_by_id", None)
    return next((m for m in members if created and m.user_id == created), members[0] if members else None)


def publisher(survey):
    """(name, from an institution): who publishes the study (Ralf 2026-10-27: the researchers, not the operator of
    the installation): the owner's affiliation, else the first affiliation in the author order, else the owner's
    name (from an institution = False, the archive box asks for the institution)."""
    first = owner(survey)
    for m in ([first] if first else []) + list(survey.members):
        if (m.affiliation or "").strip():
            return m.affiliation.strip(), True
    return ((first.display_name if first else "") or "").strip(), False


def creators(survey):
    """[(name, affiliation, orcid)] of the team in the author order; orcid "" without one."""
    return [(m.display_name, (m.affiliation or "").strip(), m.orcid_id or "") for m in survey.members if m.display_name]


def _consent_config(survey):
    consent = getattr(survey, "consent_item", None)
    return (consent.config or {}) if consent is not None else {}


def funding(survey):
    """(line, funder, grant number) of the consent ("Förderung"); "" where not given."""
    config = _consent_config(survey)
    line = (config.get("funding") or "").strip()
    funder = (config.get("funder") or "").strip() if "funder" in config or "grant_number" in config else line
    return line, funder, (config.get("grant_number") or "").strip()


def licence(survey):
    """(name, url) of the data licence of the consent, or None (app/cdif.licence)."""
    try:
        from app import cdif

        return cdif.licence(survey)
    except Exception:  # noqa: BLE001 - a broken consent must not stop the documentation
        return None


def access_text(survey):
    """The conditions of use in English words, from the licence; "" without one."""
    found = licence(survey)
    if not found:
        return ""
    name, url = found
    return f"The data may be used under the licence {name} ({url})." if url else f"Licence: {name}."


def publications(survey):
    """[(doi, title)] of the publications the researchers entered (Export > "Veröffentlichungen", app/citations.py),
    without the survey's own data set."""
    if getattr(survey, "id", None) is None:
        return []
    from app import citations

    own = doi(survey).lower()
    return [(d, ((meta or {}).get("title") or "").strip()) for d, meta in citations.for_survey(survey) if d.lower() != own]


def mode_text(term):
    """A mode of collection in English words."""
    return MODE_NAMES.get(term, ("", term))[1]


def sampling_text(survey):
    """The recruitment of the research plan in English words, "" if open."""
    from app import study_plan

    how = (_plan(survey).get("sample") or {}).get("recruitment") or ""
    return study_plan.RECRUITMENT_EN.get(how, "") if how else ""


def data_file_name(survey, public):
    """The data file the columns describe: data.csv of a deposit, else the CSV export's file name."""
    from app import export

    return "data.csv" if public else export.filename(survey, "data")


def holdings_url(survey):
    """Where the study can be found: its DOI, else its participant link."""
    if doi(survey):
        return f"https://doi.org/{doi(survey)}"
    from app.links import participant_url

    return participant_url(survey)


def question_texts(survey, item, langs, withheld):
    """{lang: question text} of an element in the survey languages (only translations that exist); {} for an
    instrument whose wording a deposit leaves out (withheld)."""
    if item.id in withheld:
        return {}
    default = survey.default_language
    return {lng: _plain(((item.t(lng) if lng == default else item.lang_texts(lng)) or {}).get("question")) for lng in langs}


# ---- the document ---------------------------------------------------------------------------------------------------


def build(survey, lang=None, public=False, open_answers=False):
    """The DDI Lifecycle 4.0 XML of the survey as bytes (UTF-8). lang: the language of Askurious's own labels and
    notes in the codebook part ("de"/"en"; None: the reader's). public: for a deposit (Zenodo, Coscine): the columns
    of its codebook.csv, without the wording of instruments that have no general publication permission
    (export.withheld); open_answers: the deposit holds the open answers (export.codebook_rows)."""
    from app import export

    doc = _Doc(survey)
    langs = _languages(survey)
    withheld = export.withheld(survey) if public else set()

    study, study_id = doc.item("StudyUnit")
    collection, collection_id = doc.item("DataCollection")
    instrument, instrument_id = doc.item("Instrument")
    method_id = None

    # -- questions, flow and code lists
    questions, constructs_, categories, code_lists = [], [], {}, {}
    question_of = {}  # column -> question item id

    def code_list(pairs, missing):
        """The id of the CodeList of these (code, label) pairs; equal lists are written once."""
        key = tuple(pairs) + (("missing",) + tuple(sorted(missing)),)
        if key in code_lists:
            return code_lists[key]
        el, ident = doc.item("CodeList", repr(key))
        _lang(el, "Label", {survey.default_language: " / ".join(label for _, label in pairs)[:250]})
        for code, label in pairs:
            cat_key = (code, label, code in missing)
            if cat_key not in categories:  # one Category per answer, shared by the lists that use it
                cat, cat_id = doc.item("Category", repr(cat_key))
                _lang(cat, "Label", {survey.default_language: label})
                if code in missing:
                    _sub(cat, "IsMissing", "true")
                categories[cat_key] = (cat_id, cat)
            cat_id = categories[cat_key][0]
            c, _ = doc.item("Code", repr((key, code)))
            c.tag = _q("Code")
            el.append(c)
            doc.ref(c, "CategoryReference", "Category", cat_id)
            _sub(_sub(c, "Value"), "StringValue", code)
        code_lists[key] = (ident, el)
        return code_lists[key]

    book = _codebook(survey, lang, public, open_answers)
    column_item = _columns_by_item(survey)

    def response_domain(parent, col, row, item):
        pairs = _pairs(row)
        missing = {str(m) for m in item.kind.missing_codes(item, _base_column(item, col))} if item else set()
        if pairs:
            ident, _ = code_list(pairs, missing)
            dom = _sub(parent, "ResponseDomain")
            dom.set(f"{{{XSI}}}type", "ddi:CodeDomainType")
            doc.ref(dom, "CodeListReference", "CodeList", ident)
            return
        kind = _kind(row, item, col)
        dom = _sub(parent, "ResponseDomain")
        dom.set(f"{{{XSI}}}type", {"date": "ddi:DateTimeDomainType", "number": "ddi:NumericDomainType"}.get(
            kind, "ddi:TextDomainType"))

    top, top_id = doc.item("Sequence", "survey")
    _lang(top, "ConstructName", {"en": "questionnaire"})
    blocks = []
    badges = _badges(survey)
    for n, block in enumerate(survey.blocks, start=getattr(survey, "block_start", 1)):
        seq, seq_id = doc.item("Sequence", f"block-{block.id}")
        _lang(seq, "Label", {lng: (block.titles or {}).get(lng) for lng in langs})
        flow_text = "; ".join(badges.get(block.id, []))
        if flow_text:
            _lang(seq, "Description", {"en": f"Block {n}. Flow: {flow_text}"})
        if block.randomize_items:
            _sub(_sub(seq, "TypeOfSequence"), "StringValue", "RandomOrderWithinPages")
        children = []
        for page in block.pages():
            for item in page:
                if item.type == "page_break":
                    continue
                children += _item_constructs(doc, survey, item, langs, withheld, public, book, question_of, questions,
                                             constructs_, response_domain, column_item)
        for kind, ident in children:
            doc.ref(seq, "ControlConstructReference", kind, ident)
        blocks.append((seq_id, seq))
    for seq_id, _ in blocks:
        doc.ref(top, "ControlConstructReference", "Sequence", seq_id)

    # -- variables
    variables = []
    for row in book:
        col = row[0]
        var, var_id = doc.item("Variable", col)
        _lang(var, "VariableName", {"en": col})
        _lang(var, "Label", {survey.default_language: row[1] or col})
        if row[4]:
            _lang(var, "Description", {survey.default_language: row[4]})
        if col in question_of:
            doc.ref(var, "QuestionReference", "QuestionItem", question_of[col])
        rep = _sub(var, "VariableRepresentation")
        pairs = _pairs(row)
        item = column_item.get(col)
        if pairs:
            missing = {str(m) for m in item.kind.missing_codes(item, _base_column(item, col))} if item else set()
            ident, _ = code_list(pairs, missing)
            value = _sub(rep, "ValueRepresentation")
            value.set(f"{{{XSI}}}type", "ddi:CodeRepresentationBaseType")
            doc.ref(value, "CodeListReference", "CodeList", ident)
        else:
            kind = _kind(row, item, col)
            value = _sub(rep, "ValueRepresentation")
            value.set(f"{{{XSI}}}type", {"date": "ddi:DateTimeRepresentationBaseType",
                                         "number": "ddi:NumericRepresentationBaseType"}.get(
                kind, "ddi:TextRepresentationBaseType"))
        variables.append((var_id, var))

    # -- schemes and products
    qscheme, qscheme_id = doc.item("QuestionScheme")
    for ident, _ in questions:
        doc.ref(qscheme, "QuestionItemReference", "QuestionItem", ident)
    cscheme, cscheme_id = doc.item("ControlConstructScheme")
    for kind, ident, _ in constructs_:
        doc.ref(cscheme, "ControlConstructReference", kind, ident)
    for seq_id, _ in blocks:
        doc.ref(cscheme, "ControlConstructReference", "Sequence", seq_id)
    doc.ref(cscheme, "ControlConstructReference", "Sequence", top_id)
    catscheme, catscheme_id = doc.item("CategoryScheme")
    for ident, _ in categories.values():
        doc.ref(catscheme, "CategoryReference", "Category", ident)
    clscheme, clscheme_id = doc.item("CodeListScheme")
    for ident, _ in code_lists.values():
        doc.ref(clscheme, "CodeListReference", "CodeList", ident)
    vscheme, vscheme_id = doc.item("VariableScheme")
    for ident, _ in variables:
        doc.ref(vscheme, "VariableReference", "Variable", ident)
    logical, logical_id = doc.item("LogicalProduct")
    doc.ref(logical, "CategorySchemeReference", "CategoryScheme", catscheme_id)
    doc.ref(logical, "CodeListSchemeReference", "CodeListScheme", clscheme_id)
    doc.ref(logical, "VariableSchemeReference", "VariableScheme", vscheme_id)
    physical, physical_id = doc.item("PhysicalInstance")
    data_citation = _sub(physical, "Citation")  # the languages of the data file: those of the survey
    _lang(data_citation, "Title", {"en": data_file_name(survey, public)})
    for lng in langs:
        _sub(_sub(data_citation, "Language"), "StringValue", lng)
    _sub(_sub(_sub(physical, "DataFileIdentification"), "DataFileURI"), "AnyURIValue", quote(data_file_name(survey, public)))

    # -- instrument and data collection
    _lang(instrument, "Label", {lng: _title(survey, lng) for lng in langs})
    _code_value(instrument, "TypeOfInstrument", "TypeOfInstrument",
                "Questionnaire.Semistructured" if getattr(survey, "interview", None) is not None
                else "Questionnaire.Structured")
    doc.ref(instrument, "ControlConstructReference", "Sequence", top_id)
    for lng in langs:
        _sub(_sub(instrument, "FieldedLanguages"), "StringValue", lng)

    tm, sp, how = time_method(survey), sampling(survey), _method_text(survey)
    note = ((_plan(survey).get("sample") or {}).get("note") or "").strip()
    if tm or sp or how or note:
        method, method_id = doc.item("Methodology")
        if how:
            _lang(method, "Description", {"en": how})
        if note:  # the researchers' own words, in the survey's language
            _lang(method, "Description", {survey.default_language: note})
        if tm:
            _code_value(doc.inner(method, "TimeMethod"), "TypeOfTimeMethod", "TimeMethod", tm)
        if sp:
            procedure = doc.inner(method, "SamplingProcedure")
            _code_value(procedure, "TypeOfSamplingProcedure", "SamplingProcedure", sp)
            _lang(procedure, "Description", {"en": sampling_text(survey)})
    _lang(collection, "Label", {lng: _title(survey, lng) for lng in langs})
    if method_id:
        doc.ref(collection, "MethodologyReference", "Methodology", method_id)
    event = doc.inner(collection, "CollectionEvent")
    if survey.opened_at:
        date = _sub(event, "DataCollectionDate")
        _sub(date, "StartDate", f"{survey.opened_at:%Y-%m-%d}")
        if survey.closed_at:
            _sub(date, "EndDate", f"{survey.closed_at:%Y-%m-%d}")
    for term in modes(survey):
        mode = doc.inner(event, "ModeOfCollection", term)
        _code_value(mode, "TypeOfModeOfCollection", "ModeOfCollection", term)
        _lang(mode, "Description", {"en": mode_text(term)})
    doc.ref(event, "InstrumentReference", "Instrument", instrument_id)
    doc.ref(collection, "QuestionSchemeReference", "QuestionScheme", qscheme_id)
    doc.ref(collection, "ControlConstructSchemeReference", "ControlConstructScheme", cscheme_id)
    doc.ref(collection, "InstrumentReference", "Instrument", instrument_id)

    # -- the population the survey speaks for (research plan, "Grundgesamtheit")
    universe_id = None
    population = population_text(survey)
    if population:
        universe, universe_id = doc.item("Universe")
        _lang(universe, "Label", {survey.default_language: population})
        region = (_plan(survey).get("sample") or {}).get("region")
        if region:
            _lang(universe, "GeographyOfUniverse", {survey.default_language: region})
        _sub(universe, "IsInclusive", "true")

    # -- the people with an ORCID, the publications, the conditions of use
    people = []  # (id, element) of an Individual per creator with ORCID
    for name, _, orcid in creators(survey):
        if not orcid:
            continue
        person, person_id = doc.item("Individual", f"orcid-{orcid}")
        ident = _sub(person, "IndividualIdentification")
        _lang(_sub(ident, "IndividualName"), "FullName", {"en": name})
        researcher = _sub(ident, "ResearcherID")
        _sub(_sub(researcher, "TypeOfID"), "StringValue", "ORCID")
        _sub(researcher, "ResearcherIdentification", orcid)
        _sub(researcher, "URI", f"https://orcid.org/{orcid}")
        people.append((orcid, person_id, person))
    person_of = {orcid: ident for orcid, ident, _ in people}
    materials = []
    for pub_doi, title in publications(survey):
        material, material_id = doc.item("OtherMaterial", f"doi-{pub_doi.lower()}")
        _sub(_sub(material, "TypeOfMaterial"), "StringValue", "Publication")
        cited = _sub(material, "Citation")
        _lang(cited, "Title", {"en": title or pub_doi})
        number = _sub(cited, "InternationalIdentifier")
        _sub(number, "IdentifierContent", pub_doi)
        _sub(_sub(number, "ManagingAgency"), "StringValue", "DOI")
        _sub(material, "ExternalURLReference", f"https://doi.org/{quote(pub_doi, safe='/:;()._-')}")
        materials.append((material_id, material))
    conditions = access_text(survey)
    archive_id = None
    if conditions:
        conditions_of_use, archive_id = doc.item("Archive")
        _lang(conditions_of_use, "Label", {"en": "Conditions of use"})
        access = doc.inner(_sub(conditions_of_use, "ArchiveSpecific"), "DefaultAccess")
        _lang(access, "AccessConditions", {"en": conditions})

    # -- the study, its parts in the order of the schema
    number = _sub(study, "UserID")  # the study number under the agency
    _sub(number, "StringValue", study_number(survey))
    _sub(_sub(number, "TypeOfUserID"), "StringValue", doc.agency)
    for material_id, _ in materials:
        doc.ref(study, "RelatedOtherMaterialReference", "OtherMaterial", material_id)
    software = _sub(study, "Software")
    _lang(software, "SoftwareName", {"en": brand.name()})
    citation = _sub(study, "Citation")
    _lang(citation, "Title", {lng: _title(survey, lng) for lng in langs})
    for name, affiliation, orcid in creators(survey):
        creator = _sub(citation, "Creator")
        creator_name = _sub(creator, "CreatorName")
        _lang(creator_name, "Name", {"en": name})
        if affiliation:
            _sub(creator_name, "Affiliation", affiliation)
        if orcid:
            doc.ref(creator, "CreatorReference", "Individual", person_of[orcid])
    publisher_name = publisher(survey)[0]
    if publisher_name:
        _lang(_sub(_sub(citation, "Publisher"), "PublisherName"), "Name", {"en": publisher_name})
    for lng in langs:
        _sub(_sub(citation, "Language"), "StringValue", lng)
    if doi(survey):
        number = _sub(citation, "InternationalIdentifier")
        _sub(number, "IdentifierContent", doi(survey))
        _sub(_sub(number, "ManagingAgency"), "StringValue", "DataCite")
    abstract = " ".join((survey.description or "").split())
    if abstract:
        _lang(study, "Abstract", {survey.default_language: abstract})
    if universe_id:
        doc.ref(study, "UniverseReference", "Universe", universe_id)
    line, _, grant = funding(survey)
    if line or grant:
        money = _sub(study, "FundingInformation")
        if grant:
            _sub(money, "GrantNumber", grant)
        if line:
            _lang(money, "Description", {survey.default_language: line})
    sample = _plan(survey).get("sample") or {}
    country, region = sample.get("country") or "", sample.get("region") or ""
    from app import study_plan

    archive = study_plan.archive_of(_plan(survey).get("archive"))
    if country or region or survey.opened_at or archive["keywords"] or archive["topic"]:
        coverage = _sub(study, "Coverage")
        if archive["keywords"] or archive["topic"]:  # topic of the CESSDA Topic Classification, the keywords
            topical = doc.inner(coverage, "TopicalCoverage")
            if archive["topic"]:
                subject = _sub(topical, "Subject")
                _sub(subject, "MultilingualStringValue", study_plan.topic_name(archive["topic"], "en")).set(XML_LANG, "en")
                _sub(subject, "ControlledVocabularyID", archive["topic"])
            for word in archive["keywords"]:
                _sub(_sub(topical, "Keyword"), "MultilingualStringValue", word).set(XML_LANG, survey.default_language)
        if country or region:
            spatial = doc.inner(coverage, "SpatialCoverage")
            if region:
                _lang(spatial, "Description", {survey.default_language: region})
            if country:  # ISO 3166-1 alpha-2, as the CESSDA list CountryNamesAndCodes
                code = _sub(spatial, "CountryCode")
                _sub(code, "StringValue", country)
                _sub(code, "ControlledVocabularyURI", COUNTRY_VOCABULARY)
        if survey.opened_at:
            temporal = doc.inner(coverage, "TemporalCoverage")
            date = _sub(temporal, "ReferenceDate")
            _sub(date, "StartDate", f"{survey.opened_at:%Y-%m-%d}")
            if survey.closed_at:
                _sub(date, "EndDate", f"{survey.closed_at:%Y-%m-%d}")
    _code_value(study, "AnalysisUnit", "AnalysisUnit", "Individual")
    _code_value(study, "KindOfData", "KindOfData",
                "Qualitative" if getattr(survey, "interview", None) is not None else "Quantitative")
    doc.ref(study, "DataCollectionReference", "DataCollection", collection_id)
    doc.ref(study, "LogicalProductReference", "LogicalProduct", logical_id)
    doc.ref(study, "PhysicalInstanceReference", "PhysicalInstance", physical_id)
    if archive_id:
        doc.ref(study, "ArchiveReference", "Archive", archive_id)

    # -- write: the top-level reference, then the items in the order of the schema (ItemContainer is a choice list)
    doc.ref(doc.root, "TopLevelReference", "StudyUnit", study_id)
    items = ([("StudyUnit", study), ("DataCollection", collection), ("Instrument", instrument)]
             + ([("Archive", conditions_of_use)] if archive_id else [])
             + [("Individual", el) for _, _, el in people]
             + [("OtherMaterial", el) for _, el in materials]
             + ([("Methodology", method)] if method_id else [])
             + [("QuestionScheme", qscheme), ("ControlConstructScheme", cscheme), ("LogicalProduct", logical),
                ("CategoryScheme", catscheme), ("CodeListScheme", clscheme), ("VariableScheme", vscheme),
                ("PhysicalInstance", physical)]
             + [("Sequence", top)] + [("Sequence", s) for _, s in blocks]
             + [(kind, el) for kind, _, el in constructs_]
             + [("QuestionItem", el) for _, el in questions]
             + [("CodeList", el) for _, el in code_lists.values()]
             + [("Category", el) for _, el in categories.values()]
             + [("Variable", el) for _, el in variables]
             + ([("Universe", universe)] if universe_id else []))
    for kind, el in _schema_order(items):
        doc.root.append(el)
    ET.register_namespace("ddi", NS)
    ET.register_namespace("xsi", XSI)
    return ET.tostring(doc.root, encoding="utf-8", xml_declaration=True)


def _schema_order(items):
    """Items in the order the ItemContainer of the schema lists their types (its sequence of choices is ordered)."""
    order = {k: n for n, k in enumerate(CONTAINER_ORDER)}
    return sorted(items, key=lambda kv: order.get(kv[0], len(order)))


# the ItemContainer's element order (lifecycle40.xsd, ItemContainerType)
CONTAINER_ORDER = [
    "Archive", "Category", "CategoryScheme", "CodeList", "CodeListScheme", "ControlConstructScheme", "DataCollection",
    "IfThenElse", "Individual", "Instrument", "LogicalProduct", "Methodology", "OtherMaterial", "PhysicalInstance",
    "QuestionConstruct",
    "QuestionItem", "QuestionScheme", "Sequence", "StatementItem", "StudyUnit", "Universe", "Variable",
    "VariableScheme"]


def _title(survey, lang):
    """The survey's title in `lang`; only a translation that exists (no fallback to the default language)."""
    if lang == survey.default_language:
        return survey.title
    return (getattr(survey, "title_translations", None) or {}).get(lang)





def _badges(survey):
    from app import flow

    try:
        return flow.badges(survey, "en")
    except Exception:  # noqa: BLE001 - the flow words are a note; a survey without a flow tree has none
        return {}


def _codebook(survey, lang, public=False, open_answers=False):
    """Rows (variable, label, type, values, notes) as the codebook CSV writes them (export.codebook_rows)."""
    from app import export

    return export.codebook_rows(survey, public=public, open_answers=open_answers, lang=lang)


def _columns_by_item(survey):
    """{column: item} of every answer column, loop passes included (<col>_<n>)."""
    from app import export

    out = {}
    for item in export._items(survey):
        passes = [n for n, _ in (export._loop_rows(item) or [])]
        for col, _ in item.kind.export_columns(item):
            for c in ([f"{col}_{n}" for n in passes] or [col]):
                out[c] = item
    return out


def _base_column(item, col):
    """The column of the item's own export_columns for a column of a loop pass (<col>_<n>)."""
    own = {c for c, _ in item.kind.export_columns(item)}
    if col in own:
        return col
    base, _, n = col.rpartition("_")
    return base if base in own and n.isdigit() else col


VALUE_RE = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*=\s*(.+?)\s*")
CODE_RE = re.compile(r"\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*")


def _pairs(row):
    """[(code, label)] of a codebook entry "1 = ja; 2 = nein" (labels hold no ";", see export._label), or []."""
    values = row[3] or ""
    parts = [p for p in values.split(";") if p.strip()]
    if not parts:
        return []
    for pattern in (VALUE_RE, CODE_RE):
        found = [pattern.fullmatch(p) for p in parts]
        if all(found):
            return [(f.group(1), f.group(2)) for f in found]
    return []


FREE_TYPES = ("Text", "Postleitzahl")
NUMBER_COLUMNS = {"resp_id", "qversion", "duration_s", "duration_rel", "speeder", "last_page", "qc_rsi", "qc_marked"}


def _kind(row, item, col):
    """"date" | "number" | "text" of a column without codes."""
    if item is not None:
        if item.kind.date_kind(item, _base_column(item, col)):
            return "date"
        if row[2] in FREE_TYPES:
            return "text"
        if item.kind.column_level(item, _base_column(item, col)) == "metric":
            return "number"
        return "text"
    if col in ("started", "finished", "last_active"):
        return "date"
    return "number" if col in NUMBER_COLUMNS else "text"


def _item_constructs(doc, survey, item, langs, withheld, public, book, question_of, questions, constructs_, response_domain,
                     column_item):
    """The constructs of one element: [(kind, id)] for its block's sequence."""
    from app import item_condition

    rows = {r[0]: r for r in book}
    cols = [c for c, _ in item.kind.export_columns(item)]
    loop_cols = [c for c, it in column_item.items() if it is item and c not in cols]
    out = []
    hidden = item.id in withheld  # no wording of a protected instrument in a deposit
    texts = question_texts(survey, item, langs, withheld)
    if not cols:  # a text, an image, a stimulus: shown, nothing answered
        st, st_id = doc.item("StatementItem", f"item-{item.id}")
        _lang(st, "ConstructName", {"en": item.code})
        if any(texts.values()):
            display = _sub(st, "DisplayText")
            content = _sub(display, "TextContent")
            content.set(f"{{{XSI}}}type", "ddi:LiteralTextType")
            _lang(content, "Text", texts)
        constructs_.append(("StatementItem", st_id, st))
        out.append(("StatementItem", st_id))
    else:
        for col in cols + loop_cols:
            if public and col not in rows:  # a column the deposit leaves out (open answers, technical columns)
                continue
            q, q_id = doc.item("QuestionItem", col)
            _lang(q, "QuestionItemName", {"en": col})
            label = (rows.get(col) or (col, ""))[1]
            if label:
                _lang(q, "Label", {survey.default_language: label})
            if any(texts.values()):
                qt = _sub(q, "QuestionText")
                content = _sub(qt, "TextContent")
                content.set(f"{{{XSI}}}type", "ddi:LiteralTextType")
                _lang(content, "Text", texts)
            if col in rows:
                response_domain(q, col, rows[col], item)
            questions.append((q_id, q))
            question_of[col] = q_id
            qc, qc_id = doc.item("QuestionConstruct", col)
            _lang(qc, "ConstructName", {"en": col})
            doc.ref(qc, "QuestionReference", "QuestionItem", q_id)
            constructs_.append(("QuestionConstruct", qc_id, qc))
            out.append(("QuestionConstruct", qc_id))
    if item_condition.of(item) and out and not hidden:
        ite, ite_id = doc.item("IfThenElse", f"item-{item.id}")
        cond = _sub(ite, "IfCondition")
        _lang(cond, "Description", {"en": "Shown only if: " + item_condition.text(item, survey.default_language, "en")})
        # the element's constructs as the "then" branch: one sequence around them
        then, then_id = doc.item("Sequence", f"then-{item.id}")
        for kind, ident in out:
            doc.ref(then, "ControlConstructReference", kind, ident)
        doc.ref(ite, "ThenConstructReference", "Sequence", then_id)
        constructs_.append(("Sequence", then_id, then))
        constructs_.append(("IfThenElse", ite_id, ite))
        return [("IfThenElse", ite_id)]
    return out


# ---- what an archive gets (the box "Für Datenarchive" on the last step of the wizard) ------------------------------


def readiness(survey):
    """The study description a data archive asks for (CESSDA Metadata Model, DDI), as rows {state, de, en, value_de,
    value_en, why_de, why_en, step}: state "ok" (given), "auto" (Askurious sets it), "missing" (recommended, not
    given) or "optional"; value: what is given; why: what it is for, shown while it is missing; step: the wizard
    step to fill it in. Shown on the wizard's overview (wizard/_summary.html; Ralf 2026-10-27, variant A)."""
    from app import geoip, study_plan

    plan = _plan(survey)
    sample = plan.get("sample") or {}
    archive = study_plan.archive_of(plan.get("archive"))
    mode = MODE_NAMES.get(modes(survey)[0], ("", ""))
    kind = ("Qualitativ", "qualitative") if getattr(survey, "interview", None) is not None else ("Quantitativ", "quantitative")
    tm, sp = time_method(survey), sampling(survey)
    consent = getattr(survey, "consent_item", None)
    config = (consent.config or {}) if consent is not None else {}
    funding = config.get("funding") or ""
    licence = None
    try:
        from app import cdif

        licence = cdif.licence(survey)
    except Exception:  # noqa: BLE001
        licence = None
    design = study_plan.design_key(plan.get("design") or "")
    how = sample.get("recruitment") or ""
    orcid = [m.display_name for m in survey.members if m.orcid_id]

    def row(state, de, en, step="", value=("", ""), why=("", "")):
        return {"state": state, "de": de, "en": en, "value_de": value[0], "value_en": value[1], "why_de": why[0],
                "why_en": why[1], "step": step}

    def given(ok, optional=False):
        return "ok" if ok else "optional" if optional else "missing"

    short = lambda text: text if len(text) <= 70 else text[:67] + "…"  # noqa: E731
    population = population_text(survey)
    publisher_name, from_institution = publisher(survey)
    own_doi = doi(survey)
    return [
        row("ok", "Titel", "Title", "projekt", (short(survey.title), short(survey.title))),
        row(given(from_institution), "Herausgeber", "Publisher", "team", (short(publisher_name), short(publisher_name)),
            ("die Organisation der Person, die die Befragung angelegt hat; tragen Sie sie im Team ein",
             "the organisation of the person who created the survey; enter it in the team")),
        row(given((survey.description or "").strip()), "Beschreibung (Zusammenfassung)", "Description (abstract)", "projekt",
            why=("für Einwilligung, DDI und Zenodo", "for the consent, DDI and Zenodo")),
        row(given(orcid), "Team mit ORCID", "Team with ORCID", "team", (", ".join(orcid), ", ".join(orcid)),
            ("für die Autorenliste in Archiven", "for the list of authors in archives")),
        row(given(tm), "Zeitbezug", "Time method", "studienplan",
            (study_plan.DESIGNS.get(design, "Tagebuchstudie" if tm else ""), study_plan.DESIGNS_EN.get(design, "diary survey" if tm else "")),
            ("Querschnitt, Panel oder Trend", "cross-section, panel or trend")),
        row(given(sp), "Auswahlverfahren", "Sampling procedure", "studienplan",
            (study_plan.RECRUITMENT.get(how, ""), study_plan.RECRUITMENT_EN.get(how, "")),
            ("sagt, für wen die Ergebnisse gelten", "says whom the results hold for")),
        row(given(population), "Grundgesamtheit", "Population", "studienplan", (short(population), short(population)),
            ("für wen die Ergebnisse gelten sollen", "whom the results are meant to speak for")),
        row(given(sample.get("country")), "Land", "Country", "studienplan",
            (geoip.country_name(sample.get("country"), "de"), geoip.country_name(sample.get("country"), "en")) if sample.get("country") else ("", ""),
            ("für die Suche in Katalogen", "for search in catalogues")),
        row(given(licence), "Lizenz", "Licence", "einwilligung", (config.get("license") or "", config.get("license") or ""),
            ("wer die Daten nachnutzen darf", "who may reuse the data")),
        row(given(archive["keywords"] or archive["topic"], optional=True), "Schlagwörter und Thema", "Keywords and topic", "projekt",
            (", ".join(archive["keywords"]), ", ".join(archive["keywords"])), ("für die Suche in Archiven", "for search in archives")),
        row(given(funding, optional=True), "Förderung", "Funding", "team", (short(funding), short(funding)),
            ("nur falls gefördert", "only if funded")),
        row(given(own_doi, optional=True), "DOI", "DOI", value=(own_doi, own_doi),
            why=("kommt mit der Veröffentlichung bei Zenodo", "comes with publishing on Zenodo")),
        row("auto", "Kennung der Befragung", "Survey identifier", value=(f"{study_number(survey)} ({agency()})",
                                                            f"{study_number(survey)} ({agency()})")),
        row("auto", "Erhebungsart", "Mode of collection", value=(mode[0], mode[1])),
        row("auto", "Datenart · Analyseeinheit", "Kind of data · unit of analysis", value=(f"{kind[0]} · Individuum", f"{kind[1]} · individual")),
        row("auto", "Erhebungszeitraum", "Field period", value=("ab dem Start der Datenerhebung", "from the start of data collection")),
    ]


MODE_NAMES = {"SelfAdministeredQuestionnaire.CAWI": ("Selbstausgefüllter Fragebogen: Webbasiert (CAWI)",
                                                     "Self-administered questionnaire: Web-based (CAWI)"),
              "Interview": ("Interview", "Interview"),
              "Interview.FaceToFace": ("Persönliches Interview", "Face-to-face interview"),
              "Interview.Telephone": ("Telefonisches Interview", "Telephone interview"),
              "Interview.WebBased": ("Webbasiertes Interview", "Web-based interview"),
              "SelfAdministeredQuestionnaire.Paper": ("Selbstausgefüllter Fragebogen: Papier",
                                                      "Self-administered questionnaire: Paper"),
              "Experiment.WebBased": ("Webbasiertes Experiment", "Web-based experiment")}
