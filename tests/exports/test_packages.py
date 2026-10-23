"""The shared parts of every package of a whole survey (app/packages.py; concept "Vereinheitlichung der
Befragungsexporte", E2, E5 to E7): English names, one checksum file, the survey file without protected wording for
public packages, and older packages still read."""
import hashlib
import io
import json
import zipfile

from app import packages, survey_files, transfer
from app.extensions import db
from app.models import Item, Survey
from tests.instrument_permissions import grant_instrument


def _sums_hold(z):
    lines = z.read(packages.CHECKSUMS).decode().splitlines()
    assert lines
    for line in lines:
        digest, name = line.split("  ")
        assert hashlib.sha256(z.read(name)).hexdigest() == digest, name
    return {line.split("  ")[1] for line in lines}


def test_survey_zip_has_english_names_and_checksums(app, client, survey):
    with app.app_context():
        buf = survey_files.write([db.session.get(Survey, 1)])
        with zipfile.ZipFile(buf) as z:
            names = set(z.namelist())
            assert {packages.SURVEY, packages.README, packages.CHECKSUMS} <= names
            assert packages.SURVEY in _sums_hold(z)
            buf.seek(0)
            [part] = survey_files.read(buf.read())
            assert part.load()["survey"]["title"]


def test_a_zip_with_the_old_names_still_reads_with_its_images(app, client, survey):
    with app.app_context():
        data = transfer.dump(db.session.get(Survey, 1), with_files=False)
    data["blocks"][-1]["items"].append({"id": 999, "type": "image", "code": "bild", "answer_mode": "optional",
                                        "config": {}, "texts": {}, "images": [{"file": "a.png", "key": "a"}]})
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("befragung.json", json.dumps(data))
        z.writestr("dateien/a.png", b"\x89PNG\r\n\x1a\n")
        z.writestr("LIESMICH.txt", "alt")
    with app.app_context():
        [part] = survey_files.read(raw.getvalue())
        assert part.image_count == 1 and part.fetch("a.png", 100) == b"\x89PNG\r\n\x1a\n"


def test_prereg_snapshot_is_built_from_the_shared_parts(app, client, survey):
    from app import prereg

    with app.app_context():
        s = db.session.get(Survey, 1)
        name = prereg.freeze(s, lang="de")
        with zipfile.ZipFile(prereg.path(s, name)) as z:
            names = set(z.namelist())
            assert {packages.README, packages.SURVEY, packages.QUESTIONNAIRE, packages.CODEBOOK,
                    packages.CHECKSUMS} <= names
            assert not names & {"LIESMICH.txt", "befragung.json", "fragebogen.md", "codebuch.csv", "pruefsummen.sha256"}
            assert _sums_hold(z) == names - {packages.README, packages.CHECKSUMS}
            assert packages.SURVEY in z.read(packages.README).decode()


def test_public_survey_file_leaves_out_protected_wording(app, client, survey):
    grant_instrument(client.application, "gdms")
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter(Item.block_id.in_([b.id for b in s.blocks]), Item.type == "likert"))
        own = transfer.dump(s, with_files=False)
        public = transfer.dump(s, with_files=False, public=True)
    by_id = lambda d: {i["id"]: i for b in d["blocks"] for i in b["items"]}  # noqa: E731
    mine, theirs = by_id(own)[item.id], by_id(public)[item.id]
    words = [v for t in mine["texts"].values() for v in (t.get("statements") or {}).values() if v]
    assert words, "the authorised study file keeps the wording"
    text = json.dumps(theirs, ensure_ascii=False)
    assert theirs["wording_withheld"] is True and not any(w in text for w in words)
    assert [st["code"] for st in theirs["config"]["statements"]] == [st["code"] for st in mine["config"]["statements"]]
    others = [i for i in by_id(public).values() if i["id"] != item.id]
    assert all("wording_withheld" not in i for i in others)
