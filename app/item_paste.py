"""Statements of a Likert matrix pasted as a list (user test 2026-10-27, point 7: "In 99 % der Fälle habe ich meine
Items in der Zwischenablage").

One statement per line, as in Qualtrics. What comes out of a PDF is cleaned on the way:
- a leading number ("01", "3.", "4)", "a)") is cut off and starts a statement; lines before the first number
  are a heading and left out;
- with numbers in the list, a line without one continues the statement above (a PDF breaks long items);
  without numbers, a line continues the one above when that has no sentence end and this one starts in lower case;
  a word broken with a hyphen at the end of a line is joined ("Sys-" + "temen");
- an empty line always ends a statement;
- "(R)", "(r)", "(-)" or "(umgepolt)" at the end marks a reversed statement;
- "Variable<Tab>Text" (from a spreadsheet) or "Variable;Text" gives the variable name, if it can be one.

The same rules run in the browser for the preview "n Aussagen erkannt" (app.js, likertPaste)."""
import re

MAX_STATEMENTS = 100
NUMBER = re.compile(r"^\(?(\d{1,3}|[a-zA-Z])[.):]\s+|^(\d{1,3})\s+(?=\S)")
REVERSED = re.compile(r"\s*\((?:R|r|-|−|umgepolt|reversed|rev\.?)\)\s*$")
CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
SENTENCE_END = re.compile(r"[.!?…:\"“”»«)]$")


def _numbered(line):
    m = NUMBER.match(line)
    return (line[m.end():].strip(), True) if m else (line, False)


def parse(text):
    """[(code, text, reversed)] of the pasted list, at most MAX_STATEMENTS."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\t" if any("\t" in line for line in raw) else None
    rows = []  # (code, text) per line, None for an empty line
    for line in raw:
        line = line.strip()
        if not line:
            rows.append(None)
            continue
        code = ""
        cells = [c.strip() for c in (line.split(sep) if sep else [line]) if c.strip()]
        if len(cells) > 1 and CODE.match(cells[0]) and not cells[0].isdigit():
            code, line = cells[0], " ".join(cells[1:])
        elif len(cells) > 1:
            line = " ".join(cells)
        elif ";" in line:
            first, rest = line.split(";", 1)
            if CODE.match(first.strip()) and rest.strip():
                code, line = first.strip(), rest.strip()
        rows.append((code, " ".join(line.split())))
    has_numbers = sum(1 for r in rows if r and _numbered(r[1])[1]) >= 2
    out = []  # [code, text]
    open_ = False  # the statement above may go on
    started = not has_numbers  # with numbers, what comes before the first one is a heading
    for r in rows:
        if r is None:
            open_ = False
            continue
        code, line = r
        line, numbered = _numbered(line) if has_numbers else (line, False)
        started = started or numbered
        if not started:
            continue
        if not line:
            continue
        joins = open_ and out and not code and not numbered and (
            has_numbers or (not SENTENCE_END.search(out[-1][1]) and line[:1].islower()))
        if joins:
            prev = out[-1][1]
            if prev.endswith("-"):  # "Sys-" + "temen", "E-Mail-" + "Adresse"
                out[-1][1] = (prev[:-1] if line[:1].islower() else prev) + line
            else:
                out[-1][1] = prev + " " + line
        else:
            out.append([code, line])
        open_ = True
    result = []
    for code, line in out[:MAX_STATEMENTS]:
        reverse = bool(REVERSED.search(line))
        line = REVERSED.sub("", line).strip()
        if line:
            result.append((code, line, reverse))
    return result
