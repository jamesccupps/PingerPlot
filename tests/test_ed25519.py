"""ed25519.py: RFC 8032 vectors for signing and verifying, rejection of
anything altered, and agreement with the ``cryptography`` package when it
happens to be installed (it is not a dependency of anything here)."""
import os

import pytest

from pingerplot import ed25519

# RFC 8032, section 7.1, tests 1-3: (secret key, public key, message, signature).
RFC = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
     "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
     "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18"
     "ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("seed,pub,msg,sig", RFC)
def test_rfc8032_public_key(seed, pub, msg, sig):
    assert ed25519.public_key(bytes.fromhex(seed)).hex() == pub


@pytest.mark.parametrize("seed,pub,msg,sig", RFC)
def test_rfc8032_signature(seed, pub, msg, sig):
    assert ed25519.sign(bytes.fromhex(seed), bytes.fromhex(msg)).hex() == sig


@pytest.mark.parametrize("seed,pub,msg,sig", RFC)
def test_rfc8032_verifies(seed, pub, msg, sig):
    assert ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))


@pytest.mark.parametrize("seed,pub,msg,sig", RFC)
def test_any_flipped_bit_fails(seed, pub, msg, sig):
    sig = bytes.fromhex(sig)
    for i in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[i] ^= 0x01
        assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(bad))
    assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg) + b"!", sig)


def test_another_key_fails():
    _, _, msg, sig = RFC[0]
    other = RFC[1][1]
    assert not ed25519.verify(bytes.fromhex(other), bytes.fromhex(msg), bytes.fromhex(sig))


def test_non_canonical_s_is_rejected():
    """S + L verifies the same equation; RFC 8032 requires it be refused, or
    one signature has many encodings."""
    _, pub, msg, sig = RFC[0]
    sig = bytearray.fromhex(sig)
    s = int.from_bytes(sig[32:], "little") + ed25519._L
    sig[32:] = s.to_bytes(32, "little")
    assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(sig))


@pytest.mark.parametrize("pub,sig", [(b"", b"x" * 64), (b"x" * 31, b"x" * 64),
                                     (b"x" * 32, b"x" * 63), ("str", b""), (None, None),
                                     (b"\xff" * 32, b"\x00" * 64)])
def test_malformed_inputs_fail_cleanly(pub, sig):
    assert ed25519.verify(pub, b"m", sig) is False


@pytest.mark.parametrize("seed", [b"", b"x" * 31, b"x" * 33, "x" * 32, None])
def test_a_bad_private_key_is_an_error(seed):
    with pytest.raises(ValueError):
        ed25519.sign(seed, b"m")


def test_sign_then_verify_roundtrip():
    for i in range(10):
        seed, msg = os.urandom(32), os.urandom(i * 37)
        sig = ed25519.sign(seed, msg)
        assert len(sig) == 64
        assert ed25519.verify(ed25519.public_key(seed), msg, sig)


def test_agrees_with_cryptography():
    """Byte-for-byte: the same seed gives the same public key and signature
    (Ed25519 signing is deterministic), and each verifies the other's."""
    crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization

    raw = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    for i in range(20):
        seed, msg = os.urandom(32), os.urandom(i * 11)
        k = crypto.Ed25519PrivateKey.from_private_bytes(seed)
        pub = k.public_key().public_bytes(*raw)
        assert ed25519.public_key(seed) == pub
        assert ed25519.sign(seed, msg) == k.sign(msg)
        assert ed25519.verify(pub, msg, k.sign(msg))
