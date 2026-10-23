# Files of one installation

The public code of Askurious carries no operator of its own. An installation puts its own files here, over the code,
without changing it:

- `templates/`: a template here takes the place of the template of the same name in `app/templates/`. Meant for the
  operator's paragraphs, which the code keeps neutral: `public/_operator_imprint.html` (and `_en`), `public/_owner.html`
  (controller and data protection officer), `public/_operator_legal_basis.html`, `public/_operator_authority.html`,
  `public/_operator_a11y.html`, `public/_operator_enforcement.html`, `participant/_operator.html`,
  `participant/_operator_a11y.html`, `participant/_operator_enforcement.html`, on the privacy page of every survey
  the controller, the data protection officer and the supervisory authority where the researchers entered none
  (`participant/_controller.html`, `participant/_dpo.html`, `participant/_authority.html`), and for section 1 of the terms
  `terms/_operator.md` and `terms/_operator_en.html`. The paths are the same as below `app/templates/`.
- `privacy_concept.json`: what the installation adds to the privacy concept (Administration > Datenschutz,
  app/privacy_concept.py, `overlay()`): per processing activity its name in the operator's catalogue and the legal
  basis the operator relies on, `{"activities": {"<key>": {"catalog": "...", "basis": "..."}}}`; a text is a string or
  `{"de": "...", "en": "..."}`, the keys are those of `ACTIVITY_META`.
- `protected_nets.txt`: the operator's own networks (its campus, its hospital), one per line, which web services in
  the flow and typed AI endpoints may never reach; `PROTECTED_NETS` in the `.env` adds further ones (config.py).
  The public code protects none of its own.

- `instruments.json`: the wording of the scales of others in the construct toolbox (GESIS/ZIS short scales, TAM,
  KUT, GDMS; `INSTRUMENTS` in app/constructs.py), which the public code does not carry because their licences do not
  allow passing them on: `{"instruments": {"<key>": {"question": {"<lang>": "..."}, "statements": [{"<lang>": "..."},
  ...]}}}`, one statement per item of the code's structure, in its order. Without it these constructs are left out of
  the toolbox; the study template "Technikakzeptanz" then goes without them. The installation checks before an export
  that none of these texts went into the code: `python tools/public_export.py --check --instruments instruments.json`.
  `INSTRUMENT_WORDINGS` in the `.env` can name another file.

Settings that are plain values (imprint name and address, donation links) stay in the `.env`.

Only this README is part of the code; everything else in this folder is ignored by git (`.gitignore`). The private
repository of an installation copies its files here before the Docker build (`deploy/docker/Dockerfile` copies the
folder into the image), or sets `INSTANCE_OVERLAY` to a folder of its own.
