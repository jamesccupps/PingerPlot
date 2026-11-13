"""Where a router is, read from its name.

IP geolocation is good for homes and offices and poor for the backbone: a
carrier's routers are often registered to its headquarters, so a router in
Chicago "is" in Denver or Ashburn. The router's own DNS name is usually the
better witness, because carriers name routers after where they stand:

* a CLLI code, the telephone industry's six-letter place code
  (``ae-5.r21.chcgil09.us.bb.example.net``: CHCG IL, Chicago);
* an airport or metro code (``be3036.ccr22.dfw01.example.com``: DFW, Dallas;
  ``core1.lax1.example.net``: Los Angeles);
* the city spelled out (``ae-1.ear2.Dallas1.example.net``,
  ``be-1101-cr01.newyork.ny.example.net``).

This reads those and nothing else, offline. It is a hint, said to be one:
names are a convention, not a promise, and a router can be named after the
city at the other end of its link. The tables hold only codes that are in
common use on carrier networks and unlikely to be anything else; airport
codes that are also router jargon or English words (MAN, LOS, SAN, AUS, CAN,
PER, DEL, IND, SAT) are left out, so a wrong guess is rarer than no guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Six-letter CLLI place codes: four letters of city, two of state.
CLLI = {
    "chcgil": ("Chicago", "US"), "nycmny": ("New York", "US"), "nwrknj": ("Newark", "US"),
    "dllstx": ("Dallas", "US"), "hstntx": ("Houston", "US"), "snantx": ("San Antonio", "US"),
    "austtx": ("Austin", "US"), "lsanca": ("Los Angeles", "US"), "snjsca": ("San Jose", "US"),
    "sntcca": ("Santa Clara", "US"), "plalca": ("Palo Alto", "US"), "snfcca": ("San Francisco", "US"),
    "sndgca": ("San Diego", "US"), "sttlwa": ("Seattle", "US"), "prtlor": ("Portland, Oregon", "US"),
    "dnvrco": ("Denver", "US"), "phnxaz": ("Phoenix", "US"), "tcsnaz": ("Tucson", "US"),
    "albqnm": ("Albuquerque", "US"), "slkcut": ("Salt Lake City", "US"), "atlnga": ("Atlanta", "US"),
    "miamfl": ("Miami", "US"), "tampfl": ("Tampa", "US"), "orldfl": ("Orlando", "US"),
    "jcvlfl": ("Jacksonville", "US"), "asbnva": ("Ashburn", "US"), "rcmdva": ("Richmond", "US"),
    "washdc": ("Washington", "US"), "bltmmd": ("Baltimore", "US"), "phlapa": ("Philadelphia", "US"),
    "pitbpa": ("Pittsburgh", "US"), "bstnma": ("Boston", "US"), "ptldme": ("Portland, Maine", "US"),
    "mnchnh": ("Manchester, New Hampshire", "US"), "clevoh": ("Cleveland", "US"),
    "clmboh": ("Columbus", "US"), "dtrtmi": ("Detroit", "US"), "mplsmn": ("Minneapolis", "US"),
    "stlsmo": ("St. Louis", "US"), "kscymo": ("Kansas City", "US"), "omahne": ("Omaha", "US"),
    "nsvltn": ("Nashville", "US"), "mmphtn": ("Memphis", "US"), "chrlnc": ("Charlotte", "US"),
    "rlghnc": ("Raleigh", "US"), "nworla": ("New Orleans", "US"), "hnllhi": ("Honolulu", "US"),
}

# Airport and metro codes carriers use for their points of presence.
CODES = {
    # North America
    "atl": ("Atlanta", "US"), "bos": ("Boston", "US"), "bna": ("Nashville", "US"),
    "bwi": ("Baltimore", "US"), "chi": ("Chicago", "US"), "cle": ("Cleveland", "US"),
    "clt": ("Charlotte", "US"), "cmh": ("Columbus", "US"), "dal": ("Dallas", "US"),
    "dca": ("Washington", "US"), "den": ("Denver", "US"), "dfw": ("Dallas", "US"),
    "dtw": ("Detroit", "US"), "ewr": ("Newark", "US"), "hnl": ("Honolulu", "US"),
    "hou": ("Houston", "US"), "iad": ("Ashburn", "US"), "ash": ("Ashburn", "US"),
    "iah": ("Houston", "US"), "jax": ("Jacksonville", "US"), "jfk": ("New York", "US"),
    "lax": ("Los Angeles", "US"), "lga": ("New York", "US"), "mci": ("Kansas City", "US"),
    "mco": ("Orlando", "US"), "mem": ("Memphis", "US"), "mia": ("Miami", "US"),
    "mke": ("Milwaukee", "US"), "msp": ("Minneapolis", "US"), "msy": ("New Orleans", "US"),
    "nyc": ("New York", "US"), "oma": ("Omaha", "US"), "ord": ("Chicago", "US"),
    "pdx": ("Portland, Oregon", "US"), "phl": ("Philadelphia", "US"), "phx": ("Phoenix", "US"),
    "pit": ("Pittsburgh", "US"), "rdu": ("Raleigh", "US"), "sea": ("Seattle", "US"),
    "sfo": ("San Francisco", "US"), "sjc": ("San Jose", "US"), "slc": ("Salt Lake City", "US"),
    "stl": ("St. Louis", "US"), "tpa": ("Tampa", "US"), "was": ("Washington", "US"),
    "yul": ("Montreal", "CA"), "yvr": ("Vancouver", "CA"), "yyz": ("Toronto", "CA"),
    "yyc": ("Calgary", "CA"), "yow": ("Ottawa", "CA"), "mex": ("Mexico City", "MX"),
    "qro": ("Queretaro", "MX"),
    # Europe
    "ams": ("Amsterdam", "NL"), "arn": ("Stockholm", "SE"), "sto": ("Stockholm", "SE"),
    "ath": ("Athens", "GR"), "bcn": ("Barcelona", "ES"), "bru": ("Brussels", "BE"),
    "bud": ("Budapest", "HU"), "cdg": ("Paris", "FR"), "par": ("Paris", "FR"),
    "cph": ("Copenhagen", "DK"), "dub": ("Dublin", "IE"), "fra": ("Frankfurt", "DE"),
    "fco": ("Rome", "IT"), "hel": ("Helsinki", "FI"), "ham": ("Hamburg", "DE"),
    "ist": ("Istanbul", "TR"), "lhr": ("London", "GB"), "lon": ("London", "GB"),
    "lis": ("Lisbon", "PT"), "mad": ("Madrid", "ES"), "mrs": ("Marseille", "FR"),
    "mil": ("Milan", "IT"), "mxp": ("Milan", "IT"), "muc": ("Munich", "DE"),
    "osl": ("Oslo", "NO"), "otp": ("Bucharest", "RO"), "prg": ("Prague", "CZ"),
    "sof": ("Sofia", "BG"), "vie": ("Vienna", "AT"), "waw": ("Warsaw", "PL"),
    "zrh": ("Zurich", "CH"), "dus": ("Dusseldorf", "DE"),
    # Asia, Oceania, Middle East, Africa, South America
    "akl": ("Auckland", "NZ"), "bkk": ("Bangkok", "TH"), "bom": ("Mumbai", "IN"),
    "cgk": ("Jakarta", "ID"), "dxb": ("Dubai", "AE"), "hkg": ("Hong Kong", "HK"),
    "icn": ("Seoul", "KR"), "sel": ("Seoul", "KR"), "jnb": ("Johannesburg", "ZA"),
    "kix": ("Osaka", "JP"), "osa": ("Osaka", "JP"), "kul": ("Kuala Lumpur", "MY"),
    "maa": ("Chennai", "IN"), "mnl": ("Manila", "PH"), "nrt": ("Tokyo", "JP"),
    "hnd": ("Tokyo", "JP"), "tyo": ("Tokyo", "JP"), "sin": ("Singapore", "SG"),
    "syd": ("Sydney", "AU"), "mel": ("Melbourne", "AU"), "tpe": ("Taipei", "TW"),
    "gru": ("Sao Paulo", "BR"), "eze": ("Buenos Aires", "AR"), "scl": ("Santiago", "CL"),
    "bog": ("Bogota", "CO"), "lim": ("Lima", "PE"), "cpt": ("Cape Town", "ZA"),
    "nbo": ("Nairobi", "KE"), "doh": ("Doha", "QA"),
}

# Cities spelled out, as some carriers name routers (spaces and dots removed).
NAMES = {
    "chicago": ("Chicago", "US"), "dallas": ("Dallas", "US"), "houston": ("Houston", "US"),
    "newyork": ("New York", "US"), "losangeles": ("Los Angeles", "US"), "sanjose": ("San Jose", "US"),
    "seattle": ("Seattle", "US"), "denver": ("Denver", "US"), "phoenix": ("Phoenix", "US"),
    "atlanta": ("Atlanta", "US"), "miami": ("Miami", "US"), "ashburn": ("Ashburn", "US"),
    "washington": ("Washington", "US"), "boston": ("Boston", "US"),
    "philadelphia": ("Philadelphia", "US"), "newark": ("Newark", "US"), "paloalto": ("Palo Alto", "US"),
    "santaclara": ("Santa Clara", "US"), "sanfrancisco": ("San Francisco", "US"),
    "sandiego": ("San Diego", "US"), "minneapolis": ("Minneapolis", "US"),
    "stlouis": ("St. Louis", "US"), "kansascity": ("Kansas City", "US"), "detroit": ("Detroit", "US"),
    "cleveland": ("Cleveland", "US"), "columbus": ("Columbus", "US"),
    "saltlakecity": ("Salt Lake City", "US"), "lasvegas": ("Las Vegas", "US"),
    "nashville": ("Nashville", "US"), "charlotte": ("Charlotte", "US"), "raleigh": ("Raleigh", "US"),
    "tampa": ("Tampa", "US"), "orlando": ("Orlando", "US"), "jacksonville": ("Jacksonville", "US"),
    "neworleans": ("New Orleans", "US"), "memphis": ("Memphis", "US"),
    "sanantonio": ("San Antonio", "US"), "austin": ("Austin", "US"), "albuquerque": ("Albuquerque", "US"),
    "honolulu": ("Honolulu", "US"), "pittsburgh": ("Pittsburgh", "US"), "baltimore": ("Baltimore", "US"),
    "richmond": ("Richmond", "US"), "omaha": ("Omaha", "US"), "indianapolis": ("Indianapolis", "US"),
    "milwaukee": ("Milwaukee", "US"), "sacramento": ("Sacramento", "US"), "buffalo": ("Buffalo", "US"),
    "portland": ("Portland", ""), "toronto": ("Toronto", "CA"), "montreal": ("Montreal", "CA"),
    "vancouver": ("Vancouver", "CA"), "london": ("London", "GB"), "paris": ("Paris", "FR"),
    "frankfurt": ("Frankfurt", "DE"), "amsterdam": ("Amsterdam", "NL"), "brussels": ("Brussels", "BE"),
    "madrid": ("Madrid", "ES"), "milan": ("Milan", "IT"), "zurich": ("Zurich", "CH"),
    "vienna": ("Vienna", "AT"), "stockholm": ("Stockholm", "SE"), "oslo": ("Oslo", "NO"),
    "copenhagen": ("Copenhagen", "DK"), "helsinki": ("Helsinki", "FI"), "warsaw": ("Warsaw", "PL"),
    "prague": ("Prague", "CZ"), "dublin": ("Dublin", "IE"), "marseille": ("Marseille", "FR"),
    "tokyo": ("Tokyo", "JP"), "osaka": ("Osaka", "JP"), "seoul": ("Seoul", "KR"),
    "singapore": ("Singapore", "SG"), "hongkong": ("Hong Kong", "HK"), "sydney": ("Sydney", "AU"),
    "melbourne": ("Melbourne", "AU"), "mumbai": ("Mumbai", "IN"), "chennai": ("Chennai", "IN"),
    "johannesburg": ("Johannesburg", "ZA"), "saopaulo": ("Sao Paulo", "BR"),
}

_TOKEN = re.compile(r"^([a-z]+)\d*[a-z]?$")   # "lax1", "dfw01", "chcgil09", "dallas1", "ord2a"


@dataclass(frozen=True)
class Place:
    city: str
    country: str
    clue: str           # the part of the name it came from

    def __str__(self) -> str:
        return f"{self.city}, {self.country}" if self.country else self.city


def from_name(hostname: Optional[str]) -> Optional[Place]:
    """The place a router's name points to, or None.

    Only the labels in front of the domain are read (``…cogentco.com`` names
    no city), and a whole word must match: a code is never found inside a
    longer token."""
    if not hostname:
        return None
    labels = hostname.lower().rstrip(".").split(".")
    if len(labels) < 3:
        return None
    for label in labels[:-2]:
        # "newyork.ny" comes as one label per word; "new-york" and "new_york"
        # as one label, so try the label whole (dashes dropped) before its parts.
        joined = re.sub(r"[-_]", "", label)
        m = _TOKEN.match(joined)
        if m and m.group(1) in NAMES:
            city, cc = NAMES[m.group(1)]
            return Place(city, cc, label)
        for part in re.split(r"[-_]", label):
            m = _TOKEN.match(part)
            if not m:
                continue
            word = m.group(1)
            if len(word) == 6 and word in CLLI:
                city, cc = CLLI[word]
                return Place(city, cc, part)
            if word in NAMES:
                city, cc = NAMES[word]
                return Place(city, cc, part)
            if len(word) == 3 and word in CODES:
                city, cc = CODES[word]
                return Place(city, cc, part)
    return None


# Where those cities are (lat, lon; city centres, two decimals), so the map
# can draw a router where its name says it is. "Portland" alone is ambiguous
# and has none.
COORDS = {
    "Chicago": (41.88, -87.63), "New York": (40.71, -74.01), "Newark": (40.74, -74.17),
    "Dallas": (32.78, -96.80), "Houston": (29.76, -95.37), "San Antonio": (29.42, -98.49),
    "Austin": (30.27, -97.74), "Los Angeles": (34.05, -118.24), "San Jose": (37.34, -121.89),
    "Santa Clara": (37.35, -121.96), "Palo Alto": (37.44, -122.14), "San Francisco": (37.77, -122.42),
    "San Diego": (32.72, -117.16), "Seattle": (47.61, -122.33), "Portland, Oregon": (45.52, -122.68),
    "Denver": (39.74, -104.99), "Phoenix": (33.45, -112.07), "Tucson": (32.22, -110.97),
    "Albuquerque": (35.08, -106.65), "Salt Lake City": (40.76, -111.89), "Atlanta": (33.75, -84.39),
    "Miami": (25.76, -80.19), "Tampa": (27.95, -82.46), "Orlando": (28.54, -81.38),
    "Jacksonville": (30.33, -81.66), "Ashburn": (39.04, -77.49), "Richmond": (37.54, -77.44),
    "Washington": (38.91, -77.04), "Baltimore": (39.29, -76.61), "Philadelphia": (39.95, -75.17),
    "Pittsburgh": (40.44, -79.99), "Boston": (42.36, -71.06), "Portland, Maine": (43.66, -70.26),
    "Manchester, New Hampshire": (42.99, -71.46), "Cleveland": (41.50, -81.69),
    "Columbus": (39.96, -83.00), "Detroit": (42.33, -83.05), "Minneapolis": (44.98, -93.27),
    "St. Louis": (38.63, -90.20), "Kansas City": (39.10, -94.58), "Omaha": (41.26, -95.93),
    "Nashville": (36.16, -86.78), "Memphis": (35.15, -90.05), "Charlotte": (35.23, -80.84),
    "Raleigh": (35.78, -78.64), "New Orleans": (29.95, -90.07), "Honolulu": (21.31, -157.86),
    "Milwaukee": (43.04, -87.91), "Las Vegas": (36.17, -115.14), "Indianapolis": (39.77, -86.16),
    "Sacramento": (38.58, -121.49), "Buffalo": (42.89, -78.88),
    "Montreal": (45.50, -73.57), "Vancouver": (49.28, -123.12), "Toronto": (43.65, -79.38),
    "Calgary": (51.05, -114.07), "Ottawa": (45.42, -75.70), "Mexico City": (19.43, -99.13),
    "Queretaro": (20.59, -100.39),
    "Amsterdam": (52.37, 4.90), "Stockholm": (59.33, 18.07), "Athens": (37.98, 23.73),
    "Barcelona": (41.39, 2.17), "Brussels": (50.85, 4.35), "Budapest": (47.50, 19.04),
    "Paris": (48.86, 2.35), "Copenhagen": (55.68, 12.57), "Dublin": (53.35, -6.26),
    "Frankfurt": (50.11, 8.68), "Rome": (41.90, 12.50), "Helsinki": (60.17, 24.94),
    "Hamburg": (53.55, 9.99), "Istanbul": (41.01, 28.98), "London": (51.51, -0.13),
    "Lisbon": (38.72, -9.14), "Madrid": (40.42, -3.70), "Marseille": (43.30, 5.37),
    "Milan": (45.46, 9.19), "Munich": (48.14, 11.58), "Oslo": (59.91, 10.75),
    "Bucharest": (44.43, 26.10), "Prague": (50.08, 14.44), "Sofia": (42.70, 23.32),
    "Vienna": (48.21, 16.37), "Warsaw": (52.23, 21.01), "Zurich": (47.38, 8.54),
    "Dusseldorf": (51.23, 6.77),
    "Auckland": (-36.85, 174.76), "Bangkok": (13.76, 100.50), "Mumbai": (19.08, 72.88),
    "Jakarta": (-6.21, 106.85), "Dubai": (25.20, 55.27), "Hong Kong": (22.32, 114.17),
    "Seoul": (37.57, 126.98), "Johannesburg": (-26.20, 28.05), "Osaka": (34.69, 135.50),
    "Kuala Lumpur": (3.14, 101.69), "Chennai": (13.08, 80.27), "Manila": (14.60, 120.98),
    "Tokyo": (35.68, 139.69), "Singapore": (1.35, 103.82), "Sydney": (-33.87, 151.21),
    "Melbourne": (-37.81, 144.96), "Taipei": (25.03, 121.57), "Sao Paulo": (-23.55, -46.63),
    "Buenos Aires": (-34.60, -58.38), "Santiago": (-33.45, -70.67), "Bogota": (4.71, -74.07),
    "Lima": (-12.05, -77.04), "Cape Town": (-33.92, 18.42), "Nairobi": (-1.29, 36.82),
    "Doha": (25.29, 51.53),
}


def coords(place: Optional[Place]) -> Optional[tuple]:
    """(lat, lon) for a place read from a name, or None."""
    return COORDS.get(place.city) if place is not None else None
