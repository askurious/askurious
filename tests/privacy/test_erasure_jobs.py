"""A deletion on request and the period "Antworten" stop every running estimation and remove its job files with the
answers, whatever its kind (code review 2026-10-08, S6 = R2): before, only the files named item_* (HB, latent
classes) went, the bootstraps of SEM (sem.*) and omega (omega-*) ran on with the deleted answers."""
import json
import os

from app import compute, erasure, jobs, retention
from app.extensions import db
from app.models import Survey

# the files of a run of each kind of compute.KINDS in hb/<uid>/ (maxdiff_hb.paths, segments, sem_model._paths,
# results.fill_omega): a new kind has to be named here, so it cannot be left out of the deletion
STEMS = {"maxdiff_hb": "item_5", "conjoint_hb": "item_6", "paired_hb": "item_8", "cva_hb": "item_9", "segments": "item_7.lc", "sem": "sem",
         "omega": "omega-abc", "sem_power": "sem_power", "panel": "panel",
         "sem_fit": "sem_fit"}


def _runs(app, uid):
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", uid)
    remote = os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_remote")
    os.makedirs(folder, exist_ok=True)
    os.makedirs(remote, exist_ok=True)
    for stem in STEMS.values():
        marker = os.path.join(folder, f"{stem}.run.json")
        for name in (f"{stem}.run.json", f"{stem}.run.json.job.json", f"{stem}.run.json.job.json.4242.running",
                     f"{stem}.json"):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                json.dump({"state": "running", "pids": ["p1"], "inp": {"rows": [[1, 2, 3]]}}, f)
        with open(os.path.join(remote, f"{stem}.json"), "w", encoding="utf-8") as f:
            json.dump({"paths": [marker]}, f)  # the Rechenknecht's index of the job (compute._folder)
    with open(os.path.join(folder, "board-steps.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return folder, remote


def _left(folder, remote):
    found = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    return [n for n in found if n.startswith(tuple(STEMS.values()))], sorted(os.listdir(remote))


def test_the_list_names_every_kind_of_job():
    assert set(STEMS) == set(compute.KINDS)
    assert all(f"{stem}.run.json".startswith(jobs.ANSWER_FILES) for stem in STEMS.values())


def test_a_deletion_on_request_stops_every_kind_of_job(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder, remote = _runs(app, s.uid)
        erasure._forget_in_files(s, {"p1"})
        assert _left(folder, remote) == ([], [])


def test_a_deletion_not_in_the_kept_results_still_stops_every_job(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder, remote = _runs(app, s.uid)
        erasure._forget_in_files(s, {"p1"}, results=False)
        files, index = _left(folder, remote)
        assert not [n for n in files if jobs.is_job_file(n)] and index == []


def test_the_period_answers_removes_every_kind_of_job_and_its_result(app, client, survey):
    with app.app_context():
        s = db.session.get(Survey, 1)
        folder, remote = _runs(app, s.uid)
        retention._clean_files(s.uid, blank_ids=False)
        assert _left(folder, remote) == ([], [])
