"""The MCP tools run editor views through agent_tools.editor_call, an inner request without the app's before_request
hooks. Only the views of EDITOR_CALL_ENDPOINTS may run that way, and every hook that depends on the endpoint is
checked against that list here: a new hook or a new endpoint fails until someone decided whether editor_call has to
run the hook too (audit 2026-10-18, V-MCP; until then it was only checked by hand)."""
import ast
import pathlib

import pytest
from flask_login import login_user

from app import agent_tools
from app.agent_tools import EDITOR_CALL_ENDPOINTS
from app.extensions import db
from app.models import User

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# every before_request hook of the app, with why the inner request of editor_call may go without it
REVIEWED = {
    # global ones, run by the outer MCP request (/mcp) that the tool call comes in with
    "app.create_app.<locals>._development_key_stays_local": "outer request",
    "app.create_app.<locals>._redirect_old_hosts": "outer request",
    "app.url_names.redirect_german": "outer request; editor_call builds its address with url_for",
    "app.hosts.guard": "outer request; the editor's views are on the researchers' side like /mcp",
    "flask_wtf.csrf.CSRFProtect.init_app.<locals>.csrf_protect": "the token is the proof (Bearer); no browser",
    "app.create_app.<locals>._researcher_csrf_age": "only after a passed CSRF check (g.csrf_valid), which editor_call "
                                                     "does not run: the token is the proof (Bearer); no browser",
    "app.create_app.<locals>._auto_close": "outer request",
    "app.terms.gate": "outer request (mcp checks the terms of the token's account)",
    # depend on the endpoint: checked below for every view of EDITOR_CALL_ENDPOINTS
    "app.create_app.<locals>._small_forms_for_participants": "participants' blueprints only",
    "app.live.control._redirect_pages": "pages of a live vote, GET only",
    "app.interview._guard": "run by editor_call itself (interview.guard)",
    "app.ai_access._ai_off_page": "AI pages only (AI_PAGES)",
    "app.account_setup.gate": "GET of pages only",
    # blueprints editor_call never reaches
    "app.admin._admins_only": "blueprint admin",
    "app.org_admin._org_admins_only": "blueprint org",
    "app.participate._remember_session": "blueprint participate",
}
ENDPOINT_HOOKS = {"app.create_app.<locals>._small_forms_for_participants", "app.live.control._redirect_pages",
                  "app.ai_access._ai_off_page", "app.account_setup.gate"}


def _name(func):
    return f"{func.__module__}.{func.__qualname__}"


def test_every_editor_call_names_an_endpoint_of_the_list():
    used = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "editor_call":
                first = node.args[0] if node.args else None
                assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
                    f"{path.name}:{node.lineno}: editor_call with an endpoint that is not written out"
                used.add(first.value)
    assert used and used <= EDITOR_CALL_ENDPOINTS, used - EDITOR_CALL_ENDPOINTS
    assert EDITOR_CALL_ENDPOINTS <= used, f"no longer called, take it off the list: {EDITOR_CALL_ENDPOINTS - used}"


def test_every_before_request_hook_was_checked_for_editor_call(app):
    found = {_name(f) for funcs in app.before_request_funcs.values() for f in funcs}
    assert found == set(REVIEWED), ("check the new hook for agent_tools.editor_call, then add it to REVIEWED: "
                                    f"new {sorted(found - set(REVIEWED))}, gone {sorted(set(REVIEWED) - found)}")
    blueprints = {view.split(".")[0] for view in EDITOR_CALL_ENDPOINTS}
    assert not any(app.before_request_funcs.get(bp) for bp in blueprints)  # no hook of the editor's own blueprint


def test_the_hooks_that_depend_on_the_endpoint_let_every_listed_view_through(app, client, survey):
    hooks = [f for f in app.before_request_funcs[None] if _name(f) in ENDPOINT_HOOKS]
    assert len(hooks) == len(ENDPOINT_HOOKS)
    args = {"block_id": 2, "item_id": 1, "direction": "up"}
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        for endpoint in sorted(EDITOR_CALL_ENDPOINTS):
            rule = next(r for r in app.url_map.iter_rules(endpoint))
            values = {k: v for k, v in args.items() if k in rule.arguments}
            from flask import url_for

            with app.test_request_context():
                path = url_for(endpoint, survey_id=1, **values)
            for method in ("GET", "POST"):
                with app.test_request_context(path, method=method):
                    login_user(user)
                    for hook in hooks:
                        assert hook() is None, f"{_name(hook)} would refuse {endpoint} ({method})"


def test_editor_call_refuses_a_view_off_the_list(app, client, survey):
    with app.test_request_context():
        from app.models import Survey

        with pytest.raises(RuntimeError):
            agent_tools.editor_call("editor.index", db.session.get(Survey, 1), method="GET")
