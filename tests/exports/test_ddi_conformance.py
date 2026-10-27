"""Both DDI files are conform by construction (Ralf 2026-10-27: "die Berechnungen und Dokumente der App automatisch
konform"): for every kind of survey (questionnaire, diary survey, interview guide), for a minimal survey and for every
study template, the Lifecycle 4.0 and the Codebook 2.5 file are valid against their XSD, and the Codebook 2.5 file
has every mandatory path of the CESSDA profiles CDC 2.5 and EQB 2.5, as the download and as the file of a deposit.
And the flow, derived columns, the sample and panel waves (app/ddi.py): conditions as expressions, else, random
assignment, loops, Rechenwerte, reverse-coded statements, the weight, the target size, waves as a series."""
import pytest

from app import ddi, ddi_codebook
from app.extensions import db
from app.models import Block, Item, Survey
from tests.conftest import add, experiments_on
from tests.exports.test_ddi import NS as NS40, build as build_items, schema, valid  # noqa: F401 - fixture
from tests.exports.test_ddi_codebook import NS, profile, schema25, valid25  # noqa: F401 - fixture


def conform(schema, schema25, survey):
    """Both files of `survey`, download and deposit: valid, and every mandatory CESSDA path there."""
    for public in (False, True):
        valid(schema, ddi.build(survey, lang="en", public=public))
        doc = valid25(schema25, ddi_codebook.build(survey, lang="en", public=public))
        for name in ("cdc25_profile.xml", "eqb25_profile.xml"):
            for xpath, level, if_parent in profile(name):
                if level != "Mandatory":
                    continue
                if if_parent:
                    parent, _, last = xpath.rpartition("/")
                    for node in doc.xpath(parent, namespaces=NS):
                        assert node.xpath(last, namespaces=NS), f"{survey.title}: {name}: {xpath}"
                else:
                    assert doc.xpath(xpath, namespaces=NS), f"{survey.title} (public={public}): {name}: {xpath}"


def test_every_kind_and_every_study_template_is_conform(app, client, schema, schema25):
    from app import study_templates
    from tests.interview.test_interview import make

    client.post("/surveys/new", data={"title": "Nur ein Titel", "languages": ["de"], "default_language": "de"})
    make(app, client, title="Leitfaden minimal", template="leer")
    make(app, client, title="Leitfaden Vorlage", template="witzel")
    experiments_on(client, "diary")
    client.post("/diary/new", data={"title": "Tagebuch minimal", "language": "de"})
    for key in list(dict.fromkeys(study_templates.TEMPLATES)):
        client.post(f"/surveys/template/{key}")
    with app.app_context():
        surveys = db.session.scalars(db.select(Survey).order_by(Survey.id)).all()
        kinds = {s.kind for s in surveys}
        assert {"survey", "diary", "interview"} <= kinds and len(surveys) >= 4 + len(study_templates.TEMPLATES)
        for s in surveys:
            conform(schema, schema25, s)
        bare = next(s for s in surveys if s.title == "Nur ein Titel")
        doc = etree_of(ddi_codebook.build(bare))
    # the abstract CESSDA requires, from real data: without description or research question the title
    assert doc.xpath("//ddi:stdyInfo/ddi:abstract/text()", namespaces=NS) == ["Nur ein Titel"]


def etree_of(body):
    from lxml import etree

    return etree.fromstring(body)


