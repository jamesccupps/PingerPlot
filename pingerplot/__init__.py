"""PingerPlot — a continuous traceroute / latency monitor (MTR-style).

Zero third-party dependencies. IPv4. No administrator rights, raw sockets or
Npcap are required for ICMP: Windows uses the IP Helper API via ctypes, and
Linux/macOS use unprivileged SOCK_DGRAM ICMP sockets (see icmp_posix).
"""

__version__ = "1.3.2"
