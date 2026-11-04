"""Ed25519 (RFC 8032), pure Python: verification for the updater, and signing
for the release build.

The updater needs one operation, checking a release manifest's signature
against the public key built into the app; doing it here keeps the app free of
a compiled crypto dependency. Verification handles only public data, so timing
side channels don't apply to it.

Signing is here too, so that building a release needs nothing beyond the
standard library either (UniFi Stock Watcher signs with the ``cryptography``
package; this project's release job installs only hash-pinned build tools).
Hand-written signing code is usually avoided because Python's big-integer
arithmetic isn't constant-time; that matters when an attacker can time many
signatures. A release is signed once, by a CI job on a throwaway machine or on
the maintainer's PC, so nobody can. The implementation is checked against the
RFC's test vectors and against ``cryptography`` (tests/test_updater.py).

Strict where the RFC is: a non-canonical S (>= L) or a point that does not
decode is a failed verification, not an exception.

Verification adapted from UniFi Stock Watcher, a sibling project.
"""
from __future__ import annotations

import hashlib

__all__ = ["verify", "sign", "public_key"]

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


# Points in extended homogeneous coordinates (X, Y, Z, T): x = X/Z, y = Y/Z,
# x*y = T/Z.

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)                     # neutral element
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q):
    return ((p[0] * q[2] - q[0] * p[2]) % _P == 0
            and (p[1] * q[2] - q[1] * p[2]) % _P == 0)


def _compress(p):
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _expand(seed: bytes):
    if not (isinstance(seed, (bytes, bytearray)) and len(seed) == 32):
        raise ValueError("an Ed25519 private key is 32 bytes")
    h = hashlib.sha512(bytes(seed)).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8                  # clear the low 3 bits and the top bit
    a |= 1 << 254                        # set bit 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte private key (seed)."""
    a, _prefix = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """The 64-byte Ed25519 signature of ``message`` (RFC 8032, 5.1.6)."""
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + bytes(message)).digest(), "little") % _L
    big_r = _compress(_mul(r, _G))
    h = int.from_bytes(hashlib.sha512(big_r + pub + bytes(message)).digest(), "little") % _L
    s = (r + h * a) % _L
    return big_r + int.to_bytes(s, 32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True if ``signature`` is a valid Ed25519 signature of ``message``."""
    if not (isinstance(public_key, (bytes, bytearray)) and len(public_key) == 32):
        return False
    if not (isinstance(signature, (bytes, bytearray)) and len(signature) == 64):
        return False
    a = _decompress(bytes(public_key))
    r_bytes = bytes(signature[:32])
    r = _decompress(r_bytes)
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(
        hashlib.sha512(r_bytes + bytes(public_key) + bytes(message)).digest(),
        "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
