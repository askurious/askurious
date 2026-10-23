"""Images: uploaded, or generated with GenAI from a prompt.

The prompt may use {{variables}}. Outside a loop block the image is static (one asset).
Inside a loop block (M5) it becomes dynamic: one asset per combination of the
variables the prompt actually uses. Generated images must be approved before the start,
and participants only ever see approved, pre-generated images.
"""
import hashlib
import json

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.lang import Both, de_en
from app.items.base import r_flag, r_one, r_text  # rules for survey files, see base.py
from app.items.base import PLACEHOLDER, ItemType, placeholders


# what models.Asset.key holds; a longer key (loop values of up to 500 characters each) is stored as a hash
KEY_LENGTH = 500
HASHED = "h:"


def asset_key(variables, used):
    """Canonical key for the variable combination an image depends on ('' if static): the JSON of the values,
    or "h:" and its SHA-256 where that JSON is longer than the column (PostgreSQL refused it after the image
    was paid for; code review 2026-10-03, PG-4). The JSON always starts with "{", so the two never meet."""
    if not used:
        return ""
    full = json.dumps({k: str(variables.get(k, "")) for k in sorted(used)}, ensure_ascii=False, sort_keys=True)
    return full if len(full) <= KEY_LENGTH else HASHED + hashlib.sha256(full.encode("utf-8")).hexdigest()


