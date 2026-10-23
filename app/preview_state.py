"""Example-only previews: isolated transient responses, no owner's test data or HTTP services."""
from datetime import datetime

from flask import after_this_request, g
from flask_login import current_user

from app import browser_receipts
from app.extensions import db
from app.models import Response, new_pid, utcnow

FIELDS = ("answers", "layout", "paradata", "params", "position", "status", "occasion", "quota", "external", "pid")
DATES = ("started_at", "completed_at", "page_started_at", "outside_since")


def response(survey, lang, restart=False):
    row = browser_receipts.receipt(survey, "example-preview", str(current_user.id), lock=True, hours=1)
    saved = {} if restart else row.data or {}
    obj = Response(survey_id=survey.id, is_preview=True, source="test", language=lang, answers={},
                   qversion=survey.qversion, position=0, status=Response.ACTIVE, started_at=utcnow(),
                   pid=new_pid(survey.code))
    if saved.get("qversion") == survey.qversion:
        for name in FIELDS:
            if name in saved:
                setattr(obj, name, saved[name])
        for name in DATES:
            if saved.get(name):
                setattr(obj, name, datetime.fromisoformat(saved[name]))
    g.example_preview = (row, obj)
    if not g.get("example_preview_hook"):
        g.example_preview_hook = True

        @after_this_request
        def keep(http_response):
            receipt, preview = g.example_preview
            if http_response.status_code < 400:
                receipt.data = {"qversion": survey.qversion,
                                **{k: getattr(preview, k, None) for k in FIELDS},
                                **{k: getattr(preview, k).isoformat() if getattr(preview, k, None) else None for k in DATES}}
                db.session.commit()
            return http_response
    return obj
