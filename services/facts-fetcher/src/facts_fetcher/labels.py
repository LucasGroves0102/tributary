"""Readable labels for XBRL segment members, from their QNames alone.

A filing's own member labels live in its label linkbase (a separate file we don't fetch), so a
label is derived from the member's name, in this order:

1. `data/member_labels.csv`: curated labels for standard members (us-gaap:NonUsMember →
   "Non-US") and names the rules below get wrong (aapl:WearablesHomeandAccessoriesMember).
2. `country:XX` (ISO 3166 alpha-2) → the country's short name; `stpr:XX` → the US state.
3. Otherwise the local name: drop the prefix and a trailing "Member"/"Domain", then a trailing
   "Segment"/"Segments" (when something remains), split CamelCase into words keeping acronyms
   together (OEMAndOther → OEM and Other), write a leading "I" + word as iWord (IPhone →
   iPhone), and lowercase joining words after the first (and, of, the, including, ...).

Gold matches members across filings by these labels folded (case, spaces, punctuation,
"&" = "and"), so a QName renamed without changing its words stays one member (migration 018,
gold.refresh_segment_financials). Changing these rules changes labels only after
`facts-fetcher-segments --rebuild`.
"""

import csv
import re
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "data"

_TOKENS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^A-Za-z\d]+")
_SMALL = {"and", "of", "the", "in", "for", "to", "on", "by", "with", "or", "a", "an", "at",
          "including", "excluding", "except", "from", "into", "per", "via"}
_SUFFIX = re.compile(r"(Member|Domain)$")
_SEGMENT = re.compile(r"(Reportable)?(Operating)?Segments?$")

COUNTRIES = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "BE": "Belgium", "BM": "Bermuda", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland",
    "CL": "Chile", "CN": "China", "CO": "Colombia", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DK": "Denmark", "EG": "Egypt", "ES": "Spain", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong", "HU": "Hungary",
    "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IT": "Italy",
    "JP": "Japan", "KR": "South Korea", "KY": "Cayman Islands", "KZ": "Kazakhstan",
    "LU": "Luxembourg", "MO": "Macau", "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria",
    "NL": "Netherlands", "NO": "Norway", "NZ": "New Zealand", "PE": "Peru",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PR": "Puerto Rico",
    "PT": "Portugal", "QA": "Qatar", "RO": "Romania", "RU": "Russia", "SA": "Saudi Arabia",
    "SE": "Sweden", "SG": "Singapore", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan",
    "UA": "Ukraine", "US": "United States", "VE": "Venezuela", "VN": "Vietnam",
    "ZA": "South Africa",
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


@cache
def curated() -> dict[str, str]:
    with (DATA / "member_labels.csv").open(newline="") as f:
        return {r["member"]: r["label"] for r in csv.DictReader(f)}


def words(name: str) -> str:
    """CamelCase → words: OEMAndOther → OEM and Other, IPhone → iPhone."""
    tokens = [t for t in _TOKENS.findall(name) if t.strip()]
    out: list[str] = []
    for i, t in enumerate(tokens):
        if t in ".,&-/" and out:
            out[-1] += t
            continue
        if i == 0 and t == "I" and len(tokens) > 1 and tokens[1][:1].isupper() \
                and tokens[1][1:].islower():
            tokens[1] = "i" + tokens[1]
            continue
        if out and t.lower() in _SMALL and t[:1].isupper() and t[1:].islower():
            t = t.lower()
        out.append(t)
    return " ".join(out)


def member_label(qname: str) -> str:
    """A readable label for a member QName; see the module docstring for the rules."""
    if qname in curated():
        return curated()[qname]
    prefix, _, name = qname.rpartition(":")
    if name.startswith("typed:") or prefix == "typed":
        return qname.split("typed:", 1)[-1]
    if prefix == "country" and name in COUNTRIES:
        return COUNTRIES[name]
    if prefix == "stpr" and name in US_STATES:
        return US_STATES[name]
    name = _SUFFIX.sub("", name) or name
    trimmed = _SEGMENT.sub("", name)
    if trimmed:
        name = trimmed
    return words(name) or qname

