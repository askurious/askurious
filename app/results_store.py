"""The last computed results of a survey, kept on the server: the results page computes anew only when what the
results rest on has changed, and shows the kept ones otherwise.

Whether it has changed is read from a fingerprint, not guessed from the time: a hash over
  the participations of the data pool   count plus transactional revision of the selected sources/statuses
  the data pool and the quality assurance sources, also unfinished ones, speeder threshold, marking rule
  the questionnaire                     every block and question with settings and texts, the flow
  the program                           the files of the application (a new version computes anew)
  the maps                              the tile server named in the results
A different fingerprint: computed and kept. The same one: the kept results, exactly what compute() returned.
The hierarchical Bayes estimates are not part of it: their views are read anew from the stored estimates on
every display (results.refresh_estimates), so starting, running and ending an estimation computes nothing else
again (Ralf, 2026-10-03).

Rights: the store is read and written only by the results page, after it has checked the person's access to
the survey as on every request; what the role "Ansehen" must not see is taken out of the kept results on every
display, as it is out of fresh ones. The file lies next to the HB results (never served; removed with the
survey), holds what the database next to it holds anyway, and is signed with the SECRET_KEY: a file that was
changed or comes from elsewhere is not read (it is a pickle, which runs code when loaded).
"""
import hashlib
import hmac
import io
import json
import os
import pickle
import threading

from flask import current_app

from app.extensions import db
from app.models import Item, Response

FORMAT = 1


def _path(survey, pool=None, name="results"):
    """The file of the kept results. name: "results" for the results, "compare" for the group comparisons of the
    last grouping chosen (app/compare.py; their key is the fingerprint plus the grouping question); the weighted
    results (pool["weighted"], app/weights.py) have a file of their own, so switching between both does not
    compute anew. Texts made while computing (warnings, notes) are in the language of the page that computed them,
    so an English page has files of its own ("-en"); outside a request (and before signing in) it is German."""
    from app import lang

    if (pool or {}).get("weighted"):
        name += "-weighted"
    # the view with what quality assurance marks ("Markierte einbeziehen", pool["drop"] False) has a file of its own
    # too: it no longer pushes out the kept results of the page, the method part and the scale table (code review
    # 2026-10-07, R5)
    if pool is not None and pool.get("drop") is False:
        name += "-markiert"
    if lang.current() != lang.STANDARD:
        name += f"-{lang.current()}"
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", survey.uid, f"{name}.bin")  # see maxdiff_hb.folder


_program = None


def program():
    """Changes with the code: sizes and times of the application's Python files, read once per process."""
    global _program
    if _program is None:
        root = os.path.dirname(os.path.abspath(__file__))
        seen = []
        for folder, _, names in sorted(os.walk(root)):
            for name in sorted(names):
                if name.endswith(".py"):
                    st = os.stat(os.path.join(folder, name))
                    seen.append(f"{os.path.relpath(os.path.join(folder, name), root)}:{st.st_size}:{st.st_mtime_ns}")
        _program = hashlib.sha256("\n".join(seen).encode()).hexdigest()
    return _program


def _participations(survey, pool):
    """Count and monotonic pool revision; no answers/layout/paradata are transferred.
    Triggers also cover bulk SQL updates, deletes and moves between pools in the same transaction; each pool counts in
    several rows (data_revision.SHARDS), so that saving participants do not wait for each other (T05-N-1)."""
    from app.models import ResponseRevision as revisions

    db.session.flush()
    query = db.select(db.func.count(Response.id)).where(Response.survey_id == survey.id,
                                                       Response.source.in_(pool["sources"]), Response.status != "review")
    if not pool["all"]:
        query = query.where(Response.status == Response.COMPLETED)
    changed = db.select(revisions.source, revisions.status, revisions.shard, revisions.revision, revisions.nonce).where(
        revisions.survey_id == survey.id, revisions.source.in_(pool["sources"]), revisions.status != "review")

    if not pool["all"]:
        changed = changed.where(revisions.status == Response.COMPLETED)
    changed = changed.order_by(revisions.source, revisions.status, revisions.shard)
    return [db.session.scalar(query), [tuple(row) for row in db.session.execute(changed)]]


