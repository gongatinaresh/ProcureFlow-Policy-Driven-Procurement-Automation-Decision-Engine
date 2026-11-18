import re
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Mapping

from app.services.procurement_types import ItemMatch, RequestLine

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _normalize(
    text: str,
    abbreviations: Mapping[str, str],
    ignored_terms: set[str],
) -> list[str]:
    tokens = TOKEN_PATTERN.findall(text.casefold())
    normalized: list[str] = []
    for token in tokens:
        replacement = abbreviations.get(token, token)
        for expanded in TOKEN_PATTERN.findall(replacement.casefold()):
            if expanded not in ignored_terms:
                normalized.append(expanded)
    return normalized


def _similarity(left: list[str], right: list[str]) -> Decimal:
    if not left or not right:
        return Decimal("0")
    left_set = set(left)
    right_set = set(right)
    overlap = Decimal(2 * len(left_set & right_set)) / Decimal(len(left_set) + len(right_set))
    char_score = Decimal(str(SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()))
    return max(overlap, char_score)


def match_item(
    line: RequestLine,
    items: list[Any],
    settings: Mapping[str, Any],
) -> ItemMatch:
    if line.supplied_item_code:
        found = next((item for item in items if item.item_code == line.supplied_item_code), None)
        if found is not None:
            return ItemMatch(found, Decimal("1"), [(found.item_code, Decimal("1"))], "item_code")
        return ItemMatch(None, Decimal("0"), [], "unknown_item_code")

    abbreviations = {
        str(key).casefold(): str(value).casefold()
        for key, value in settings.get("abbreviations", {}).items()
    }
    ignored_terms = {str(term).casefold() for term in settings.get("ignored_terms", [])}
    request_tokens = _normalize(line.description, abbreviations, ignored_terms)
    candidates = [
        (
            _similarity(
                request_tokens,
                _normalize(item.description, abbreviations, ignored_terms),
            ),
            item,
        )
        for item in items
    ]
    candidates.sort(key=lambda pair: (-pair[0], pair[1].item_code))
    if not candidates:
        return ItemMatch(None, Decimal("0"))
    score, best_item = candidates[0]
    exposed = [(item.item_code, rank) for rank, item in candidates[:5]]
    threshold = Decimal(str(settings.get("minimum_score", "1")))
    margin = Decimal(str(settings.get("ambiguity_margin", "0")))
    next_score = candidates[1][0] if len(candidates) > 1 else Decimal("0")
    if score < threshold:
        return ItemMatch(None, score, exposed, "unmatched")
    if score - next_score < margin:
        return ItemMatch(None, score, exposed, "ambiguous")
    return ItemMatch(best_item, score, exposed, "description")
