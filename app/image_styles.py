"""Survey-wide illustration style for all AI-generated images.

Style prompts and demo images are Askurious' own (demo images: app/static/styles/
<style>_<context>.jpg, generated once, not per study; "realistic" added 2026-10-05, its demo images made with the
image model of the platform from the same four scenes; all of them AI-generated, shown as "Beispielbilder KI-generiert"
in the style picker, copyright audit 2026-10-16, U35). The style prompt, the scene of each
image, and neutral rules are composed into the final prompt, so all images of a survey look
consistent and differences between conditions come from the manipulated content only.
"""

from app.lang import de_en, english

NEUTRAL_RULES = ("Neutral, factual depiction: no emotional or dramatic framing, no utopian or dystopian "
                 "atmosphere, no text, letters, numbers, labels, logos or brand names; no recognisable real people, "
                 "no characters or works protected by copyright.")  # the last part: copyright audit 2026-10-16, U29

# key: (label, short description, style prompt)
IMAGE_STYLES = {
    "watercolor": (
        "Tusche & Aquarell",
        "Leichte Tuschezeichnung mit Aquarell. Ruhig und zugänglich.",
        "Light, soft ink drawing with delicate watercolor washes, like a professional watercolor painting: "
        "loose but confident linework, muted natural palette, generous white paper, gentle and calm.",
    ),
    "comic": (
        "Schwarz-weiß-Comic",
        "Klare Tuschekonturen, flächiges Schwarz und Raster, keine Farbe.",
        "Black and white comic / graphic novel style: clean bold ink outlines, flat black fills and halftone "
        "shading, no color at all, no speech bubbles.",
    ),
    "comic60": (
        "Alter Comic",
        "Comic der 60er Jahre in Schwarz-Weiß: kräftige Pinselkonturen, Rasterpunkte, Zeitungspapier.",
        "1960s vintage comic book art in black and white: bold brush-and-ink outlines, Ben-Day dot shading, high "
        "contrast, slightly rough newsprint texture, retro printing look; no color, no speech bubbles, no text.",
    ),
    "sketch": (
        "Handskizze",
        "Einfache Strichzeichnung von Hand, ohne Schattierung; wenige Farben, wo sie helfen.",
        "Hand-drawn sketch with simple, clean line work, like a quick marker or pen drawing: plain outlines only, no "
        "shading, no hatching, no gradients; a few flat accent colors where they help to tell things apart, lots of "
        "white paper.",
    ),
    "technical": (
        "Technische Zeichnung",
        "Präzise Linien wie eine Konstruktions- oder Patentzeichnung.",
        "Technical drawing / engineering illustration: precise thin black line work on white paper, isometric "
        "view, subtle construction lines and hatching, like a patent drawing or blueprint.",
    ),
    "renaissance": (
        "Renaissance-Zeichnung",
        "Sepia- und Rötelstudie auf altem Papier, wie ein da-Vinci-Skizzenbuch.",
        "Renaissance drawing in the manner of a Leonardo da Vinci study: sepia and red chalk with brown ink on "
        "aged paper, fine cross-hatching, careful observational detail, no handwriting.",
    ),
    "flat": (
        "Flache Vektorgrafik",
        "Moderne, klare Formen mit wenigen Farben, wie eine Infografik.",
        "Modern flat vector illustration: simple geometric shapes, limited calm color palette, no gradients, "
        "no shadows, clean and friendly but neutral, like an explanatory infographic.",
    ),
    "realistic": (
        "Realistische Illustration",
        "Gemalt wie in einer Zeitschrift, mit natürlichen Proportionen und Farben. Konkret, aber erkennbar kein Foto.",
        "Painted editorial illustration in gouache style: visible brushstrokes, simplified textures and softly painted "
        "edges, natural proportions and true-to-life colors, gentle natural light, clearly hand-painted and not "
        "photographic, calm neutral composition at eye level, no caricature.",
    ),
    "photo": (
        "Dokumentarfoto",
        "Realistisches Foto mit natürlichem Licht. Am konkretesten, beeinflusst die Wahrnehmung aber am stärksten.",
        "Documentary-style photograph: realistic everyday setting, natural light, neutral eye-level "
        "composition, unstaged look, no dramatic lighting.",
    ),
}

# the label and short description of each style in English (researcher pages in English, app/lang.py)
IMAGE_STYLES_EN = {
    "watercolor": ("Ink & watercolour", "Light ink drawing with watercolour. Calm and approachable."),
    "comic": ("Black-and-white comic", "Clear ink outlines, flat black and halftone, no colour."),
    "comic60": ("Vintage comic", "1960s comic in black and white: strong brush outlines, halftone dots, newsprint."),
    "sketch": ("Hand sketch", "Simple line drawing by hand, without shading; a few colours where they help."),
    "technical": ("Technical drawing", "Precise lines like an engineering or patent drawing."),
    "renaissance": ("Renaissance drawing", "Sepia and red chalk study on old paper, like a da Vinci sketchbook."),
    "flat": ("Flat vector graphic", "Modern, clear shapes with few colours, like an infographic."),
    "realistic": ("Realistic illustration", "Painted as in a magazine, with natural proportions and colours. "
                                            "Concrete, but recognisably not a photo."),
    "photo": ("Documentary photo", "Realistic photo with natural light. The most concrete, but it influences "
                                   "perception the most."),
}


def styles():
    """IMAGE_STYLES with label and description in the language of the page: {key: (label, description, prompt)}."""
    if not english():
        return IMAGE_STYLES
    return {key: IMAGE_STYLES_EN[key] + (style[2],) for key, style in IMAGE_STYLES.items()}


def style_name(key):
    """The label of a style in the language of the page."""
    return styles().get(key, (key,))[0]


DEMO_CONTEXTS = {
    "care": "Pflegeroboter",
    "mobility": "Smarte Mobilität",
    "energy": "Nachhaltige Energie",
    "stem": "MINT-Unterricht",
}


def demo_filename(style_key, context_key):
    return f"styles/{style_key}_{context_key}.jpg"


def settings(survey):
    s = survey.image_style or {}
    return {"key": s.get("key") if s.get("key") in IMAGE_STYLES or s.get("key") == "custom" else None,
            "custom": s.get("custom", "")}


def style_prompt(survey):
    s = settings(survey)
    if s["key"] == "custom":
        return s["custom"].strip()
    style = IMAGE_STYLES.get(s["key"])
    return style[2] if style else ""


def style_id(survey):
    """Identifies the style an image was made with (to flag outdated images)."""
    s = settings(survey)
    if s["key"] == "custom":
        import hashlib

        return "custom:" + hashlib.sha1(s["custom"].encode()).hexdigest()[:8]
    return s["key"] or ""


def style_label(survey, sid=None):
    sid = style_id(survey) if sid is None else sid
    if not sid:
        return de_en("kein Stil", "no style")
    if sid.startswith("custom:"):
        return de_en("eigener Stil", "own style")
    return style_name(sid)


def compose(survey, scene, text_free=True):
    """Final image prompt: style, scene, and (optionally) the neutral rules."""
    style = style_prompt(survey)
    parts = [f"Style: {style}" if style else "", f"Scene: {scene.strip()}" if scene else "",
             NEUTRAL_RULES if text_free else ""]
    return "\n\n".join(p for p in parts if p)
