"""DDI Codebook 2.5 export (app/ddi_codebook.py) and the study facts both DDI files share (app/ddi.py): valid against
the XSD of the DDI Alliance (tests/data/ddi/codebook25-xsd.tar.gz, LGPL 3.0), with every path the CESSDA profiles CDC
2.5 and EQB 2.5 require (tests/data/ddi/*_profile.xml, © CESSDA ERIC, CC BY 4.0); the new fields of the Lifecycle 4.0
file stay valid there; a deposit leaves out withheld wording."""
import re
import tarfile
from pathlib import Path

import pytest

from app import ddi, ddi_codebook
from app.extensions import db
from app.models import Survey
from tests.conftest import fill_consent
from tests.exports.test_ddi import NS as NS40, build as build_items, schema, valid  # noqa: F401 - fixture

etree = pytest.importorskip("lxml.etree")
DATA = Path(__file__).resolve().parent.parent / "data" / "ddi"
NS = {"ddi": "ddi:codebook:2_5", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
ORCID = "0000-0002-0000-0001"


@pytest.fixture(scope="module")
def schema25(tmp_path_factory):
    folder = tmp_path_factory.mktemp("ddi25-xsd")
    with tarfile.open(DATA / "codebook25-xsd.tar.gz") as tar:
        tar.extractall(folder, filter="data")
    return etree.XMLSchema(etree.parse(str(folder / "codebook.xsd")))


def valid25(schema25, body):
    doc = etree.fromstring(body)
    if not schema25.validate(doc):
        raise AssertionError("\n".join(str(e) for e in list(schema25.error_log)[:20]))
    return doc


def profile(name):
    """[(xpath, level, if the parent is there)] of a CESSDA profile: level "Mandatory" / "Recommended" / "Optional"."""
    out = []
    for used in etree.parse(str(DATA / name)).getroot().iter("{*}Used"):
        words = " ".join(c.text or "" for c in used.iter("{*}Content"))
        level = (re.search(r"Required: (\w+)", words) or [None, "Optional"])[1]
        out.append((used.get("xpath"), "Mandatory" if used.get("isRequired") == "true" else level,
                    "MandatoryNodeIfParentPresentConstraint" in words))
    return out


def filled(app, client, monkeypatch):
    """A survey with everything an archive asks for: description, research plan, keywords and topic, licence,
    funding, a co-author with ORCID and institution, a DOI and a publication."""
    from app import citations

    build_items(client)
    fill_consent(client, open_data="1", license="CC BY 4.0")
    client.post("/surveys/1/members", data={"orcid": ORCID, "name": "Julia Offermann"})
    client.post("/surveys/1/researchdesign", data={
        "research_question": "", "design": "cross", "sample_target": "300", "recruitment": "quota",
        "sample_note": "über das Hochschulpanel", "population": "Studierende ab 18 Jahren", "country": "DE",
        "region": "Nordrhein-Westfalen"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.description = "Worum es geht."
        s.title_translations = {"en": "Test in English"}
        from app import study_plan

        plan = study_plan.plan_of(s)
        plan["archive"] = {"keywords": ["Pflege", "Roboter"], "topic": "Health.HealthCareServicesAndPolicies"}
        s.study_plan = plan
        s.zenodo = {"doi": "10.5281/zenodo.42", "state": "published", "target": "zenodo"}
        s.consent_item.config = {**(s.consent_item.config or {}), "funder": "Deutsche Forschungsgemeinschaft",
                                 "grant_number": "123456789", "funding": "Deutsche Forschungsgemeinschaft, Förderkennzeichen 123456789"}
        for m in s.members:
            m.affiliation = "RWTH Aachen University" if m.name != "Julia Offermann" else "Universität Köln"
        from datetime import datetime

        s.opened_at, s.closed_at = datetime(2026, 9, 1), datetime(2026, 10, 1)
        db.session.commit()
    monkeypatch.setattr(citations, "for_survey", lambda survey: [("10.1000/xyz123", {"title": "Ein Artikel"}),
                                                                  ("10.5281/zenodo.42", {})])


def test_codebook25_is_valid_and_has_what_cessda_requires(app, client, survey, schema25, monkeypatch):
    filled(app, client, monkeypatch)
    assert 'export/ddi-codebook.xml"' in client.get("/surveys/1/data").data.decode()
    r = client.get("/surveys/1/export/ddi-codebook.xml")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith('.xml"')
    doc = valid25(schema25, r.data)
    for name in ("cdc25_profile.xml", "eqb25_profile.xml"):
        for xpath, level, if_parent in profile(name):
            if level != "Mandatory":
                continue
            if if_parent:  # wherever the parent is, the node is
                parent, _, last = xpath.rpartition("/")
                for node in doc.xpath(parent, namespaces=NS):
                    assert node.xpath(last, namespaces=NS), f"{name}: {xpath} missing"
            else:
                assert doc.xpath(xpath, namespaces=NS), f"{name}: {xpath} missing"
    # the recommended ones where the survey has the data
    have = {x for x, level, _ in profile("cdc25_profile.xml") + profile("eqb25_profile.xml")
            if level == "Recommended" and doc.xpath(x, namespaces=NS)}
    for tail in ("rspStmt/ddi:AuthEnty", "subject/ddi:keyword", "topcClas/@vocabURI", "sumDscr/ddi:collDate",
                 "nation/@abbr", "anlyUnit/ddi:concept", "sumDscr/ddi:universe", "timeMeth/ddi:concept",
                 "sampProc/ddi:concept", "collMode/ddi:concept", "useStmt/ddi:restrctn", "fileTxt/ddi:fileName",
                 "var/ddi:labl", "AuthEnty/ddi:ExtLink/@role"):
        assert any(x.endswith(tail) for x in have), tail
    def one(path):
        return doc.xpath(path, namespaces=NS)

    assert one("//ddi:stdyDscr//ddi:IDNo[@agency='de.askurious']/text()")[0] == one("//ddi:stdyDscr//ddi:IDNo/text()")[0]
    assert one("/ddi:codeBook/ddi:stdyDscr/ddi:citation/ddi:titlStmt/ddi:IDNo[@agency='DOI']/text()") == ["10.5281/zenodo.42"]
    assert one("//ddi:stdyDscr//ddi:parTitl[@xml:lang='en']/text()") == ["Test in English"]
    assert one("//ddi:distrbtr/text()") == ["RWTH Aachen University"]  # the owner's institution, not the operator
    assert one("//ddi:holdings/@URI") == ["https://doi.org/10.5281/zenodo.42"]
    assert one(f"//ddi:AuthEnty[ddi:ExtLink/@URI='https://orcid.org/{ORCID}']/@affiliation") == ["Universität Köln"]
    assert one("//ddi:grantNo/text()") == ["123456789"] and one("//ddi:fundAg/text()") == ["Deutsche Forschungsgemeinschaft"]
    assert "creativecommons.org/licenses/by/4.0" in one("//ddi:restrctn/text()")[0]
    assert one("//ddi:relPubl/ddi:ExtLink/@URI") == ["https://doi.org/10.1000/xyz123"]  # not the own data set
    assert one("//ddi:sampProc/ddi:concept/text()") == ["Nonprobability.Quota"]
    assert "Planned sample: 300" in " ".join(one("//ddi:sampProc/ddi:txt/text()"))
    assert one("//ddi:collMode/ddi:txt/text()")[0].startswith("Self-administered questionnaire")
    assert one("//ddi:nation/@abbr") == ["DE"] and one("//ddi:collDate/@event") == ["start", "end"]
    # the columns: question text, categories, format
    gender = one("//ddi:var[@name='gender']")[0]
    assert gender.xpath("ddi:qstn/ddi:qstnLit[@xml:lang='de']/text()", namespaces=NS) == ["Geschlecht?"]
    assert gender.xpath("ddi:catgry/ddi:labl/text()", namespaces=NS) == ["weiblich", "männlich"]
    assert gender.xpath("ddi:varFormat/@type", namespaces=NS) == ["numeric"]
    assert one("//ddi:var[@name='kommentar']/ddi:varFormat/@type") == ["character"]
    assert int(one("//ddi:varQnty/text()")[0]) == len(one("//ddi:var"))


def test_codebook25_in_the_deposit_leaves_out_withheld_wording(app, client, survey, schema25, monkeypatch):
    """The Zenodo/Coscine package holds ddi-codebook.xml beside ddi-lifecycle.xml; it describes data.csv and leaves
    out the question text of an instrument without a general publication permission."""
    from app import export, zenodo

    filled(app, client, monkeypatch)
    with app.app_context():
        s = db.session.get(Survey, 1)
        files = dict(zenodo.package(s))
        doc = valid25(schema25, files["ddi-codebook.xml"])
        assert doc.xpath("//ddi:fileName/text()", namespaces=NS) == ["data.csv"]
        assert "ddi-codebook.xml" in files["README.md"].decode()
        assert doc.xpath("//ddi:var[@name='gender']/ddi:qstn", namespaces=NS)
        gender = next(i for b in s.blocks for i in b.items if i.code == "gender")
        monkeypatch.setattr(export, "withheld", lambda survey: {gender.id})
        doc = valid25(schema25, ddi_codebook.build(s, lang="en", public=True))
    assert not doc.xpath("//ddi:var[@name='gender']/ddi:qstn", namespaces=NS)
    assert doc.xpath("//ddi:var[@name='gender']/ddi:labl", namespaces=NS)  # the column itself stays
    assert not doc.xpath("//ddi:var[@name='pid']", namespaces=NS)  # only the columns of codebook.csv


def test_codebook25_of_a_bare_survey_and_an_interview_guide(app, client, schema25):
    """Without plan, licence or DOI the file stays valid and leaves the open parts out."""
    from tests.interview.test_interview import make

    client.post("/surveys/new", data={"title": "Leer", "languages": ["de"], "default_language": "de"})
    code, sid = make(app, client)
    with app.app_context():
        bare = valid25(schema25, ddi_codebook.build(db.session.scalar(db.select(Survey).filter_by(title="Leer"))))
        guide = valid25(schema25, ddi_codebook.build(db.session.get(Survey, sid)))
    assert not bare.xpath("//ddi:sampProc | //ddi:IDNo[@agency='DOI']", namespaces=NS)
    assert bare.xpath("//ddi:abstract/text()", namespaces=NS) == ["Leer"]  # CESSDA requires it: the title, no description
    assert bare.xpath("//ddi:distrbtr/text()", namespaces=NS) == ["Philipp"]  # no institution: the owner's name
    assert guide.xpath("//ddi:dataKind/ddi:concept/text()", namespaces=NS) == ["Qualitative"]
    assert guide.xpath("//ddi:collMode/ddi:concept/text()", namespaces=NS) == ["Interview.FaceToFace"]


def test_lifecycle40_gets_the_archive_fields_and_stays_valid(app, client, survey, schema, monkeypatch):
    filled(app, client, monkeypatch)
    with app.app_context():
        s = db.session.get(Survey, 1)
        uid = s.uid
        doc = valid(schema, ddi.build(s))
        rows = {r["en"]: r for r in ddi.readiness(s)}
    def one(path):
        return doc.xpath(path, namespaces=NS40)

    assert one("//d:StudyUnit/d:UserID/d:StringValue/text()") == [uid]
    assert one("//d:StudyUnit/d:UserID/d:TypeOfUserID/d:StringValue/text()") == ["de.askurious"]
    assert one("//d:StudyUnit/d:Citation/d:InternationalIdentifier/d:IdentifierContent/text()") == ["10.5281/zenodo.42"]
    assert one("//d:StudyUnit/d:Citation/d:InternationalIdentifier/d:ManagingAgency/d:StringValue/text()") == ["DataCite"]
    assert one("//d:Citation/d:Publisher/d:PublisherName/d:Name/text()") == ["RWTH Aachen University"]
    assert one("//d:StudyUnit/d:FundingInformation/d:GrantNumber/text()") == ["123456789"]
    assert "creativecommons.org/licenses/by/4.0" in one("//d:Archive//d:DefaultAccess/d:AccessConditions/text()")[0]
    assert one("//d:StudyUnit/d:ArchiveReference")
    assert one("//d:OtherMaterial/d:ExternalURLReference/text()") == ["https://doi.org/10.1000/xyz123"]
    assert one("//d:StudyUnit/d:RelatedOtherMaterialReference")
    assert one("//d:Individual//d:ResearcherID/d:ResearcherIdentification/text()") == ["0000-0000-0000-001X", ORCID]
    assert one("//d:Creator[d:CreatorReference]/d:CreatorName/d:Affiliation/text()") == ["RWTH Aachen University", "Universität Köln"]
    assert one("//d:SamplingProcedure/d:Description/text()")[0].startswith("Quota")
    assert one("//d:ModeOfCollection/d:Description/text()")
    assert one("//d:StudyUnit/d:Citation/d:Language/d:StringValue/text()") == ["de", "en"]
    assert one("//d:PhysicalInstance/d:Citation/d:Language/d:StringValue/text()") == ["de", "en"]
    own = one("//*[not(@isReference)]/d:URN/text()")
    assert set(one("//*[@isReference='true']/d:URN/text()")) <= set(own) and len(own) == len(set(own))
    # the archive box: publisher from the institution, DOI given, study number set automatically
    assert (rows["Publisher"]["state"], rows["Publisher"]["value_en"]) == ("ok", "RWTH Aachen University")
    assert (rows["DOI"]["state"], rows["DOI"]["value_en"]) == ("ok", "10.5281/zenodo.42")
    assert rows["Survey identifier"]["state"] == "auto" and uid in rows["Survey identifier"]["value_en"]


def test_readiness_without_institution_or_doi(app, client, survey):
    with app.app_context():
        rows = {r["en"]: r for r in ddi.readiness(db.session.get(Survey, 1))}
    assert rows["Publisher"]["state"] == "missing" and rows["Publisher"]["value_en"] == "Philipp"
    assert rows["DOI"]["state"] == "optional"
    page = client.get("/surveys/1/data").data.decode()
    assert "DDI Codebook 2.5" in page


def test_strict_extras_statement_construct_wave_and_material(app, client, survey, schema, schema25, monkeypatch):
    """The recommended and optional parts of CESSDA's strict check (Ralf 2026-10-27): the language at every variable,
    the stem of a battery before its statement (preQTxt, qstnLit), the validated construct as concept (vocab its
    source, vocabURI its DOI; 4.0: a Concept), the wave of a panel (collDate/@cycle; 4.0: Cycle) and the other files of
    a deposit (otherMat; 4.0: OtherMaterial)."""
    from app import zenodo
    from app.models import Item

    filled(app, client, monkeypatch)
    with app.app_context():
        battery = db.session.scalar(db.select(Item).filter_by(code="A"))
        battery.config = {**(battery.config or {}), "construct": {"key": "tb"}}
        s = db.session.get(Survey, 1)
        s.waves = {"waves": [{"survey": 1, "label": "vorher"}, {"survey": 99, "label": "nachher"}]}
        db.session.commit()
        s = db.session.get(Survey, 1)
        doc = valid25(schema25, ddi_codebook.build(s))
        doc40 = valid(schema, ddi.build(s))
        files = dict(zenodo.package(s))
        deposit = valid25(schema25, files["ddi-codebook.xml"])
        deposit40 = valid(schema, files["ddi-lifecycle.xml"])
    def one(path, d=doc):
        return d.xpath(path, namespaces=NS)

    assert one("//ddi:var[not(@xml:lang)]") == [] and one("//ddi:var/@xml:lang")[0] == "de"
    statement = one("//ddi:var[@name='A_1']")[0]
    assert statement.xpath("ddi:qstn/ddi:preQTxt[@xml:lang='de']/text()", namespaces=NS) == ["Wie sehr stimmen Sie zu?"]
    assert statement.xpath("ddi:qstn/ddi:qstnLit[@xml:lang='de']/text()", namespaces=NS) == ["Aussage A"]
    assert not one("//ddi:var[@name='gender']/ddi:qstn/ddi:preQTxt")  # a single question has no stem
    concept = statement.xpath("ddi:concept", namespaces=NS)[0]
    assert concept.text == "Kurzskala Technikbereitschaft (TB)" and concept.get("vocabURI") == "https://doi.org/10.6102/zis244"
    assert concept.get("vocab")
    assert set(one("//ddi:collDate/@cycle")) == {"Wave 1 of 2 (vorher)"}
    assert not one("//ddi:otherMat")  # the download names no package files
    names = set(one("//ddi:otherMat/@URI", deposit))
    assert {"codebook.csv", "questionnaire.md", "survey.json"} <= names and names <= set(files)
    assert all(one("//ddi:otherMat/ddi:txt[@xml:lang='en']/text()", deposit))
    # the same in Lifecycle 4.0
    assert doc40.xpath("//d:Concept/d:ConceptName/text()", namespaces=NS40) == ["Kurzskala Technikbereitschaft (TB)"]
    assert doc40.xpath("//d:Variable[d:VariableName='A_1']/d:ConceptReference", namespaces=NS40)
    assert doc40.xpath("//d:DataCollectionDate/d:Cycle/text()", namespaces=NS40) == ["1"]
    urls = set(deposit40.xpath("//d:OtherMaterial/d:ExternalURLReference/text()", namespaces=NS40))
    assert {"codebook.csv", "questionnaire.md"} <= urls
    # every mandatory path of both profiles is still there
    for name in ("cdc25_profile.xml", "eqb25_profile.xml"):
        for xpath, level, if_parent in profile(name):
            if level == "Mandatory" and if_parent:
                parent, _, last = xpath.rpartition("/")
                for node in deposit.xpath(parent, namespaces=NS):
                    assert node.xpath(last, namespaces=NS), f"{name}: {xpath} missing"
