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


def test_no_loss_is_placed_when_the_destination_never_answers():
    # A relay that drops pings: the last router that answers is rate-limiting
    # its replies, which isn't loss reaching anything.
    hops = [hop(1, "Boston", 4), hop(11, "Ashburn", 16, loss=20), hop(12, "Washington", 21, loss=53),
            hop(13, None, None, loss=100), hop(14, None, None, loss=100)]
    rows = _rank(hops)
    assert all(r.loss_starts == 0 for r in rows)
    assert trouble.destination_silent(hops)
    assert trouble.verdict(rows, True) == ("The destination doesn't answer, so where loss starts can't be "
                                           "told; the most delay is added in Ashburn (+12 ms).")


def test_delay_is_credited_to_the_place_it_was_added():
    rows = _rank([hop(1, "Portland", 2), hop(2, "Boston", 9), hop(3, "Chicago", 38),
                  hop(4, "Chicago", 39), hop(5, "Denver", 52)])
    by = {r.place: r for r in rows}
    assert by["Chicago"].delay_added == 30 and by["Denver"].delay_added == 13
    assert rows[0].place == "Chicago"
    assert trouble.verdict(rows) == "The most delay is added in Chicago (+30 ms)."


def test_a_slow_answering_router_is_credited_only_with_delay_that_carries():
    # B answers its own pings slowly: 80 ms, though C behind it is at 20. Only
    # the 15 ms that reaches C counts, and nothing goes negative.
    rows = _rank([hop(1, "A", 5), hop(2, "B", 80), hop(3, "C", 20)])
    assert {r.place: r.delay_added for r in rows} == {"A": 5, "B": 15, "C": 0}


def test_delay_never_adds_up_to_more_than_the_destinations():
    # A real path: a router 2 ms from home whose few answers average 102 ms,
    # and a slow-pathed router midway at 130 ms; the destination is at 61.
    rows = _rank([hop(1, "Gateway", 102), hop(2, "Boston", 4), hop(8, "Montreal", 62),
                  hop(12, "Des Moines", 130), hop(14, "Cheyenne", 61)])
    by = {r.place: r.delay_added for r in rows}
    assert by["Gateway"] == 4 and by["Des Moines"] == 0 and by["Cheyenne"] == 0
    assert sum(by.values()) == 61
    assert trouble.verdict(rows) == "The most delay is added in Montreal (+57 ms)."


def test_silent_and_unplaced_hops_still_count_for_their_neighbours():
    rows = _rank([hop(1, "A", 5), hop(2, None, None), hop(3, None, 30), hop(4, "D", 32)])
    assert {r.place: r.delay_added for r in rows} == {"A": 5, "D": 2}


def test_one_metro_named_two_ways_is_one_row():
    # "New York" from a router's name, "New York City" from the database, and
    # Newark across the river: one row. Washington, 42 km from Ashburn, isn't.
    places = {1: ("New York, US", (40.71, -74.01)), 2: ("New York City, US", (40.71, -74.0)),
              3: ("Newark, US", (40.74, -74.17)), 4: ("Ashburn, US", (39.04, -77.49)),
              5: ("Washington, US", (38.91, -77.04))}
    rows = trouble.rank([hop(t, None, 10 * t) for t in places], lambda h: places[h.ttl])
    assert [(r.place, r.ttls) for r in sorted(rows, key=lambda r: r.ttls)] == [
        ("New York, US", [1, 2, 3]), ("Ashburn, US", [4]), ("Washington, US", [5])]


def test_nothing_to_say():
    assert trouble.rank([], lambda h: "x") == []
    assert trouble.verdict([]) == ""
    calm = _rank([hop(1, "A", 1), hop(2, "B", 4)])
    assert trouble.verdict(calm) == "Nothing on the path stands out."


def test_carried_loss():
    assert trouble.carried_loss([0, 50, 0, 10]) == [0, 0, 0, 10]
    assert trouble.carried_loss([5, 20, 12, 15]) == [5, 12, 12, 15]


def _path(*hops):
    """(ttl, address, avg, loss) tuples, as rank reads them."""
    return [H(ttl=t, address=a, avg=avg, loss_pct=loss) for t, a, avg, loss in hops]


def test_loss_starting_at_one_router_on_several_paths_is_shared():
    gw, isp = "192.0.2.1", "198.51.100.2"
    paths = {
        "a.example.com": _path((1, gw, 1, 0), (2, isp, 5, 8), (3, "203.0.113.10", 20, 8)),
        "b.example.com": _path((1, gw, 1, 0), (2, isp, 5, 9), (3, "203.0.113.20", 30, 9)),
        "c.example.com": _path((1, gw, 1, 0), (2, isp, 5, 0), (3, "203.0.113.30", 12, 0)),
    }
    (s,) = trouble.shared(paths)
    assert (s.ttl, s.address, s.targets) == (2, isp, ["a.example.com", "b.example.com"])
    assert s.says(3) == ("The same router (hop 2, 198.51.100.2) is where loss starts for 2 of your 3 "
                         "targets: it's that router or something before it, not the destinations.")


def test_one_path_alone_or_rate_limiting_or_delay_isnt_shared():
    isp = "198.51.100.2"
    paths = {
        # Loss only at the router (rate limiting): it doesn't reach the destination.
        "a.example.com": _path((1, isp, 5, 40), (2, "203.0.113.10", 20, 0)),
        # Real loss, but this path alone.
        "b.example.com": _path((1, "198.51.100.9", 5, 10), (2, "203.0.113.20", 20, 10)),
        # The same router adding delay on two paths: distance, not trouble.
        "c.example.com": _path((1, isp, 70, 0), (2, "203.0.113.30", 71, 0)),
        "d.example.com": _path((1, isp, 70, 0), (2, "203.0.113.40", 72, 0)),
    }
    assert trouble.shared(paths) == []
