"""DDI Lifecycle 4.0 export (app/ddi.py): valid against the XSD of the DDI Alliance's public review package
(tests/data/ddi, CC BY 4.0), with questions, code lists, variables, flow and the controlled vocabularies."""
import gzip
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from app import ddi
from app.extensions import db
from app.models import Survey
from tests.conftest import add

etree = pytest.importorskip("lxml.etree")
DATA = Path(__file__).resolve().parent.parent / "data" / "ddi"
NS = {"d": "ddi:instance:4_0"}


@pytest.fixture(scope="module")
def schema(tmp_path_factory):
    folder = tmp_path_factory.mktemp("ddi-xsd")
    text = gzip.decompress((DATA / "lifecycle40.xsd.gz").read_bytes()).decode("utf-8")
    # libxml2 (lxml) holds at most 24 digits in a decimal facet and refuses the schema's bounds of ±(2^96 − 1) on
    # ddi:decimal; without the two bounds the type keeps totalDigits 29 / fractionDigits 28 and Askurious writes no
    # decimal values of that size
    for bound in ("minInclusive", "maxInclusive"):
        text = text.replace(f'<xs:{bound} value="{"-" if bound == "minInclusive" else ""}79228162514264337593543950335" />', "")
    (folder / "lifecycle40.xsd").write_text(text, encoding="utf-8")
    (folder / "xml.xsd").write_bytes((DATA / "xml.xsd").read_bytes())
    return etree.XMLSchema(etree.parse(str(folder / "lifecycle40.xsd")))


def valid(schema, body):
    doc = etree.fromstring(body)
    if not schema.validate(doc):
        raise AssertionError("\n".join(str(e) for e in list(schema.error_log)[:20]))
    return doc


def build(client):
    add(client, "single_choice")                                    # 2
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "gender"), ("question", "Geschlecht?"), ("option_id", "a"), ("option_code", "1"), ("option_label", "weiblich"),
        ("option_id", "b"), ("option_code", "2"), ("option_label", "männlich")]))
    add(client, "likert")                                           # 3
    client.post("/surveys/1/editor/items/3", data=MultiDict([
        ("code", "A"), ("question", "Wie sehr stimmen Sie zu?"), ("points", "5"), ("labeling", "full"),
        ("stmt_id", "s1"), ("stmt_code", ""), ("stmt_text", "Aussage A")]))
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "kommentar", "question": "Ihr Kommentar"})


def test_ddi_export_is_valid_and_holds_questions_codes_and_variables(app, client, survey, schema):
    build(client)
    assert 'export/ddi-lifecycle.xml"' in client.get("/surveys/1/data").data.decode()  # the row under "Dokumentation"
    r = client.get("/surveys/1/export/ddi-lifecycle.xml")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.xml"')
    doc = valid(schema, r.data)
    names = doc.xpath("//d:Variable/d:VariableName/text()", namespaces=NS)
    assert {"resp_id", "gender", "kommentar"} <= set(names)
    # the single choice: a question with its text in German, a code list with both answers
    q = doc.xpath("//d:QuestionItem[d:QuestionItemName='gender']", namespaces=NS)[0]
    assert "Geschlecht?" in q.xpath(".//d:Text[@xml:lang='de']/text()", namespaces=NS)
    # English is a survey language without its own text here: no German text marked as English
    assert not q.xpath(".//d:Text[@xml:lang='en']", namespaces=NS)
    labels = set(doc.xpath("//d:Category/d:Label/text()", namespaces=NS))
    assert {"weiblich", "männlich"} <= labels
    # every reference points at an item in the file
    own = doc.xpath("//*[not(@isReference)]/d:URN/text()", namespaces=NS)
    refs = set(doc.xpath("//*[@isReference='true']/d:URN/text()", namespaces=NS))
    assert refs <= set(own) and len(own) == len(set(own))  # and no item twice
    # the study and its vocabularies
    assert doc.xpath("//d:StudyUnit/d:KindOfData/d:StringValue/text()", namespaces=NS) == ["Quantitative"]
    assert "SelfAdministeredQuestionnaire.CAWI" in doc.xpath(
        "//d:ModeOfCollection/d:TypeOfModeOfCollection/d:StringValue/text()", namespaces=NS)
    assert doc.xpath("//d:Instrument/d:TypeOfInstrument/d:StringValue/text()", namespaces=NS) == ["Questionnaire.Structured"]


def test_ddi_export_is_stable_and_open_to_viewers(app, client, survey, schema):
    """The same survey gives the same ids (uuid5 of survey and key), so a second export replaces the first; the file
    holds no answers, so the role "Ansehen" may download it like the codebook."""
    build(client)
    first = client.get("/surveys/1/export/ddi-lifecycle.xml").data
    assert client.get("/surveys/1/export/ddi-lifecycle.xml").data == first
    with app.app_context():
        s = db.session.get(Survey, 1)
        valid(schema, ddi.build(s))


