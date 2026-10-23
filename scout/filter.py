"""Title filter: at the applicant's level or not. Substring matches on
the lowercased title, so `engineer ii` matches "Software Engineer II,
YouTube" and `senior` drops "Senior Software Engineer". Word-bounded,
so `iv` does not fire inside "Driver"."""
from __future__ import annotations

import re

from scout import Posting, Watch


def _hit(needle: str, title: str) -> bool:
    needle = needle.strip().lower()
    if not needle:
        return False
    # Word boundaries around the phrase; "sr." keeps its dot, "sr " its space.
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, title) is not None


def at_level(title: str, positive: list[str], negative: list[str]) -> bool:
    title = (title or "").lower()
    if any(_hit(n, title) for n in negative):
        return False
    return not positive or any(_hit(p, title) for p in positive)


def matching(watch: Watch, postings: list[Posting]) -> list[Posting]:
    return [p for p in postings if at_level(p.title, watch.positive, watch.negative)
            and not CLEARANCE_TITLE.search(p.title or "")
            and not (watch.us_only and outside_us(p.location or place_in_url(p.url)))]


_URL_PLACE = re.compile(r"/job/([^/?#]+)/", re.I)


def place_in_url(url: str) -> str:
    """The place a Workday link names (`/job/Bengaluru/...`), for a row that
    lists none: Accenture's rows on Lenny's Jobs carry no location text,
    so "Custom Software Engineer" in Bengaluru read as unknown and was
    queued (2026-10-22). "" when the link names no place."""
    match = _URL_PLACE.search(url or "")
    return match.group(1).replace("-", " ").replace("_", " ") if match else ""


# ------------------------------------------------------------------ place
# "US only" in code, on the listing's location line, before the screen.
# A place is kept only when it reads as the US: the country, a state (by
# name, or its code after a comma: "Austin, TX"), or a major US city. An
# empty line and a bare "Remote" / "Hybrid" stay too, and the screen reads
# the posting. Anything else is out, including a city the lists do not
# know: "only the United States" (2026-10-22) means an unknown place is not
# a yes. It used to be the other way round, and "Herzliya (Remote)" and
# "Remoto" came through.
# Named places abroad. They no longer decide alone (an unknown place is out
# anyway); they overrule a two-letter code, which is ambiguous: "Hyderabad,
# TS, IN" is India, not Indiana, and "Toronto, CA" is Canada.
FOREIGN = [
    "japan", "tokyo", "osaka", "aichi", "okazaki", "netherlands", "best,", "amsterdam", "eindhoven",
    "united kingdom", "uk", "london", "england", "scotland", "ireland", "dublin", "germany", "berlin",
    "munich", "france", "paris", "spain", "madrid", "barcelona", "italy", "milan", "rome", "poland",
    "warsaw", "krakow", "kraków", "sweden", "stockholm", "norway", "denmark", "copenhagen", "finland",
    "helsinki", "switzerland", "zurich", "austria", "vienna", "belgium", "brussels", "portugal",
    "lisbon", "czech", "prague", "brno", "hungary", "romania", "israel", "tel aviv", "herzliya",
    "india", "bangalore", "bengaluru", "hyderabad", "chennai", "pune", "mumbai", "delhi", "gurgaon",
    "gurugram", "noida", "kolkata", "china", "beijing", "shanghai", "shenzhen", "hong kong", "taiwan",
    "taipei", "korea", "seoul", "singapore", "australia", "sydney", "melbourne", "new zealand",
    "auckland", "canada", "toronto", "vancouver", "montreal", "ottawa", "waterloo", "mexico",
    "jalisco", "brazil", "sao paulo", "são paulo", "argentina", "colombia", "chile", "philippines",
    "manila", "vietnam", "indonesia", "jakarta", "malaysia", "thailand", "bangkok", "uae", "dubai",
    "abu dhabi", "saudi", "egypt", "nigeria", "lagos", "kenya", "south africa", "turkey", "istanbul",
    "ukraine", "kyiv", "russia", "moscow", "emea", "apac", "latam",
]
# A region that includes the US is a US place: "Remote (North America)" dropped
# every Hightouch role (2026-10-22).
US_WORDS = ["united states", "usa", "u.s.", "us-", "remote - us", "us remote", "north america", "americas"]
US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
]
US_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks",
    "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny",
    "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
