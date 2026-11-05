"""packaging/update_test_helper.py's question to GitHub, "which release came
before this build?": tried again when GitHub hiccups, and a failure that
lasts is a failure, never an empty answer (which test_update.ps1 reads as
"no earlier release", and quietly tests the build updating itself instead).
No network: urlopen is faked."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))
import update_test_helper as helper  # noqa: E402

sys.path.pop(0)

RELEASES = [
    {"tag_name": "v1.4.1", "assets": [{"name": "latest.json"}]},
    {"tag_name": "v1.4.0", "assets": [{"name": "latest.json"}]},
    {"tag_name": "v1.3.3", "assets": [{"name": "PingerPlot.exe"}]},
]


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(monkeypatch, *outcomes):
    """urlopen gives each outcome in turn: an exception to raise, or JSON."""
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return Answer(json.dumps(out).encode())
    monkeypatch.setattr(helper.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(helper.time, "sleep", lambda s: None)
    return calls


def _http(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "x", {}, io.BytesIO())


def test_a_5xx_is_tried_again(monkeypatch, capsys):
    calls = _fake(monkeypatch, _http(504), urllib.error.URLError("timed out"), RELEASES)
    helper.previous()
    out = capsys.readouterr()
    assert out.out.strip() == "1.4.1" and len(calls) == 3
    assert "trying again" in out.err


def test_a_failure_that_lasts_raises_and_prints_nothing(monkeypatch, capsys):
    _fake(monkeypatch, *[_http(503)] * 4)
    with pytest.raises(urllib.error.HTTPError):
        helper.previous()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("code", [401, 403, 404])
def test_other_errors_fail_at_once(monkeypatch, code):
    calls = _fake(monkeypatch, _http(code), RELEASES)
    with pytest.raises(urllib.error.HTTPError):
        helper.previous()
    assert len(calls) == 1


def test_the_ps1_treats_a_failed_question_as_a_failure():
    ps1 = (REPO / "packaging" / "test_update.ps1").read_text(encoding="utf-8")
    ask = ps1.index("$helper previous")
    assert "$LASTEXITCODE -ne 0" in ps1[ask:ask + 400]
    assert ps1.index("throw \"couldn't ask GitHub") < ps1.index("$From = 'self'")
