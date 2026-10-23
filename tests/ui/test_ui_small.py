"""Smaller findings of the UI/UX audit of 2026-10-03 (package H): the results' note, construct filters, the
privacy page's deletion link, the admin list's status."""
from tests.privacy.test_erasure import build


def test_results_page_has_no_note_on_what_it_shows(client, survey):
    html = client.get("/surveys/1/results").data.decode()
    assert "wip-note" not in html and "Was diese Seite zeigt" not in html     # gone (Ralf, 2026-10-03)


def test_long_construct_filters_wait_in_a_fold(client):
    html = client.get("/constructs/").data.decode()
    assert '<details class="facet-more">' in html and "Weitere Filter:" in html


def test_deletion_link_only_where_deleting_can_work(app, client, survey):
    uid = build(app, client, codes=False)
    stranger = app.test_client().get(f"/s/{uid}/privacy").data.decode()
    assert f"/s/{uid}/delete" not in stranger                     # no participation here, no codes: no dead end


def test_deletion_link_with_codes(app, client, survey):
    uid = build(app, client, codes=True)
    assert f"/s/{uid}/delete" in app.test_client().get(f"/s/{uid}/privacy").data.decode()


def test_admin_list_shows_the_status_badge(client, survey):
    html = client.get("/admin/surveys").data.decode()
    assert '<span class="badge badge-draft">Entwurf</span>' in html


def test_link_fields_offer_their_placeholders(client, survey):
    """U35: placeholders of completion links are inserted with a click, not typed."""
    client.post("/surveys/1/panel", data={"param_name": "PID", "param_mode": "store", "param_required": "1",
                                          "param_allowed": ""})
    html = client.get("/surveys/1/field").data.decode()
    chips = html[html.index("data-link-chips"):]
    chips = chips[:chips.index("</div>")]
    assert 'data-insert="{{pid}}"' in chips and 'data-insert="{{PID}}"' in chips and "URL-Parameter PID" in chips
    assert 'name="redirect_completed" value="" placeholder="https://…" data-link-field>' in html


def test_export_pool_is_one_line_until_unfolded(client, survey):
    html = client.get("/surveys/1/data").data.decode()
    assert '<details class="pool-fold" >' in html and "Vorauswahl aus dem gespeicherten Datenpool" in html
    assert '<details class="pool-fold" open>' in client.get("/surveys/1/data?source=real&source=dummy").data.decode()
