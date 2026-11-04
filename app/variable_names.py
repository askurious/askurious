"""The one rule for variable names (Philipp 2026-11-04): the name of a question, a randomisation, a loop and anything else
that becomes a column of the data. A letter first, then letters, digits and _, at most 64 characters.

So a name means the same in every export: SPSS allows at most 64 bytes, and R, jamovi and CSV read such a name as it is.
Everything that checks a name takes it from here, the browser too (base.html hands it to app.js), so there are no copies
to drift apart. There is no list of forbidden words: SPSS renames what it does not accept (app/sav.py), and the codebook
says so. The handbook explains the rule (build.md, section 6)."""
import re

MAX_LENGTH = 64
# without anchors, for those who build a longer pattern from it; JavaScript reads the same text
VARIABLE_PATTERN = rf"[A-Za-z][A-Za-z0-9_]{{0,{MAX_LENGTH - 1}}}"
# \Z, not $: "$" also lets a newline at the end pass
CODE_RE = re.compile(rf"^{VARIABLE_PATTERN}\Z")
