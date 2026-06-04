"""ICMP echo via the Windows IP Helper API (iphlpapi.dll).

Uses ``IcmpCreateFile`` / ``IcmpSendEcho`` so no administrator privileges,
raw sockets, or Npcap are needed. The TTL field of ``IP_OPTION_INFORMATION``
lets us probe individual hops: a router that drops the packet for TTL expiry
answers with an ICMP "TTL exceeded in transit" message, and IcmpSendEcho
surfaces that as status ``IP_TTL_EXPIRED_TRANSIT`` with the router's address.
That is exactly the primitive a traceroute needs.

IPv4 only. The address round-trips below assume a little-endian host (x86/x64),
which is every Windows machine this targets.
"""
from __future__ import annotations

import ctypes
import socket
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

# --- ICMP status codes (subset of the IP_STATUS_BASE range) ----------------
IP_SUCCESS = 0
IP_BUF_TOO_SMALL = 11001
IP_DEST_NET_UNREACHABLE = 11002
IP_DEST_HOST_UNREACHABLE = 11003
IP_DEST_PROT_UNREACHABLE = 11004
IP_DEST_PORT_UNREACHABLE = 11005
IP_NO_RESOURCES = 11006
IP_BAD_OPTION = 11007
IP_HW_ERROR = 11008
IP_PACKET_TOO_BIG = 11009
IP_REQ_TIMED_OUT = 11010
IP_BAD_ROUTE = 11012
IP_TTL_EXPIRED_TRANSIT = 11013
IP_TTL_EXPIRED_REASSEM = 11014

_STATUS_TEXT = {
    IP_SUCCESS: "ok",
    IP_DEST_NET_UNREACHABLE: "net unreachable",
    IP_DEST_HOST_UNREACHABLE: "host unreachable",
    IP_DEST_PROT_UNREACHABLE: "protocol unreachable",
    IP_DEST_PORT_UNREACHABLE: "port unreachable",
    IP_NO_RESOURCES: "no resources",
    IP_BAD_OPTION: "bad option",
    IP_HW_ERROR: "hardware error",
    IP_PACKET_TOO_BIG: "packet too big",
    IP_REQ_TIMED_OUT: "timed out",
    IP_BAD_ROUTE: "bad route",
    IP_TTL_EXPIRED_TRANSIT: "ttl expired (hop)",
    IP_TTL_EXPIRED_REASSEM: "ttl expired (reassembly)",
}


def status_text(status: int) -> str:
    return _STATUS_TEXT.get(status, f"status {status}")


@dataclass(frozen=True)
class PingResult:
    """Outcome of a single ICMP echo probe."""

    status: int
    rtt_ms: Optional[float]  # round-trip time; None on timeout / error
    address: Optional[str]   # IPv4 of whoever answered (router or destination)
    reached: bool            # True only when the final destination replied

    @property
    def responded(self) -> bool:
        """True if *anything* answered (a hop or the destination)."""
        return self.address is not None and self.status in (
            IP_SUCCESS,
            IP_TTL_EXPIRED_TRANSIT,
        )


# --- Win32 structures ------------------------------------------------------
class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),  # PUCHAR
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", ctypes.c_uint32),        # IPAddr, network byte order
        ("Status", ctypes.c_ulong),
        ("RoundTripTime", ctypes.c_ulong),   # milliseconds
        ("DataSize", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


_AVAILABLE = sys.platform == "win32"
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

if _AVAILABLE:
    _iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

    _IcmpCreateFile = _iphlpapi.IcmpCreateFile
    _IcmpCreateFile.restype = wintypes.HANDLE
    _IcmpCreateFile.argtypes = []

    _IcmpCloseHandle = _iphlpapi.IcmpCloseHandle
    _IcmpCloseHandle.restype = wintypes.BOOL
    _IcmpCloseHandle.argtypes = [wintypes.HANDLE]

    _IcmpSendEcho = _iphlpapi.IcmpSendEcho
    _IcmpSendEcho.restype = wintypes.DWORD
    _IcmpSendEcho.argtypes = [
        wintypes.HANDLE,                          # IcmpHandle
        ctypes.c_uint32,                          # DestinationAddress (IPAddr)
        ctypes.c_char_p,                          # RequestData
        wintypes.WORD,                            # RequestSize
        ctypes.POINTER(IP_OPTION_INFORMATION),    # RequestOptions
        ctypes.c_void_p,                          # ReplyBuffer
        wintypes.DWORD,                           # ReplySize
        wintypes.DWORD,                           # Timeout (ms)
    ]


DEFAULT_PAYLOAD = b"PingerPlot-probe.."  # 18 bytes (fallback; the monitor builds its own)


def _ip_to_uint32(ip: str) -> int:
    """Dotted IPv4 -> 32-bit int laid out as the API's network-order IPAddr."""
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def _uint32_to_ip(addr: int) -> str:
    """Inverse of :func:`_ip_to_uint32`."""
    return socket.inet_ntoa(struct.pack("<I", addr & 0xFFFFFFFF))


def is_available() -> bool:
    """True when the Windows ICMP backend can be used."""
    return _AVAILABLE


def ping(
    dest_ip: str,
    ttl: int = 128,
    timeout_ms: int = 1000,
    payload: bytes = DEFAULT_PAYLOAD,
) -> PingResult:
    """Send one ICMP echo to ``dest_ip`` with the given ``ttl``.

    ``dest_ip`` must already be a dotted IPv4 literal (resolve hostnames first).
    A short ``ttl`` is how we coax intermediate routers into identifying
    themselves for traceroute.
    """
    if not _AVAILABLE:
        raise RuntimeError("ICMP backend requires Windows (iphlpapi.dll)")

    handle = _IcmpCreateFile()
    if not handle or handle == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "IcmpCreateFile failed")
    try:
        dest = _ip_to_uint32(dest_ip)
        opts = IP_OPTION_INFORMATION(
            Ttl=max(1, min(int(ttl), 255)), Tos=0, Flags=0, OptionsSize=0, OptionsData=None
        )
        # Reply buffer must hold an ICMP_ECHO_REPLY, the echoed payload, and
        # room for an embedded ICMP error message. Allocate generously.
        reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(payload) + 8 + 128
        reply_buf = ctypes.create_string_buffer(reply_size)

        n = _IcmpSendEcho(
            handle,
            dest,
            payload,
            len(payload),
            ctypes.byref(opts),
            reply_buf,
            reply_size,
            int(timeout_ms),
        )
        if n == 0:
            # No reply at all: timeout or a send-side error. GetLastError holds
            # an IP_STATUS code in the same range as reply.Status.
            err = ctypes.get_last_error()
            return PingResult(err or IP_REQ_TIMED_OUT, None, None, False)

        reply = ctypes.cast(reply_buf, ctypes.POINTER(ICMP_ECHO_REPLY)).contents
        status = int(reply.Status)
        address = _uint32_to_ip(reply.Address) if reply.Address else None
        rtt = float(reply.RoundTripTime)

        if status == IP_SUCCESS:
            return PingResult(status, rtt, address, True)
        if status == IP_TTL_EXPIRED_TRANSIT:
            return PingResult(status, rtt, address, False)
        # Unreachable / other: an address may still be present (the router that
        # reported the problem), but there is no meaningful RTT to record.
        return PingResult(status, None, address, False)
    finally:
        _IcmpCloseHandle(handle)
