"""Geographic normalisation, remote-eligibility extraction and matching.

The rest of the application never trusts a single free-text ``location``
string.  A listing carries four separate concepts:

* ``workplace_type`` — remote, hybrid or on-site
* ``company_location`` — where the employer is based (often unknown)
* ``job_location`` — the advertised place of work
* ``eligible_locations`` / ``remote_scope`` — where a remote employee may live

Remote eligibility that cannot be established is stored as ``unknown`` and is
never presented as a verified geographic match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

PlaceKind = Literal["city", "region", "country", "macro", "remote", "worldwide"]


@dataclass(frozen=True)
class Place:
    name: str
    kind: PlaceKind
    city: str = ""
    region: str = ""
    country: str = ""
    country_code: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.kind == "city":
            return ", ".join(part for part in (self.city, self.region, self.country) if part)
        return self.name


@dataclass
class RemoteInfo:
    scope: Literal["worldwide", "country", "region", "city", "unknown"] = "unknown"
    eligible: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    confidence: Literal["verified", "likely", "inferred", "unknown"] = "unknown"


@dataclass
class LocationInfo:
    workplace_type: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    company_location: str = ""
    job_location: str = ""
    eligible_locations: list[str] = field(default_factory=list)
    remote_scope: Literal["worldwide", "country", "region", "city", "unknown"] = "unknown"
    location_confidence: Literal["verified", "likely", "inferred", "unknown"] = "unknown"
    restrictions: list[str] = field(default_factory=list)
    remote: bool = False


@dataclass
class Match:
    tier: Literal["exact", "regional", "remote", "unverified", "mismatch", "any"] = "any"
    verified: bool = False
    score: float = 0.0
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.tier != "mismatch"


# ---------------------------------------------------------------------------
# Place data
# ---------------------------------------------------------------------------
COUNTRY_ALIASES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # name: (code, macro region, aliases)
    "Pakistan": ("PK", "Asia", ("pakistan", "pk", "pak")),
    "Canada": ("CA", "North America", ("canada", "ca")),
    "United States": (
        "US",
        "North America",
        ("united states", "united states of america", "usa", "u.s.", "u.s.a.", "us", "america"),
    ),
    "United Kingdom": (
        "GB",
        "Europe",
        ("united kingdom", "uk", "u.k.", "great britain", "britain", "england", "scotland", "wales"),
    ),
    "United Arab Emirates": ("AE", "Middle East", ("united arab emirates", "uae", "u.a.e.", "emirates")),
    "Saudi Arabia": ("SA", "Middle East", ("saudi arabia", "ksa", "saudi")),
    "Qatar": ("QA", "Middle East", ("qatar",)),
    "Germany": ("DE", "Europe", ("germany", "deutschland")),
    "France": ("FR", "Europe", ("france",)),
    "Netherlands": ("NL", "Europe", ("netherlands", "holland")),
    "Ireland": ("IE", "Europe", ("ireland",)),
    "Spain": ("ES", "Europe", ("spain",)),
    "Portugal": ("PT", "Europe", ("portugal",)),
    "Poland": ("PL", "Europe", ("poland",)),
    "Sweden": ("SE", "Europe", ("sweden",)),
    "Switzerland": ("CH", "Europe", ("switzerland",)),
    "Turkey": ("TR", "Europe", ("turkey", "türkiye", "turkiye")),
    "Australia": ("AU", "Asia Pacific", ("australia",)),
    "New Zealand": ("NZ", "Asia Pacific", ("new zealand",)),
    "India": ("IN", "Asia", ("india",)),
    "Bangladesh": ("BD", "Asia", ("bangladesh",)),
    "Sri Lanka": ("LK", "Asia", ("sri lanka",)),
    "Nepal": ("NP", "Asia", ("nepal",)),
    "Singapore": ("SG", "Asia", ("singapore",)),
    "Malaysia": ("MY", "Asia", ("malaysia",)),
    "Japan": ("JP", "Asia", ("japan",)),
    "China": ("CN", "Asia", ("china",)),
    "South Africa": ("ZA", "Africa", ("south africa",)),
    "Egypt": ("EG", "Africa", ("egypt",)),
    "Brazil": ("BR", "Latin America", ("brazil", "brasil")),
    "Mexico": ("MX", "Latin America", ("mexico", "méxico")),
}


def _city(
    city: str,
    region: str,
    country: str,
    aliases: Iterable[str] = (),
) -> Place:
    code = COUNTRY_ALIASES.get(country, ("", "", ()))[0]
    return Place(
        name=city,
        kind="city",
        city=city,
        region=region,
        country=country,
        country_code=code,
        aliases=tuple(dict.fromkeys((city, *aliases))),
    )


CITIES: tuple[Place, ...] = (
    # Pakistan
    _city("Karachi", "Sindh", "Pakistan"),
    _city("Lahore", "Punjab", "Pakistan"),
    _city("Islamabad", "Islamabad Capital Territory", "Pakistan"),
    _city("Rawalpindi", "Punjab", "Pakistan", ("pindi",)),
    _city("Faisalabad", "Punjab", "Pakistan"),
    _city("Multan", "Punjab", "Pakistan"),
    _city("Peshawar", "Khyber Pakhtunkhwa", "Pakistan"),
    _city("Quetta", "Balochistan", "Pakistan"),
    _city("Hyderabad", "Sindh", "Pakistan"),
    _city("Gujranwala", "Punjab", "Pakistan"),
    _city("Sialkot", "Punjab", "Pakistan"),
    _city("Bahawalpur", "Punjab", "Pakistan"),
    _city("Abbottabad", "Khyber Pakhtunkhwa", "Pakistan"),
    _city("Nowshera", "Khyber Pakhtunkhwa", "Pakistan"),
    _city("Jhelum", "Punjab", "Pakistan"),
    _city("Attock", "Punjab", "Pakistan"),
    _city("Mardan", "Khyber Pakhtunkhwa", "Pakistan"),
    _city("Sukkur", "Sindh", "Pakistan"),
    _city("Larkana", "Sindh", "Pakistan"),
    _city("Rahim Yar Khan", "Punjab", "Pakistan"),
    _city("Dera Ghazi Khan", "Punjab", "Pakistan"),
    # Canada
    _city("Toronto", "Ontario", "Canada"),
    _city("Vancouver", "British Columbia", "Canada"),
    _city("Montreal", "Quebec", "Canada"),
    _city("Ottawa", "Ontario", "Canada"),
    _city("Calgary", "Alberta", "Canada"),
    _city("Edmonton", "Alberta", "Canada"),
    _city("Waterloo", "Ontario", "Canada"),
    _city("Mississauga", "Ontario", "Canada"),
    # United Kingdom
    _city("London", "England", "United Kingdom"),
    _city("Manchester", "England", "United Kingdom"),
    _city("Birmingham", "England", "United Kingdom"),
    _city("Leeds", "England", "United Kingdom"),
    _city("Bristol", "England", "United Kingdom"),
    _city("Cambridge", "England", "United Kingdom"),
    _city("Edinburgh", "Scotland", "United Kingdom"),
    _city("Glasgow", "Scotland", "United Kingdom"),
    # United States
    _city("New York", "New York", "United States", ("nyc", "new york city")),
    _city("San Francisco", "California", "United States", ("sf", "bay area")),
    _city("Seattle", "Washington", "United States"),
    _city("Austin", "Texas", "United States"),
    _city("Boston", "Massachusetts", "United States"),
    _city("Chicago", "Illinois", "United States"),
    _city("Los Angeles", "California", "United States", ("la",)),
    _city("Denver", "Colorado", "United States"),
    _city("Atlanta", "Georgia", "United States"),
    _city("Washington", "District of Columbia", "United States", ("washington dc", "dc")),
    _city("San Diego", "California", "United States"),
    _city("Dallas", "Texas", "United States"),
    _city("Houston", "Texas", "United States"),
    _city("Miami", "Florida", "United States"),
    _city("Philadelphia", "Pennsylvania", "United States"),
    _city("Phoenix", "Arizona", "United States"),
    _city("Portland", "Oregon", "United States"),
    # Middle East
    _city("Dubai", "Dubai", "United Arab Emirates"),
    _city("Abu Dhabi", "Abu Dhabi", "United Arab Emirates"),
    _city("Riyadh", "Riyadh", "Saudi Arabia"),
    _city("Jeddah", "Makkah", "Saudi Arabia"),
    _city("Dammam", "Eastern Province", "Saudi Arabia"),
    _city("Doha", "Doha", "Qatar"),
    # Europe
    _city("Berlin", "Berlin", "Germany"),
    _city("Munich", "Bavaria", "Germany"),
    _city("Hamburg", "Hamburg", "Germany"),
    _city("Frankfurt", "Hesse", "Germany"),
    _city("Amsterdam", "North Holland", "Netherlands"),
    _city("Dublin", "Leinster", "Ireland"),
    _city("Paris", "Île-de-France", "France"),
    _city("Istanbul", "Istanbul", "Turkey"),
    # Asia Pacific
    _city("Sydney", "New South Wales", "Australia"),
    _city("Melbourne", "Victoria", "Australia"),
    _city("Brisbane", "Queensland", "Australia"),
    _city("Perth", "Western Australia", "Australia"),
    _city("Bangalore", "Karnataka", "India", ("bengaluru",)),
    _city("Mumbai", "Maharashtra", "India"),
    _city("Delhi", "Delhi", "India", ("new delhi",)),
    _city("Hyderabad", "Telangana", "India"),
    _city("Chennai", "Tamil Nadu", "India"),
    _city("Pune", "Maharashtra", "India"),
    _city("Gurgaon", "Haryana", "India", ("gurugram",)),
    _city("Noida", "Uttar Pradesh", "India"),
    _city("Singapore", "Singapore", "Singapore"),
    _city("Kuala Lumpur", "Kuala Lumpur", "Malaysia"),
    _city("Tokyo", "Tokyo", "Japan"),
    _city("Dhaka", "Dhaka", "Bangladesh"),
    _city("Colombo", "Western Province", "Sri Lanka"),
    # Africa / Latin America
    _city("Johannesburg", "Gauteng", "South Africa"),
    _city("Cape Town", "Western Cape", "South Africa"),
    _city("Cairo", "Cairo", "Egypt"),
    _city("São Paulo", "São Paulo", "Brazil", ("sao paulo",)),
    _city("Mexico City", "Mexico City", "Mexico", ("cdmx",)),
)

REGIONS: tuple[Place, ...] = (
    Place("Punjab", "region", region="Punjab", country="Pakistan", country_code="PK", aliases=("punjab",)),
    Place("Sindh", "region", region="Sindh", country="Pakistan", country_code="PK", aliases=("sindh",)),
    Place(
        "Khyber Pakhtunkhwa",
        "region",
        region="Khyber Pakhtunkhwa",
        country="Pakistan",
        country_code="PK",
        aliases=("khyber pakhtunkhwa", "kpk", "nwfp"),
    ),
    Place("Balochistan", "region", region="Balochistan", country="Pakistan", country_code="PK", aliases=("balochistan",)),
    Place("Ontario", "region", region="Ontario", country="Canada", country_code="CA", aliases=("ontario",)),
    Place("Quebec", "region", region="Quebec", country="Canada", country_code="CA", aliases=("quebec", "québec")),
    Place(
        "British Columbia",
        "region",
        region="British Columbia",
        country="Canada",
        country_code="CA",
        aliases=("british columbia", "bc"),
    ),
    Place("Alberta", "region", region="Alberta", country="Canada", country_code="CA", aliases=("alberta",)),
    Place("California", "region", region="California", country="United States", country_code="US", aliases=("california", "calif")),
    Place("Texas", "region", region="Texas", country="United States", country_code="US", aliases=("texas",)),
    Place("England", "region", region="England", country="United Kingdom", country_code="GB", aliases=("england",)),
    Place("Scotland", "region", region="Scotland", country="United Kingdom", country_code="GB", aliases=("scotland",)),
    Place("Wales", "region", region="Wales", country="United Kingdom", country_code="GB", aliases=("wales",)),
)

MACRO_REGIONS: tuple[Place, ...] = (
    Place("Worldwide", "worldwide", aliases=("worldwide", "anywhere", "global", "world")),
    Place("Europe", "macro", aliases=("europe", "european union", "eu", "emea")),
    Place("Asia Pacific", "macro", aliases=("asia pacific", "asia-pacific", "apac", "asia")),
    Place("Middle East", "macro", aliases=("middle east", "mena", "gulf")),
    Place("North America", "macro", aliases=("north america", "northern america")),
    Place("Latin America", "macro", aliases=("latin america", "latam", "south america")),
    Place("Africa", "macro", aliases=("africa",)),
)

COUNTRIES: tuple[Place, ...] = tuple(
    Place(
        name,
        "country",
        country=name,
        country_code=code,
        aliases=tuple(dict.fromkeys((name, *aliases))),
    )
    for name, (code, _macro, aliases) in COUNTRY_ALIASES.items()
)

_ALL_PLACES: tuple[Place, ...] = (*CITIES, *REGIONS, *COUNTRIES, *MACRO_REGIONS)

REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere", "distributed", "telecommute")
_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.sub(" ", (text or "").lower()).split())


def _contains(haystack: str, needle: str) -> bool:
    needle = normalize(needle)
    if not needle:
        return False
    padded = f" {haystack} "
    return f" {needle} " in padded


def find_location(text: str) -> Place | None:
    """Find the most specific geographic reference in *text*."""
    if not text:
        return None
    haystack = normalize(text)
    if not haystack:
        return None
    if any(_contains(haystack, word) for word in REMOTE_WORDS) and not any(
        _contains(haystack, place.name) for place in (*CITIES, *COUNTRIES)
    ):
        return Place("Remote", "remote", aliases=("remote", "anywhere", "worldwide"))
    for group in (CITIES, REGIONS, COUNTRIES, MACRO_REGIONS):
        matches = [
            place
            for place in group
            for alias in sorted(place.aliases, key=len, reverse=True)
            if _contains(haystack, alias)
        ]
        if matches:
            return max(matches, key=lambda place: max(len(normalize(a)) for a in place.aliases))
    return None


def find_all_locations(text: str) -> list[Place]:
    haystack = normalize(text)
    found: dict[str, Place] = {}
    for place in _ALL_PLACES:
        for alias in place.aliases:
            if _contains(haystack, alias):
                found[place.name] = place
                break
    return list(found.values())


def is_remote_text(text: str) -> bool:
    return any(_contains(normalize(text), word) for word in REMOTE_WORDS)


def detect_workplace_type(
    title: str = "",
    location: str = "",
    description: str = "",
    *,
    remote_flag: bool | None = None,
    workplace_hint: str = "",
) -> Literal["remote", "hybrid", "onsite", "unknown"]:
    """Classify the workplace from structured evidence only.

    ``description`` is accepted for API compatibility but is deliberately not
    read: prose such as “we offer remote work” or “remote-friendly culture”
    describes a perk, not the arrangement for this role.  Evidence comes from
    a structured source flag, the title, or the dedicated location field.
    """
    hint = normalize(workplace_hint)
    if hint in ("remote", "hybrid", "onsite", "on site", "office"):
        return "onsite" if hint in ("onsite", "on site", "office") else hint  # type: ignore[return-value]
    head = normalize(f"{title} {location}")
    if _contains(head, "hybrid"):
        return "hybrid"
    if _contains(head, "onsite") or _contains(head, "on site") or _contains(head, "in office"):
        return "onsite"
    if _contains(head, "remote") or any(_contains(head, word) for word in REMOTE_WORDS):
        return "remote"
    if remote_flag is True:
        return "remote"
    if remote_flag is False:
        # An explicit “not remote” flag plus a stated location is evidence of an
        # on-site role; without a location it is still unknown.
        return "onsite" if normalize(location) else "unknown"
    return "unknown"


_COUNTRY_SCOPE_PATTERNS = (
    re.compile(
        r"(?:must|need(?:s)? to|required to|have to)\s+(?:be\s+)?(?:based|located|reside|live|work)\s+in\s+(?:the\s+)?([a-z][a-z .&'-]{2,40})",
        re.I,
    ),
    re.compile(r"(?:only|exclusively)\s+(?:open\s+to|available\s+to|for)\s+(?:candidates|applicants|residents)?\s*(?:in|from)\s+(?:the\s+)?([a-z][a-z .&'-]{2,40})", re.I),
    re.compile(r"(?:eligible|authori[sz]ed)\s+to\s+work\s+in\s+(?:the\s+)?([a-z][a-z .&'-]{2,40})", re.I),
    re.compile(r"remote\s*(?:[-–—:,]|\()\s*(?:only\s+)?(?:in\s+)?([a-z][a-z .&'-]{2,30})\)?", re.I),
    re.compile(r"location\s*(?:restrictions?|requirements?)?\s*[:=-]\s*([a-z][a-z ,.&'-]{2,60})", re.I),
)

_WORLDWIDE_PATTERNS = (
    re.compile(r"work\s+from\s+anywhere", re.I),
    re.compile(r"anywhere\s+in\s+the\s+world", re.I),
    re.compile(r"\bworldwide\b", re.I),
    re.compile(r"\bglobally\s+remote\b", re.I),
    re.compile(r"\bremote\s*(?:[-–—:,]\s*)?(?:global|worldwide|anywhere)\b", re.I),
)


def extract_remote_info(
    *texts: str,
    restrictions: Iterable[str] | None = None,
    remote_flag: bool | None = None,
) -> RemoteInfo:
    """Extract where a remote worker may actually live.

    ``restrictions`` should contain structured values supplied by a source
    (for example Himalayas ``locationRestrictions``).  Structured values win
    over prose inferences.
    """
    clean_restrictions = [str(item).strip() for item in (restrictions or []) if str(item).strip()]
    if clean_restrictions:
        lowered = [item.lower() for item in clean_restrictions]
        if any(item in ("worldwide", "anywhere", "global", "world") for item in lowered):
            return RemoteInfo("worldwide", ["Worldwide"], clean_restrictions, "verified")
        places = [find_location(item) for item in clean_restrictions]
        places = [place for place in places if place is not None]
        eligible = []
        scope: Literal["worldwide", "country", "region", "city", "unknown"] = "unknown"
        for place in places:
            if place.kind == "worldwide":
                return RemoteInfo("worldwide", ["Worldwide"], clean_restrictions, "verified")
            eligible.append(place.label)
            if place.kind == "city":
                scope = "city"
            elif place.kind in ("region", "macro") and scope in ("unknown", "city"):
                scope = "region"
            elif place.kind == "country" and scope == "unknown":
                scope = "country"
        if eligible:
            return RemoteInfo(scope, list(dict.fromkeys(eligible)), clean_restrictions, "verified")
        return RemoteInfo("unknown", clean_restrictions, clean_restrictions, "likely")

    combined = " ".join(text for text in texts if text)
    if not combined:
        return RemoteInfo()

    if any(pattern.search(combined) for pattern in _WORLDWIDE_PATTERNS):
        return RemoteInfo("worldwide", ["Worldwide"], [], "likely")

    for pattern in _COUNTRY_SCOPE_PATTERNS:
        match = pattern.search(combined)
        if not match:
            continue
        candidate = match.group(1)
        place = find_location(candidate)
        if place is None:
            # Try the first 2-4 words only ("United States of America" etc.).
            words = candidate.split()
            for size in (4, 3, 2, 1):
                place = find_location(" ".join(words[:size]))
                if place is not None:
                    break
        if place is not None:
            scope: Literal["worldwide", "country", "region", "city", "unknown"] = "unknown"
            if place.kind == "city":
                scope = "city"
            elif place.kind in ("region", "macro"):
                scope = "region"
            elif place.kind == "country":
                scope = "country"
            return RemoteInfo(scope, [place.label], [candidate.strip()], "likely")

    # "Remote — Toronto" or "Remote (Canada)" style labels.
    head = next((text for text in texts if text and is_remote_text(text)), "")
    if head:
        place = find_location(head)
        if place is not None and place.kind != "remote":
            scope = "city" if place.kind == "city" else ("region" if place.kind in ("region", "macro") else "country")
            return RemoteInfo(scope, [place.label], [], "likely")

    if remote_flag or any(is_remote_text(text) for text in texts):
        return RemoteInfo("unknown", [], [], "unknown")
    return RemoteInfo()


def infer_location(
    *,
    job_location: str = "",
    company_location: str = "",
    title: str = "",
    description: str = "",
    remote_flag: bool | None = None,
    workplace_hint: str = "",
    eligible_hint: Iterable[str] | None = None,
) -> LocationInfo:
    workplace = detect_workplace_type(
        title, job_location, description, remote_flag=remote_flag, workplace_hint=workplace_hint
    )
    remote = workplace in ("remote", "hybrid")
    advertised = (job_location or "").strip()
    info = LocationInfo(
        workplace_type=workplace,
        company_location=(company_location or "").strip(),
        job_location=advertised,
        remote=remote,
    )
    if not remote:
        info.eligible_locations = [advertised] if advertised else []
        info.location_confidence = "verified" if advertised else "unknown"
        info.remote_scope = "unknown"
        return info

    remote_info = extract_remote_info(
        job_location,
        title,
        description[:4000],
        restrictions=eligible_hint,
        remote_flag=True,
    )
    info.remote_scope = remote_info.scope
    info.eligible_locations = remote_info.eligible
    info.restrictions = remote_info.restrictions
    info.location_confidence = remote_info.confidence
    # A remote label without an eligibility statement is not a worldwide role.
    if remote_info.scope == "unknown" and advertised and not is_remote_text(advertised):
        place = find_location(advertised)
        if place is not None:
            info.eligible_locations = [place.label]
            info.remote_scope = "city" if place.kind == "city" else ("region" if place.kind in ("region", "macro") else "country")
            info.location_confidence = "inferred"
    return info


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _eligible_places(eligible: Iterable[str]) -> list[Place]:
    places: list[Place] = []
    for item in eligible:
        place = find_location(item)
        if place is not None:
            places.append(place)
    return places


def location_match(
    requested_text: str,
    info: LocationInfo,
    *,
    workplace_filter: str = "",
) -> Match:
    """Return a transparent verdict for geography and workplace type.

    Location and workplace are independent constraints.  A requested city is an
    *exact* constraint: the advertised work location (or, for remote roles, an
    explicitly named eligibility city) must resolve to that city.  Country or
    macro-level statements never stand in for a city, and the employer's head
    office is never used as the work location.
    """
    requested_text = (requested_text or "").strip()

    # Workplace is checked first and on its own.  Unknown is never silently
    # treated as any particular arrangement.
    if workplace_filter in ("remote", "hybrid", "onsite"):
        actual = info.workplace_type
        if actual == "unknown":
            return Match(
                "mismatch",
                False,
                0.0,
                f"{_WORKPLACE_LABEL[workplace_filter]} was requested, but the listing "
                "does not state a verifiable workplace type.",
            )
        if actual != workplace_filter:
            return Match(
                "mismatch",
                False,
                0.0,
                f"{_WORKPLACE_LABEL[workplace_filter]} was requested, but this listing is "
                f"marked {_WORKPLACE_LABEL.get(actual, actual)}.",
            )

    if not requested_text:
        return Match("any", True, 1.0, "No geographic restriction was requested.")

    requested = find_location(requested_text)
    if requested is None:
        # An unrecognised place can never be verified; showing everything would
        # be silently broadening the request.
        return Match(
            "mismatch",
            False,
            0.0,
            f"Could not verify “{requested_text}” as a known location; no listing "
            "is being presented as a match.",
        )
    if requested.kind == "remote":
        if info.workplace_type == "remote":
            return Match("remote", True, 1.0, "Remote role.")
        return Match("mismatch", False, 0.0, "Remote work was requested, but this role is not remote.")

    if requested.kind == "city":
        return _match_city(requested, info)

    if requested.kind == "region":
        return _match_region(requested, info)

    if requested.kind == "country":
        return _match_country(requested, info)

    if requested.kind == "macro":
        return _match_macro(requested, info)

    return Match("mismatch", False, 0.0, "Geography could not be resolved.")


_WORKPLACE_LABEL = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "On-site",
    "unknown": "Unknown workplace type",
}

_REMOTE_TYPES = ("remote", "hybrid")


def _match_city(requested: Place, info: LocationInfo) -> Match:
    """Exact city matching — never expand to a region, country or the world."""
    work_places = find_all_locations(info.job_location) if info.job_location else []
    work_cities = [place for place in work_places if place.kind == "city"]

    # The advertised work location names the city: this is the strongest
    # evidence, whether the role is on-site, hybrid or remote.
    if any(place.city == requested.city for place in work_cities):
        return Match("exact", True, 1.0, f"Work location is {requested.city}.")

    if info.workplace_type in _REMOTE_TYPES:
        eligible = _eligible_places(info.eligible_locations)
        if any(place.kind == "city" and place.city == requested.city for place in eligible):
            return Match(
                "exact",
                True,
                1.0,
                f"Remote role explicitly open to candidates in {requested.city}.",
            )
        # Country / region / worldwide eligibility is not a city match.  It may
        # tell us the role is available, but it does not place the job in the
        # requested city and must not be presented as a verified match.
        if work_cities:
            return Match(
                "mismatch",
                False,
                0.0,
                f"This remote role is listed in {work_cities[0].city}, not {requested.city}.",
            )
        if eligible:
            scope = ", ".join(place.label for place in eligible[:3]) or "a broader area"
            return Match(
                "mismatch",
                False,
                0.0,
                f"Remote role is open within {scope}, not specifically {requested.city}.",
            )
        if info.remote_scope in ("country", "region", "worldwide"):
            return Match(
                "mismatch",
                False,
                0.0,
                f"Remote role is advertised as {info.remote_scope}-wide, not "
                f"specifically {requested.city}.",
            )
        return Match(
            "mismatch",
            False,
            0.0,
            f"Remote role does not state eligibility for {requested.city}.",
        )

    work_place = find_location(info.job_location) if info.job_location else None
    if work_place is None:
        return Match(
            "mismatch",
            False,
            0.0,
            f"The listing does not state a work location; missing data is not a "
            f"verified match for {requested.city}.",
        )
    if work_place.kind == "country":
        return Match(
            "mismatch",
            False,
            0.0,
            f"The listing is identified only as {work_place.country}, not verified as "
            f"{requested.city}.",
        )
    if work_place.kind in ("region", "macro", "worldwide"):
        return Match(
            "mismatch",
            False,
            0.0,
            f"The listing is identified only as {work_place.name}, not verified as "
            f"{requested.city}.",
        )
    return Match(
        "mismatch",
        False,
        0.0,
        f"The listing is not verified as {requested.city}.",
    )


def _match_region(requested: Place, info: LocationInfo) -> Match:
    work_places = find_all_locations(info.job_location) if info.job_location else []
    for place in work_places:
        if place.country == requested.country and place.region == requested.region:
            return Match("exact", True, 1.0, f"Work location is in {requested.region}.")
    if info.workplace_type in _REMOTE_TYPES:
        eligible = _eligible_places(info.eligible_locations)
        for place in eligible:
            if place.country == requested.country and place.region == requested.region:
                return Match(
                    "exact", True, 1.0, f"Remote role explicitly open in {requested.region}."
                )
        if eligible:
            return Match(
                "mismatch",
                False,
                0.0,
                f"Remote role is not explicitly open in {requested.region}.",
            )
        return Match(
            "mismatch",
            False,
            0.0,
            f"Remote role does not state eligibility for {requested.region}.",
        )
    return Match("mismatch", False, 0.0, f"The listing is not verified as {requested.name}.")


def _match_country(requested: Place, info: LocationInfo) -> Match:
    work_places = find_all_locations(info.job_location) if info.job_location else []
    if any(place.country == requested.country for place in work_places):
        return Match("exact", True, 1.0, f"Work location is in {requested.country}.")
    if info.workplace_type in _REMOTE_TYPES:
        eligible = _eligible_places(info.eligible_locations)
        if any(place.kind == "worldwide" for place in eligible):
            return Match("remote", True, 1.0, "Remote role open worldwide.")
        if any(place.country == requested.country for place in eligible):
            return Match("exact", True, 1.0, f"Remote role eligible in {requested.country}.")
        if any(
            place.kind == "macro" and _country_macro(requested.country) == place.name
            for place in eligible
        ):
            return Match(
                "regional", True, 0.85, f"Remote role eligible across {requested.country}'s region."
            )
        if eligible:
            return Match(
                "mismatch",
                False,
                0.0,
                f"Remote role is not open to candidates in {requested.country}.",
            )
        return Match(
            "mismatch",
            False,
            0.0,
            f"Remote role does not state eligibility for {requested.country}.",
        )
    if info.job_location:
        return Match(
            "mismatch", False, 0.0, f"The listing is not verified as being in {requested.country}."
        )
    return Match(
        "mismatch",
        False,
        0.0,
        f"The listing does not state a work location; cannot verify {requested.country}.",
    )


def _match_macro(requested: Place, info: LocationInfo) -> Match:
    work_places = find_all_locations(info.job_location) if info.job_location else []
    for place in work_places:
        if place.country and _country_macro(place.country) == requested.name:
            return Match("regional", True, 0.85, f"Work location is in {requested.name}.")
    if info.workplace_type in _REMOTE_TYPES:
        eligible = _eligible_places(info.eligible_locations)
        if any(place.kind == "worldwide" for place in eligible):
            return Match("remote", True, 1.0, "Remote role open worldwide.")
        if any(place.kind == "macro" and place.name == requested.name for place in eligible):
            return Match("exact", True, 1.0, f"Remote role eligible across {requested.name}.")
        if any(
            place.country and _country_macro(place.country) == requested.name
            for place in eligible
        ):
            return Match(
                "regional", True, 0.85, f"Remote role eligible in {requested.name}."
            )
        if eligible:
            return Match(
                "mismatch",
                False,
                0.0,
                f"Remote role is not open across {requested.name}.",
            )
        return Match(
            "mismatch",
            False,
            0.0,
            f"Remote role does not state eligibility across {requested.name}.",
        )
    return Match("mismatch", False, 0.0, f"The listing is not verified as being in {requested.name}.")



def _country_macro(country: str) -> str:
    record = COUNTRY_ALIASES.get(country)
    return record[1] if record else ""


def describe_remote(info: LocationInfo) -> str:
    if info.workplace_type not in ("remote", "hybrid"):
        return info.job_location or "Location not stated"
    if info.remote_scope == "worldwide":
        return "Remote · worldwide"
    if info.eligible_locations:
        return f"Remote · {', '.join(info.eligible_locations[:3])}"
    if info.job_location:
        return f"Remote · {info.job_location}"
    return "Remote · eligibility unknown"


def country_for(text: str) -> str:
    place = find_location(text)
    return place.country if place and place.country else ""


def country_code_for(text: str) -> str:
    place = find_location(text)
    if place is None:
        return ""
    return place.country_code or (COUNTRY_ALIASES.get(place.country, ("",))[0] if place.country else "")


def city_slug(text: str) -> str:
    place = find_location(text)
    target = place.city if place and place.city else text
    return re.sub(r"[^a-z0-9]+", "-", (target or "").lower()).strip("-")
