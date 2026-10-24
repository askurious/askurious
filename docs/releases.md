# Releases

## Version numbers

- **The platform** (this repository) is released by calendar: `YYYY.MM.N`, for example `2026.10.0`. `YYYY.MM` is
  the month of the release, `N` counts the releases of that month from 0 (`2026.10.0`, `2026.10.1`, then
  `2026.11.0`). The git tag is the version itself, without a `v`. The platform has no version number in the code:
  the tag names a commit, and the server knows its commit (`app/deployed_at.txt`).
- **The diary app and add-on modules** keep semantic versions (`MAJOR.MINOR.PATCH`) in their own repositories: they
  have interfaces that others build against. A platform release names the versions it was tested with when they
  changed.

## When to tag

Only a commit that

1. is on `main`,
2. ran green with the whole test suite (`pytest tests --full` on that tree; `python tools/green_run.py` says so on
   the machine that ran it), and
3. runs on the RWTH server: the second line of `~/askurious/app/deployed_at.txt` is the commit. A `skipped=`
   line there means the deploy did not run the tests itself; then point 2 has to be shown on its own.

```bash
ssh queryous-rwth 'cat ~/askurious/app/deployed_at.txt'
```

Releases are not on a schedule: tag when a deployed state is worth citing (a paper, a course, a larger change).

## Release notes

The GitHub release carries the notes; they come from `log.md`, the entries since the previous tag, in short:

- **Changes** for researchers and participants, a few lines per area, no internal names.
- **Database:** the migration level (the newest revision in `migrations/versions/`, for example `0099`) and whether
  the release brings migrations. An installation updates with `deploy/docker/deploy-docker.sh`, which runs them.
- **Terms of use:** the `Version:` line of `licence.md`. If it changed since the previous release, researchers
  accept the terms again at their next sign-in.
- **Diary app and modules:** their versions, when they changed.

## Steps

```bash
git fetch origin main --tags
git log --oneline 2026.10.0..<commit>     # what came since the previous release
gh release create 2026.11.0 --target <commit> --title "Askurious 2026.11.0" --notes-file notes.md --draft
```

Check the draft on GitHub, then publish it there; publishing creates the tag. `CITATION.cff` names no version, so it
needs no change for a release.
