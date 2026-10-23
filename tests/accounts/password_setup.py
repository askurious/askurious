"""Exercise mailbox confirmation when a test adds self-service password access."""
import re
from tests.conftest import mail_on


def confirmed_password(app, client, data):
    sent = mail_on(app)
    page = client.post("/account/password", data=data, follow_redirects=True)
    assert "Bestätigen Sie die Anmeldeadresse" in page.data.decode()
    link = re.search(r"(/account/confirm-email/[\w-]+)", sent[-1].get_content())[1]
    return client.post(link, follow_redirects=True)