def _flow_survey(app, client):
    """A questionnaire with a branch and its else, a randomizer over two blocks, a loop, a Rechenwert, a reverse-coded
    statement, a planned sample and two panel waves."""
    build_items(client)                                        # gender (2), A (3), kommentar (4) in block 2
    add(client, "slider")                                      # 5
    add(client, "calc")                                        # 6
    with app.app_context():
        s = db.session.get(Survey, 1)
        gender = db.session.scalar(db.select(Item).filter_by(code="gender"))
        likert = db.session.scalar(db.select(Item).filter_by(code="A"))
        likert.config = {**likert.config, "statements": [{**likert.config["statements"][0], "reverse": True}]}
        slider = db.session.get(Item, 5)
        slider.code = "einkommen"
        calc = db.session.get(Item, 6)
        calc.code = "jahr"
        calc.config = {**calc.config, "a": str(slider.id), "op": "mul", "fixed": 12}
        kommentar = db.session.scalar(db.select(Item).filter_by(code="kommentar"))
        kommentar.config = {**(kommentar.config or {}), "when": {"item": gender.id, "op": "eq", "value": "1"}}
        blocks = []
        for n in range(4):
            b = Block(position=len(s.blocks), titles={"de": f"Teil {n}"})
            s.blocks.append(b)
            blocks.append(b)
        db.session.flush()
        for b in blocks:
            b.items = [Item(type="text_input", code=f"t{b.id}", config={}, texts={"de": {"question": f"Frage {b.id}"}})]
        base = [{"t": "block", "block": b.id} for b in s.blocks if b not in blocks]
        s.flow = {"t": "root", "children": base + [
            {"t": "branch", "id": "b1", "cond": {"item": gender.id, "op": "in", "value": "1,2"},
             "children": [{"t": "block", "block": blocks[0].id}]},
            {"t": "else", "id": "x1", "of": "b1", "children": [{"t": "block", "block": blocks[1].id}]},
            {"t": "rand", "id": "r1", "code": "cond1", "name": "Bedingung", "k": 1, "balanced": True,
             "children": [{"t": "block", "block": blocks[2].id}, {"t": "block", "block": blocks[3].id}]},
        ]}
        plan = dict(s.study_plan or {})
        plan["sample"] = {**(plan.get("sample") or {}), "target": 300, "exclusions": 10, "recruitment": "panel",
                          "panel_kind": "access", "response": 25}
        s.study_plan = plan
        other = Survey(title="Welle 2", created_by_id=s.created_by_id, languages=["de"], default_language="de")
        db.session.add(other)
        db.session.flush()
        s.waves = {"waves": [{"survey": s.id, "label": "vorher"}, {"survey": other.id, "label": "nachher"}]}
        other.waves = s.waves
        db.session.commit()
        return other.uid


