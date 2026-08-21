"""No real network addresses in the repository.

This is a public repo for a tool whose whole job is capturing the topology of
whatever network it is pointed at. Pasting a real trace into the README or a
test is a one-keystroke mistake, and it publishes an ISP's edge routers and an
internal gateway together — enough to identify a site.

It happened while writing 1.3.0: a live comparison against a real path went
into the README with the ISP's first two hops and the LAN gateway intact. It
was caught before it reached a commit, but only because a scan was run; the
scan is now this test, so the next one fails CI instead of relying on someone
remembering.

Allowed: RFC 5737 documentation ranges, RFC 1918 private space, loopback,
link-local, multicast, the unspecified and broadcast addresses, and the handful
of well-known public resolvers used as example targets. Anything else is
presumed real until someone widens this list on purpose.
"""
import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Well-known public resolvers, fine as example targets in docs and tests.
EXAMPLE_TARGETS = {"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9"}

# Values that are addresses only incidentally: byte-order fixtures and the like.
ENCODING_FIXTURES = {"1.0.0.0", "0.0.0.2", "1.2.3.4", "255.254.253.252"}

SCANNED_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json",
                    ".cfg", ".ini", ".txt", ".ps1", ".cmd", ".bat", ".vbs"}

# RFC 5737, spelled out. CPython happens to fold these into is_private, but that
# is an implementation detail of the stdlib's private-network table, not part of
# the documented API — and a guard that silently stops guarding is worse than no
# guard, so it is not relied on.
DOC_NETS = tuple(ipaddress.IPv4Network(n) for n in
                 ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def _tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    for name in out.stdout.splitlines():
        path = REPO / name
        if path.suffix.lower() in SCANNED_SUFFIXES and path.is_file():
            yield name, path


def _is_allowed(text: str) -> bool:
    if text in EXAMPLE_TARGETS or text in ENCODING_FIXTURES:
        return True
    try:
        addr = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        return True          # not actually an address (a version, a timestamp)
    if any(addr in net for net in DOC_NETS):
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def test_no_real_public_addresses_are_committed():
    offenders = []
    for name, path in _tracked_files():
        if name == "tests/test_no_real_addresses.py":
            continue          # this file names the allowed set
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for found in IPV4.findall(line):
                if not _is_allowed(found):
                    offenders.append(f"{name}:{lineno}: {found}")
    assert not offenders, (
        "real-looking public IP addresses are committed:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse RFC 5737 documentation space (192.0.2.0/24, 198.51.100.0/24, "
          "203.0.113.0/24) for examples instead."
    )


def test_the_documentation_ranges_are_recognised():
    """Guards the guard: if is_documentation ever stopped covering these, the
    test above would pass by being blind rather than by the repo being clean."""
    for ip in ("192.0.2.1", "198.51.100.7", "203.0.113.9"):
        assert _is_allowed(ip), ip


def test_a_real_address_would_be_caught():
    for ip in ("144.121.8.209", "160.72.252.38", "104.16.132.229"):
        assert not _is_allowed(ip), f"{ip} slipped through the filter"
