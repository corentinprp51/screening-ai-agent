"""Service-area matching: a place name said by the candidate against the client's list."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain.models import ServiceAreas

# Below this similarity (0 to 1) a name is not a match; at or above it without being
# exact, it is a close match the candidate confirms ("Getaffe" → "Getafe").
FUZZY_MATCH_RATIO = 0.85


@dataclass(frozen=True)
class AreaMatch:
    country: str
    city: str
    zone: str | None = None


@dataclass(frozen=True)
class Matches:
    """Every entry matching a name; `exact` is False when they are close matches only."""

    found: list[AreaMatch]
    exact: bool


def normalize(text: str) -> str:
    """Lowercase, accents removed, punctuation as spaces ("L'Hospitalet" → "l hospitalet")."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]", " ", letters).split())


def match_cities(name: str, areas: ServiceAreas) -> Matches:
    entries = [AreaMatch(country, city) for country, cities in areas.items() for city in cities]
    return _match(name, entries, lambda entry: entry.city)


def match_zones(name: str, areas: ServiceAreas, city: AreaMatch | None = None) -> Matches:
    """Zones of every city, or of `city` only."""
    entries = [
        AreaMatch(country, city_name, zone)
        for country, cities in areas.items()
        for city_name, zones in cities.items()
        for zone in zones
        if city is None or (country, city_name) == (city.country, city.city)
    ]
    return _match(name, entries, lambda entry: entry.zone or "")


def _match(name: str, entries: list[AreaMatch], key: Callable[[AreaMatch], str]) -> Matches:
    """Exact matches after normalization if any, else close matches, most similar first."""
    target = normalize(name)
    exact = [entry for entry in entries if normalize(key(entry)) == target]
    if exact:
        return Matches(exact, exact=True)
    ratios = [
        (SequenceMatcher(None, target, normalize(key(entry))).ratio(), entry) for entry in entries
    ]
    close = [
        entry
        for ratio, entry in sorted(ratios, key=lambda pair: -pair[0])
        if ratio >= FUZZY_MATCH_RATIO
    ]
    return Matches(close, exact=False)
