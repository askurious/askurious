"""Pluggable item types.

Each item type is a module in this package that defines a subclass of ItemType and
registers it with @register. To add a type: create app/items/<key>.py, subclass
ItemType, and add two templates: items/<key>/editor.html and items/<key>/participant.html.

Storage model (see models.Item):
  item.config  language-independent settings (codes, number of points, flags, ...)
  item.texts   {lang: {...}} everything participants read, per language
Texts may contain {{variable}} placeholders, filled at render time (loop blocks, M5).
"""
import importlib
import pkgutil

REGISTRY = {}


def register(cls):
    REGISTRY[cls.key] = cls()
    return cls


def get(key):
    return REGISTRY[key]


_STABLE = {}


def stable_defaults(key):
    """The settings of default_config() that are the same on every call: what an item of this kind lacks is read
    as these (Item.cfg). Content made up anew for each new item (rows with fresh ids) is no default to read."""
    if key not in _STABLE:
        kind = REGISTRY.get(key)
        if kind is None:
            return {}
        a, b = kind.default_config() or {}, kind.default_config() or {}
        _STABLE[key] = {k: v for k, v in a.items() if b.get(k) == v}
    return _STABLE[key]


def with_defaults(key, config):
    """`config` for reading, with the stable defaults of kind `key` for the settings it lacks (Item.cfg)."""
    import copy

    config = config or {}
    defaults = stable_defaults(key)
    if all(k in config for k in defaults):
        return config
    return {**{k: copy.deepcopy(v) for k, v in defaults.items() if k not in config}, **config}


def all_types():
    return sorted(REGISTRY.values(), key=lambda t: t.order)


def _load_all():
    for mod in pkgutil.iter_modules(__path__):
        if mod.name != "base":
            importlib.import_module(f"{__name__}.{mod.name}")


_load_all()
