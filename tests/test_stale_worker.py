"""A worker from a superseded start() must not write into the new run.

stop() joins the worker for timeout + 2 s and then moves on whether or not it
exited. Probes are bounded by the reply timeout, so the join normally wins --
but name resolution is not bounded by anything the app controls. Press
Add / Start again on a target stuck at "Resolving..." (DNS down is exactly when
people do that) and the old worker outlives the join. When its lookup finally
returned it used to:

  * overwrite target_ip with its own answer,
  * run _trace, whose loop exits at once on the stale generation but whose
    unconditional trim then did ``del self._hops[0:]`` -- wiping the new run's
    hops -- and set route_len to 0 underneath it.

_apply_probe already dropped stale results; these are the writes that did not.
"""
from pingerplot import monitor as monitor_mod
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


def _new_run_state(m):
    m.running = True
    m._generation = 2
    m._hops = [Hop(1), Hop(2), Hop(3)]
    m.route_len = 3


def test_a_superseded_trace_does_not_trim_the_new_runs_hops():
    m = Monitor()
    _new_run_state(m)
    assert m._trace(1) == 0
    assert len(m._hops) == 3 and m.route_len == 3
    m.shutdown()


def test_a_superseded_resolve_does_not_overwrite_target_ip(monkeypatch):
    m = Monitor()
    m.target_input = "slow.example.net"
    m.running = True
    m._generation = 1

    def resolve_while_a_new_run_starts(_name):
        # start() lands while this lookup is still blocked
        _new_run_state(m)
        m.target_ip = "198.51.100.7"
        return "203.0.113.99"

    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", resolve_while_a_new_run_starts)
    m._run(1)
    assert m.target_ip == "198.51.100.7"
    assert len(m._hops) == 3 and m.route_len == 3
    assert m.running, "the stale worker must not clear the new run's flag"
    m.running = False
    m.shutdown()


def test_a_superseded_shrink_or_grow_leaves_the_route_alone():
    m = Monitor()
    _new_run_state(m)
    assert m._shrink_route(3, 1, gen=1) == 3
    assert len(m._hops) == 3 and m.route_len == 3
    assert not m.events_after(-1)[0], "no route event for a change that did not happen"
    m.shutdown()
