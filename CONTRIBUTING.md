# Contributing to PingerPlot

Thanks for your interest! PingerPlot is a small, dependency-free network path monitor, and contributions that keep it
that way are very welcome. [ROADMAP.md](ROADMAP.md) has what's planned; [CLAUDE.md](CLAUDE.md) has the architecture
and the reasons behind the less obvious decisions.

## Ground rules

- **Zero third-party runtime dependencies.** The app is the Python standard library only (Tkinter, ctypes, sockets).
  Please don't add `pip` dependencies to the app; `pytest` is the only one for development.
- **Keep platform code guarded.** The Windows ICMP backend (`icmp.py`) and the Linux/macOS one (`icmp_posix.py`)
  are chosen at run time, and the package imports and the suite runs on every platform. Keep `ctypes.windll` and
  other platform-only calls behind `sys.platform` checks.
- **Read-only on the network.** PingerPlot sends ICMP, TCP-SYN and UDP probes and reads the replies; it never
  changes anything on the network it measures. Please keep contributions within that.
- **Match the existing style.** PEP 8, type hints where they earn their keep, real error handling (no bare `except:`
  that hides bugs), and comments that explain *why*.

## Setting up

```bash
git clone https://github.com/jamesccupps/PingerPlot
cd PingerPlot
python -m pip install pytest
python -m pytest          # ~30 s; runs on Windows, Linux and macOS
python main.py            # the app
```

On Linux the ICMP backend needs unprivileged ICMP sockets:
`sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"`. TCP/UDP traceroute on Windows needs Administrator.

## Tests

- Add or change tests with any change in behaviour. Each test file starts with a docstring saying what went wrong
  and why the test exists; please keep that up.
- No test may depend on what the network happens to do. The TCP/UDP round loops run against
  `tests/mock_router.py`, and live probes go to loopback only (plus one TTL-1 probe that accepts any answer).
- The GUI tests drive the real `gui.App` (a withdrawn window on Windows, `xvfb-run -a python -m pytest` on Linux)
  and never touch your real settings.
- CI runs the suite on Windows and Linux, Python 3.10 to 3.13, and fails the Linux jobs if the live ICMP tests would
  be skipped.

## Pull requests

1. Open an issue first for anything non-trivial so we can agree on the approach.
2. Keep PRs focused: one change per PR.
3. Make sure `python -m pytest` passes and the app still starts.
4. Update the README, CHANGELOG and docstrings if behaviour changes.

Security problems: please report them privately, as [SECURITY.md](SECURITY.md) describes.
