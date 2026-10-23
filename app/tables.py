"""Long lists (the admin pages): sorting by a column, a search field and pages, all done by the database.

A page reads only its own rows (LIMIT/OFFSET). What the address may ask for is checked here: the sort column
must be one of the listing's own, the direction "asc" or "desc"; the search words reach the query as bound
values. Several listings on one page keep apart by their prefix (`f_sort`, `f_dir`, `f_q`, `f_page`)."""
import re
import unicodedata
from urllib.parse import parse_qsl

from flask import request, url_for

from app.extensions import db

PER_PAGE = 25
ROW_SIZES = (10, 20, 50)  # lists of people and groups: 10 rows, switchable to 20 or 50 (Ralf 2026-10-16)
MAX_WORDS = 6
# kinds: Personen, app/kind_access.py; period and since: the period of the dashboard on "Befragungen" (app/admin_stats.py)
_STATE_KEY = re.compile(r"^(?:[a-z]{1,3}_(sort|dir|q|page|kinds|n|org|group|type|end)|period|since)\Z")


def text_key(value):
    """How texts are compared: without case and accents ("Özdemir" next to "Oswald", found by "ozdemir").
    SQLite's own lower() and LIKE know ASCII only; registered there as q_key (extensions.sqlite_settings)."""
    if not isinstance(value, str):
        return value
    folded = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def _fold_table():
    """PostgreSQL: every Latin letter (Latin-1, Extended-A/B, Extended Additional) that text_key turns into one
    other letter, upper case included, so translate() folds as text_key does, also where lower() leaves letters
    outside ASCII as they are (a database with the C collation). Without the extension unaccent, which needs
    rights on the database server that a hosting package does not always give. Was a hand-written list that
    missed ğ, ș and others: "dogan" found "Doğan" only on SQLite (code review 2026-10-03, PG-8)."""
    accented, plain = [], []
    for code in [*range(0xC0, 0x250), *range(0x1E00, 0x1F00)]:
        letter = chr(code)
        folded = text_key(letter) if letter.isalpha() else letter
        if len(folded) == 1 and folded != letter:
            accented.append(letter)
            plain.append(folded)
    return "".join(accented), "".join(plain)


_ACCENTED, _PLAIN = _fold_table()


def key(expr):
    """The SQL side of text_key: in SQLite the function itself (extensions.sqlite_settings), in PostgreSQL the
    same rules in SQL (lower case, accents and ß as text_key does, blanks collapsed)."""
    if db.engine.dialect.name == "sqlite":
        return db.func.q_key(expr)
    if db.engine.dialect.name == "postgresql":
        folded = db.func.replace(db.func.translate(db.func.lower(expr), _ACCENTED, _PLAIN), "ß", "ss")
        return db.func.btrim(db.func.regexp_replace(folded, r"\s+", " ", "g"))
    return db.func.lower(expr)


def by_text(expr):
    """Sort key of a text: empty ones last, in both directions."""
    return db.func.nullif(key(expr), "")


