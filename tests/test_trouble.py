"""Where the trouble is (trouble.py): loss that carries to the destination,
delay added by each place, worst first."""
from types import SimpleNamespace as H

from pingerplot import trouble


def hop(ttl, place, avg, loss=0.0):
    return H(ttl=ttl, place=place, avg=avg, loss_pct=loss)


def _rank(hops):
    return trouble.rank(hops, lambda h: h.place)


def test_loss_that_stops_at_a_router_is_not_trouble():
    """The router at hop 2 ignores half its pings; everything after is fine."""
    rows = _rank([hop(1, "Portland", 1), hop(2, "Boston", 9, loss=50), hop(3, "Chicago", 30),
                  hop(4, "Chicago", 31)])
    assert all(r.loss_starts == 0 for r in rows)


def test_loss_that_reaches_the_destination_is_pinned_where_it_starts():
    rows = _rank([hop(1, "Portland", 1), hop(2, "Boston", 9), hop(3, "Denver", 60, loss=12),
                  hop(4, "Denver", 61, loss=15)])
    worst = rows[0]
    # 12% starts at hop 3 and 3% more at hop 4: 15% of the loss begins in Denver.
    assert worst.place == "Denver" and worst.loss_starts == 15 and worst.ttls == [3, 4]
    assert trouble.verdict(rows).startswith("Loss reaching the destination starts in Denver (hop 3, 15%)")


def test_delay_is_credited_to_the_place_it_was_added():
    rows = _rank([hop(1, "Portland", 2), hop(2, "Boston", 9), hop(3, "Chicago", 38),
                  hop(4, "Chicago", 39), hop(5, "Denver", 52)])
    by = {r.place: r for r in rows}
    assert by["Chicago"].delay_added == 30 and by["Denver"].delay_added == 13
    assert rows[0].place == "Chicago"
    assert trouble.verdict(rows) == "The most delay is added in Chicago (+30 ms)."


def test_a_slow_answering_router_adds_no_negative_delay():
    rows = _rank([hop(1, "A", 5), hop(2, "B", 80), hop(3, "C", 20)])
    assert {r.place: r.delay_added for r in rows} == {"A": 5, "B": 75, "C": 0}


def test_silent_and_unplaced_hops_still_count_for_their_neighbours():
    rows = _rank([hop(1, "A", 5), hop(2, None, None), hop(3, None, 30), hop(4, "D", 32)])
    assert {r.place: r.delay_added for r in rows} == {"A": 5, "D": 2}


def test_nothing_to_say():
    assert trouble.rank([], lambda h: "x") == []
    assert trouble.verdict([]) == ""
    calm = _rank([hop(1, "A", 1), hop(2, "B", 4)])
    assert trouble.verdict(calm) == "Nothing on the path stands out."


def test_carried_loss():
    assert trouble.carried_loss([0, 50, 0, 10]) == [0, 0, 0, 10]
    assert trouble.carried_loss([5, 20, 12, 15]) == [5, 12, 12, 15]
