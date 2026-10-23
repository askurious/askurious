"""Public dashboard/quota links: hash for lookup, encrypted copy for the authorised designer."""
import hashlib


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SharedLinkToken:
    @property
    def token(self):
        from app.secrets_box import decrypt

        return decrypt(self.token_box)

    @token.setter
    def token(self, value):
        from app.secrets_box import encrypt

        self.token_hash = digest(value)
        self.token_box = encrypt(value)


def find(model, token):
    from app.extensions import db

    if not isinstance(token, str) or not token or len(token) > 64:
        return None
    return db.session.scalar(db.select(model).filter_by(token_hash=digest(token)))
