"""The texts of an element per language: what is stored, what is a standard wording, where a shown text comes from.

Stored (item.texts, {lang: {...}}) is only what someone wrote or changed. The standard wordings of an element type
(kind.default_texts(lang): the buttons and the sentence of the consent, the federal states, "Keine davon", the dual
response question, "Option", ...) are taken in the language of the page when the element is shown, so a language
switched on later and every improvement of the catalog reach the element without a copy (UI/UX audit 2026-10-07, S9).

Surveys that started before keep the copies they had (participants saw them; the migration 0017 only cleans drafts):
such a copy in the default language that is still the standard wording counts as unchanged, and another language
without a text of its own shows its own standard wording instead (W44).

resolve() also says per text which language it is written in: a text that falls back to the default language gets
its own lang and dir on the page, and the preview can mark it as not translated yet (W45).
"""
import copy

EMPTY = ("", None, [], {})


def empty(value):
    return value is None or (isinstance(value, (str, list, dict)) and not value)


_DEFAULTS = {}


def defaults(kind, lang):
    """kind.default_texts(lang), computed once per element type and language (do not change the result)."""
    if kind is None:
        return {}
    key = (kind.key, lang)
    if key not in _DEFAULTS:
        _DEFAULTS[key] = kind.default_texts(lang) or {}
    return _DEFAULTS[key]


def former(kind, lang):
    return (kind.former_default_texts(lang) or {}) if kind is not None else {}


def _keys(*dicts):
    """The keys of all dicts, each once, in the order they first appear. A dict, not a list, keeps them: with a list
    the check for doubles grew with the square of the options (10 000 entries of a drill-down: 0.5 s a call; N-1 of
    the audit 2026-10-09)."""
    return list(dict.fromkeys(k for d in dicts for k in d))


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _own(stored, std, lang):
    """The texts of the default language: what is stored (also a text emptied on purpose), else the standard."""
    out, origin = {}, {}
    for k in _keys(stored, std):
        s, d = stored.get(k), std.get(k)
        if k in stored and isinstance(s, dict) and isinstance(d, dict):
            out[k], origin[k] = _own(s, d, lang)
        elif k in stored:
            out[k], origin[k] = s, lang
        else:
            out[k], origin[k] = copy.deepcopy(d), lang
    return out, origin


def _merge(own, src, std_lang, std_src, old_src, lang, default):
    """The texts of another language: its own text; else the text of the default language unless that is still a
    standard wording (then the standard wording of this language); else the standard wording of this language."""
    out, origin = {}, {}
    for k in _keys(src, own, std_lang):
        o, s, t, d, f = own.get(k), src.get(k), std_lang.get(k), std_src.get(k), old_src.get(k)
        if isinstance(o, dict) or isinstance(s, dict) or isinstance(t, dict):
            out[k], origin[k] = _merge(_as_dict(o), _as_dict(s), _as_dict(t), _as_dict(d), _as_dict(f), lang, default)
        elif not empty(o):
            out[k], origin[k] = o, lang
        elif k in src:
            if not empty(s) and (s == d or (f is not None and s == f)) and not empty(t):
                out[k], origin[k] = copy.deepcopy(t), lang
            else:
                out[k], origin[k] = s, (default if not empty(s) else lang)
        else:
            out[k], origin[k] = copy.deepcopy(t), lang
    return out, origin


def resolve(kind, stored, default, lang=None):
    """(texts, origin) of an element in `lang`: the texts as shown, and per text the language it is written in
    (the same nesting; a list counts as one text)."""
    stored = stored or {}
    lang = lang or default
    src = _as_dict(stored.get(default))
    if lang == default:
        return _own(src, defaults(kind, default), lang)
    return _merge(_as_dict(stored.get(lang)), src, defaults(kind, lang), defaults(kind, default),
                  former(kind, default), lang, default)


def only(texts, origin, lang):
    """The texts written in `lang` (own texts and standard wordings of the language), without those falling back
    to the default language: what the editor of that language shows in its fields."""
    out = {}
    for k, v in texts.items():
        o = origin.get(k)
        if isinstance(o, dict):
            out[k] = only(_as_dict(v), o, lang)
        elif o == lang:
            out[k] = v
    return out


def origin_of(origin, path, lang):
    """The language of the text at `path` (keys) in an origin tree; `lang` where nothing is known."""
    node = origin
    for part in path:
        if not isinstance(node, dict):
            break
        node = node.get(str(part)) if str(part) in node else node.get(part)
    if isinstance(node, dict):  # a group of texts: the language of the first one written in another language
        found = [x for x in _leaves(node) if x != lang]
        return found[0] if found else lang
    return node or lang


def _leaves(origin):
    for v in origin.values():
        if isinstance(v, dict):
            yield from _leaves(v)
        else:
            yield v


def _strip(texts, std, old):
    out = {}
    for k, v in texts.items():
        d, f = std.get(k), old.get(k)
        if isinstance(v, dict) and isinstance(d, dict):
            sub = _strip(v, d, _as_dict(f))
            if sub:
                out[k] = sub
        elif v == d or (f is not None and not empty(v) and v == f):
            continue
        else:
            out[k] = v
    return out


def _strip_other(texts, std, old, src):
    out = {}
    for k, v in texts.items():
        d, f = std.get(k), old.get(k)
        if isinstance(v, dict) and isinstance(d, dict):
            sub = _strip_other(v, d, _as_dict(f), _as_dict(src.get(k)))
            if sub:
                out[k] = sub
        elif k not in src and (v == d or (f is not None and not empty(v) and v == f)):
            continue  # the standard wording of this language; it stays when the default language has its own text
        else:
            out[k] = v
    return out


def strip(kind, texts, default, earlier=True):
    """`texts` without the standard wordings: what an element stores (a copy in the default language that is
    still a standard wording; in another language only where the default language has no text of its own, since
    there the standard wording is a translation that differs from the fallback). earlier: also the standard
    wordings of earlier versions ("Fortsetzen"): copies made back then (migration 0017, survey files); off after a
    change in the editor, where such a word is typed on purpose."""
    if kind is None or not isinstance(texts, dict):
        return texts
    out = {}
    src = texts.get(default)
    if isinstance(src, dict):
        out[default] = _strip(src, defaults(kind, default), former(kind, default) if earlier else {})
    for lang, t in texts.items():
        if lang == default:
            continue
        out[lang] = (_strip_other(t, defaults(kind, lang), former(kind, lang) if earlier else {}, out.get(default) or {})
                     if isinstance(t, dict) else t)
    return {lang: out[lang] for lang in texts}


def strippable(item):
    """Whether an element stores only what was changed: in a draft, and not for a construct or linked element,
    whose wording is a validated or frozen version kept as it is (app/constructs.py, app/linked.py)."""
    survey = item.block.survey if item.block is not None else None
    return (survey is not None and survey.status == "draft" and not (item.config or {}).get("construct"))


def tidy(item):
    """Drop the stored copies of standard wordings of an element in a draft (after a change in the editor)."""
    if strippable(item):
        cleaned = strip(item.kind, item.texts or {}, item.block.survey.default_language, earlier=False)
        if cleaned != (item.texts or {}):
            item.texts = cleaned


def new(kind, langs, default, extra=None):
    """The texts of a new element: nothing for the standard wordings, `extra` ({lang: {...}}) as given."""
    texts = {lang: {} for lang in langs}
    for lang, t in (extra or {}).items():
        texts[lang] = dict(t)
    return strip(kind, texts, default)