def fingerprint(survey, pool):
    """The hash of everything the results of this survey and data pool are computed from (see the module text)."""
    from app import export, quality

    config = current_app.config
    basis = {
        "format": FORMAT, "program": program(),
        "pool": {"sources": sorted(pool["sources"]), "all": bool(pool["all"]), "drop": bool(pool["drop"]),
                 "speeder": pool["speeder"], "speed_real": bool(pool.get("speed_real", True))},
        "rules": quality.rules(survey),
        "speed": [export.speeder_rule(survey), export.rsi_limit(survey)],
        **questionnaire(survey),
        "participations": _participations(survey, pool),
        "maps": {k: config.get(k) for k in sorted(config) if k.startswith("MAP_")},
    }
    if pool.get("weighted"):  # the weighting and its targets (quota targets, the census figures)
        from app import weights

        basis["weighting"] = weights.fingerprint(survey)
    from app import coding

    blind = coding.hidden(survey)  # the second coder's results lack what they code blind (audit 2026-10-10, B-4)
    if blind:
        basis["coding_hidden"] = blind
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def parts(survey, pool):
    """(data, setup): two hashes of what fingerprint() reads, without the program. data: the data pool and every
    participation in it with all that is stored about it; setup: the quality rules, the speeder rule and the
    questionnaire. For a stored estimate (maxdiff_hb.stamp): other data make it stale, another setup is checked."""
    from app import export, quality

    def digest(basis):
        return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()

    data = {"pool": {"sources": sorted(pool["sources"]), "all": bool(pool["all"]), "drop": bool(pool["drop"]),
                     "speeder": pool["speeder"], "speed_real": bool(pool.get("speed_real", True))},
            "participations": _participations(survey, pool)}
    setup = {"rules": quality.rules(survey), "speed": [export.speeder_rule(survey), export.rsi_limit(survey)],
             **questionnaire(survey)}
    return digest(data), digest(setup)


def questionnaire(survey):
    """The part of fingerprint() about the survey and its questionnaire: its settings, every block and question
    with settings and texts, the quotas. Also what the values of quality assurance rest on besides the
    participation itself (quality.memo_key)."""
    from app.simulator import without_sim  # saved scenarios of the market simulator are no change of the survey

    items = [i for b in survey.blocks for i in b.items]
    return {
        "survey": [survey.id, survey.uid, survey.qversion, survey.default_language, survey.languages, survey.flow,
                   survey.paradata, survey.page_timing, survey.focus_paste, getattr(survey, "focus_inputs", False), getattr(survey, "ai_bait", False), survey.tempo_hint, survey.tempo_dose, survey.tempo_options, survey.panel, survey.codings],
        "blocks": [[b.id, b.position, b.randomize_items, b.settings, b.titles] for b in survey.blocks],
        "items": [[i.id, i.block_id, i.position, i.type, i.code, i.answer_mode, without_sim(i.config), i.texts] for i in items],
        "quotas": [[q.id, q.code, [[c.id, c.code] for c in q.cells]] for q in survey.quotas],
    }


class _Pickler(pickle.Pickler):
    """Questions are kept by their number and looked up again when the results are read; nothing else of the
    database may be part of the results (it would be kept as it was)."""

    def persistent_id(self, obj):
        if isinstance(obj, Item):
            return ("item", obj.id)
        if isinstance(obj, db.Model):
            raise pickle.PicklingError(f"{type(obj).__name__} in the results")
        return None


# The only classes the kept results may name (security audit 2026-10-04, H1). The file is signed with the
# SECRET_KEY and never read otherwise; this list is the second line: should the key ever become known, a file
# put in its place still cannot name os.system or the like, which a pickle otherwise runs when loaded. A class
# that is missing here only means that the results are computed anew (and the log says which one).
ALLOWED = {
    "datetime": {"datetime", "date", "time", "timedelta", "timezone"},
    "zoneinfo": {"ZoneInfo"},
    "numpy": {"dtype", "ndarray"},
    "numpy._core.multiarray": {"scalar", "_reconstruct"},
    "numpy.core.multiarray": {"scalar", "_reconstruct"},  # older numpy
    "numpy._core.numeric": {"_frombuffer"},  # arrays written with protocol 5
    "numpy.core.numeric": {"_frombuffer"},
    "builtins": {"set", "frozenset", "complex", "getattr"},
    "collections": {"OrderedDict", "Counter"},
    "markupsafe": {"Markup"},
    "app.results": {"_Price"},  # the price format of a PSM item (a plain class: no code runs when it is read)
}


def _getattr(obj, name):
    """The getattr a pickle may call: only for the one use in the results (a ZoneInfo is written as
    ZoneInfo._unpickle), never to walk from an allowed class to anything else."""
    from zoneinfo import ZoneInfo

    if obj is ZoneInfo and name == "_unpickle":
        return ZoneInfo._unpickle
    raise pickle.UnpicklingError(f"getattr({obj!r}, {name!r}) in the kept results")


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, items):
        super().__init__(file)
        self.items = items

    def find_class(self, module, name):
        if name not in ALLOWED.get(module, ()):
            current_app.logger.warning("Kept results name %s.%s: not read, computed anew", module, name)
            raise pickle.UnpicklingError(f"{module}.{name} is not allowed in the kept results")
        if (module, name) == ("builtins", "getattr"):
            return _getattr
        return super().find_class(module, name)

    def persistent_load(self, pid):
        kind, number = pid
        if kind != "item" or number not in self.items:
            raise pickle.UnpicklingError("a question of the kept results is gone")
        return self.items[number]


