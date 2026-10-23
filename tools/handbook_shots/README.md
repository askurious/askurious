# Handbook screenshots

Takes the pictures of the handbook (`/docs/`) anew, once with the German and once with the English researcher UI:

    python tools/handbook_shots/run.py

They go to `app/static/handbook/de/<name>.jpg` and `app/static/handbook/en/<name>.jpg` (the names the handbook uses; a
picture without a language version comes from `app/static/handbook/`, today only `cj-ai-images.jpg`). Takes a few minutes per language.

Options: `--lang de` or `--lang en` for one language, `--only editor,quotas` for some pictures (the names in
`SHOTS` in `shots.mjs`), `--work <folder>` for the throwaway databases (default: a folder in the system's temp
folder).

## What it does

For each language:

1. `seed.py` builds a fresh database in the work folder, never `instance/app.db`, through the app itself: the
   researcher "Maria Muster" (made-up name and ORCID iD) with the researcher UI in that language, five of the six study
   templates with their dummy data, a demo survey "Lernen mit KI im Studium" / "Learning with AI at university"
   (own scales, the validated scale FITAI, a research plan, 120 dummy participations, an English or German
   translation), an interview guide, a live poll with 26 answers and a small survey with crowd feedback.
2. `serve.py` runs the app on http://localhost:5070 with `DEV_LOGIN` (local only).
3. `shots.mjs` starts headless Microsoft Edge with its DevTools port and takes each picture: 1600 × 1029 CSS pixels,
   phone pictures 390 × 844 at 1.5x.
4. The server and Edge are stopped.

Nothing is installed: Python with the app's requirements, Node 22 or later (built-in WebSocket and fetch) and
Microsoft Edge are enough. `EDGE_PATH` points to another Edge or Chrome, `SHOTS_PORT` sets another port.

## Notes

- No AI service is called. Where a picture shows AI results (translations "AI, unchecked"), `seed.py` stores them
  through the app's own translation code with fixed demo texts standing in for the model's answer; AI dialogs are
  shown filled in but not sent.
- The study templates have German questions only, so their participant pages (conjoint preview, flows) are German
  in the English run too. The demo survey, interview guide, live poll and crowd survey are in the run's language.
- The "Technikakzeptanz (TAM)" template is not used: its scales need a documented permission of the operator.
- `cj-ai-images` (conjoint tasks with AI-generated product pictures) is not taken: it would need image generation.
  The old picture in `app/static/handbook/` stays.
- If a page changes, adapt its entry in `SHOTS` in `shots.mjs` (selectors, texts to click, scrolling).

## Pictures of the page "Anleitungen"

`guides_seed.py` (called by `seed.py`) adds what the `guide-*` pictures need: a panel connection on the panel
template (PID stored once, provider parameter `source` = a/b, completion links and a rule on the made-up host
`panel.example`), set running, its dummy participations turned into made-up "real" ones from the two providers, a
read-only quota link and a field-report dashboard; a seminar copy of the customer-satisfaction template with own links
for made-up people and a dashboard link each; three waves with the element "Persönlicher Code", linked under "Wellen"
and computed at the end of the seed. Take only these with
`--only guide-panel-params,guide-panel-rules,…` (the names in `SHOTS`).