@register
class Image(ItemType):
    key = "image"
    import_rules = {"source": r_one("upload", "ai"), "prompt": r_text(5000), "text_free": r_flag,
                    "size": r_one("1024x1024", "1536x1024", "1024x1536"),
                    "slide": r_flag}  # a page of a PDF in a live vote (app/live/ pdf_page)
    label = Both("Bild", "Image")
    short = Both("Hochgeladen oder mit KI erzeugt", "Uploaded or made with AI")
    description = Both("Hochgeladenes oder KI-generiertes Bild, optional mit Platzhaltern aus einer Schleife",
                       "Uploaded or AI-generated image, optionally with placeholders from a loop")
    order = 60
    fact_sheet = FactSheet(
        "spatial",
        keywords=["Bild", "Image", "Foto", "Photo", "Stimulus", "KI-Bild", "AI image"],
        can=[("Hochgeladen oder KI-generiert: quadratisch, quer oder hoch.",
              "Uploaded or AI-generated: square, landscape or portrait."),
             ("Auf Wunsch ohne Text im Bild, mit Platzhaltern aus Schleifen.",
              "If you wish without text in the image, with placeholders from loops.")],
        good_for=[("Stimuli in Experimenten und Vignetten", "Stimuli in experiments and vignettes"),
                  ("Varianten im A/B-Test", "Variants in an A/B test")],
        not_for=[("Antworten als Bilder zum Antippen (dann Einfachauswahl als Bildauswahl)",
                  "Answers as images to tap (then single choice as image choice)")],
        designs=["framing", "ab", "within", "vignettes"],
        examples=[("Framing-Experiment: dasselbe Produkt als freundliches oder technisches Bild",
                   "Framing experiment: the same product as a friendly or a technical image")],
    )
    collects_answer = False
    has_image = True

    def default_config(self):
        return {"source": "upload", "prompt": "", "text_free": True, "size": "1536x1024"}

    def default_texts(self, lang):
        return {"question": "", "alt": ""}

    def update_from_form(self, item, form, lang, structural):
        # Caption and alt text; the image source is set by the upload / generate actions
        texts = dict(item.texts or {})
        texts[lang] = {
            **texts.get(lang, {}),
            "question": form.get("question", "").strip(),
            "alt": form.get("alt", "").strip(),
        }
        item.texts = texts

    def update_ai_settings(self, item, form):
        item.config = {
            **item.config,
            "source": "ai",
            "prompt": form.get("prompt", "").strip(),
            "text_free": bool(form.get("text_free")),
            "size": form.get("size") if form.get("size") in ("1024x1024", "1536x1024", "1024x1536") else "1536x1024",
        }

    def prompt_variables(self, item):
        return placeholders(item.config.get("prompt", "")) if item.config.get("source") == "ai" else set()

    def is_dynamic(self, item):
        return bool(self.prompt_variables(item))

    def full_prompt(self, item, variables=None):
        from app import image_styles

        variables = variables or {}

        def sub(m):  # {{var}} and {{var|text}}, as in the texts: the text where var is empty (code review 2026-10-15, K3)
            name, fallback = m.group(1), m.group(2)
            if variables.get(name) not in (None, ""):
                return str(variables[name])
            if fallback is not None:
                return fallback.strip()
            return "" if name in variables else m.group(0)

        scene = PLACEHOLDER.sub(sub, item.config.get("prompt", ""))
        return image_styles.compose(item.survey, scene, text_free=item.config.get("text_free", True))

    def required_keys(self, item, variable_rows=None):
        """The asset keys a survey needs before it can start: one per distinct combination of the
        loop variables the prompt uses (not one per row)."""
        from app import loops

        used = self.prompt_variables(item)
        if not used:
            return [""]
        rows = variable_rows if variable_rows is not None else (
            loops.all_variables(item.block) if loops.active(item.block) else [])
        return sorted({asset_key(r, used) for r in rows})

    def key_variables(self, item, key):
        """{variable: value} a key stands for; a hashed key is looked up among the rows of the loop ({} if no
        row gives it any more)."""
        if not key:
            return {}
        if not key.startswith(HASHED):
            return json.loads(key)
        from app import loops

        used = self.prompt_variables(item)
        rows = loops.all_variables(item.block) if loops.active(item.block) else []
        row = next((r for r in rows if asset_key(r, used) == key), None)
        return {k: str(row.get(k, "")) for k in sorted(used)} if row is not None else {}

    def key_label(self, item, key):
        return " · ".join(f"{k}: {v}" for k, v in self.key_variables(item, key).items()) if key else "Bild"

    def prompt_for_key(self, item, key):
        return self.full_prompt(item, self.key_variables(item, key))

    def asset_for(self, item, variables=None):
        key = asset_key(variables or {}, self.prompt_variables(item))
        for a in item.assets:
            if a.key == key and a.status == "approved":
                return a
        return None

    def validate(self, item):
        from app import loops

        problems = []
        used = self.prompt_variables(item)
        if used and not loops.active(item.block):
            problems.append(de_en("Platzhalter im Prompt ({names}) brauchen einen Block in einer Schleife",
                                  "Placeholders in the prompt ({names}) need a block in a loop", names=", ".join(sorted(used))))
        elif used:
            keys = self.required_keys(item)
            if len(keys) > 500:
                problems.append(de_en("{n} Bilder nötig: zu viele (höchstens 500)", "{n} images needed: too many (at most 500)", n=len(keys)))
            missing = [k for k in keys if not any(a.key == k and a.status == "approved" for a in item.assets)]
            if missing and len(keys) <= 500:
                problems.append(de_en("{k} von {n} Bildern fehlen oder sind nicht freigegeben",
                                      "{k} of {n} images are missing or not approved", k=len(missing), n=len(keys)))
        elif self.asset_for(item) is None:
            problems.append(de_en("kein freigegebenes Bild", "no approved image"))
        return problems

    def check_warnings(self, item):
        """An image without an alternative text says nothing to those who cannot see it (accessibility D8). A hint,
        no error: an image that only decorates may stay without one."""
        langs = [lang for lang in (item.survey.languages or [item.survey.default_language])
                 if not (item.lang_texts(lang).get("alt") or "").strip()]
        if not langs:
            return []
        return [de_en("Bild ohne Alternativtext ({langs}): Screenreader sagen dann nichts über das Bild. Beschreiben Sie "
                      "kurz, was es zeigt; nur ein Bild, das bloß schmückt, darf ohne bleiben.",
                      "image without alternative text ({langs}): screen readers then say nothing about it. Describe briefly "
                      "what it shows; only an image that merely decorates may go without.",
                      langs=", ".join(lang.upper() for lang in langs))]