def _signature(body):
    key = hashlib.sha256(b"results-store:" + str(current_app.config["SECRET_KEY"]).encode()).digest()
    return hmac.new(key, body, hashlib.sha256).digest()


def save(survey, key, data, pool=None, name="results", basis=None):
    """Keep the results computed for the fingerprint `key`. Returns False if they cannot be kept (they are
    shown all the same). pool: the one of the fingerprint (weighted results have their own file).

    basis: what the results were computed under, checked once the file is written: the fingerprint(survey, pool)
    taken before computing (key itself, or the part a longer key starts with), or a function giving the key anew
    (for a key built from more than the fingerprint). Has a participation been deleted while computing (on request,
    by a retention period), the file would hold its answers again after the deletion had removed the files: it
    is removed and nothing is kept (code review 2026-10-15, D1; as export_tables.table). Every caller in app/ names
    it (tests/analysis/test_results_store.py)."""
    from app.models import utcnow

    buffer = io.BytesIO()
    try:
        _Pickler(buffer, protocol=pickle.HIGHEST_PROTOCOL).dump({"format": FORMAT, "key": key, "at": utcnow(), "data": data})
    except Exception as e:  # something in the results that cannot be written down: the page still shows them
        current_app.logger.warning("Results of survey %s not kept: %s", survey.id, e)
        return False
    body = buffer.getvalue()
    path = _path(survey, pool, name)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(_signature(body) + body)
        os.replace(tmp, path)  # readers never see half a file
    except OSError as e:
        current_app.logger.warning("Results of survey %s not kept: %s", survey.id, e)
        return False
    # after writing, not before: a deletion committed before this check is seen here, one committed after it removes
    # the files itself (erasure._after_commit, retention), so no window is left in which the file outlives it
    if basis is not None and not _still(survey, pool, basis, key):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            current_app.logger.error("Outdated kept results of survey %s not removed: %s", survey.id, e)
        return False
    return True


def _still(survey, pool, basis, key):
    """Whether what the results were computed under is still current (see save)."""
    if callable(basis):
        return basis() == key
    return fingerprint(survey, pool) == basis


def load(survey, key, pool=None, name="results"):
    """(results, computed at) kept for exactly this fingerprint, else None."""
    try:
        with open(_path(survey, pool, name), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    signature, body = raw[:32], raw[32:]
    if len(signature) != 32 or not hmac.compare_digest(signature, _signature(body)):
        return None  # not written by this application: never unpickled
    items = {i.id: i for b in survey.blocks for i in b.items}
    try:
        kept = _Unpickler(io.BytesIO(body), items).load()
    except Exception:  # written by another version of the program, or a question is gone
        return None
    if not isinstance(kept, dict) or kept.get("format") != FORMAT or kept.get("key") != key:
        return None
    return kept["data"], kept.get("at")


# every file save() writes (see _path), German and English, weighted and with the marked ones: the results, the
# group comparisons, the evaluated hypotheses, the omega intervals (results.fill_omega), the scenarios the market
# simulator opens with (simulator.page), the expected effects another survey's power analysis read from this one
# (power_pilot.estimate), the structural equation model (surveys._sem_result), the models over the waves of a panel (app/panel_models.py), the facts of the method section
# (methods.facts), the course of a diary study over time (surveys._diary_time) and its scales in two levels (surveys._diary_scales)
# the counts of the export page (surveys.data._export_counts) and what the page "Qualitätssicherung" computes from the
# participations (quality.page); forget_files removes every "*.bin"
# of the folder, whatever its name
KEPT = tuple(f"{name}{weighted}{marked}{language}.bin"
             for name in ("results", "compare", "hypotheses", "omega", "sim", "pilot", "sem", "methods", "diarytime", "panel", "diaryscales",
                          "counts", "quality")
             for weighted in ("", "-weighted") for marked in ("", "-markiert") for language in ("", "-en"))


def forget(survey):
    try:
        forget_files(survey.uid)
    except OSError:
        pass


def forget_files(uid):
    """Remove every file of kept results of the survey with this uid: every "*.bin" save() wrote into its folder
    (KEPT and any other name, e.g. the structural equation model "sem", which holds the answer rows of every
    person; code review 2026-10-07, R1), so that a name added later cannot slip through. For a deletion on request
    and the retention periods (audit 2026-10-05, L-1), which only know the uid once the rows are gone; raises
    OSError if a file stays, so they can try again."""
    from types import SimpleNamespace
    from app import export_tables

    export_tables.forget(SimpleNamespace(uid=uid))  # serialise with a writer also for retention/restore paths
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "hb", uid)
    try:
        names = os.listdir(folder)
    except (FileNotFoundError, NotADirectoryError):
        return
    for name in names:
        if not name.endswith(".bin"):
            continue
        try:
            os.remove(os.path.join(folder, name))
        except FileNotFoundError:
            pass
