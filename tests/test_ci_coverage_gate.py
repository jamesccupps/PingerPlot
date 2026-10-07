"""On the CI Linux legs, the live POSIX tests must run, not skip.

CI opens net.ipv4.ping_group_range so the POSIX backend's live tests execute
against a real kernel. Every one of them is gated on icmp_posix.is_available(),
and that gate is also the only guard: if the sysctl step ever stops taking
effect (a runner image change, a renamed sysctl), they all skip, the job stays
green, and the backend is back to never having run a socket operation in CI --
which is how it shipped before 1.3.1. The kernel receive-timestamp path, which
only a Linux kernel can exercise, would go with them.

`-rs` lists skips in the log, but a green tick does not make anyone read it.
This turns "skipped" into a red build on exactly the legs where it matters.
"""
import os
import sys

import pytest

from pingerplot import icmp_posix

ON_CI_LINUX = os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform.startswith("linux")


@pytest.mark.skipif(not ON_CI_LINUX, reason="only meaningful on the CI Linux legs")
def test_the_live_icmp_tests_are_not_silently_skipped():
    assert icmp_posix.is_available(), icmp_posix.unavailable_reason()


@pytest.mark.skipif(not ON_CI_LINUX, reason="only meaningful on the CI Linux legs")
def test_kernel_receive_timestamps_are_in_use():
    """GitHub's runners are x86_64, which the SO_TIMESTAMPNS mapping covers. If
    this is None the timing fix is inert there and the busy-thread live test is
    measuring the user-space clock."""
    assert icmp_posix._SO_TIMESTAMPNS is not None
