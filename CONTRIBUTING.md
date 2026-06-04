# Contributing to PingerPlot

Thanks for your interest! PingerPlot is a small, dependency-free Windows network
tool, and contributions that keep it that way are very welcome.

## Ground rules

- **Zero third-party runtime dependencies.** The app is pure Python standard
  library (Tkinter + ctypes + sockets). Please don't add `pip` dependencies for
  the app itself; `pytest` is the only dev dependency.
- **Keep the DLL/Win32 access guarded.** The ICMP backend is Windows-only, but
  the code is structured so the package still *imports* and the test suite still
  *runs* on Linux/macOS (the `ctypes.windll` access is behind
  `sys.platform == "win32"`). Keep it that way so CI can test on any OS.
- **Match the existing style.** PEP 8, type hints where they earn their keep,
  real error handling (no bare `except:` that hides bugs), and comments that
  explain the *why*.

## Dev setup

```bash
git clone https://github.com/jamesccupps/PingerPlot
cd PingerPlot
python -m pip install pytest
python -m pytest          # 56 tests, run on any OS
```

To run the app you need Windows (the ICMP backend is the Win32 IP Helper API):

```powershell
python main.py
```

## Tests

- The pure logic (stats, MOS, address encoding, alert state machine, ICMP-error
  correlation) is unit-tested and runs on any platform — add tests there when you
  change behavior.
- The raw-socket / Tkinter layers need real hardware or a display; exercise them
  by running the app or `python -m pingerplot.selftest <host>`.

## Pull requests

1. Open an issue first for anything non-trivial so we can agree on the approach.
2. Keep PRs focused — one change per PR.
3. Make sure `python -m pytest` passes and the app still launches.
4. Update the README/docstrings if behavior changes.

## Scope and safety

PingerPlot is a **read-only diagnostic** tool: it sends ICMP/TCP-SYN/UDP probes
and reads replies; it never writes to or reconfigures anything on the network.
Please keep contributions within that posture. The TCP/UDP traceroute modes use a
raw capture socket and require Administrator on Windows — that's by design and
documented.