US_CITIES = [
    "san francisco", "sf", "bay area", "silicon valley", "nyc", "seattle", "boston", "austin",
    "los angeles", "chicago", "denver", "atlanta", "san jose", "mountain view", "palo alto",
    "sunnyvale", "menlo park", "cupertino", "santa clara", "redwood city", "san mateo", "redmond",
    "bellevue", "kirkland", "miami", "dallas", "houston", "san diego", "irvine", "pittsburgh",
    "philadelphia", "phoenix", "salt lake city", "raleigh", "durham", "minneapolis", "detroit",
    "nashville", "brooklyn", "jersey city", "columbus", "charlotte", "st. louis", "kansas city",
    "baltimore", "ann arbor", "boulder", "plano", "reston", "mclean", "arlington", "princeton",
    "madison", "oakland", "berkeley", "santa monica", "culver city", "playa vista", "burbank",
]
_CODE_AFTER_COMMA = re.compile(r",\s*([a-z]{2})(?![a-z])")
_BARE = re.compile(r"[^a-z]+")
_WORK_MODE = {"remote", "hybrid", "onsite", "on", "site", "in", "office", "anywhere", "flexible"}


def _foreign(loc: str) -> bool:
    return any(_hit(n.rstrip(","), loc) if not n.endswith(",") else n in loc for n in FOREIGN)


def _reads_us(loc: str) -> bool:
    if any(w in loc for w in US_WORDS) or _hit("us", loc) or any(_hit(s, loc) for s in US_STATES) \
            or any(_hit(c, loc) for c in US_CITIES):
        return True
    # A state code only when nothing in the place names another country.
    return not _foreign(loc) and any(m.group(1) in US_CODES for m in _CODE_AFTER_COMMA.finditer(loc))


_COUNTED = re.compile(r"^\s*\d+\s+locations?\s*$")


def _bare(loc: str) -> bool:
    """Empty, only a work mode ("Remote", "Hybrid", "On-site"), or a count
    ("2 Locations") that hides the places; the screen reads the posting."""
    return bool(_COUNTED.match(loc)) or all(word in _WORK_MODE for word in _BARE.split(loc) if word)


def _part_outside(loc: str) -> bool:
    if _bare(loc) or _reads_us(loc):
        return False
    return True


def outside_us(location: str) -> bool:
    """Every listed place is abroad. "Austin, TX; Toronto, Canada" stays:
    one of them is here."""
    loc = (location or "").lower()
    parts = [x for x in re.split(r"[;|/]|\bor\b", loc) if x.strip()]
    return bool(parts) and all(_part_outside(x) for x in parts)


# ---------------------------------------------------------------- prescreen
# Hard rejects read off the description in code, before the model sees
# it. In bulk most of what the title filter lets through is still not
# entry level ("5+ years of experience" under a plain "Software Engineer"
# title), and each of those is a model call and a wait. Only the cheap,
# high-precision reasons live here; anything softer is the model's.
YEARS_MIN = 4          # "4+ years", "4-6 years", "at least 4 years" and up is not entry level
_YEARS = re.compile(
    r"(?:(?:minimum|at least|min\.?)\s+(?:of\s+)?)?(\d{1,2})\s*(?:\+|\s*(?:-|–|to)\s*\d{1,2})?\s*"
    r"(?:\+\s*)?years?(?:'|’)?\s+(?:of\s+)?(?:\w+\s+){0,4}?experience", re.I)
_CLEARANCE = re.compile(
    r"(?:active|current|hold(?:s|ing)?\s+an?|must\s+(?:have|hold|possess|obtain)\s+an?|requires?\s+an?|"
    r"eligible\s+(?:for|to\s+obtain)\s+an?|(?:able|ability)\s+to\s+obtain(?:\s+and\s+maintain)?\s+an?|"
    r"obtain\s+and\s+maintain\s+an?)\s+(?:(?:active\s+)?(?:top\s+secret|secret|ts/?sci|dod|government|security|"
    r"public\s+trust)\s+)+clearance|\bts/sci\b|\bpolygraph\b",
    re.I)
# The same requirement, said in the title: "AI Engineer, Special Programs - Top Secret
# Clearance", "Software Engineer, Connected Warfare (Active Clearance)". Dropped with the
# title filter, before a description is fetched or a model is asked (2026-10-22).
CLEARANCE_TITLE = re.compile(
    r"clearance|ts/?sci|top\s+secret|\bsecret\b|classified|special\s+programs|polygraph", re.I)
NO_SPONSOR = "no visa sponsorship"
_CITIZEN = re.compile(
    r"(?:u\.?s\.?|united\s+states)\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|is\s+a\s+requirement|only)|"
    r"must\s+be\s+an?\s+(?:u\.?s\.?|united\s+states)\s+citizen|(?:u\.?s\.?|united\s+states)\s+citizens\s+only",
    re.I)

