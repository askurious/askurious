"""Lock every stored secret anew with the current key (audit 2026-10-13, KR-6; BETRIEB.md, "Schlüssel wechseln").

After a new TOKEN_KEY (or SECRET_KEY on a server without TOKEN_KEY) the old key goes into TOKEN_KEY_FALLBACKS (or
SECRET_KEY_FALLBACKS): app/secrets_box.py then still opens what it locked. This pass locks it anew with the current
key, so that the fallback can go afterwards:

- the second factor of the accounts (UserLogin.totp_box),
- the Zenodo and Coscine connections and the own AI accesses of researchers,
- the keys in the surveys: the header value of a web service and the signing key of an external section (flow),
  the key of signed completion links of a panel (panel) and the model access of a KI-Chat (ai_chat_access); these
  with PARTICIPANT_TOKEN_KEY once it is set, the box
  the participants' containers open (audit 2026-10-22, E-01).

What it cannot reach stays readable only while the fallback stays: the undo history of the editor and secrets in a
browser's session (a few minutes). On the server, in the researchers' container (prints counts only, never a value):

    docker compose exec -T researcher python -m app.key_rotation
"""
import copy


def _rotate_columns(model, *names, box=None):
    """Locks the encrypted columns of every row of `model` anew (in `box`, secrets_box). Returns how many values
    changed."""
    from app import secrets_box
    from app.extensions import db

    changed = 0
    for row in db.session.scalars(db.select(model)):
        for name in names:
            old = getattr(row, name)
            new = secrets_box.reencrypt(old, box)
            if new != old:
                setattr(row, name, new)
                changed += 1
    return changed


def _rotate_node(node):
    """The encrypted values of one flow node, locked anew in place; True if one changed."""
    from app import external, secrets_box, webservice

    changed = False
    if node.get("t") == webservice.NODE and node.get("secret"):
        new = secrets_box.reencrypt(node["secret"], secrets_box.PARTICIPANT)
        changed, node["secret"] = new != node["secret"], new
    if node.get("t") == external.NODE and isinstance(node.get("sign"), dict) and node["sign"].get("key"):
        new = secrets_box.reencrypt(node["sign"]["key"], secrets_box.PARTICIPANT)
        changed, node["sign"]["key"] = changed or new != node["sign"]["key"], new
    return changed


def _rotate_surveys():
    """The keys in the surveys' flow and panel settings. Returns how many surveys changed."""
    from sqlalchemy.orm.attributes import flag_modified

    from app import flow, secrets_box
    from app.extensions import db
    from app.models import Survey

    changed = 0
    for survey in db.session.scalars(db.select(Survey)):
        touched = False
        if survey.flow:
            tree = copy.deepcopy(survey.flow)
            if any([_rotate_node(node) for node, _, _ in flow.walk(tree)]):
                survey.flow = tree
                flag_modified(survey, "flow")
                touched = True
        sign = (survey.panel or {}).get("sign")
        if isinstance(sign, dict) and sign.get("key"):
            new = secrets_box.reencrypt(sign["key"], secrets_box.PARTICIPANT)
            if new != sign["key"]:
                panel = copy.deepcopy(survey.panel)
                panel["sign"]["key"] = new
                survey.panel = panel
                flag_modified(survey, "panel")
                touched = True
        changed += touched
    return changed


def rotate():
    """Locks every stored secret anew with the current key and commits. Returns {place: number changed}."""
    from app import secrets_box
    from app.extensions import db
    from app.models import (AccountJob, AiAccess, AiChatAccess, AuthToken, BoardShare, CoscineConnection, QuotaShare,
                            UserLogin, ZenodoConnection)

    counts = {
        "account_jobs": _rotate_columns(AccountJob, "payload_box"),
        "email_confirmation": _rotate_columns(AuthToken, "secret_box"),
        "totp": _rotate_columns(UserLogin, "totp_box"),
        "zenodo": _rotate_columns(ZenodoConnection, "access_token", "refresh_token"),
        "coscine": _rotate_columns(CoscineConnection, "token"),
        "ai_access": _rotate_columns(AiAccess, "key_enc"),
        # the model access of a KI-Chat: opened by the participants' containers (app/ai_chat.py)
        "ai_chat_access": _rotate_columns(AiChatAccess, "key_enc", box=secrets_box.PARTICIPANT),
        "dashboard_links": _rotate_columns(BoardShare, "token_box"),
        "quota_links": _rotate_columns(QuotaShare, "token_box"),
        "surveys": _rotate_surveys(),
    }
    db.session.commit()
    return counts


def unreadable():
    """How many stored secrets no key opens any more (they stay as they are): {place: number}."""
    from app import secrets_box
    from app.extensions import db
    from app.models import AccountJob, AuthToken, AiAccess, BoardShare, CoscineConnection, QuotaShare, UserLogin, ZenodoConnection

    out = {}
    for label, model, names in (("account_jobs", AccountJob, ("payload_box",)),
                                ("email_confirmation", AuthToken, ("secret_box",)),
                                ("totp", UserLogin, ("totp_box",)),
                                ("zenodo", ZenodoConnection, ("access_token", "refresh_token")),
                                ("coscine", CoscineConnection, ("token",)), ("ai_access", AiAccess, ("key_enc",)),
                                ("dashboard_links", BoardShare, ("token_box",)), ("quota_links", QuotaShare, ("token_box",))):
        out[label] = sum(1 for row in db.session.scalars(db.select(model)) for name in names
                         if getattr(row, name) and not secrets_box.decrypt(getattr(row, name)))
    from app import external, flow, webservice
    from app.models import Survey

    lost = 0
    for survey in db.session.scalars(db.select(Survey)):
        sign = (survey.panel or {}).get("sign")
        values = [sign.get("key")] if isinstance(sign, dict) else []
        for node, _, _ in flow.walk(survey.flow or {}):
            if node.get("t") == webservice.NODE:
                values.append(node.get("secret"))
            elif node.get("t") == external.NODE:
                sign = node.get("sign")
                if isinstance(sign, dict):
                    values.append(sign.get("key"))
        lost += sum(1 for value in values if value and not secrets_box.decrypt(value, secrets_box.PARTICIPANT))
    from app.models import AiChatAccess

    lost += sum(1 for row in db.session.scalars(db.select(AiChatAccess))
                if row.key_enc and not secrets_box.decrypt(row.key_enc, secrets_box.PARTICIPANT))
    out["survey_keys"] = lost
    return out


def main():
    from app import create_app

    app = create_app()
    with app.app_context():
        counts = rotate()
        lost = unreadable()
    print("locked anew with the current key: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    if any(lost.values()):
        print("no key opens: " + ", ".join(f"{k} {v}" for k, v in lost.items() if v)
              + " (a key of before missing in TOKEN_KEY_FALLBACKS or SECRET_KEY_FALLBACKS?)")
    else:
        print("current database fields checked; retain previous keys for undo history, active sessions and backups (BETRIEB.md)")


if __name__ == "__main__":
    main()
