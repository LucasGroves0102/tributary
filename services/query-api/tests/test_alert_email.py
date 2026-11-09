"""Email for filing alerts (query_api/alert_email.py): off unless configured, plain SMTP with
TLS when it is, and the job sends nothing without a verified recipient. No network: the SMTP
connection is a fake."""

from query_api.alert_email import SmtpEmail, digest, email_from_env, no_recipients


class FakeSmtp:
    made: list["FakeSmtp"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = self.login_as = None
        self.sent = []
        FakeSmtp.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, context=None):
        self.tls = context

    def login(self, user, password):
        self.login_as = user

    def send_message(self, msg):
        self.sent.append(msg)


def test_email_is_off_unless_configured():
    assert email_from_env({}) is None
    assert email_from_env({"ALERT_SMTP_HOST": "smtp.example.org"}) is None
    on = email_from_env({"ALERT_SMTP_HOST": "smtp.example.org",
                         "ALERT_SMTP_FROM": "alerts@example.org", "ALERT_SMTP_USER": "u",
                         "ALERT_SMTP_PASSWORD": "not-a-real-password"})
    assert isinstance(on, SmtpEmail) and on.port == 587
    assert "not-a-real-password" not in repr(on)  # never printed with the provider
    assert no_recipients("anyone") is None


def test_smtp_sends_over_tls():
    FakeSmtp.made = []
    mail = SmtpEmail(host="smtp.example.org", sender="alerts@example.org", username="u",
                     password="not-a-real-password", connect=FakeSmtp)
    mail.send("me@example.org", "Tributary: 1 new filing alert", "NVIDIA filed a 10-Q")
    (smtp,) = FakeSmtp.made
    assert smtp.tls is not None and smtp.login_as == "u" and smtp.timeout == 10.0
    (msg,) = smtp.sent
    assert (msg["To"], msg["From"]) == ("me@example.org", "alerts@example.org")
    assert "NVIDIA filed a 10-Q" in msg.get_content()


def test_digest_is_one_message():
    subject, body = digest(["one", "two"])
    assert subject == "Tributary: 2 new filing alerts"
    assert body.startswith("one\n\n---\n\ntwo") and "Settings" in body