# A posting that will not sponsor a visa, now or later. The person is on F-1 and
# OPT and will need sponsorship, so this is checked before anything else and the
# Scout never queues the role (2026-10-22, "check this first always"). YC's own
# line "US citizen/visa only" is the same thing. "Will you now or in the future
# require sponsorship?" is a form question and does not match.
_NO_SPONSOR = re.compile(
    r"u\.?s\.?\s+citizen\s*/\s*visa\s+only|"
    r"(?:will|do(?:es)?|can|are|is)\s*(?:not|n't)\s+(?:be\s+)?(?:able\s+to\s+)?(?:offer(?:ing)?\s+|provide\s+)?"
    r"(?:visa\s+|immigration\s+|employment\s+)?sponsor|"
    r"(?:unable|not\s+able)\s+to\s+(?:offer\s+|provide\s+)?(?:visa\s+|immigration\s+)?sponsor|"
    r"cannot\s+(?:offer\s+|provide\s+)?(?:visa\s+|immigration\s+)?sponsor|"
    r"\bno\s+(?:visa\s+|immigration\s+|h-?1b\s+)?sponsorship|"
    r"sponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided|possible)|unavailable)|"
    r"not\s+eligible\s+for\s+(?:visa\s+|immigration\s+)?sponsorship|"
    r"without\s+(?:the\s+)?(?:need\s+for\s+)?(?:current\s+or\s+future\s+|present\s+or\s+future\s+|any\s+)?"
    r"(?:visa\s+|employer\s+|immigration\s+)?sponsorship|"
    r"(?:u\.?s\.?|united\s+states)\s+citizens?\s+(?:or|and|/)\s+(?:lawful\s+)?(?:permanent\s+residents?|green\s+card\s+holders?)"
    r"(?:\s+only)?|green\s+card\s+holders?\s+only|"
    r"not\s+(?:open|eligible)\s+to\s+(?:candidates\s+on\s+)?(?:opt|cpt|f-?1)|"
    r"(?:unable|not\s+able)\s+to\s+(?:accept|consider)\s+(?:candidates\s+on\s+)?(?:opt|cpt|f-?1)",
    re.I)


# A form's own question or the candidate's answer reads like a policy: "Are you
# authorized to work in the US without sponsorship?", "Yes, I am authorized to
# work without sponsorship". Upstream's fix (85487d2): a question, or a sentence
# that opens the way an answer opens, is never the employer refusing.
_ANSWER_OPENS = re.compile(
    r"^\s*(?:(?:yes|no|n/?a|not\s+applicable)\s*[,:;\u2014-]|i\s+(?:do|am|have|will|would)\b)", re.I)
_QUESTION_OPENS = re.compile(
    r"^\s*(?:are|do|does|will|would|can|could|is|have|has)\s+(?:you|your)\b", re.I)


_END = re.compile(r"\n|[.!?](?=\s+[A-Z]|\s*$)")


def _sentence(text: str, start: int, end: int) -> str:
    """The sentence (or line) a match sits in. "U.S. without" is one sentence:
    a stop only ends one before a capital or the end of the text."""
    left = max((m.end() for m in _END.finditer(text) if m.end() <= start), default=0)
    after = _END.search(text, end)
    return text[left:after.end() if after else len(text)].strip()


def needs_no_sponsor(text: str) -> bool:
    """True when the posting refuses visa sponsorship. A form's question about
    sponsorship, or an answer to one, is not a refusal."""
    text = text or ""
    for match in _NO_SPONSOR.finditer(text.replace("\r", "")):
        sentence = _sentence(text, match.start(), match.end())
        if sentence.endswith("?") or _QUESTION_OPENS.match(sentence) or _ANSWER_OPENS.match(sentence):
            continue
        return True
    return False


def prescreen(text: str, years_min: int | None = None) -> str | None:
    """Why the posting is out before a model reads it, else None. No visa
    sponsorship (checked first), years of experience at `YEARS_MIN` or more,
    a security clearance, or citizenship as a requirement. "0-2 years" and "2+ years" pass: the
    first number of a range is what the role really asks."""
    text = text or ""
    if not text.strip():
        return None
    if needs_no_sponsor(text):
        return NO_SPONSOR
    years = [int(m.group(1)) for m in _YEARS.finditer(text)]
    years = [y for y in years if y <= 30]      # "2024 years" is a date that slipped in
    if years and min(years) >= (years_min or YEARS_MIN):
        return f"asks for {min(years)}+ years of experience"
    if _CLEARANCE.search(text):
        return "needs a security clearance"
    if _CITIZEN.search(text):
        return "US citizenship required"
    return None
