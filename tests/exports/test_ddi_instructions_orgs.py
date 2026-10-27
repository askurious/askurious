"""DDI Lifecycle 4.0 (app/ddi.py): the hints for the interviewers of an interview guide as Instruction items attached
to their constructs, and the team's institutions as Organization items the Publisher and the creators point at."""
from app import ddi
from app.extensions import db
from app.models import Item, Survey
from tests.exports.test_ddi import NS, build as build_items, schema, valid  # noqa: F401 - fixture

ORCID = "0000-0002-0000-0001"


def test_ddi_interviewer_hints_are_instructions_of_their_construct(app, client, schema):
    from tests.interview.test_interview import make

    code, sid = make(app, client)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        guide = [i for b in survey.blocks for i in b.items if i.type == "guide_question"]
        first, second = guide[0], guide[1]
        first.texts = {**(first.texts or {}), "de": {**((first.texts or {}).get("de") or {}),
                                                     "hint": "Pausen aushalten, nicht <b>unterbrechen</b>."}}
        second.config = {**(second.config or {}), "sub": True}
        second.texts = {**(second.texts or {}), "de": {**((second.texts or {}).get("de") or {}), "hint": "",
                                                       "when": "falls zutreffend"}}
        db.session.commit()
        first_id, second_id = first.id, second.id
        doc = valid(schema, ddi.build(db.session.get(Survey, sid)))
        public = valid(schema, ddi.build(db.session.get(Survey, sid), public=True))

    texts = doc.xpath("//d:Instruction/d:InstructionText//d:Text[@xml:lang='de']/text()", namespaces=NS)
    assert "Pausen aushalten, nicht unterbrechen." in texts and "falls zutreffend" in texts
    # each hint hangs at the StatementItem of its Leitfrage
    for item_id, text in ((first_id, "Pausen aushalten"), (second_id, "falls zutreffend")):
        with app.app_context():
            item_code = db.session.get(Item, item_id).code
        st = doc.xpath(f"//d:StatementItem[d:ConstructName='{item_code}']", namespaces=NS)[0]
        ref = st.xpath("d:InterviewerInstructionAttachment/d:InterviewerInstructionReference/d:ID/text()",
                       namespaces=NS)
        assert len(ref) == 1
        instruction = doc.xpath(f"//d:Instruction[d:ID='{ref[0]}']", namespaces=NS)[0]
        assert any(text in t for t in instruction.xpath(".//d:Text/text()", namespaces=NS))
    # the DataCollection names the scheme, the scheme names every instruction
    scheme = doc.xpath("//d:InterviewerInstructionScheme/d:InstructionReference/d:ID/text()", namespaces=NS)
    assert set(scheme) == set(doc.xpath("//d:Instruction/d:ID/text()", namespaces=NS))
    assert doc.xpath("//d:DataCollection/d:InterviewerInstructionSchemeReference", namespaces=NS)
    # every reference points at an item in the file
    own = doc.xpath("//*[not(@isReference)]/d:URN/text()", namespaces=NS)
    assert set(doc.xpath("//*[@isReference='true']/d:URN/text()", namespaces=NS)) <= set(own)
    # the hints are no protected wording: the deposit has them too
    assert "falls zutreffend" in public.xpath("//d:Instruction//d:Text/text()", namespaces=NS)


def test_ddi_questionnaire_without_hints_has_no_instructions(app, client, survey, schema):
    build_items(client)
    with app.app_context():
        doc = valid(schema, ddi.build(db.session.get(Survey, 1)))
    assert not doc.xpath("//d:Instruction | //d:InterviewerInstructionScheme", namespaces=NS)


def test_ddi_institutions_are_organizations(app, client, survey, schema):
    build_items(client)
    client.post("/surveys/1/members", data={"orcid": ORCID, "name": "Julia Offermann"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        for m in s.members:
            m.affiliation = "Universität Köln" if m.name == "Julia Offermann" else "RWTH Aachen University"
        db.session.commit()
        doc = valid(schema, ddi.build(db.session.get(Survey, 1)))

    def org_id(name):
        return doc.xpath(f"//d:Organization[d:OrganizationIdentification/d:OrganizationName/"
                         f"d:MultilingualStringValue='{name}']/d:ID/text()", namespaces=NS)

    rwth, koeln = org_id("RWTH Aachen University"), org_id("Universität Köln")
    assert len(rwth) == 1 and len(koeln) == 1  # each institution once
    # the publisher (the owner's institution) points at its Organization
    assert doc.xpath("//d:Citation/d:Publisher/d:PublisherReference/d:ID/text()", namespaces=NS) == rwth
    # the creator with ORCID is tied to their institution by a Relation
    person = doc.xpath(f"//d:Individual[.//d:ResearcherIdentification='{ORCID}']/d:ID/text()", namespaces=NS)
    relation = doc.xpath(f"//d:Relation[d:SourceObject/d:IndividualReference/d:ID='{person[0]}']", namespaces=NS)
    assert len(relation) == 1
    assert relation[0].xpath("d:TargetObject/d:OrganizationReference/d:ID/text()", namespaces=NS) == koeln
    assert relation[0].xpath("d:TargetObject/d:Role/d:Description/text()", namespaces=NS) == ["Affiliation"]
    # the scheme holds them; the affiliation stays as text at the creator, too
    scheme = doc.xpath("//d:OrganizationScheme", namespaces=NS)[0]
    assert set(scheme.xpath("d:OrganizationReference/d:ID/text()", namespaces=NS)) == set(rwth + koeln)
    assert "Universität Köln" in doc.xpath("//d:CreatorName/d:Affiliation/text()", namespaces=NS)
    # nothing about the people beyond name, ORCID and institution
    assert not doc.xpath("//d:ContactInformation", namespaces=NS)
    own = doc.xpath("//*[not(@isReference)]/d:URN/text()", namespaces=NS)
    assert set(doc.xpath("//*[@isReference='true']/d:URN/text()", namespaces=NS)) <= set(own)


def test_ddi_without_institution_has_no_organization(app, client, survey, schema):
    """A publisher without institution (the owner's name) is no Organization."""
    build_items(client)
    with app.app_context():
        doc = valid(schema, ddi.build(db.session.get(Survey, 1)))
    assert not doc.xpath("//d:Organization | //d:OrganizationScheme | //d:PublisherReference", namespaces=NS)
