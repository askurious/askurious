"""Minimise recognisable contact details before AI calls; this is not complete anonymisation."""
import re
import secrets

EMAIL = re.compile(r"(?<![\w@])[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
PHONE = re.compile(r"(?<!\w)(?:\+|00)?\d(?:[\d ()/.-]{5,}\d)(?!\w)")


def contact_details(text, replace):
    text = URL.sub(lambda m: replace(m.group(), "URL"), text)
    text = EMAIL.sub(lambda m: replace(m.group(), "EMAIL"), text)
    return PHONE.sub(lambda m: replace(m.group(), "PHONE") if 7 <= sum(c.isdigit() for c in m.group()) <= 15
                     else m.group(), text)


def redact(text):
    return contact_details(text if isinstance(text, str) else "", lambda value, kind: f"[{kind}]")


class TranslationMask:
    """Reversible, call-specific placeholders. The mapping never leaves the server."""
    def __init__(self, survey):
        from app.items import get

        self.values = {}
        self.prefix = "__ASKURI_" + secrets.token_hex(8) + "_"
        self.people = set()
        for member in getattr(survey, "members", []):
            self.people.update(value for value in (member.display_name, member.affiliation) if value)
            if member.user:
                self.people.update(value for value in (member.user.name, member.user.affiliation) if value)
        for block in survey.blocks:
            for item in block.items:
                if item.type == "consent":
                    _, real = get(item.type).masked_facts(item, survey.default_language)
                    self.people.update(value for value in real.values() if value)

        from html import escape

        self.people.update(escape(value, quote=True) for value in list(self.people))

    def _keep(self, value, kind="PERSON"):
        for token, known in self.values.items():
            if known == value:
                return token
        token = f"{self.prefix}{len(self.values)}__"
        self.values[token] = value
        return token

    def mask(self, text):
        # Longest first: replacing a person's short name must not expose the rest of their affiliation.
        for value in sorted(self.people, key=len, reverse=True):
            text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", lambda m: self._keep(m.group()), text)
        return contact_details(text, self._keep)

    def restore(self, text, source):
        expected = {token for token in self.values if token in source}
        found = set(re.findall(re.escape(self.prefix) + r"\d+__", text))
        if expected != found or any(text.count(token) != source.count(token) for token in expected):
            return None
        for token in expected:
            text = text.replace(token, self.values[token])
        return text
