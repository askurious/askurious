"""Numbers that name rows: ids in addresses (<int:...>) and in forms. Python reads any number of digits; the database
does not (SQLite: OverflowError above 2**63-1, PostgreSQL: DataError above 2**31-1 for an integer column). A larger
number names no row, so it is answered like one that does not exist: 404 for an address, the form's default for a
field, instead of a 500 with a stack trace in the log (audit 2026-10-10, C2)."""
from werkzeug.routing import IntegerConverter

ID_MAX = 2**31 - 1  # the largest value of an integer column in PostgreSQL


class IdConverter(IntegerConverter):
    """<int:...> of the whole app: as Werkzeug's, but at most ID_MAX unless the route says otherwise."""

    def __init__(self, map, fixed_digits=0, min=None, max=ID_MAX, signed=False):
        super().__init__(map, fixed_digits=fixed_digits, min=min, max=max, signed=signed)


def db_id(value):
    """For request.form.get(..., type=db_id): a whole number from 0 to ID_MAX; anything else raises ValueError, which
    the form reads as missing."""
    number = int(value)
    if not 0 <= number <= ID_MAX:
        raise ValueError("not an id")
    return number
