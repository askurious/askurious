"""Load test: simulated participants fill in a running survey, page by page, as people do.

    python -m locust -f tools/lasttest/locustfile.py --host https://<server> --headless \
        --html lasttest-bericht.html --csv lasttest

Settings through the environment (PowerShell: $env:LASTTEST_CODE = "abc123"):
    LASTTEST_CODE    the code of the survey (the part after /s/ in its link); required
    LASTTEST_STUFEN  participants at once per stage, e.g. "25,50,100,200,400" (default)
    LASTTEST_MINUTEN minutes per stage (default 3)
    LASTTEST_PAUSE   seconds a person reads and answers per page, "von-bis" (default "3-8")

Each simulated person opens the link with an empty browser, agrees to the consent, answers every page with
random but valid answers (also leaves some questions out), sends it and starts over as a new person. Requests
are named by page ("Seite 3"), so the report shows which page gets slow. A finished participation counts as
"Teilnahme abgeschlossen" (its time is the whole participation, pauses included).

The form is read from the page itself, so the script fits any survey made of the usual question types;
tools/lasttest/lasttest-befragung.json is the survey built for it (tools/lasttest/build_survey.py).
"""
import os
import random
import re
import time
from html.parser import HTMLParser

from locust import HttpUser, LoadTestShape, between, events, task  # noqa: F401

CODE = os.environ.get("LASTTEST_CODE", "").strip().strip("/")
STAGES = [int(n) for n in os.environ.get("LASTTEST_STUFEN", "25,50,100,200,400").split(",") if n.strip()]
MINUTES = float(os.environ.get("LASTTEST_MINUTEN", "3"))
PAUSE = [float(n) for n in os.environ.get("LASTTEST_PAUSE", "3-8").split("-")]
MAX_PAGES = 60  # a survey that never ends (a loop, a check that refuses every answer) stops here


class Form(HTMLParser):
    """The fields of the participant form: hidden values, radio groups, checkboxes, selects, text fields and the
    submit buttons with their name and value."""

    def __init__(self, html):
        super().__init__()
        self.in_form = False
        self.hidden, self.radios, self.checks, self.texts, self.areas = {}, {}, {}, [], []
        self.selects, self.buttons = {}, []
        self._select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "form" and a.get("method", "").lower() == "post":
            self.in_form = True
        if not self.in_form:
            return
        name = a.get("name", "")
        if tag == "input" and name:
            kind = a.get("type", "text").lower()
            if a.get("tabindex") == "-1" and kind == "text":
                return  # the field people never see (honeypot): stays empty
            if kind == "hidden":
                self.hidden[name] = a.get("value", "")
            elif kind == "radio":
                self.radios.setdefault(name, []).append(a.get("value", ""))
            elif kind == "checkbox":
                self.checks.setdefault(name, []).append(a.get("value", "1"))
            elif kind in ("text", "number", "email", "tel", "url", "date", "time", "datetime-local"):
                self.texts.append((name, kind, a))
        elif tag == "textarea" and name:
            self.areas.append(name)
        elif tag == "select" and name:
            self._select = name
            self.selects[name] = []
        elif tag == "option" and self._select is not None and a.get("value"):
            self.selects[self._select].append(a["value"])
        elif tag == "button" and name and a.get("type", "submit") == "submit":
            self.buttons.append((name, a.get("value", ""), a.get("class", "")))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "form":
            self.in_form = False

    def answers(self):
        """One random, valid set of answers; about one question in ten stays empty."""
        data = dict(self.hidden)
        skip = lambda: random.random() < 0.1  # noqa: E731
        for name, values in self.radios.items():
            if not skip():
                data[name] = random.choice(values)
        for name, values in self.checks.items():
            if re.match(r"^i\d+_c_", name):  # confirmations of the consent: all ticked
                data[name] = values[0]
            elif not skip():
                data[name] = random.sample(values, k=random.randint(1, min(2, len(values))))
        for name, values in self.selects.items():
            if values and not skip():
                data[name] = random.choice(values)
        for name, kind, a in self.texts:
            if skip():
                continue
            numeric = a.get("inputmode") in ("numeric", "decimal") or kind == "number"
            if kind == "date":
                data[name] = "2026-10-01"
            elif kind == "time":
                data[name] = "12:30"
            elif numeric:
                data[name] = str(random.randint(18, 80))
            else:
                data[name] = "Lasttest"
        for name in self.areas:
            if not skip():
                data[name] = "Eine kurze Antwort aus dem Lasttest."
        return data

    def button(self):
        """The button a person presses: agree to the consent, else next or send; None on the last page."""
        for name, value, css in self.buttons:
            if "consent-agree" in css:
                return name, value
        for name, value, _ in self.buttons:
            if name == "nav" and value not in ("back",):
                return name, value
        return None


class Participant(HttpUser):
    wait_time = between(1, 3)  # before a new participation

    def on_start(self):
        if not CODE:
            raise SystemExit("LASTTEST_CODE fehlt: der Code der Befragung (der Teil nach /s/ im Link).")

    @task
    def take_part(self):
        self.client.cookies.clear()  # a new person with an empty browser
        url, started = f"/s/{CODE}", time.monotonic()
        with self.client.get(url, name="Seite 1", catch_response=True) as r:
            if r.status_code != 200 or "<form" not in r.text:
                r.failure(f"Start: {r.status_code}")
                return
            html = r.text
        for page in range(2, MAX_PAGES + 1):
            form = Form(html)
            press = form.button()
            if press is None:
                self._done(started)
                return
            time.sleep(random.uniform(*PAUSE))  # reading and answering
            data = form.answers()
            data[press[0]] = press[1]
            # a browser sends the page it comes from; over https the CSRF check wants it (Flask-WTF, strict referrer)
            with self.client.post(url, data=data, name=f"Seite {page}", headers={"Referer": self.host.rstrip("/") + url},
                                  catch_response=True) as r:
                if r.status_code != 200:
                    r.failure(f"Seite {page}: {r.status_code}")
                    return
                if "has-error" in r.text or "p-end-error" in r.text:
                    r.failure(f"Seite {page}: Antworten nicht angenommen")
                    return
                html = r.text
        events.request.fire(request_type="TEILNAHME", name="ohne Ende abgebrochen", response_time=0,
                            response_length=0, exception=RuntimeError("zu viele Seiten"), context={})

    def _done(self, started):
        events.request.fire(request_type="TEILNAHME", name="Teilnahme abgeschlossen",
                            response_time=(time.monotonic() - started) * 1000, response_length=0,
                            exception=None, context={})


class Stages(LoadTestShape):
    """LASTTEST_STUFEN participants at once, each stage LASTTEST_MINUTEN long; new people join at 5 per second."""

    def tick(self):
        run = self.get_run_time()
        stage = int(run // (MINUTES * 60))
        if stage >= len(STAGES):
            return None
        return STAGES[stage], 5