class Listing:
    """One list of a page. `columns`: {key: expressions to order by, or (expressions, "desc") for columns that
    start with the largest value}; `search`: text expressions, or functions that get `match` and return a
    condition (for texts in other tables); `then`: the order within equal values."""

    def __init__(self, prefix, columns, default, search=(), then=(), anchor="", per_page=PER_PAGE, sizes=None):
        self.prefix, self.anchor, self.per_page, self.sizes = prefix, anchor, per_page, sizes
        if sizes:  # the rows per page the person chose (_listing.html, pager), else the first size
            wanted = request.args.get(f"{prefix}_n", type=int)
            self.per_page = wanted if wanted in sizes else sizes[0]
        self.columns, self.first_dir = {}, {}
        for name, spec in columns.items():
            exprs, first = spec if isinstance(spec, tuple) and spec[-1] in ("asc", "desc") else (spec, "asc")
            self.columns[name] = list(exprs) if isinstance(exprs, (list, tuple)) else [exprs]
            self.first_dir[name] = first
        self.search, self.then = list(search), list(then)
        sort = request.args.get(f"{prefix}_sort", "")
        self.sort = sort if sort in self.columns else default
        direction = request.args.get(f"{prefix}_dir", "")
        self.dir = direction if direction in ("asc", "desc") else self.first_dir[self.sort]
        self.q = " ".join(request.args.get(f"{prefix}_q", "").split())[:100]
        self.page = max(request.args.get(f"{prefix}_page", 1, type=int) or 1, 1)
        self.total, self.pages = 0, 1

    def rows(self, query):
        """The rows of the current page of this select, filtered and ordered."""
        for word in self.q.split()[:MAX_WORDS]:
            needle = text_key(word)

            def match(expr, needle=needle):
                return key(expr).contains(needle, autoescape=True)

            query = query.where(db.or_(*[s(match) if callable(s) else match(s) for s in self.search]))
        self.total = db.session.scalar(db.select(db.func.count()).select_from(query.order_by(None).subquery())) or 0
        self.pages = max(-(-self.total // self.per_page), 1)
        self.page = min(self.page, self.pages)
        order = [(e.desc() if self.dir == "desc" else e.asc()).nulls_last() for e in self.columns[self.sort]]
        return db.session.execute(query.order_by(*order, *self.then).limit(self.per_page)
                                  .offset((self.page - 1) * self.per_page)).all()

    # --- for the templates (_listing.html) ---

    @property
    def first(self):
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last(self):
        return min(self.page * self.per_page, self.total)

    def url(self, **changes):
        """This page with a changed state of this listing; the other listings of the page keep theirs."""
        # only the state of listings, as back_args: the address may carry anything, and names like _anchor or
        # _external mean something to url_for
        args = {k: v for k, v in request.args.items() if _STATE_KEY.match(k)}
        for name, value in changes.items():
            args[f"{self.prefix}_{name}"] = value
        if str(args.get(f"{self.prefix}_page")) == "1":
            args.pop(f"{self.prefix}_page")
        args = {k: v for k, v in args.items() if v not in ("", None)}
        return url_for(request.endpoint, **(request.view_args or {}), **args, _anchor=self.anchor or None)

    def sort_url(self, name):
        """A click on a column head: its first direction, or the other one when it is the current column."""
        direction = ("asc" if self.dir == "desc" else "desc") if name == self.sort else self.first_dir[name]
        return self.url(sort=name, dir=direction, page=None)

    def kept(self):
        """Everything else in the address, as hidden fields of the search form (a GET form drops the query
        of its action)."""
        own = (f"{self.prefix}_q", f"{self.prefix}_page")
        return [(k, v) for k, v in request.args.items() if k not in own]

    def page_numbers(self):
        """1 … 4 5 [6] 7 8 … 20; None stands for a gap."""
        wanted = sorted({1, self.pages, *range(max(self.page - 2, 1), min(self.page + 2, self.pages) + 1)})
        out = []
        for n in wanted:
            if out and n - out[-1] == 2:
                out.append(n - 1)
            elif out and n - out[-1] > 2:
                out.append(None)
            out.append(n)
        return out


def back_args(value):
    """The state of the listings a form sent along (`back`, the query of the page it came from), for the
    redirect after a POST: only the keys of listings, nothing else."""
    return {k: v[:100] for k, v in parse_qsl(value or "", keep_blank_values=False) if _STATE_KEY.match(k)}


class ListPage(Listing):
    """A list already in memory, cut into pages like a Listing (sizes, pager, url), without sorting or search."""

    def __init__(self, prefix, items, anchor="", sizes=ROW_SIZES):
        super().__init__(prefix, {"none": [db.literal(0)]}, "none", anchor=anchor, sizes=sizes)
        self.total = len(items)
        self.pages = max(-(-self.total // self.per_page), 1)
        self.page = min(self.page, self.pages)
        self.items = items[(self.page - 1) * self.per_page:self.page * self.per_page]
