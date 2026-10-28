"""The address of the coding links of before (/guide/<code>/coding-link, 2026-10-27 to 2026-10-28; Ralf 2026-10-28:
only made to try out). Coding without an account goes by an interviewer link with the right "darf kodieren" and
without "darf Interviews führen" (app/interviewer.py, app/transcript_coding.py); the coding links and their table are
gone (migrations 0105, 0106). Their address says so kindly instead of an error."""
from flask import render_template

from app.transcript_coding import bp, guide


@bp.route("/<code>/coding-link", methods=["GET", "POST"])
def link_start(code):
    return render_template("transcript_coding/start.html", survey=guide(code)), 410
