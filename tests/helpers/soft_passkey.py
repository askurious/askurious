"""A passkey in software for the tests of app/passkeys.py: an ES256 key pair (cryptography) that answers the options
of the server as a browser with its device would (navigator.credentials.create and .get), packed with the data
classes of fido2. Each switch makes one thing wrong, so a test can ask for exactly that refusal."""
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2.cose import ES256
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import Aaguid, AttestationObject, AttestedCredentialData, AuthenticatorData, CollectedClientData

FLAG = AuthenticatorData.FLAG


class SoftPasskey:
    def __init__(self, origin="http://localhost:5060", synced=False):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.origin = origin
        self.rp_id = origin.split("://", 1)[1].split(":")[0]
        self.synced = synced
        self.counter = 0
        self.user_handle = None

    def _flags(self, verified):
        flags = FLAG.UP | (FLAG.UV if verified else 0)
        return flags | (FLAG.BE | FLAG.BS if self.synced else 0)

    def _rp_hash(self):
        return hashlib.sha256(self.rp_id.encode()).digest()

    def create(self, options, verified=True, origin=None):
        """The answer to creation options ({"challenge", "user", ...}, bytes as base64url)."""
        client = CollectedClientData.create("webauthn.create", options["challenge"], origin or self.origin)
        credential = AttestedCredentialData.create(Aaguid.NONE, self.credential_id,
                                                   ES256.from_cryptography_key(self.key.public_key()))
        data = AuthenticatorData.create(self._rp_hash(), self._flags(verified) | FLAG.AT, self.counter, credential)
        self.user_handle = websafe_decode(options["user"]["id"])
        return {"id": websafe_encode(self.credential_id), "rawId": websafe_encode(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": websafe_encode(client),
                             "attestationObject": websafe_encode(AttestationObject.create("none", data, {}))}}

    def get(self, options, verified=True, origin=None, counter=None):
        """The answer to request options: a signature over the one-time value. counter: the device's counter for
        this answer (the default counts up; synchronised passkeys always say 0)."""
        if counter is None:
            self.counter = 0 if self.synced else self.counter + 1
            counter = self.counter
        client = CollectedClientData.create("webauthn.get", options["challenge"], origin or self.origin)
        data = AuthenticatorData.create(self._rp_hash(), self._flags(verified), counter)
        signature = self.key.sign(bytes(data) + client.hash, ec.ECDSA(hashes.SHA256()))
        response = {"clientDataJSON": websafe_encode(client), "authenticatorData": websafe_encode(data),
                    "signature": websafe_encode(signature)}
        if self.user_handle:
            response["userHandle"] = websafe_encode(self.user_handle)
        return {"id": websafe_encode(self.credential_id), "rawId": websafe_encode(self.credential_id),
                "type": "public-key", "clientExtensionResults": {}, "response": response}


def add(client, passkey, name="Laptop", **proof):
    """Add `passkey` to the account `client` is signed in as; returns the two answers of the server."""
    first = client.post("/account/passkeys/add/options", json={"name": name, **proof})
    if not (first.get_json() or {}).get("ok"):
        return first, None
    return first, client.post("/account/passkeys/add", json={"credential": passkey.create(first.get_json()["publicKey"])})


def sign_in(client, passkey, **switches):
    """A sign-in of `client` with `passkey`; switches go to SoftPasskey.get (verified, origin, counter)."""
    options = client.post("/account/passkeys/sign-in/options", json={}).get_json()["publicKey"]
    return client.post("/account/passkeys/sign-in", json={"credential": passkey.get(options, **switches)})
