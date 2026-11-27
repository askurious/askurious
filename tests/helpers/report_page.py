"""The results page as the PDF results report reads it (app/results_pdf.py, page_tree): with the section "Quoten"
(target and actual), which people no longer see on the page itself since the restructuring of the results part
(stage 3, E6: the quotas are on "Ablauf" > "Quoten"). For the tests of that section."""
import inspect


def report_page(app, survey_id=1, user_id=1, query=""):
    """The HTML of the results page of a survey, made within a request of its own as the report makes it."""
    from flask import g
    from flask_login import login_user

    from app.extensions import db
    from app.models import User
    from app.surveys import results as page

    with app.test_request_context(f"/surveys/{survey_id}/results{query}"):
        login_user(db.session.get(User, user_id))
        g.results_report = True
        out = inspect.unwrap(page.results)(survey_id)
        return out.get_data(as_text=True) if hasattr(out, "get_data") else str(out)
