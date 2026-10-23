"""Helpers of the tests from test_audit_2026_10_06_c.py; the tests themselves are in tests/editor/test_editor_audit_findings.py, tests/exports/test_exports_audit_findings.py, tests/items/test_items_audit_findings.py, tests/participation/test_participation_audit_findings.py, tests/platform/test_platform_audit_findings.py (sorted by area, architecture concept 2026-10-12, G).

Audit of 2026-10-06, package C (uploads and resources): voice messages written only when the page goes on, with a
bound per recording, per survey and for the disk (S6); a ZIP of several surveys within the limits of one upload
(S7); a transcript .docx that unpacks to too much (S8); the DOI lookup follows redirects only to the agencies and
reads at most 1 MB (H6).
"""
import io


import json


import os


import zipfile


import pytest


from tests.conftest import add


from tests.exports.test_transfer import _file, _preview, _upload


from tests.interview.test_voice import WEBM, E, _folder, _participant, _row, _send, _start, _voice


@pytest.fixture(autouse=True)
def _fresh_count():
    from app.items import voice

    voice._used.clear()  # bytes counted per survey uid and process
    yield
    voice._used.clear()


def _with_required_text(app, client):
    _voice(app, client)
    add(client, "text_input")                                                    # item 3, same page
    client.post(E + "/items/3", data={"code": "t", "question": "Name?", "answer_mode": "required"})


def _pack(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _docx(megabytes, filler=b"a"):
    from docx import Document

    base = io.BytesIO()
    Document().save(base)
    src = zipfile.ZipFile(io.BytesIO(base.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            if info.filename == "word/document.xml":
                with z.open("word/document.xml", "w", force_zip64=True) as f:
                    f.write(b'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                            b'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>')
                    for _ in range(megabytes):
                        f.write(filler * (1024 * 1024 // len(filler)))
                    f.write(b"</w:t></w:r></w:p></w:body></w:document>")
            else:
                z.writestr(info, src.read(info.filename))
    return out.getvalue()


class _Answer:
    def __init__(self, status=200, location=None, body=b"{}"):
        self.status_code, self.headers, self.body, self.closed = status, ({"Location": location} if location else {}), body, False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("requests").HTTPError(str(self.status_code))

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def close(self):
        self.closed = True


def _real_fetch(monkeypatch, answers):
    from app import citations

    monkeypatch.undo()  # the real fetch, in case another test file replaced it
    asked = []

    def get(url, headers, timeout, allow_redirects, stream):
        assert allow_redirects is False and stream is True
        asked.append(url)
        return answers[url]
    monkeypatch.setattr(citations.requests, "get", get)
    return citations, asked
