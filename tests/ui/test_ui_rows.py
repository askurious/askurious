"""Rows that are added with "+" and removed with × instead of spare empty rows (UI/UX audit of 2026-10-03,
package C): link parameters, link rules, return values of an external section."""
import re

from tests.conftest import add


def test_link_parameters_and_rules_have_one_template_row_and_buttons(client, survey):
    client.post("/surveys/1/panel", data={"param_name": "PID", "param_mode": "store", "param_required": "1",
                                          "param_allowed": ""})
    html = client.get("/surveys/1/field").data.decode()
    params = html[html.index("data-param-rows"):html.index("+ Parameter")]
    assert params.count('name="param_name"') == 2                    # the saved one and the template
    assert params.index('value="PID"') < params.index("<template data-row-template>")
    assert 'aria-label="Parameter PID entfernen"' in params
    rules = html[html.index('class="link-rules" data-rows'):html.index("+ Regel")]
    assert rules.count('_url"') == 1 and 'name="r__N___url"' in rules and "<template data-row-template>" in rules
    assert "Nach dem Speichern erscheinen zwei weitere leere Zeilen" not in html


def test_return_values_of_an_external_section_are_rows_with_buttons(app, client, survey):
    from tests.editor.test_external import build

    build(app, client)
    html = client.get("/surveys/1/flow/").data.decode()
    values = html[html.index("ext-values"):]
    values = values[:values.index("+ Rückgabewert")]
    assert values.count('name="value_name"') == 3                    # score, label and the template
    assert values.index('value="label"') < values.index("<template data-row-template>")
    assert 'aria-label="Rückgabewert score entfernen"' in values


def test_no_editor_speaks_of_saving_by_button(client, survey):
    for kind in ("single_choice", "likert", "ranking", "maxdiff", "conjoint"):
        add(client, kind)
    for item_id in range(2, 7):
        html = client.get(f"/surveys/1/editor/items/{item_id}").data.decode()
        assert not re.search(r"beim Speichern|anlegen und speichern", html), item_id
