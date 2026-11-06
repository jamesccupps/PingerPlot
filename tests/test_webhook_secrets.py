"""A webhook's path is its secret (Slack, Teams, ntfy): an error about the
URL must not repeat it, since errors go into events, the console and saved
sessions. The 2026-11-05 review found a URL with a space did exactly that
(http.client's InvalidURL quotes the path)."""
import urllib.error

import pytest

from pingerplot import monitor

URL = "https://hooks.example.com/services/T000/B000/XXSECRETXX"


@pytest.mark.parametrize("bad", [URL + " trailing", URL.replace("T000", "T0\t00"),
                                 URL + "\n", URL + "\x7f"])
def test_whitespace_and_control_characters_are_refused_without_the_path(bad):
    why = monitor._send_webhook(bad, {"event": "test"})
    assert why == "the URL contains a space or a control character"


def test_an_error_quoting_the_url_is_scrubbed(monkeypatch):
    def fail(req, timeout=None):
        raise urllib.error.URLError(f"something about {req.full_url} and /services/T000/B000/XXSECRETXX")
    monkeypatch.setattr(monitor.urllib.request, "urlopen", fail)
    why = monitor._send_webhook(URL + "?token=abc", {"event": "test"})
    assert "XXSECRETXX" not in why and "token=abc" not in why
    assert "hooks.example.com" not in why or "/..." in why


def test_a_good_url_is_still_sent(monkeypatch):
    sent = []

    class Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(monitor.urllib.request, "urlopen",
                        lambda req, timeout=None: sent.append(req.full_url) or Resp())
    assert monitor._send_webhook(URL, {"event": "test"}) is None and sent == [URL]