def test_ddi_methodology_from_the_research_plan(app, client, survey, schema, monkeypatch):
    """Time method and sampling procedure come from the research plan; an open field stays out."""
    build(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        doc = valid(schema, ddi.build(s))
        assert not doc.xpath("//d:SamplingProcedure", namespaces=NS)
        monkeypatch.setattr(ddi, "_plan", lambda survey: {"design": "panel", "sample": {"recruitment": "quota"}})
        doc = valid(schema, ddi.build(s))
    assert doc.xpath("//d:TypeOfTimeMethod/d:StringValue/text()", namespaces=NS) == ["Longitudinal.Panel"]
    assert doc.xpath("//d:TypeOfSamplingProcedure/d:StringValue/text()", namespaces=NS) == ["Nonprobability.Quota"]
    assert doc.xpath("//d:TypeOfSamplingProcedure/d:ControlledVocabularyURI/text()", namespaces=NS) == [
        "http://rdf-vocabulary.ddialliance.org/cv/SamplingProcedure/2.0.2"]


def test_ddi_every_element_type_and_a_condition(app, client, survey, schema):
    """One element of every type that can be added to a block: the file stays valid (questions, statements, code
    lists, dates, numbers); an element shown only under a condition sits in an IfThenElse with the condition in words."""
    from app.items import all_types
    from app.models import Item

    build(client)
    for kind in all_types():
        add(client, kind.key)
    with app.app_context():
        s = db.session.get(Survey, 1)
        text_item = db.session.scalar(db.select(Item).filter_by(code="kommentar"))
        text_item.config = {**(text_item.config or {}), "when": {"item": 2, "op": "eq", "value": "1"}}
        db.session.commit()
        s = db.session.get(Survey, 1)
        assert len({i.type for b in s.blocks for i in b.items}) > 20
        doc = valid(schema, ddi.build(s))
    own = doc.xpath("//*[not(@isReference)]/d:URN/text()", namespaces=NS)
    assert len(own) == len(set(own))
    cond = doc.xpath("//d:IfThenElse/d:IfCondition/d:Description/text()", namespaces=NS)
    assert cond and cond[0].startswith("Shown only if:")
    assert doc.xpath("//d:StatementItem", namespaces=NS)  # texts and images without answers


def test_ddi_interview_guide_is_qualitative_with_its_mode(app, client, schema):
    from tests.interview.test_interview import make

    code, sid = make(app, client)
    with app.app_context():
        doc = valid(schema, ddi.build(db.session.get(Survey, sid)))
    assert doc.xpath("//d:StudyUnit/d:KindOfData/d:StringValue/text()", namespaces=NS) == ["Qualitative"]
    assert doc.xpath("//d:TypeOfModeOfCollection/d:StringValue/text()", namespaces=NS) == ["Interview.FaceToFace"]
    assert doc.xpath("//d:TypeOfInstrument/d:StringValue/text()", namespaces=NS) == ["Questionnaire.Semistructured"]


def test_ddi_sampling_with_the_kind_of_panel_or_random_sample(app, client, survey, schema, monkeypatch):
    """The research plan's new picks: an online panel only with its kind, a random sample in detail."""
    build(client)
    client.post("/surveys/1/researchdesign", data={"research_question": "", "sample_target": "", "recruitment": "panel",
                                                    "panel_kind": "access", "random_kind": "nonsense", "sample_note": ""})
    with app.app_context():
        s = db.session.get(Survey, 1)
        from app import study_plan

        stored = study_plan.plan_of(s)["sample"]
        assert (stored["recruitment"], stored["panel_kind"], stored["random_kind"]) == ("panel", "access", "")
        assert "Online-Panel (Access-Panel" in study_plan.markdown(s, "de")
        assert valid(schema, ddi.build(s)).xpath("//d:TypeOfSamplingProcedure/d:StringValue/text()", namespaces=NS) == [
            "Nonprobability"]
        for sample, term in (({"recruitment": "panel"}, None),
                             ({"recruitment": "panel", "panel_kind": "probability"}, "Probability"),
                             ({"recruitment": "panel", "panel_kind": "access"}, "Nonprobability"),
                             ({"recruitment": "random", "random_kind": "stratified"}, "Probability.Stratified"),
                             ({"recruitment": "random"}, "Probability")):
            monkeypatch.setattr(ddi, "_plan", lambda survey, sample=sample: {"sample": sample})
            doc = valid(schema, ddi.build(s))
            assert doc.xpath("//d:TypeOfSamplingProcedure/d:StringValue/text()", namespaces=NS) == ([term] if term else [])


def test_ddi_population_country_and_sample_description(app, client, survey, schema):
    """The research plan's population, country and region become Universe and spatial coverage; target, exclusions,
    response rate and note the description of the methodology."""
    build(client)
    client.post("/surveys/1/researchdesign", data={
        "research_question": "", "sample_target": "300", "sample_exclusions": "10", "recruitment": "panel",
        "panel_kind": "probability", "sample_response": "25", "sample_note": "über das Hochschulpanel",
        "population": "Studierende der RWTH Aachen ab 18 Jahren", "country": "DE", "region": "Nordrhein-Westfalen"})
    page = client.get("/surveys/1/researchdesign").data.decode()
    assert 'name="population"' in page and 'value="DE" selected' in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        from app import study_plan

        text = study_plan.markdown(s, "de")
        assert "Grundgesamtheit: Studierende der RWTH Aachen ab 18 Jahren" in text
        assert "Land und Region: Deutschland, Nordrhein-Westfalen" in text
        client.post("/surveys/1/researchdesign", data={"research_question": "", "country": "XX"})
        assert study_plan.plan_of(db.session.get(Survey, 1))["sample"]["country"] == ""  # only codes of the list
        client.post("/surveys/1/researchdesign", data={
            "research_question": "", "sample_target": "300", "sample_exclusions": "10", "recruitment": "panel",
            "panel_kind": "probability", "sample_response": "25", "sample_note": "über das Hochschulpanel",
            "population": "Studierende der RWTH Aachen ab 18 Jahren", "country": "DE", "region": "Nordrhein-Westfalen"})
        doc = valid(schema, ddi.build(db.session.get(Survey, 1)))
    assert doc.xpath("//d:Universe/d:Label/text()", namespaces=NS) == ["Studierende der RWTH Aachen ab 18 Jahren"]
    assert doc.xpath("//d:StudyUnit/d:UniverseReference", namespaces=NS)
    assert doc.xpath("//d:SpatialCoverage/d:CountryCode/d:StringValue/text()", namespaces=NS) == ["DE"]
    how = " ".join(doc.xpath("//d:Methodology/d:Description/text()", namespaces=NS))
    assert "Planned sample: 300" in how and "response rate of the panel: 25 %" in how and "Hochschulpanel" in how
    assert doc.xpath("//d:TypeOfSamplingProcedure/d:StringValue/text()", namespaces=NS) == ["Probability"]


def test_cdif_file_and_the_files_in_the_deposit(app, client, survey, schema):
    """CDIF JSON-LD (app/cdif.py): a schema.org Dataset with the CDIF profiles, the team, the period and every column
    as an InstanceVariable with its codes; the Zenodo/Coscine package holds it and the DDI file, both valid."""
    import json

    from app import zenodo

    build(client)
    assert 'export/cdif.jsonld"' in client.get("/surveys/1/data").data.decode()
    r = client.get("/surveys/1/export/cdif.jsonld")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.jsonld"')
    data = json.loads(r.data)
    assert data["@type"] == ["schema:Dataset"] and data["schema:name"] == "Test"
    assert {"https://w3id.org/cdif/core/1.1", "https://w3id.org/cdif/discovery/1.1",
            "https://w3id.org/cdif/data_description/1.1"} == {
        c["@id"] for c in data["schema:subjectOf"]["dcterms:conformsTo"]}
    variables = {v["schema:name"]: v for v in data["schema:variableMeasured"]}
    assert {"resp_id", "gender", "kommentar"} <= set(variables)
    gender = variables["gender"]
    assert gender["@type"] == ["schema:PropertyValue", "cdi:InstanceVariable"]
    assert [(t["schema:termCode"], t["schema:name"]) for t in gender["schema:valueReference"]] == [
        ("1", "weiblich"), ("2", "männlich")]
    assert data["schema:measurementTechnique"][0]["schema:termCode"] == "SelfAdministeredQuestionnaire.CAWI"
    with app.app_context():
        s = db.session.get(Survey, 1)
        files = dict(zenodo.package(s))
        valid(schema, files["ddi-lifecycle.xml"])
        deposit = json.loads(files["cdif.jsonld"])
        # the deposit describes the columns of its codebook.csv: no participation id or URL parameters
        assert "pid" not in {v["schema:name"] for v in deposit["schema:variableMeasured"]}
        assert deposit["schema:distribution"][0]["schema:contentUrl"] == "data.csv"
        assert "ddi-lifecycle.xml" in files["README.md"].decode() and "cdif.jsonld" in files["README.md"].decode()
