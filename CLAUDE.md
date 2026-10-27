# Askurious: rules for every change

## Every text in every language

Askurious speaks to two kinds of readers, and every new or changed text has to reach both in all their languages.

- **Researchers and admins** (everything outside `app/templates/participant/` and the participant parts of the
  element types): German (the standard) and English, chosen per person on "Konto" (app/lang.py). Write each text as
  a pair, German first: `{{ de_en("Konto", "Account") }}` in templates, `de_en("…", "…")` in Python (`from app.lang
  import de_en`), `de_en("…", "…")` in app/static/*.js. Values go in as str.format fields:
  `de_en("{n} Tage", "{n} days", n=days)`. Never compare a shown label to decide something; use a key.
- **Participants**: every survey language. Write `ui("Weiter", "Next")` in templates, `tr(lang, "…", "…")` in
  Python, `uiT("…", "…")` in participant.js, and add the English text with all its translations to
  `app/ui_catalog.json` (app/ui_i18n.py).
- Words: `docs/glossar.md`, German and English (section "Englisch"). German addresses researchers with "Sie",
  English with "you"; British spelling.
- Tests that guard this and run with every change to templates, scripts or messages:
  `tests/ui/test_researcher_i18n.py` (no German without its English half; the rest list `tests/i18n_rest.json` only
  shrinks: after translating, `python tests/ui/test_researcher_i18n.py --update`; never raise a count by hand),
  `tests/ui/test_ui_i18n.py` (participants' catalog) and `tests/ui/test_glossary.py`.

Decisions behind this: decisions.md, "Sprache der Oberfläche für Forschende und Admins (2026-10-06)".

## No concepts or audit reports in the repository

Concepts, audit and review reports, remediation lists and other planning documents never go into the repository
(Ralf, 2026-10-19): they belong in the project's library. The repository is to become public, and such documents
carry internal plans and lists of weaknesses. Code comments may still name a concept by its file name.
`tests/platform/test_no_concepts_in_repo.py` refuses tracked documents named like one (konzept, concept, audit,
prüfung, review, remediation) outside app/, tests/ and migrations/.

## Legal texts with every change

Every code change can make a legal text wrong. Before a PR is ready, check whether one of these has to change, and
if so change it in the same PR:

- `README.md` and `licence.md` in the repository (licence.md is also the terms of use researchers accept; a change
  there needs a new "Version:" line, app/terms.py, so everyone accepts it again).
- In the app: the privacy page (`app/templates/public/privacy.html`, `privacy_en.html`), the participants' privacy
  texts (`app/templates/participant/privacy.html`, `privacy_info_text` in app/items/consent.py) and the terms of
  use (`app/templates/terms.html`, `terms_en.html`).
- The privacy concept under Administration > Datenschutz (`app/privacy_concept.py`, Word file
  app/privacy_concept_docx.py): it is built from the code, so a new kind of personal data, a new outside service,
  a new role or a new protective measure has to appear there.

Typical triggers: new personal data or a new field about people, a new outside service or host, cookies, logging,
retention or deletion, sharing or export, new roles or rights. In the PR description name what was adjusted, or
write one sentence: "Rechtstexte geprüft, keine Änderung nötig." (Ralf, 2026-10-10.)

## Handbook with every tab and page change

Ändert ein PR Reiter oder Seitenaufbau, ändert er das Handbuch mit: `app/data/handbook/de/dialogs.md` and
`en/dialogs.md` describe every tab and page. Each tab of `surveys/_tabs.html` has a section there, linked as "Hilfe zu
dieser Seite" from the menu "Hilfe und Werkzeuge" (`TAB_ANCHORS` and `PAGE_ANCHORS` in app/handbook.py);
`tests/platform/test_handbook.py` refuses a tab without an anchor in both languages (UI/UX audit 2026-10-16, 156).

Ändert ein PR den Metadatenexport (DDI Lifecycle 4.0 in app/ddi.py: neue Einträge, Verweise oder Inhalte), zieht er
die Abbildung „Askurious-Metadaten“ im Handbuch (/docs/metadata) mit: `tools/ddi_figure.py` anpassen und ausführen
(schreibt `app/static/handbook/<lang>/ddi-structure.svg`). `tests/exports/test_ddi_figure.py` verlangt jede Art aus
`ddi.CONTAINER_ORDER` in der Abbildung und dass die Dateien der Ausgabe des Skripts entsprechen (Ralf 2026-10-27).

## Blog: no experimental features

Readers of the public blog (/blog, app/blog.py) do not see experimental features (app/experimental.py).
Write about a feature only once it is open to everyone, and then as a new feature; before that not even as a
mention or with "(experimentell)". When a feature leaves the experimental group, check the published entries
too. The AI draft gets the list from FEATURES (blog.draft_instructions). (Ralf, 2026-10-11.)

## Migrations and the rolling deploy

The participants' side runs in two containers on the Docker server, and a deploy restarts them one after the other
(deploy/docker/restart.sh), so that participants never see an error page. For that moment the container not yet
restarted runs the **old code against the new schema**. Every new Alembic migration therefore says what it does:

- `ROLLING = True` when it only adds: a new table, a column that may stay empty or has a `server_default`, an
  index, a longer text column. Then the deploy rolls.
- `ROLLING = False` (the default of `migrations/script.py.mako`; also when the line is missing) when it drops or
  renames a table or column, makes a column required, or changes data the old code reads differently. Deployment
  then stops: split the change into compatible deployments first. Ralf, 2026-10-13: participant containers must
  always restart sequentially; the first must be healthy and serving again before the second is touched.
  FORCE_ROLLING must not bypass an incompatible migration; ALL_AT_ONCE is refused.

`tests/platform/test_rolling_restart.py` refuses `ROLLING = True` on a migration that drops, renames or adds a required
column, changes a type, adds a UNIQUE, changes or deletes rows or replaces triggers (read from the syntax tree of the
whole module, helpers and loops included), unless `ROLLING_ALLOWED` names that step for the revision with the reason
why the old code is not hurt. A `ROLLING = True` migration that creates or drops an index on a large table of
participation (`BIG_TABLES`: response and others) does it with `postgresql_concurrently=True` inside
`with op.get_context().autocommit_block():`, so PostgreSQL does not hold the table for the whole build (audit
2026-10-21, B13). To rename without a gap, take two deploys: add the new column and fill both, later drop the old one.
That later drop says `ROLLING = False` and `DROPS_UNUSED_SINCE = "<revision>"`, the revision whose code stopped
reading what it drops: restart.sh rolls it once the database is at that revision, never in the same deploy
(Ralf, 2026-10-18; first use: the key columns of konto-schluessel-2b-vorschlag.md). "Stopped reading" means the
models of that revision no longer map it: SQLAlchemy puts every mapped column into every query of its class, so a
column still in the old models breaks the old participant as soon as it is gone (503 on every survey page, audit
2026-10-18, FUNC-01). So the first deploy takes the column out of the models (or the class out for a table), the
next one drops it, and the drop lists it in `DROPS = ("table.column", "table", ...)`;
`tests/platform/test_rolling_restart.py` reads the models of `<revision>` from git and refuses a drop they still map.
Concept: zwei-teilnahme-container-konzept.md (Ralf, 2026-10-08).

## Packages instead of one big file

`app/surveys/`, `app/editor/`, `app/live/` and `app/boards/` are packages with one module per part (a tab, a page
group, a step), plus `base.py` with the blueprint and shared helpers. New code goes into the module of its part; a
helper two parts need goes into `base.py` (the modules import only from `base.py` or along one direction, never in a
circle). Other code imports from the package (`from app.surveys import get_survey`); its `__init__.py` re-exports
those names. A test that replaces a helper with `monkeypatch` replaces it in the module that calls it
(`monkeypatch.setattr(boards.compute, "build", ...)`). Architecture concept 2026-10-12, D.

## Where tests go

The tests live in one folder per area: `tests/accounts`, `analysis`, `dashboards`, `diary`, `editor`, `exports`,
`interview`, `items`, `live`, `participation`, `platform`, `privacy`, `quotas`, `ui`; helpers and data files
(`conftest.py`, `tsa_fake.py`, `*.json`, `*.js`, `data/`) stay in `tests/`. A new test goes into the folder of what it
tests and is named after that, not after the audit or review it came from (say the audit in its docstring). For the
tests a change needs, run the folder of the area (`pytest tests/quotas`). The tests of the former mixed audit and
review files are in `tests/<area>/test_<area>_audit_findings.py`, their helpers in `tests/helpers/audit_<file>.py`.
`tools/sort_tests.py` sorts files left in `tests/` (architecture concept 2026-10-12, G).
No test may wait without a limit: `tests/conftest.py` ends any test after `TEST_TIMEOUT` seconds (default 1200) as a
failure, and a test that runs a script takes `tests/helpers/bounded_run.run` with a `timeout`, which kills the whole
process tree instead of `subprocess.run`, which can wait forever on a child's pipes (2026-10-15).

## Leave CPUs free when running tests

Several assistant threads run on one machine with 24 logical CPUs, and about 4 of them have to stay free for the person at the machine.

- Never `pytest -n auto`. Take `-n 4` when other threads are running (the usual case), at most `-n 8` when this is
  the only one. A single test file runs without `-n`.
- `tests/conftest.py` sets `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to 1 before numpy is
  imported, so every worker stays on one core; do not remove it.
- A run with `-n` waits for one of two places on this machine (`TEST_SLOTS`, tests/helpers/slots.py) and says
  "Waiting for a free test place". Let it wait; do not set `TEST_SLOTS` higher or to 0 to jump the queue. Five
  suites at once each take longer than the same runs one after another.
- `pytest -n 4 --testmon tests/<area>` runs only the tests whose Python code changed since they last ran (one
  record for all checkouts in the temp folder). It does not see templates, JavaScript or JSON: after changes there run
  the area folder as before. Traced tests take about 1.6 times as long, so for a change to a central helper the plain
  folder run is faster. Before a PR is ready still `--full` without `--testmon`. Do not set `COVERAGE_CORE=sysmon`:
  on Python 3.12 testmon then misses most of the affected tests.
- Run the tests with a Python that has every package of requirements.lock and requirements-dev.txt; otherwise
  the run stops at its start and names what is missing (tests/helpers/requirements_check.py). On the development
  machine that is `E:\Softwareentwicklung\queryous\askurious-test-venv`, built with `python tools/test_venv.py
  <folder>` (run it again after a change of requirements). A test that guards speed measures CPU time
  (`time.process_time()`), not the wall clock, and a test waits for an event instead of sleeping a fixed time
  (test suite concept 2026-10-18).
- A green `--full` run of the whole `tests` folder on a clean checkout (nothing uncommitted or untracked) leaves a
  mark for its git tree; `deploy-docker.sh` does not run the same state again (`tools/green_run.py`,
  `FULL_TESTS=1` runs it anyway). So run the last `--full` before "ready" on the committed, clean branch; the
  merge commit of a branch that was up to date with main has the same tree.
- A `--full` run of everything on Windows splits itself: the deploy tests (bash) and about half of the other test
  files run in WSL meanwhile, on a copy of the files in /tmp, with as many workers as on Windows
  (tests/helpers/wsl_deploy.py, venv `~/askurious-test-venv` with Python 3.12 from `~/.local/python3.12`). The
  halves are balanced by `tests/file_durations.txt`; files that need Node or a browser stay on Windows, and a test
  WSL skips runs again on Windows at the end. The green mark is written on Windows as before. `WSL_SPLIT=0` hands
  over only the deploy tests, `WSL_TESTS=0` keeps everything on Windows.
- The everyday run leaves out the single tests listed in `tests/slow_tests.txt` (3 s and more in a full run). Do
  not edit it by hand: `python tools/slow_tests.py <log of --full --durations=0>` writes it anew, together with
  `tests/file_durations.txt`.
- `tools/claude-cpu-begrenzen.cmd` (Windows) keeps the Claude app and everything it starts off
  CPUs 0-3 and at below-normal priority; `-Loop` repeats that every 30 seconds.
