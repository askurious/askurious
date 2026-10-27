"""The survey as DDI Codebook 2.5 XML, for CESSDA: its Data Catalogue and Metadata Validator accept DDI Codebook
2.5/2.6 and Lifecycle 3.2/3.3, the European Question Bank only Codebook 2.5 (Ralf 2026-10-27). Built from the same
sources as the Lifecycle 4.0 file (app/ddi.py) and after the CESSDA profiles CDC 2.5 and EQB 2.5
(github.com/cessda/cessda.metadata.profiles): every text with its xml:lang, the study number with its agency, the
controlled vocabularies with vocab and vocabURI.

What goes in:
- docDscr: the description of this file (title, software).
- stdyDscr: title per language, study number and DOI, the team with affiliation and ORCID, funding, the publisher
  (app/ddi.publisher: the researchers' institution) as distributor, where the study is held; keywords and the CESSDA
  topic, the abstract, field period, country, region, unit of analysis, population, kind of data; time method,
  sampling procedure and mode of collection as vocabulary terms with words; the conditions of use from the licence;
  the publications entered on the export page.
- fileDscr: the data file (data.csv of a deposit) with its languages and number of variables.
- dataDscr: a var per column of the codebook with its language, label, question text in every survey language (a
  statement of a battery as the question, its stem before it as preQTxt), categories (missing codes marked), the
  validated construct as concept (vocab its source, vocabURI its DOI) and format.
- collDate with the wave of a panel (cycle), the waves as a series (serStmt, othrStdyMat/relStdy); otherMat for the
  other files of a deposit (material).
- the flow as far as Codebook 2.5 has room for it: under which condition a question is asked (var/universe, in words
  and as an expression, ddi.PROGRAM_LANGUAGE); a column worked out from others (Rechenwert, reverse-coded statement,
  weight) with var/derivation; the planned sample (targetSampleSize) and the weighting (dataColl/weight). Loops and
  random assignment have no element in Codebook 2.5; the Lifecycle file has them.
- the abstract, which CESSDA requires: the description, else the research question, else the title (real data only).

No answers go in. public=True (Zenodo, Coscine) describes the columns of their codebook.csv and leaves out the
wording of constructs without a general publication permission (export.withheld), as app/ddi.build does.
"""
import xml.etree.ElementTree as ET

from app import brand, ddi

NS = "ddi:codebook:2_5"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XML_LANG = ddi.XML_LANG
SCHEMA_LOCATION = "ddi:codebook:2_5 http://www.ddialliance.org/Specification/DDI-Codebook/2.5/XMLSchema/codebook.xsd"
# the names CESSDA's profiles give the vocabularies (attribute vocab)
VOCAB_NAMES = {"TimeMethod": "DDI Time Method", "SamplingProcedure": "DDI Sampling Procedure",
               "ModeOfCollection": "DDI Mode of Collection", "AnalysisUnit": "DDI Analysis Unit",
               "KindOfData": "DDI Kind of Data", "TypeOfInstrument": "DDI Type of Instrument"}


def _q(tag):
    return f"{{{NS}}}{tag}"


def _sub(parent, tag, text=None, lang=None, **attrs):
    el = ET.SubElement(parent, _q(tag))
    if text is not None:
        el.text = ddi._clean(text)
    if lang:
        el.set(XML_LANG, lang)
    for key, value in attrs.items():
        if value not in (None, ""):
            el.set(key, ddi._clean(value))
    return el


def _concept(parent, vocabulary, term, words="", lang="en"):
    """A vocabulary term (concept with vocab and vocabURI) and its words (txt) in an element of conceptualTextType."""
    version = ddi.VOCABULARIES[vocabulary]
    _sub(parent, "concept", term, vocab=VOCAB_NAMES[vocabulary], vocabURI=f"{ddi.CV}{vocabulary}/{version}")
    if words:
        _sub(parent, "txt", words, lang=lang)


