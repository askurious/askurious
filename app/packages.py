"""The parts every package of a whole survey is built from (concept "Vereinheitlichung der Befragungsexporte", Ralf
2026-10-23, E5 to E7): the survey file, the questionnaire as Markdown and as Word, the codebook, the tables of the
loops, the study plan and the checksums.

The survey file as ZIP (app/survey_files.py), the pre-registration (app/prereg.py), the versions of an interview guide
(app/interview_versions.py), the snapshot before a questionnaire is changed again (app/snapshots.py) and the data
package for Zenodo and Coscine (app/zenodo.py, app/coscine.py) pick from these parts, so one content has one name
and one build everywhere.

The names are English in every package (E6). Packages written before keep their names (a frozen pre-registration
never changes), so every reader knows the old names as well (SURVEY_NAMES, FILE_FOLDERS, README_NAMES)."""
from pathlib import Path

SURVEY = "survey.json"
FILES = "files"                       # the images of the survey file, beside it in a ZIP
QUESTIONNAIRE = "questionnaire.md"
CODEBOOK = "codebook.csv"
WORD = "questionnaire-codebook.docx"
PLAN = "study-plan.md"
STUDY_MATERIAL = "survey-material.pdf"  # app/study_material.py (E10)
README = "README.md"
CHECKSUMS = "checksums.sha256"
AI_CHAT = "ai-chat-model.md"          # where the chatbot of a KI-Chat comes from (app/ai_chat.py), never the key

# the names of packages written before 2026-10-23
SURVEY_NAMES = (SURVEY, "befragung.json")
FILE_FOLDERS = (FILES, "dateien")
README_NAMES = (README, "LIESMICH.txt")


def survey_file(survey, public=False):
    """[(name, bytes or Path)]: the survey file (app/transfer.py) with its images inside; one too large for that has
    them beside it under files/ (streamed into the ZIP, app/survey_files.snapshot_zip). public: without the wording
    of instruments that may not be passed on (transfer.dump, E2)."""
    from app import transfer

    try:
        return [(SURVEY, transfer.dumps(survey, public=public).encode("utf-8"))]
    except transfer.ExportTooLarge:
        images = {}
        data = transfer.dump(survey, raw=images, public=public)
        return [(SURVEY, "".join(transfer.json_chunks(data)).encode("utf-8"))] + [
            (f"{FILES}/{name}", Path(path)) for name, path in images.items()]


def questionnaire(survey, public=False):
    from app import export

    return [(QUESTIONNAIRE, export.questionnaire_md(survey, public=public).encode("utf-8"))]


def codebook(survey, public=False, open_answers=True):
    """open_answers matters only for public: the columns of open answers are then left out (Zenodo's tick)."""
    from app import export

    text = (export.codebook_csv(survey, public=True, open_answers=open_answers) if public
            else export.codebook_csv(survey))
    return [(CODEBOOK, text.encode("utf-8"))]


def loop_tables(survey):
    """The table of each loop (its stimuli), no answers."""
    from app import export

    return [(export.loop_table_name(node), export.loop_table_csv(survey, node).encode("utf-8"))
            for node in export.loop_tables(survey)]


def study_plan(survey, lang):
    """The research question, hypotheses and analysis plan (app/study_plan.py), if the survey has one."""
    if not (survey.study_plan or survey.research_question):
        return []
    from app import study_plan as plan

    return [(PLAN, plan.markdown(survey, lang).encode("utf-8"))]


def word(survey):
    """The questionnaire as Word with codebook; nothing if python-docx is missing on the server or refuses a text."""
    from flask import current_app

    try:
        from app import questionnaire_docx

        return [(WORD, questionnaire_docx.build(survey, "codebook"))]
    except Exception:
        current_app.logger.exception("package of survey %s without the Word document", survey.id)
        return []


def study_material(survey, lang=None, public=False):
    """The survey material as PDF (app/study_material.py): the questionnaire as participants see it and with the notes
    on logic, randomisation and checks; nothing if it cannot be made (the error is logged)."""
    from flask import current_app

    try:
        from app import study_material as material

        return [(STUDY_MATERIAL, material.build(survey, lang, public=public))]
    except Exception:
        current_app.logger.exception("package of survey %s without the survey material", survey.id)
        return []


def documentation(survey, lang, public=False):
    """What documents the questionnaire for a reader: Markdown, codebook, loop tables, study plan, Word, the study
    material as PDF."""
    return (questionnaire(survey, public) + codebook(survey, public) + loop_tables(survey) + study_plan(survey, lang)
            + word(survey) + study_material(survey, lang, public) + ai_chat_model(survey))


def ai_chat_model(survey):
    """[(AI_CHAT, bytes)] for a survey with the element "KI-Chat": provider, address, model and its versions, the
    instructions and the limits of each chat (app/ai_chat.py, documentation); [] without one. No message of a
    participant: those are data (the long format "chat")."""
    from app import ai_chat

    return [(AI_CHAT, data) for _name, data in ai_chat.documentation(survey)]


def long_name(item=None, node=None, vignettes=False):
    """The file name of a long format (E6): <kind>_<variable>_long.csv for a choice item, vignettes_<loop>_long.csv
    for a vignette loop, loop_<loop>_long.csv for another loop with a table. Before 2026-10-23 the snapshots said
    long_<variable>.csv and the deposits vignetten_/schleife_."""
    if item is not None:
        return f"{item.kind.long_export}_{item.code}_long.csv"
    return f"{'vignettes' if vignettes else 'loop'}_{node.get('code')}_long.csv"


def long_files(survey, include_incomplete=False, sources=("real",), open_answers=True):
    """[(name, text)]: every long format of the survey (choice items, vignette loops, other loops with a table).
    open_answers=False (a deposit without the tick): choice items whose long format holds what a person wrote
    (kind.long_private) are left out, and the loops keep their open answers out as in data.csv."""
    from app import export, vignettes

    out = []
    for item in (i for b in survey.blocks for i in b.items if i.kind.long_export):
        if open_answers or not item.kind.long_private:
            out.append((long_name(item), export.long_csv(survey, item, include_incomplete=include_incomplete,
                                                         sources=sources)))
    for node in vignettes.nodes(survey):  # one row per person and vignette
        out.append((long_name(node=node, vignettes=True), export.vignettes_long_csv(
            survey, node, include_incomplete=include_incomplete, sources=sources, open_answers=open_answers)))
    for node in export.loop_long_nodes(survey):  # every other loop with a table: one row per person and pass
        out.append((long_name(node=node), export.loop_long_csv(
            survey, node, include_incomplete=include_incomplete, sources=sources, open_answers=open_answers)))
    return out


def checksum_lines(files):
    """"<sha256>  <name>" per file, as sha256sum writes and checks them (sha256sum -c)."""
    from app.survey_files import content_hash

    return "".join(f"{content_hash(data)}  {name}\n" for name, data in files)


def checksums(files):
    """[(CHECKSUMS, bytes)] over these files (E7: the same file in every package)."""
    return [(CHECKSUMS, checksum_lines(files).encode("utf-8"))]


def find(names, wanted):
    """The first of the names `wanted` (e.g. SURVEY_NAMES) that is among `names`, or None."""
    return next((n for n in wanted if n in names), None)
