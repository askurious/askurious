# Contributing to Askurious

Thank you for your interest. Askurious is developed by Philipp Brauner and Ralf Philipsen at the Chair of
Communication Science, RWTH Aachen University. RWTH Aachen University holds the rights in the code and licenses it
under the European Union Public Licence 1.2 ([LICENSE](LICENSE); copyright, authors and the parts under other licences: [NOTICE](NOTICE)). Copyright (c) 2026 RWTH Aachen University.
Authors: Philipp Brauner, Ralf Philipsen.

## Before you open a pull request

Until further notice we can only take contributions whose rights allow us to keep licensing Askurious as a whole,
including under a later version of the EUPL or a licence compatible with it. Therefore:

- Open an issue first and describe what you would like to change. We answer there whether we can take it.
- With a pull request you confirm that you wrote the contribution yourself (or may contribute it), and you license
  your contribution to RWTH Aachen University under the EUPL-1.2, with a non-exclusive, worldwide, royalty-free,
  perpetual right for RWTH Aachen University to use and change it and to license it, as part of Askurious, also
  under a later version of the EUPL or under a licence compatible with it (Article 5 of the EUPL).
  *(Draft: the exact wording of this grant is to be reviewed by the legal office of RWTH Aachen University before
  the first outside contribution is merged.)*
- Sign off every commit (`git commit -s`). The line `Signed-off-by: Your Name <your@email>` certifies the
  [Developer Certificate of Origin 1.1](https://developercertificate.org/): that you have the right to submit the
  contribution under the licence named above. Pull requests with commits that are not signed off cannot be merged.
- Do not include code, texts, images or data of others unless their licence allows it; name them and their licence
  in the pull request.

## How we work

- Every text exists in German and English (researchers) and in every survey language (participants); see
  [CLAUDE.md](CLAUDE.md), section "Every text in every language".
- A change that can affect a legal text (privacy, terms of use, imprint) says so in its pull request.
- Run the tests of the parts you touched (`python -m pytest tests/<area>`); the whole suite runs before a release.
- No concepts, audit reports or other planning documents in the repository.

## Security issues

Please do not report security issues in public issues; see [SECURITY.md](SECURITY.md).
