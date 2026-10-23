"""Page break: splits a block into pages. Has no text and no answer."""
from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import ItemType
from app.lang import Both


@register
class PageBreak(ItemType):
    key = "page_break"
    import_rules = {}
    label = Both("Seitenumbruch", "Page break")
    short = Both("Neue Seite im Block", "New page in the block")
    description = Both("Beginnt eine neue Seite innerhalb des Blocks", "Starts a new page within the block")
    order = 90
    fact_sheet = FactSheet(
        "structure",
        keywords=["Seite", "Page", "Neue Seite", "New page", "Umbruch", "Break"],
        can=[("Beginnt eine neue Seite innerhalb des Blocks.", "Starts a new page within the block.")],
        good_for=[("Lange Blöcke auf mehrere Seiten verteilen", "Spreading long blocks over several pages")],
        not_for=[("Neue Abschnitte mit eigener Bedingung oder Zufallsreihenfolge (dann ein neuer Block)",
                  "New sections with their own condition or random order (then a new block)")],
    )
    collects_answer = False

    def default_texts(self, lang):
        return {}

    def update_from_form(self, item, form, lang, structural):
        pass
