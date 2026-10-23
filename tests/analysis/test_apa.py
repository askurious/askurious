"""Sources in APA style (app/apa.py, templates/_sources.html): short references in the text, one folded list."""
from app import apa
from app.compare import COCHRAN, CRAMER, KERBY, MANN_WHITNEY
from app.quality import CURRAN, LEINER

CONRAD = ("Conrad, F. G., Tourangeau, R., Couper, M. P., & Zhang, C. (2017). Reducing speeding in web surveys by providing "
          "immediate feedback. Survey Research Methods, 11(1), 45–61. https://doi.org/10.18148/srm/2017.v11i1.6304")
CHAPTER = ("Krosnick, J. A., & Presser, S. (2010). Question and questionnaire design. In P. V. Marsden & J. D. Wright "
           "(Eds.), Handbook of survey research (2nd ed., pp. 263–313). Emerald.")


def test_short_reference_after_the_number_of_authors():
    assert apa.ref(LEINER)["short"] == "Leiner, 2019"
    assert apa.ref(MANN_WHITNEY)["short"] == "Mann & Whitney, 1947"
    assert apa.ref(MANN_WHITNEY)["narrative"] == "Mann und Whitney (1947)"
    assert apa.ref(CONRAD)["short"] == "Conrad et al., 2017"
    assert apa.ref("Deutsche Forschungsgemeinschaft. (2019). Leitlinien. https://doi.org/10.5281/zenodo.3923602")["short"] \
        == "Deutsche Forschungsgemeinschaft, 2019"


def test_entry_in_apa_7_with_italics_and_doi_link():
    html = str(apa.ref(CONRAD)["html"])
    assert html.startswith("Conrad, F. G., Tourangeau, R., Couper, M. P., &amp; Zhang, C. (2017). Reducing speeding")
    assert "<i>Survey Research Methods, 11</i>(1), 45–61." in html
    assert html.endswith('<a href="https://doi.org/10.18148/srm/2017.v11i1.6304" target="_blank" rel="noopener">'
                         'https://doi.org/10.18148/srm/2017.v11i1.6304</a>')
    assert "<i>Comprehensive Psychology, 3</i>, 11.IT.3.1." in str(apa.ref(KERBY)["html"])
    assert str(apa.ref(CRAMER)["html"]).endswith("<i>Mathematical methods of statistics</i>. Princeton University Press.")
    assert "(Eds.), <i>Handbook of survey research</i> (2nd ed., pp. 263–313). Emerald." in str(apa.ref(CHAPTER)["html"])


def test_cite_sorts_and_links_each_work_once():
    html = str(apa.cite([LEINER, CURRAN, LEINER]))
    assert html == ('<span class="cite-group">(<a class="cite" href="#ref-curran2016-methods">Curran, 2016</a>; '
                    '<a class="cite" href="#ref-leiner2019-too">Leiner, 2019</a>)</span>')
    assert str(apa.cite([LEINER], narrative=True, link=False)) == "Leiner (2019)"
    assert str(apa.cite([])) == ""


def test_reference_list_once_per_work_sorted_with_its_uses():
    entries = apa.references([("Speeder", [LEINER]), ("Muster", [CURRAN, LEINER]), ("Tests", [COCHRAN])])
    assert [e["short"] for e in entries] == ["Cochran, 1954", "Curran, 2016", "Leiner, 2019"]
    assert entries[2]["uses"] == ["Speeder", "Muster"]


def test_a_string_without_the_apa_head_is_shown_as_it_is():
    r = apa.ref("Statistisches Bundesamt: Tabelle 12411-0013, Stand 31.12.2025, https://www-genesis.destatis.de")
    assert "Tabelle 12411-0013" in str(r["html"]) and '<a href="https://www-genesis.destatis.de"' in str(r["html"])
