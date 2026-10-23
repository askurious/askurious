"""The bar of the Einordnung (score_band) names scale values only (pre-test 2026-10-20): the categories carried their
number 1, 2, 3 inside the bar, so "Ihr Wert: 3,3" seemed to sit in "2"."""
import re

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Item
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.participation import test_participate

E = "/surveys/1/editor"


def test_bar_shows_the_own_value_and_the_scale_values_not_the_category_numbers(app, client, survey):
    fill_consent(client)
    client.post(E + "/blocks/2/items", data={"type": "construct:asku"})                     # 2, scale 1 to 5
    add(client, "page_break")                                                               # 3
    add(client, "score_band")                                                               # 4
    form = MultiDict([("code", "band"), ("question", "Ihre Einordnung"), ("source", "2"), ("basis", "value")])
    for upper, label in (("2,5", "niedrig"), ("3,5", "mittel"), ("", "hoch"), ("", "")):
        form.add("band_id", ""); form.add("band_upper", upper); form.add("band_label", label); form.add("band_text", "")
    client.post(E + "/items/4", data=form)
    with app.app_context():
        sids = [s["id"] for s in db.session.get(Item, 2).config["statements"]]
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    url = f"/s/{test_participate.uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", **{f"i2_{sid}": str(v) for sid, v in zip(sids, [3, 4] * len(sids))}})
    page = p.get(url).data.decode()
    value = re.search(r'name="i4_value" value="([\d.]+)"', page).group(1)
    shown = f"{float(value):.1f}".replace(".", ",")
    bar = page[page.index('class="p-band-bar"'):page.index('class="p-band-legend"')]
    assert re.search(r'class="p-band-seg[^"]*"[^>]*></span>', bar)                       # no number in a category
    assert f'class="p-band-value"' in bar and f">{shown}</span>" in bar                  # the own value at the marker
    axis = re.findall(r"<span[^>]*>([^<]+)</span>", bar[bar.index('class="p-band-axis"'):])
    assert axis == ["1", "2,5", "3,5", "5"]                                                 # ends and bounds
    assert "<b>1</b>" not in page[page.index('class="p-band-legend"'):]