def test_flow_derived_sample_and_waves(app, client, survey, schema, schema25, monkeypatch):
    other_uid = _flow_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        conform(schema, schema25, s)
        d40 = valid(schema, ddi.build(s))
        d25 = valid25(schema25, ddi_codebook.build(s))

    def x40(path):
        return d40.xpath(path, namespaces=NS40)

    def x25(path):
        return d25.xpath(path, namespaces=NS)

    # conditions as expressions: the branch, its else, the element's own condition
    # numeric codes as numbers, as in the data file
    assert set(x40("//d:IfThenElse/d:IfCondition/d:Command/d:CommandContent/text()")) == {"gender in (1, 2)", "gender == 1"}
    assert set(x40("//d:IfThenElse/d:IfCondition/d:Command/d:ProgramLanguage/d:StringValue/text()")) == {ddi.PROGRAM_LANGUAGE}
    assert x40("//d:IfThenElse[d:ElseConstructReference]")
    # random assignment as Split over the two blocks
    split = x40("//d:Split")[0]
    assert len(split.xpath("d:ControlConstructReference", namespaces=NS40)) == 2
    assert "balanced" in split.xpath("d:Description/text()", namespaces=NS40)[0]
    # every reference points at an item in the file
    own = x40("//*[not(@isReference)]/d:URN/text()")
    assert set(x40("//*[@isReference='true']/d:URN/text()")) <= set(own) and len(own) == len(set(own))
    # derived columns: the Rechenwert with its inputs, the reverse-coded statement
    commands = x40("//d:GenerationInstruction/d:CommandCode/d:Command/d:CommandContent/text()")
    assert "jahr = einkommen * 12" in commands and "A_1 = 6 - A_1" in commands
    assert x40("//d:Variable[d:VariableName='jahr']/d:SourceVariableReference")
    # the sample and the waves
    assert x40("//d:Sample/d:OverallTargetSampleSize/d:PrimaryPopulation/d:NumberOfUnits/text()") == ["300"]
    assert x40("//d:SamplingProcedure/d:SampleReference")
    series = x40("//d:StudyUnit/d:SeriesStatement/d:SeriesDescription/text()")[0]
    assert "Wave 1 of 2 (vorher)" in series and other_uid in series and "Welle 2" not in series
    # Codebook 2.5: universe, derivation, target size, series
    universe = x25("//ddi:var[@name='kommentar']/ddi:universe/text()")[0]
    assert universe.startswith("Asked only if") and "gender == 1" in universe
    first = _first_part_id(app)
    assert x25(f"//ddi:var[@name='t{first}']/ddi:universe/text()")[0].count("gender in (1, 2)") == 1
    assert "not (gender in (1, 2))" in x25(f"//ddi:var[@name='t{first + 1}']/ddi:universe/text()")[0]  # the else
    assert x25("//ddi:var[@name='jahr']/ddi:derivation/ddi:drvcmd/text()") == ["jahr = einkommen * 12"]
    assert x25("//ddi:var[@name='A_1']/ddi:derivation/ddi:drvdesc/text()")[0].startswith("Reverse-coded")
    assert x25("//ddi:targetSampleSize/ddi:sampleSize/text()") == ["300"]
    assert "expected response rate of the panel: 25 %" in x25("//ddi:targetSampleSize/ddi:sampleSizeFormula/text()")[0]
    assert x25("//ddi:serStmt/ddi:serName/text()") and other_uid in x25("//ddi:othrStdyMat/ddi:relStdy/text()")[0]


def _first_part_id(app):
    with app.app_context():
        return db.session.scalar(db.select(Block).filter(Block.titles["de"].as_string() == "Teil 0")).id


def test_loop_and_weight(app, client, survey, schema, schema25, monkeypatch):
    """A loop over a table as Loop; the weight column flagged as weight with its procedure."""
    from app import weights

    build_items(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        b = Block(position=len(s.blocks), titles={"de": "Schleife"})
        s.blocks.append(b)
        db.session.flush()
        b.items = [Item(type="text_input", code="marke", config={}, texts={"de": {"question": "Wie gefällt Ihnen {{zeile}}?"}})]
        s.flow = {"t": "root", "children": [{"t": "block", "block": x.id} for x in s.blocks if x is not b] + [
            {"t": "loop", "id": "l1", "name": "Marken", "code": "loop1", "columns": [], "rows": [], "mode": "all",
             "order": "random", "children": [{"t": "block", "block": b.id}]}]}
        db.session.commit()
        monkeypatch.setattr(weights, "active", lambda survey: True)
        monkeypatch.setattr(weights, "codebook_note", lambda survey, lang=None: "Raking to the census")
        s = db.session.get(Survey, 1)
        d40 = valid(schema, ddi.build(s))
        d25 = valid25(schema25, ddi_codebook.build(s))
    loop = d40.xpath("//d:Loop", namespaces=NS40)[0]
    assert loop.xpath("d:LoopWhile/d:Command/d:CommandContent/text()", namespaces=NS40) == ["rows_left(loop1)"]
    assert "random order" in loop.xpath("d:Description/text()", namespaces=NS40)[0]
    col = weights.COLUMN[0]
    assert d40.xpath(f"//d:Variable[d:VariableName='{col}']/d:IsWeight/text()", namespaces=NS40) == ["true"]
    assert "Raking to the census" in d40.xpath("//d:Weighting/d:Description/text()", namespaces=NS40)[0]
    assert d25.xpath(f"//ddi:var[@name='{col}']/@wgt", namespaces=NS) == ["wgt"]
    assert "Raking to the census" in d25.xpath("//ddi:dataColl/ddi:weight/text()", namespaces=NS)[0]
