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
