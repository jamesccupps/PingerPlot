"""The History view's questions, answered from a hand-made history.

Ten minutes on route 1 (hop 2 = R2, destination 10 ms, no loss), then ten
on route 2 (hop 2 = R3, destination 40 ms, half the probes lost). Hop 1 is
load-balanced the whole time."""
import pytest

from pingerplot import investigate
from pingerplot.history import Reader, Recorder

T0 = 1_700_000_040.0           # a minute boundary
R1a, R1b, R2, R3 = "198.51.100.1", "198.51.100.11", "198.51.100.2", "198.51.100.3"
DEST = "203.0.113.9"


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def reader(tmp_path_factory):
    folder = tmp_path_factory.mktemp("hist")
    clock = Clock()
    rec = Recorder(folder, clock=clock).start()
    run = rec.begin_run("svc.example.com", "icmp", None, {})
    rec.set_target_ip(run, DEST)
    rec.route(run, T0, [[R1a, R1b], [R2], [DEST]])
    for i in range(1200):                         # 20 minutes, a round a second
        t = T0 + i
        clock.t = t
        second_half = i >= 600
        if i == 600:
            rec.route(run, t, [[R1a, R1b], [R3], [DEST]])
        rec.probe(run, t, i, 1, R1a if i % 2 else R1b, 1.0, 0)
        rec.probe(run, t, i, 2, R3 if second_half else R2, 5.0, 0)
        lost = second_half and i % 2 == 0
        rec.probe(run, t, i, 3, None if lost else DEST, None if lost else (40.0 if second_half else 10.0),
                  11010 if lost else 0)
    clock.t = T0 + 1300
    rec.end_run(run)
    rec.close()
    r = Reader(folder)
    yield r
    r.close()


def test_the_destination_series_shows_the_change(reader):
    pts = investigate.series(reader, "svc.example.com", T0, T0 + 1199, buckets=20)
    assert len(pts) == 20 and pts[0].t == T0
    assert [p.avg for p in pts[:10]] == [10.0] * 10
    assert [p.avg for p in pts[10:]] == [40.0] * 10
    assert pts[0].loss_pct == 0 and pts[15].loss_pct == 50.0


def test_buckets_merge_minutes(reader):
    pts = investigate.series(reader, "svc.example.com", T0, T0 + 1199, buckets=2)
    assert [p.avg for p in pts] == [10.0, 40.0]
    assert pts[0].sent == 600 and pts[1].recv == 300


def test_one_hops_series(reader):
    pts = investigate.series(reader, "svc.example.com", T0, T0 + 1199, buckets=1, ttl=1)
    (p,) = pts
    assert p.sent == 1200 and p.avg == 1.0 and p.jitter == 0.0


def test_hops_list_every_router_that_answered(reader):
    rows = investigate.hops(reader, "svc.example.com", T0, T0 + 1199)
    assert [h.ttl for h in rows] == [1, 2, 3]
    assert {r.addr for r in rows[0].routers} == {R1a, R1b}
    assert [r.addr for r in rows[1].routers] == [R2, R3] or [r.addr for r in rows[1].routers] == [R3, R2]
    dest = rows[2]
    assert dest.loss_pct == 25.0           # half the probes, for half the time
    assert dest.routers[0].addr == DEST


def test_routes_show_which_path_was_bad(reader):
    rows = investigate.routes(reader, "svc.example.com", T0, T0 + 1200, now=T0 + 1300)
    assert [r.hops[1] for r in rows] == [[R2], [R3]]
    good, bad = rows
    assert good.seconds == pytest.approx(600) and bad.seconds == pytest.approx(600)
    assert good.share == pytest.approx(0.5)
    assert good.dest_avg == 10.0 and good.dest_loss_pct == 0.0
    assert bad.dest_avg == 40.0 and bad.dest_loss_pct == 50.0


def test_a_range_before_anything_was_recorded_is_empty(reader):
    assert investigate.series(reader, "svc.example.com", 0, 1000) == []
    assert investigate.hops(reader, "svc.example.com", 0, 1000) == []
    assert investigate.routes(reader, "svc.example.com", 0, 1000) == []


def test_describe_route():
    assert investigate.describe_route([[R1a, R1b], [], [DEST]], {DEST: "svc"}) == \
        f"{R1a} / {R1b}  >  *  >  svc"
