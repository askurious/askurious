<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
    <img src="docs/logo.png" alt="Askurious" width="320">
  </picture>
</p>

<p align="center"><em>/ɑːˈskjʊə.ri.əs/, from <strong>ask</strong> and <strong>curious</strong></em></p>

<p align="center"><a href="https://doi.org/10.5281/zenodo.23612928"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.23612928.svg" alt="DOI 10.5281/zenodo.23612928"></a></p>

Askurious is a web platform for social science research: plan a study, build the questionnaire, take it to the field, check the data quality and analyse the results, all in one place.
It is developed and run by the [Chair of Communication Science of RWTH Aachen University](https://www.comm.rwth-aachen.de) for research and teaching. Authors: Philipp Brauner and Ralf Philipsen; contact: Dr. Philipp Brauner ([brauner@comm.rwth-aachen.de](mailto:brauner@comm.rwth-aachen.de)).

- Live at [askurious.de](https://askurious.de); participants answer at ask.askurious.de.
- The same platform answers at [polltergeist.de](https://polltergeist.de) as POLLtergeist, with a look of its own for live votes.
- See the question types in action: [ask.askurious.de/s/introduction](https://ask.askurious.de/s/introduction).

## What it does

### Plan
- **Study plan** with research question, design, hypotheses and the analysis that tests them; the hypotheses can also
  be drawn as a model and tested together as a structural equation model (covariance-based or PLS, with mediation and
  moderation, control variables, second-order factors, a competing model, full information ML for gaps, ordered
  categories with WLSMV, weights, measurement invariance across groups and power by simulation).
- **Sample size and power:** sixteen planners, design effect, subgroups, and the reverse direction (sample given, what can be found).
- **Construct toolbox** with validated scales (BFI-10, GESIS short scales, TAM, KUT, Technikbereitschaft and others), sources in APA style, BibTeX and RIS; own constructs can be shared and submitted for review. The code carries the structure of the scales of others, not their wording, which each installation adds itself (`instance_overlay/README.md`). The list and the page of each construct are open without an account; the wording of the items only where its licence allows passing it on (the RatSWD standard questions, own wordings), else signed in. In the questionnaire editor a quick box lists the own favourites and the constructs the organisation recommends (kept by its admins; else the platform's list, kept by the site admins); the full box opens as a dialog to search, filter, preview and insert into a chosen block.
  Demographic standard questions of the RatSWD catalogue (minimal versions: birth month and year, sex, education, employment, occupation, income, citizenship, country of birth, household size, marital status); they stay out of the pooled construct data.
- **Pre-registration snapshot:** at the start of fieldwork the survey is frozen as a ZIP file with SHA-256 checksums to cite.

### Build
- **Questionnaire editor** with drag and drop, live preview, a design check, the estimated completion time, saving without buttons and undo/redo.
- **More than forty element types** (all of them described in [docs/elementtypen.md](docs/elementtypen.md)):
  single and multiple choice (also as images, dropdown or search field), drill-down lists, ranking, constant sum, text and number fields, postal code and German federal state with maps, date and time, personal code for linking waves;
  Likert matrix, semantic differential, side-by-side matrix, rating, slider, Van Westendorp price sensitivity;
  conjoint (CBC), adaptive conjoint (ACBC), ratings-based conjoint (CVA), MaxDiff and paired comparison;
  2D positioning, image marking and hot spots, map with markers, location, card sorting, Q-sort, assignment task with reaction times, Implicit Association Test (IAT), delay discounting, free associations, cloze text and text highlighting;
  voice message (participants speak their answer) and video or audio with a playback log;
  feedback to participants (comparison with the sample, crowd, score band, IAT result).
- **Flow:** blocks, randomisation, conditions (if/then/else, also per question and per statement), loops over a table, factorial vignettes with D-optimal designs, stimulus sampling, quotas, external sections (a web app in the middle of a survey), computed values and answer piping.
- **Languages:** surveys in German (Sie and du), English, Arabic, Chinese, Dutch, French, Italian, Polish, Portuguese, Russian, Spanish and Turkish, with a translation page and AI help for what is open; the researchers' interface in German or English.
- **Look:** own colours, fonts and logos for the participant pages, templates, designs shared with a team.
- **Consent** with an assistant, retention periods, a separate section for contact details, anonymous participation, accessibility after BITV 2.0 / WCAG 2.1 AA.
- **Paging through faster, even on a weak connection:** blocks whose pages do not depend on each other come to the browser in one piece; each page is still saved in the background, briefly buffered in the browser's session storage while offline, and checked again by the server at the end of the block.

### Field
- **Links:** short links, QR codes, links per group or recruiter with counts, panel providers with URL parameters and completion links per outcome, invitation emails where mail is set up.
- **Quotas** with an assistant for the German population (age, gender, federal state; Destatis).
- **Data quality:** speeders ([Leiner, 2019](https://doi.org/10.18148/srm/2019.v13i3.7403)), attention checks, response patterns, tempo hint, protection against AI agents and bots, one tab "Qualitätskontrolle" that keeps marked participations out of the results.
- **Live votes** with a second screen for the lecture hall, slides from a PDF that the presenter can write on with a digital pen (shown live on the screen, exported with the notes as a PDF), optional notes of the audience on the slides (kept on their own device, downloadable as a PDF), and runs that can be merged (the exports keep the original run), and **interview guides** with transcription and analysis.
- **Online interviews:** recordings from Zoom, Teams, Webex or BigBlueButton are uploaded to the interview (sound only, no video); a file that holds one voice can carry its speaker, so the transcript interleaves "I:" and "B:" by time; a video call with recording needs the tick that everyone who speaks consented. The interview view can also record the sound of a shared conference tab or screen together with the microphone (Chrome and Edge; screen sharing is allowed only on that page).
- **Interviewer links:** people without an account conduct interviews of a guide with a personal link or QR code, record and transcribe them, and see only their own interviews until they hand the transcript over as checked; the researchers block, renew or delete the links (key only as a hash, at most three devices, 90 days by default).
- **Diary studies** (experience sampling) with the Android app [askurious-diary-android](https://github.com/askurious/askurious-diary-android): join by QR code or code, an entry survey once, then short surveys at fixed times or whenever participants want to report something, and an exit survey at the end, each part a section of its own in the editor (an existing questionnaire can be taken over as entry or exit survey); a test code for the draft, prompts at fixed or random times within windows (participants set quiet hours), a week preview, reminders, answering offline for simple modules, persons who stand out, the course over days and hours with ICC(1), compliance per person, export in long format with a person column (renumbered when published) and a row for every missed prompt.
- **Paper questionnaires**: printed as a PDF with numbered sheets, scans uploaded, ticks read on the server (corner marks and a code per page), uncertain ticks and handwriting checked by hand (handwriting can be read by AI as a suggestion), filter and screen-out notes printed, the deletion code on a page of its own that the participant keeps, sheets taken over as participations with the mode "paper", results split by mode; CBC and MaxDiff with the design version each sheet drew, Hot Spot areas, an optional four-eyes check; handwritten numbers read locally as a suggestion.
- **Live feedback** to participants, e.g. their position in the sample (real-time Delphi; [Kühne & Kroh, 2018](https://doi.org/10.1177/0894439316673604)).
- **Dashboards** per survey with public links, live updates and embedding.

### Analyse and share
- **Results:** descriptive statistics, Cronbach's α and McDonald's ω, exploratory factor analysis with parallel analysis, scale and SEM tables in APA style, models over the waves of a panel (invariance over time, growth curves, RI-CLPM) and multilevel models of diary studies, group comparisons with tests and effect sizes, hypotheses of the study plan tested, open answers categorised; choice models with hierarchical Bayes, latent classes and a market simulator. Every figure can be saved as a PNG (rendered in the browser, three times the screen size).
- **Method section** in German or English, written by AI from the survey's own figures.
- **Export:** CSV, SPSS (.sav), jamovi (.omv), R (.rds), long formats, codebook and questionnaire as Word, the survey as DDI Lifecycle 4.0 (XML) for data archives, as DDI Codebook 2.5 (XML) for CESSDA and as CDIF (JSON-LD) for catalogues, all three also in the Zenodo and Coscine packages; read tokens for R and Python.
- **Open data:** packages for Zenodo and Coscine; a seal chain with DFN time stamps shows that no case was removed unnoticed: the platform checks the stored data against it every hour, and anyone can check a published data file for the completeness and origin of its cases (which cases, which deletions; not the answer values).
- **Data protection:** participants delete their own answers in the same browser for 15 minutes after taking part, later with a deletion code where the survey switches it on; retention periods delete the rest; the privacy concept is built from the code (Administration › Datenschutz).
- **Performance:** transactional data revisions keep unchanged reports cheap; CSV, SPSS, R and jamovi share a signed export table for up to one hour, cleared on erasure. Docker runs one housekeeping service per database; paper scans run in low-priority child processes. Static CSS/JavaScript are minified and precompressed at build time; nginx serves static files and validated media redirects. Long caching requires the actual content hash.
- **Large reports:** from 5,000 participations (`REPORT_JOB_THRESHOLD`), results and data exports run in separate processes; below that, a process that also serves participants hands a computation of about a second or more (participations × elements from `REPORT_JOB_COST`) to one as well. Results already kept and the codebook are answered at once. Web downloads show a waiting page; the R/Python data API keeps its existing download response. `HEAVY_PARALLEL` limits computations across all processes of a container. Private report files expire after one hour and are cancelled/removed on erasure.
  Synchronous machine downloads wait at most `REPORT_JOB_WAIT` (10 seconds by default, capped at 30), with `REPORT_API_WAITERS` shared waiting places (4 by default, 1–8), at most one per token. Exhausted capacity returns 503 with `Retry-After`. For long reports, send `Prefer: respond-async`: a 202 response supplies `Location`, `X-Report-Job` and `Retry-After`. GET that location with the same bearer header and preference until it returns the file (200); the location contains no bearer secret. Repeated downloads of that job reuse its completed snapshot until expiry; start from the original URL for a fresh report. Every poll/download rechecks current token validity and permissions. Existing token rate limits apply to polling too; honour `Retry-After` and back off on 429/503. Job references expire or become unavailable after erasure (410).
- **Uploaded participant pictures:** still images become WebP at at most 1,600 pixels, preserving transparency; animations remain animated. `python -m app.resize_uploads` previews conversion of existing image/image-mark assets, `--apply` applies it with new filenames and keeps originals for recovery.
- **Teamwork:** sign-in with ORCID or email and password, roles (admin, edit, view), groups to share surveys, designs and constructs; example surveys every signed-in researcher can view and copy; surveys shared by others can be hidden from one's own list. Site admins set per account which survey types (questionnaire, live poll, interview guide, diary survey) it may see or edit; new accounts get a default, a group preset or what the invitation needs. User types (staff, thesis, internship, course) give new accounts a period in weeks, months or until a date: at the end the account is blocked, after the type's deletion period it is deleted if nothing has to be handed over; theses get a supervisor who becomes admin of every new survey; courses are groups whose lead invites an address list (without mail as a file of links) and creates one-time links for its accounts. Organisations (chairs, institutes, departments, companies) sit above the groups as a fence: groups share only within their organisation, the people search finds only one's own organisations, and only a personal invitation to a survey crosses it; site admins create an organisation directly (name, full name, a first admin by ORCID iD or account address) or make an existing group one, with a preview of who moves and what comes along. Organisation admins manage their people (a sortable, searchable list like the platform's, adding people with or without ORCID iD; user type, period, survey types, blocking; never other admins of the organisation; accounts that belong only to it are marked for deletion, blocked at once and deleted after 30 days unless taken back, their own surveys and designs passing to a person of the organisation or going with them; people who also belong elsewhere are removed with the organisation's groups), groups and courses under "Management", within limits site admins set (survey types, creating surveys, number of accounts); each organisation defines its own user types (except admins) and starts with staff only, the others come from templates; they see the organisation's surveys without content or answers. New surveys, groups and designs go into the home organisation; surveys can be moved, designs shared with a whole organisation, and surveys made its examples. Accounts that came only by an invitation to a survey are guests without an organisation; switches per organisation allow or stop invitations across. Organisations have a short name for the pages and a full one for survey texts, their own logo (light and dark) for all members and an organisation design (light and dark, uploaded as a design file or taken over from a colour scheme or design) that new surveys start with and every member can choose but only change in their copy; site admins may upload a template for it. Institutions are no longer recorded or fetched from ORCID: the organisation stands in their place. Group names are unique within an organisation.

Some features are still experimental and appear only when switched on under "Konto" or by the admins (list in [app/experimental.py](app/experimental.py)): AInstein for developing a research question, categorising open answers, Web Services in the flow and the participant screen in interviews (the interviewee solves a task of the guide on their own device, also in a video interview: a link per interview for one device, only the released task, the result back into the interview view).

The Web export shows the exact selected participation count, including quality exclusions; the codebook names sources,
speeder rule and exclusions. View filters and
weighting of results do not change exported answer files; dashboards use the saved pool without test runs.

Diary schedules offer individual time fields for fixed times and random windows, with an adjacent weekday preview,
overnight labels and overlap notices. Text entry remains available; incomplete rows are not automatically saved.
Interview guide headings and explanations wrap independently, and compact actions have at least 44-pixel touch targets.

Diary offline pages retain their questionnaire version and random order in person-scoped receipts for 30 days.
Invalid or incompatible submissions are retained for editor review, excluded from regular exports and analyses.
The review shows the frozen original questionnaire and inputs alongside proposed corrections, with a validation-only
preview. Accepting a correction keeps the original and records the accepted input fields, editor, time and clarification.
The Android queue removes
an answer only after confirmed server storage or explicit removal of the study. Diary summaries weight persons
equally per statistic, confidence intervals account for repeated measurements, and scale reliability and between-person
correlations use person means; within-person correlations are descriptive. Computed-value formulas with real input
answers cannot be changed when reopening a questionnaire; create a new variable or duplicate the survey instead.
Voice recordings are flushed to durable staging before committing their database reference; interrupted final moves
are retried and staged recordings remain downloadable. File backups compare contents; the regular backup success
marker is published only after database dumps, file snapshots and any configured offsite copy succeed.
On the Linux Docker server, a shared transaction gate includes commit/rollback file callbacks; the backup takes
an exclusive gate across the dumps and file snapshots. New requests wait during that window. The gate is released
before an offsite transfer, and a rolling deployment with older containers refuses a consistent-backup success.

## Files and exports

Questionnaire ZIP exports stream image files in 64 KiB chunks and spool output beyond 16 MiB to disk.
`EXPORT_JSON_MAX_BYTES` (32 MiB by default) bounds inline JSON; oversized questionnaire downloads redirect to ZIP.
`EXPORT_ZIP_MAX_BYTES` (5 GiB) bounds uncompressed members across all selected surveys. Small frozen snapshots retain
their inline JSON format; large ones stream images as separate ZIP members, including incremental SHA-256 hashing.
Questionnaire, Word, comment and voice exports share the heavy-work admission limit. Word image inputs share the
inline JSON byte budget and a pixel limit; an oversized document is refused clearly rather than dropping images.

Upload admission and physical writes share an OS file lock in the instance directory across processes and containers.
Every media and interview chunk checks disk, survey and account capacity. SVG logos, thumbnails, scans and file copies
use the same admission; account checks include the survey admins even for collaborators or unattended requests.
The shared instance volume must support OS file locking. This uses measured files, including failed/uncommitted writes,
instead of a counter that could under-report usage after a crash; process death automatically releases admission.

## AI

AI functions are optional and always marked: they suggest questions, scales, translations, images and method texts, check wordings, and help to categorise open answers.
By default they run over the platform's access to OpenAI; with an own AI key (KI:connect.nrw, OpenAI or any OpenAI-compatible service) they run over the researcher's provider.
The page “AI usage” shows each person their AI calls of the last 30 days and has a switch that turns generative AI off for them altogether.
What is sent where is written in the terms of use ([licence.md](licence.md), section 6) and on the privacy page of the platform.

Accounts and AI processing: a newly chosen sign-in address needs a confirmation by mail, and password resets go through an encrypted, durable queue. AI categorisation removes recognisable contact details, and translations use placeholders that are filled in again locally. This does not replace checking free texts for personal data.

### AI agent interface (MCP)

Experimental (feature `agent`, switched on per person by a site admin). An AI agent of the researcher's choice reads
questionnaires (structure and texts, never answers or data) and, with an edit token, changes drafts: blocks, elements,
constructs of the toolbox, undo of its own last step. Starting fieldwork, deleting surveys, data, participation links,
members and the consent are not offered. Askurious sends nothing to a model itself: the agent fetches the contents and
passes them to the AI service the researcher chose (terms of use, section 6).

Set-up in Claude Code: create a token on the page “Account” › “Connections” › “AI agent interface” (scope “read” or “read and edit
drafts”, optionally limited to some surveys; valid 90 days, shown once) and add the server:

```
claude mcp add --transport http askurious https://<your-askurious-address>/mcp --header "Authorization: Bearer askat_…"
```

The endpoint `/mcp` speaks Streamable HTTP (MCP 2026-07-28 with per-request metadata, and the earlier revisions with
`initialize` back to 2025-03-26), plain JSON answers, no sessions. The token goes only in the `Authorization` header;
one in the address is refused. Every tool call is in the access log (“Agenten-Zugriff”), calls per token are limited
(`AGENT_LIMIT` per `AGENT_WINDOW` seconds, default 600 per hour), and each change is an undo step named after the token.
The tools live in one registry (`app/agent_tools.py`) so that a WebMCP wrapper in the editor can follow later.

## Development

Python 3.12, Flask 3.1 with Flask-SQLAlchemy 3.1 (SQLAlchemy 2) and Alembic; SQLite or PostgreSQL (driver in requirements-postgres.txt, a move under Administration › Datenbank).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python run.py
```

The app then answers at http://localhost:5060. Settings come from `.env` next to the code, see [config.py](config.py).

**Tests:** `.venv/bin/python -m pytest -q -n 4` (pytest-xdist from requirements-dev.txt).
The tests need every package of requirements.lock and requirements-dev.txt; a run stops at its start when one is missing. `python tools/test_venv.py <folder>` builds such a venv (on Windows too, with the same versions); speed limits in tests count CPU time (`time.process_time`), so busy cores do not turn them red.
Take `-n 4` while other test runs share the machine, at most `-n 8` alone, and no `-n` for a single file (CLAUDE.md).
Everyday runs leave out the tests of 3 s and more (marker `slow`, listed in `tests/slow_tests.txt`; `python tools/slow_tests.py <durations log>` writes the list anew) and the deploy tests (marker `deploy`; they run by themselves when a branch changes `deploy/`, `deploy.sh` or `migrations/`); add `--full` for all of them, always before a pull request is ready.
A green `--full` run over the whole `tests` folder on a checkout without uncommitted or untracked files leaves a mark for its git tree in the temp folder (tests/helpers/green_runs.py); `deploy/docker/deploy-docker.sh` then skips its own full run of the same state (`tools/green_run.py` says why; `FULL_TESTS=1` runs it anyway).
On Windows a `--full` run of everything hands the deploy tests (bash) and about half of the other test files to WSL when WSL has a complete test venv at `~/askurious-test-venv` (`ASKURIOUS_WSL_PYTHON` elsewhere; build it with `tools/test_venv.py` and a Python 3.12 inside WSL): they run there on a copy of the checkout while Windows runs the rest, and their result counts for the run. The halves are balanced by the seconds per file in `tests/file_durations.txt` (written by `tools/slow_tests.py`); files that need Node or a browser stay on Windows, and a test skipped in WSL runs again on Windows. `WSL_SPLIT=0` hands over only the deploy tests, `WSL_TESTS=0` keeps everything on Windows.
A file named on the command line, `-k` or `-m` run what they pick.
Parallel runs on one machine queue for two places (`TEST_SLOTS`, default 2, 0 switches it off).
`--testmon` (pytest-testmon) runs only the tests whose Python code changed since they last ran; templates and scripts it does not see.
Run the whole suite against PostgreSQL with `TEST_DATABASE_URL=postgresql://user:password@host:port/postgres` (a user that may create databases); with `TEST_PG_SERVER` only the tests of moving into PostgreSQL use it.

**Schema changes** are Alembic migrations (`migrations/versions/`, app/migrations.py): change the model, run `flask db migrate -m "what changed"`, read and fix the new file (SQLite needs batch mode, which the files use), run `flask db upgrade` and the tests; the migration comes in the same pull request as the model.
Every migration says whether it may roll (`ROLLING`, see CLAUDE.md).
The app applies missing migrations by itself when it starts (one server process at a time; SQLite gets a copy under `instance/backups/` first). tests/platform/test_migrations.py fails while models and migrations differ.

**Rules for every change** are in [CLAUDE.md](CLAUDE.md): every text in German and English (and for participants in every survey language), the words of [docs/glossar.md](docs/glossar.md), and the legal texts checked with each change.

**Add-on modules** are separate Python packages that extend Askurious when they are installed in the same environment: they name a module with `init_app(app)` under the entry point group `askurious.plugins`, register their own pages and may add an experimental feature, a button on "My surveys" and keep surveys they made for themselves out of the lists ([app/plugins.py](app/plugins.py)). Without one installed nothing changes; a module that fails to load is left out and logged.

**Records:** what was built is in [log.md](log.md), why in [decisions.md](decisions.md), what is still open in [bucketlist.md](bucketlist.md).

## Running a server

Two ways are maintained:

- **Docker** (askurious.de): nginx, two containers for participants that restart one after the other, one for researchers, PostgreSQL, backups and a status page; `deploy/docker/deploy-docker.sh`, operation in [deploy/docker/BETRIEB.md](deploy/docker/BETRIEB.md).
  Separate runtime/migration roles and network segmentation are enabled and verified on askurious.de (2026-10-14).
  The staged deployment flow and provisioning for further servers are documented in
  [deploy/docker/isolation/README.md](deploy/docker/isolation/README.md).
- **One process behind Plesk/Passenger** (passenger_wsgi.py), e.g. for a test server. With SQLite it must stay one process (Passenger: at most one application instance, e.g. `passenger_max_pool_size 1`): SQLite ignores the row locks the app takes (`SELECT … FOR UPDATE`), so two processes could read the same row and both write it, and one change would be lost without an error. Several processes need PostgreSQL (a move under Administration › Datenbank). The log says so at every start with SQLite.

The server reads its settings from `.env`; every setting is explained in [config.py](config.py). These need care:

- `SECRET_KEY`: a long random value, set once and kept. Changing it signs everyone out, makes the deletion codes participants noted down worthless without notice, and breaks the return links of an external section that participants have not used yet.
- `TOKEN_KEY` (optional): the key of tokens and keys stored in the database; without it they depend on `SECRET_KEY` too. Steps in BETRIEB.md, section "Eigener Schlüssel für gespeicherte Geheimnisse".
- `POOL_KEY` (optional): the key of the pseudonyms (`study_id`, `participant_id`) in the pooled construct data. Without it they come from `SECRET_KEY`; set once, it keeps them when `SECRET_KEY` changes. Setting it later changes every pseudonym once (earlier `pool_*.csv` files no longer join with new ones), so best before the first download.
- `DIARY_KEY` (optional): the key of the random prompt times of diary studies. Without it they come from `SECRET_KEY`; set once, it keeps them when `SECRET_KEY` changes (otherwise every answered random prompt would count as missed). Setting it later moves the random times of studies under way, so best before the first diary study runs.
- `PROXY_COUNT`: the number of proxies in front of the app (nginx in front: 1; without a proxy leave the line out). Verify it on Administration › IP-Region: the address shown there must stay the same when the page is requested with a made-up `X-Forwarded-For` header.
- `PARTICIPANT_BASE_URL` (optional): an own address for participants, e.g. `https://ask.askurious.de` (app/hosts.py). Participation links, QR codes, return links, pretest, live, dashboard and quota links lead there; the participants' paths on `PUBLIC_BASE_URL` move there, a participation under way goes along once (until `PARTICIPANT_HANDOVER_UNTIL`, `YYYY-MM-DD`, empty: disabled), and so does the mark "took part" of a survey with `repeat_marker`. The session cookie there is signed with a salt of its own; until `PARTICIPANT_OLD_COOKIE_UNTIL` (`YYYY-MM-DD`, empty: never) that address still reads one signed the way before. Without it one address answers everything (a test server, local development).
- `SIDE` (optional, `participant` or `researcher`): one process per side, as in `deploy/docker/compose.yaml`; each answers only its own paths (`participant` only with `PARTICIPANT_BASE_URL`). Without it, with `PARTICIPANT_BASE_URL`, the name a request came to decides.
- `DIARY_APP_FINGERPRINTS` (optional): the SHA-256 fingerprints of the signing keys of the Android app for diary studies (comma-separated), served in `/.well-known/assetlinks.json` on the participants' address so that join links open the app; empty: they open the join page. `DIARY_APP_URL`: where the join page sends people for the app (Google Play by default).
- `DIARY_MIN_APP_VERSION` (optional, default 0): the oldest version of the Android app (its `versionCode`) that diary studies still accept. An older app asks the person to update it; 0 accepts every version. Raise it when the API changes in a way older apps cannot follow.
- `BRANDS` (optional): further domains with a name of their own, e.g. `polltergeist.de=POLLtergeist` (app/brand.py).
- `MAIL_HOST`, `MAIL_FROM` (optional; with `MAIL_PORT`, `MAIL_SECURITY`, `MAIL_USER`, `MAIL_PASSWORD`): the SMTP access for email (app/mail.py). Without `MAIL_HOST` and `MAIL_FROM` the site sends no mail and no page shows a mail function; Administration › Server, section "Mailserver", says what is missing and sends a test email. Every mail comes from `MAIL_FROM` (its domain needs SPF/DKIM for the mail server used), answers go to the person named in Reply-To.
- `INVITES_PER_DAY` (optional, default 2000): invitation mails to participants per survey within 24 hours. `MAIL_PER_ACCOUNT_DAY` (optional, default 2000): series mails one account sends within 24 hours across all its surveys.

Security limits guarding secrets count and insert under a shared database transaction lock; unavailable shared counters refuse protected attempts. Diary entry admission and daily caps are serialised per participation through commit, including offline submissions and UUID replays. These changes require no schema migration.

Diary questionnaire links and browser grants expire after twelve hours, work only in the browser that opened the link first, and are revoked on leaving, pausing, ending or reconnecting the app. A used personal code connects a new phone only after the researchers released it (once, for two hours). Revocation and answer writes share a participation row lock. A separate current grant still allows the exit questionnaire when appropriate; stored answers and their identifiers are preserved. Hashed link receipts use the existing short-lived receipt table (at most 100 per person), are swept after expiry, and disappear on erasure. No schema migration is needed.

Freely chosen web-service and AI endpoints cannot access the operator's own networks (its campus, its hospital), which an installation names in `instance_overlay/protected_nets.txt` or `PROTECTED_NETS`; no other setting lifts them. `WEBSERVICE_BLOCKED_NETS` adds further blocked networks. IPv4 embedded in IPv6 and every DNS answer are checked before connecting to a pinned address. Fixed AI providers and explicitly configured exact `AI_OWN_URL_HOSTS` remain privileged exceptions. Operators should also enforce this policy at the network boundary, with only necessary service exceptions. RWTH ownership was verified against the [RIPE organisation allocations](https://rest.db.ripe.net/search.json?query-string=ORG-RAU1-RIPE&inverse-attribute=org&type-filter=inetnum&type-filter=inet6num&flags=no-referenced); the hospital network uses ORG-UKA5-RIPE and is routed by AS47610. The IPv6 allocation was also verified against [RIPE](https://rest.db.ripe.net/ripe/inet6num/2a00%3A8a60%3A%3A%2F29.json) and [RWTH’s DNS documentation](https://help.itc.rwth-aachen.de/en/service/rhjc72ly4wmw/article/2ba452a647764d9aa91c80a5d18f7772/) on 14 October 2026.

Optional local interview transcription with speaker labels: [WhisperX container](deploy/docker/whisper/README.md). Disabled by default; prepare models and validate the isolated service before enabling it.

Storage accounting: `STORAGE_USAGE_ENABLED=1` uses a shared durable file inventory to avoid repeated quota tree walks. It defaults to off for a safe rolling introduction: deploy the accounting code to every writer first, then enable it and recreate the services sequentially. Existing file paths/sizes and transient reservations are kept in `instance/.storage-usage`; no answers or file contents are copied. Crashes rebuild from disk, unknown writers retain physical counting, and housekeeping periodically reconciles the inventory. See [operation instructions](deploy/docker/BETRIEB.md).

## How to cite

If you use Askurious in your research, please cite the software and name the release you used. [CITATION.cff](CITATION.cff) holds the details; GitHub turns it into APA and BibTeX under "Cite this repository". In APA style:

Brauner, P., & Philipsen, R. (2026). *Askurious* (Version 2026.10.0) [Computer software]. RWTH Aachen University. https://doi.org/10.5281/zenodo.23612929

Every release is archived on Zenodo. [10.5281/zenodo.23612928](https://doi.org/10.5281/zenodo.23612928) stands for all versions and leads to the latest; each release has a DOI of its own, shown on its Zenodo page.

Releases are named by year and month (`2026.10.0`, see [docs/releases.md](docs/releases.md)); each one lists its changes on the [releases page](https://github.com/askurious/askurious/releases).

## Licence

Copyright (c) 2026 RWTH Aachen University. Authors: Philipp Brauner, Ralf Philipsen.
RWTH Aachen University holds the rights in the code of Askurious (§ 69b UrhG) and licenses it under the European Union Public Licence 1.2 ([LICENSE](LICENSE), SPDX `EUPL-1.2`; the official German text in [LICENSE.de.txt](LICENSE.de.txt); copyright and the parts under other licences in [NOTICE](NOTICE)). The standalone checker `app/seal_verify.py` stays under the MIT License. Libraries, fonts, map outlines, ORCID icons and data files of others keep their own licences, and the names and logos of Askurious and POLLtergeist are not licensed under the EUPL; [NOTICE](NOTICE) lists these paths exactly. The running service links the licence texts, NOTICE and the source code on its page `/sources`. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md); security issues: [SECURITY.md](SECURITY.md).

Researchers accept the terms of use in [licence.md](licence.md) when they first sign in and after every new version; the app shows them under "Nutzungsbedingungen". They are the terms of the platform, not a licence for this code.
Section 7 there names what binds researchers when they use contents of others (constructs, standard questions, maps and other data). Among them are the demographic standard questions of the Rat für Sozial- und Wirtschaftsdaten (RatSWD, 2023) and the sociodemographic standard variables of KonsortSWD (Schneider et al., 2026) that the export can append as `x_…` columns, both under CC BY 4.0.
The wording of questionnaire scales of others is not part of this repository; installations provide it themselves ([instance_overlay/README.md](instance_overlay/README.md)).
The page `/sources` ("Lizenzen und Datenquellen", `app/templates/public/sources.html`) lists the data, texts, fonts, icons and libraries of others that Askurious contains, with their licences, for everyone, participants included, and describes how the derived data files (ODbL) were made, with a copy on request; `app/data/README.md` describes the data files with source and licence; `app/data/THIRD_PARTY_LICENSES.txt` lists every locked Python package with its licence and carries the full licence texts of the icons (`tools/third_party_licences.py`).