def build(survey, lang=None, public=False, open_answers=False, material=()):
    """The DDI Codebook 2.5 XML of the survey as bytes (UTF-8); arguments as app/ddi.build; material: the names of
    the other files of a deposit (app/zenodo.package), listed as otherMat with what they hold."""
    from app import export, geoip, study_plan

    langs = ddi._languages(survey)
    default = survey.default_language
    withheld = export.withheld(survey) if public else set()
    plan = ddi._plan(survey)
    sample = plan.get("sample") or {}
    agency = ddi.agency()

    root = ET.Element(_q("codeBook"))
    root.set("version", "2.5")
    root.set(XML_LANG, default)
    root.set(f"{{{XSI}}}schemaLocation", SCHEMA_LOCATION)

    def title_stmt(parent):
        stmt = _sub(parent, "titlStmt")
        _sub(stmt, "titl", survey.title, lang=default)
        for lng in langs[1:]:
            if ddi._title(survey, lng):
                _sub(stmt, "parTitl", ddi._title(survey, lng), lang=lng)
        return stmt

    # -- this file
    doc_cit = _sub(_sub(root, "docDscr"), "citation")
    title_stmt(doc_cit)
    _sub(_sub(doc_cit, "prodStmt"), "software", brand.name(), lang="en")

    # -- the study
    study = _sub(root, "stdyDscr")
    citation = _sub(study, "citation")
    stmt = title_stmt(citation)
    _sub(stmt, "IDNo", ddi.study_number(survey), lang="en", agency=agency)
    if ddi.doi(survey):
        _sub(stmt, "IDNo", ddi.doi(survey), lang="en", agency="DOI")
    team = ddi.creators(survey)
    if team:
        rsp = _sub(citation, "rspStmt")
        for name, affiliation, orcid in team:
            author = _sub(rsp, "AuthEnty", name, lang=default, affiliation=affiliation)
            if orcid:
                _sub(author, "ExtLink", URI=f"https://orcid.org/{orcid}", role="PID", title="ORCID")
    line, funder, grant = ddi.funding(survey)
    if funder or grant:
        prod = _sub(citation, "prodStmt")
        if funder:
            _sub(prod, "fundAg", funder, lang=default)
        if grant:
            _sub(prod, "grantNo", grant, lang=default, agency=funder or line or "unknown")
    publisher_name = ddi.publisher(survey)[0]
    if publisher_name:
        _sub(_sub(citation, "distStmt"), "distrbtr", publisher_name, lang=default)
    panel = ddi.series(survey)
    if panel:  # the waves of a panel (app/waves.py)
        ser = _sub(citation, "serStmt")
        _sub(ser, "serName", panel["name"], lang=default)
        _sub(ser, "serInfo", "Panel waves: " + ddi.series_text(panel), lang="en")
    url = ddi.holdings_url(survey)
    _sub(citation, "holdings", url, lang="en", URI=url)

    info = _sub(study, "stdyInfo")
    archive = study_plan.archive_of(plan.get("archive"))
    if archive["keywords"] or archive["topic"]:
        subject = _sub(info, "subject")
        for word in archive["keywords"]:
            _sub(subject, "keyword", word, lang=default)
        if archive["topic"]:
            _sub(subject, "topcClas", study_plan.topic_name(archive["topic"], "en"), lang="en",
                 vocab="CESSDA Topic Classification", vocabURI=study_plan.TOPIC_VOCABULARY)
    # CESSDA requires an abstract: the description, else the research question, else the title
    abstract = " ".join((survey.description or "").split()) or " ".join((survey.research_question or "").split()) \
        or survey.title
    _sub(info, "abstract", abstract, lang=default)
    summary = _sub(info, "sumDscr")
    cycle = ddi.wave_text(survey)  # the wave of a panel ("Wave 2 of 3")
    if survey.opened_at:
        _sub(summary, "collDate", f"{survey.opened_at:%Y-%m-%d}", lang="en", event="start", date=f"{survey.opened_at:%Y-%m-%d}",
             cycle=cycle)
        if survey.closed_at:
            _sub(summary, "collDate", f"{survey.closed_at:%Y-%m-%d}", lang="en", event="end",
                 date=f"{survey.closed_at:%Y-%m-%d}", cycle=cycle)
    country, region = sample.get("country") or "", sample.get("region") or ""
    if country:  # ISO 3166-1 alpha-2, as the CESSDA list CountryNamesAndCodes
        _sub(summary, "nation", geoip.country_name(country, "en") or country, lang="en", abbr=country)
    if region:
        _sub(summary, "geogCover", region, lang=default)
    _concept(_sub(summary, "anlyUnit", lang="en"), "AnalysisUnit", "Individual", "Individuals")
    population = ddi.population_text(survey)
    if population:
        _sub(summary, "universe", population, lang=default, clusion="I")
    qualitative = getattr(survey, "interview", None) is not None
    _concept(_sub(summary, "dataKind", lang="en"), "KindOfData", "Qualitative" if qualitative else "Quantitative",
             "Qualitative data" if qualitative else "Quantitative data")

    method = _sub(study, "method")
    collection = _sub(method, "dataColl")
    tm, sp = ddi.time_method(survey), ddi.sampling(survey)
    if tm:
        _concept(_sub(collection, "timeMeth", lang="en"), "TimeMethod", tm)
    how, note = ddi._method_text(survey), (sample.get("note") or "").strip()
    if sp or how or note:
        proc = _sub(collection, "sampProc", lang="en")
        if sp:
            _concept(proc, "SamplingProcedure", sp, " ".join(p for p in (ddi.sampling_text(survey), how) if p))
        elif how:
            _sub(proc, "txt", how, lang="en")
        if note:  # the researchers' own words, in the survey's language
            _sub(proc, "txt", note, lang=default)
    target, sample_words = ddi.sample_facts(survey)
    if target or sample_words:  # the planned sample of the research plan
        size = _sub(collection, "targetSampleSize")
        if target:
            _sub(size, "sampleSize", str(target))
        if sample_words:
            _sub(size, "sampleSizeFormula", sample_words)
    for term in ddi.modes(survey):
        _concept(_sub(collection, "collMode", lang="en"), "ModeOfCollection", term, ddi.mode_text(term))
    instrument = "Questionnaire.Semistructured" if qualitative else "Questionnaire.Structured"
    _concept(_sub(collection, "resInstru", lang="en"), "TypeOfInstrument", instrument,
             "Semi-structured interview guide" if qualitative else "Structured questionnaire")
    from app import weights

    if weights.active(survey):
        _sub(collection, "weight", "Weight of the participation: " + weights.codebook_note(survey, "en"), lang="en")
    conditions = ddi.access_text(survey)
    if conditions:
        _sub(_sub(_sub(study, "dataAccs"), "useStmt"), "restrctn", conditions, lang="en")
    pubs = ddi.publications(survey)
    if pubs or panel:
        other = _sub(study, "othrStdyMat")
        for k, label, number in (panel or {}).get("others", []):
            _sub(other, "relStdy", f"Wave {k}" + (f" ({label})" if label else "") + (f", study number {number}" if number else ""),
                 lang="en")
        for pub_doi, title in pubs:
            rel = _sub(other, "relPubl", lang="en")
            pub_stmt = _sub(_sub(rel, "citation"), "titlStmt")
            _sub(pub_stmt, "titl", title or pub_doi, lang="en")
            _sub(pub_stmt, "IDNo", pub_doi, lang="en", agency="DOI")
            _sub(rel, "ExtLink", URI=f"https://doi.org/{pub_doi}")

    # -- the data file and its columns
    book = ddi._codebook(survey, lang, public, open_answers)
    file = _sub(root, "fileDscr", ID="F1")
    text = _sub(file, "fileTxt")
    _sub(text, "fileName", ddi.data_file_name(survey, public), lang="en")
    _sub(text, "fileCont", "One row per participation; answers in the survey languages: " + ", ".join(langs) + ".",
         lang="en")
    _sub(_sub(text, "dimensns"), "varQnty", str(len(book)))
    _sub(text, "fileType", "CSV (UTF-8, comma separated)", lang="en")

    data = _sub(root, "dataDscr")
    column_item = ddi._columns_by_item(survey)
    asked_if = ddi.shown_if(survey)
    made = ddi.derived(survey, book, column_item)
    weight_col = weights.COLUMN[0] if weights.active(survey) else None
    constructs = {i.id: c for i in set(column_item.values()) if (c := ddi.construct_of(i))}
    for n, row in enumerate(book, start=1):
        col = row[0]
        item = column_item.get(col)
        pairs = ddi._pairs(row)
        kind = "code" if pairs else ddi._kind(row, item, col)
        var = _sub(data, "var", ID=f"V{n}", name=col, files="F1",
                   intrvl="contin" if kind == "number" else "discrete", wgt="wgt" if col == weight_col else None)
        var.set(XML_LANG, default)
        _sub(var, "labl", row[1] or col, lang=default)
        # a statement of a battery: its stem before the question, the statement as the question itself
        split = ddi.statement_texts(survey, item, col, langs, withheld)
        pre, texts = split if split else ({}, ddi.question_texts(survey, item, langs, withheld) if item is not None else {})
        if any(texts.values()):
            qstn = _sub(var, "qstn")
            for lng, words in pre.items():
                if words:
                    _sub(qstn, "preQTxt", words, lang=lng)
            for lng, words in texts.items():
                if words:
                    _sub(qstn, "qstnLit", words, lang=lng)
        conds = asked_if.get(item.id) if item is not None and item.id not in withheld else None
        if conds:  # under which condition the question is asked, in words and as an expression
            words = " and ".join(w for w, _ in conds if w)
            expr = " and ".join(f"({e})" if " or " in e else e for _, e in conds if e)
            _sub(var, "universe", "Asked only if " + (words or expr) + (f". Expression ({ddi.PROGRAM_LANGUAGE}): {expr}" if expr else ""),
                 lang="en", clusion="I")
        if row[4]:
            _sub(var, "txt", row[4], lang=default)
        missing = {str(m) for m in item.kind.missing_codes(item, ddi._base_column(item, col))} if item and pairs else set()
        for code, label in pairs:
            cat = _sub(var, "catgry", missing="Y" if code in missing else None)
            _sub(cat, "catValu", code)
            _sub(cat, "labl", label, lang=default)
        construct = constructs.get(item.id) if item is not None else None
        if construct is not None:  # the validated construct the question comes from, with its source
            _sub(var, "concept", construct[1], lang=default, vocab=construct[2] or None,
                 vocabURI=f"https://doi.org/{construct[3]}" if construct[3] else None)
        if col in made:  # worked out from other columns
            words, command, _ = made[col]
            derivation = _sub(var, "derivation")
            _sub(derivation, "drvdesc", words, lang="en")
            if command:
                _sub(derivation, "drvcmd", command, syntax=ddi.PROGRAM_LANGUAGE)
        numeric = kind == "number" or (pairs and all(ddi.VALUE_RE.fullmatch(f"{c}={lb}") for c, lb in pairs))
        _sub(var, "varFormat", type="numeric" if numeric else "character", schema="other")

    # -- the other files of a deposit
    for name in material:
        if name in ddi.PACKAGE_MATERIAL:
            other = _sub(root, "otherMat", type="Documentation", level="study", URI=name)
            _sub(other, "labl", name, lang="en")
            _sub(other, "txt", ddi.PACKAGE_MATERIAL[name], lang="en")

    ET.register_namespace("", NS)
    ET.register_namespace("xsi", XSI)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
