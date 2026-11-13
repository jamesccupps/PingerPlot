"""Where a router is, read from its name (places.from_name). Names follow the
patterns carriers really use; the domains are fictional."""
import pytest

from pingerplot import places


@pytest.mark.parametrize("name, city", [
    ("ae-5.r21.chcgil09.us.bb.example.net", "Chicago"),          # CLLI
    ("be3036.ccr22.dfw01.atlas.example.com", "Dallas"),          # airport code + digits
    ("core1.lax1.he.example.net", "Los Angeles"),
    ("ae22-0.ear02.chi20.ntwk.example.net", "Chicago"),          # metro code
    ("ae-1-3502.ear2.Dallas1.example.net", "Dallas"),            # city spelled out
    ("be-1101-cr01.newyork.ny.ibone.example.net", "New York"),
    ("xe-0-0-0.new-york.example.net", "New York"),               # hyphenated city
    ("et-3-1-0.cr2.ptldme.example.net", "Portland, Maine"),
    ("100ge1-1.core1.ash1.example.net", "Ashburn"),
    ("ae0.cr1.ams2.example.net", "Amsterdam"),
])
def test_carrier_naming_patterns(name, city):
    place = places.from_name(name)
    assert place is not None and place.city == city


@pytest.mark.parametrize("name", [
    None, "", "localhost", "example.net",
    "dsl-1-2-3-4.example.net",            # numbers only
    "ae-5.r21.us.bb.example.net",         # router jargon, no place
    "router.man.example.net",             # MAN is a network term, not Manchester
    "edge.los-gatos.example.net",         # "los" alone is never Lagos
    "gw.lax.com",                         # a code in the domain itself doesn't count
    "laxity.example.net",                 # a code inside a longer word doesn't count
])
def test_no_place_rather_than_a_wrong_one(name):
    assert places.from_name(name) is None


def test_the_clue_is_reported():
    place = places.from_name("be3036.ccr22.dfw01.atlas.example.com")
    assert place.clue == "dfw01" and str(place) == "Dallas, US"


def test_ambiguous_portland_says_only_portland():
    assert str(places.from_name("ae1.portland.example.net")) == "Portland"


def test_every_city_named_has_coordinates_except_the_ambiguous_one():
    cities = {v[0] for table in (places.CLLI, places.CODES, places.NAMES) for v in table.values()}
    missing = sorted(c for c in cities if c not in places.COORDS)
    assert missing == ["Portland"]
    for city, (lat, lon) in places.COORDS.items():
        assert -90 <= lat <= 90 and -180 <= lon <= 180, city


def test_coords_of_a_place():
    assert places.coords(places.from_name("ae0.cr1.ams2.example.net")) == (52.37, 4.90)
    assert places.coords(places.from_name("ae1.portland.example.net")) is None
    assert places.coords(None) is None
