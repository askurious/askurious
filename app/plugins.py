"""Add-on modules: separate Python packages that extend Askurious when they are installed (Ralf, 2026-10-22).

A module names itself in its package metadata under the entry point group "askurious.plugins"; the entry point is
a module with init_app(app). There it registers its blueprint and may

- add an experimental feature with add_feature(): it is then off for everyone until an admin switches it on
  (Administration > Experimentell), like every other feature of app/experimental.py;
- put a button on "Meine Befragungen" with add_button() or an entry under "Werkzeuge" in the menu "Hilfe und
  Werkzeuge" with add_tool() (each shown only with its feature);
- keep surveys it made for itself out of the lists with hide_surveys() (a predicate on the Survey);
- show such a survey with fewer tabs and its own way back with add_view() (surveys/_tabs.html);
- keep such surveys and their participations out of the platform's statistics with exclude_from_statistics()
  (Administration > Befragungen, the organisations' list, the monitoring feed).

Without an installed module nothing changes. A module that fails to load is left out and logged; Askurious starts
anyway.
"""
from importlib.metadata import entry_points

GROUP = "askurious.plugins"
BUTTONS = []  # [{"feature", "endpoint", "de", "en", "title_de", "title_en"}]
TOOLS = []  # [{"feature", "endpoint", "de", "en", "icon"}]
HIDDEN = []  # [predicate(survey) -> bool]
STATS_EXCLUDED = []  # [condition(Survey) -> SQL expression, true for the surveys a module keeps for itself]
VIEWS = []  # [(predicate(survey) -> bool, {"tabs", "back", "back_de", "back_en", "badge_de", "badge_en"})]
LOADED = []  # names of the modules loaded


def add_feature(key, label_de, text_de, label_en, text_en):
    """One more experimental feature (app/experimental.py), last in the list; a key that is there already is left."""
    from app import experimental

    if key in experimental.KEYS:
        return
    experimental.FEATURES.append((key, label_de, text_de))
    experimental.KEYS.append(key)
    experimental.FEATURES_EN[key] = (label_en, text_en)


def add_button(feature, endpoint, de, en, title_de="", title_en=""):
    BUTTONS.append({"feature": feature, "endpoint": endpoint, "de": de, "en": en, "title_de": title_de,
                    "title_en": title_en})


def add_tool(feature, endpoint, de, en, icon="grid"):
    TOOLS.append({"feature": feature, "endpoint": endpoint, "de": de, "en": en, "icon": icon})


def hide_surveys(predicate):
    HIDDEN.append(predicate)


def add_view(predicate, tabs, back, back_de, back_en, badge_de="", badge_en=""):
    """Surveys for which `predicate` is true show only the tab keys `tabs` (e.g. ("results", "data")), lead back to
    the endpoint `back` instead of "Meine Befragungen", and have no preview or next-step button."""
    VIEWS.append((predicate, {"tabs": tuple(tabs), "back": back, "back_de": back_de, "back_en": back_en,
                              "badge_de": badge_de, "badge_en": badge_en}))


def exclude_from_statistics(condition):
    """condition(Survey) gives an SQL expression that is true for the surveys of the module (e.g. a marker in
    Survey.data_pool); they and their participations stay out of the platform's statistics."""
    STATS_EXCLUDED.append(condition)


def counted_surveys():
    """Conditions on Survey for the platform's statistics: () without such a module."""
    from app.extensions import db
    from app.models import Survey

    if not STATS_EXCLUDED:
        return ()
    # coalesce: a condition on a column that is NULL for most surveys (data_pool) gives NULL, and NOT NULL would
    # drop exactly those surveys from the count (deployed 2026-10-22: 24 of 58 surveys counted)
    return (db.not_(db.func.coalesce(db.or_(*[condition(Survey) for condition in STATS_EXCLUDED]), False)),)


def counted_responses():
    """Conditions on Response for the platform's statistics: () without such a module."""
    from app.extensions import db
    from app.models import Response, Survey

    if not STATS_EXCLUDED:
        return ()
    return (Response.survey_id.not_in(db.select(Survey.id).where(db.or_(*[c(Survey) for c in STATS_EXCLUDED]))),)


def view(survey):
    """The view a module gives this survey (add_view), or None."""
    return next((spec for predicate, spec in VIEWS if _safe(predicate, survey)), None)


def hidden(survey):
    """True if an add-on module keeps this survey out of the lists."""
    return any(_safe(p, survey) for p in HIDDEN)


def _safe(predicate, survey):
    try:
        return bool(predicate(survey))
    except Exception:  # a broken module must not hide or break the list
        return False


def buttons():
    """The buttons of the modules whose feature is on for the person signed in."""
    from app import experimental

    return [b for b in BUTTONS if not b["feature"] or experimental.enabled(b["feature"])]


def tools():
    """The menu entries of the modules whose feature is on for the person signed in."""
    from app import experimental

    return [t for t in TOOLS if not t["feature"] or experimental.enabled(t["feature"])]


def load(app):
    """Load every installed module (called once by create_app)."""
    for ep in entry_points(group=GROUP):
        if ep.name in LOADED:
            continue
        try:
            ep.load().init_app(app)
        except Exception:
            app.logger.exception("add-on module %s not loaded", ep.name)
            continue
        LOADED.append(ep.name)
    app.jinja_env.globals["plugin_buttons"] = buttons
    app.jinja_env.globals["plugin_view"] = view
    app.jinja_env.globals["plugin_tools"] = tools
