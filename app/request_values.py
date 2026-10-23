"""Form, query and JSON values without control characters (code review 2026-10-03, PG-1/PG-5).

PostgreSQL refuses NUL in text columns and cannot cast a json value holding "\\u0000" to jsonb; a single
participant sending one (curl is enough) gave 500s on the own page and, stored in an answer, in the editor of
that survey. Browsers never send such characters from a form field, so the app removes them centrally from
every value it reads: NUL and the other control characters except tab, line feed and carriage return
(a textarea sends its line breaks as CRLF).

Lone surrogates (U+D800 to U+DFFF) go too: JSON can write them ("\\ud800"), a form field cannot, and no UTF-8 file
can hold them. One stored in an answer made every data export of the survey fail with a 500 (audit 2026-10-13,
TN-20/TN-21); exports clean answers stored before with drop_surrogates."""
import re

from flask import Request, json
from werkzeug.datastructures import ImmutableMultiDict

_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_text(value):
    """value without the control characters and lone surrogates above; anything that is not a string is returned
    as it is."""
    if isinstance(value, str) and _CONTROL.search(value):
        return _CONTROL.sub("", value)
    return value


def drop_surrogates(value):
    """A text that can be written as UTF-8: lone surrogates of an answer stored before they were refused become
    U+FFFD (audit 2026-10-13, TN-20). Anything that is not a string is returned as it is."""
    if isinstance(value, str) and _SURROGATE.search(value):
        return _SURROGATE.sub("�", value)
    return value


MAX_DEPTH = 64  # nesting of lists and objects in a JSON value the app reads; far above any form or body it sends


def clean_deep(value, depth=0):
    """clean_text for every string (keys too) in a decoded JSON value. A value nested deeper than MAX_DEPTH raises
    ValueError: a 400 like any other bad JSON, never a RecursionError further on (audit 2026-10-11, N9)."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (list, dict)) and depth >= MAX_DEPTH:
        raise ValueError("JSON nested too deeply")
    if isinstance(value, list):
        return [clean_deep(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {clean_text(k): clean_deep(v, depth + 1) for k, v in value.items()}
    return value


def safe_loads(text, **kwargs):
    """json.loads for a text from a form field or a body: ValueError for anything that is no plain JSON, also for a
    value nested so deeply that the reader runs out of recursion (it raised RecursionError: a 500; audit 2026-10-11,
    N9), with NaN and Infinity refused and control characters removed as for request.get_json."""
    import json as plain

    kwargs.setdefault("parse_constant", _no_constant)
    try:
        value = plain.loads(text, **kwargs)
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None
    return clean_deep(value)


class CleanValues(ImmutableMultiDict):
    """request.args and request.form with cleaned keys and values (uploaded files are left alone)."""

    def __init__(self, mapping=None):
        super().__init__(mapping)
        lists = [(clean_text(k), [clean_text(v) for v in vs]) for k, vs in dict.items(self)]
        if any(k != old or vs != old_vs for (k, vs), (old, old_vs) in zip(lists, dict.items(self))):
            dict.clear(self)
            for key, values in lists:
                dict.setdefault(self, key, []).extend(values)


def _no_constant(name):
    raise ValueError(f"{name} is not valid JSON")


class _CleanJSON:
    """json module for request.get_json: the app's own (Flask sets it on every request), with the decoded value
    cleaned."""

    def __init__(self, module):
        self.module = module

    def loads(self, s, **kwargs):
        # NaN, Infinity and -Infinity are no JSON; Python's reader takes them, and as numbers they would pass every
        # isinstance(v, (int, float)) check and fail later in int() or a comparison (a 500 instead of a 400)
        kwargs.setdefault("parse_constant", _no_constant)
        try:
            value = self.module.loads(s, **kwargs)
        except RecursionError:  # nested deeper than the reader can go: bad JSON, not a 500 (audit 2026-10-11, N9)
            raise ValueError("JSON nested too deeply") from None
        return clean_deep(value)

    def dumps(self, obj, **kwargs):
        return self.module.dumps(obj, **kwargs)


class CleanRequest(Request):
    parameter_storage_class = CleanValues

    @property
    def json_module(self):
        return _CleanJSON(self.__dict__.get("_json_module", json))

    @json_module.setter
    def json_module(self, module):
        self.__dict__["_json_module"] = module
