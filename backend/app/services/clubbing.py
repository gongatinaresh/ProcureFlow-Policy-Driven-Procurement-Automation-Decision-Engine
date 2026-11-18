from datetime import datetime
from typing import Any, Mapping

from app.services.procurement_types import ItemMatch, RequestLine, Requirement
from app.utils.time import business_date


def _sort_line(line: RequestLine) -> tuple[datetime, str, int]:
    return line.created_at, line.pr_no, line.pr_line


def club_lines(
    lines: list[RequestLine],
    matches: Mapping[tuple[str, int], ItemMatch],
    settings: Mapping[str, Any],
    known_plant_codes: set[str],
) -> list[Requirement]:
    ordered = sorted(lines, key=_sort_line)
    requirements: list[Requirement] = []
    creation_days = int(settings["creation_window_days"])
    need_by_days = int(settings["need_by_window_days"])

    for line in ordered:
        match = matches[line.key]
        item = match.item
        plant_code = line.plant_code if line.plant_code in known_plant_codes else None
        matching_uom = (
            item is not None and line.uom.strip().casefold() == item.uom.strip().casefold()
        )
        can_club = (
            (matching_uom or (item is None and settings["unmatched_items_club"]))
            and plant_code is not None
        )
        selected: Requirement | None = None
        if can_club:
            candidates = [
                requirement
                for requirement in requirements
                if requirement.item is not None
                and (
                    not settings["same_item"]
                    or (
                        requirement.item is not None
                        and item is not None
                        and requirement.item.item_code == item.item_code
                    )
                )
                and (not settings["same_plant"] or requirement.plant_code == plant_code)
                and (
                    business_date(line.created_at) - requirement.requirement_date
                ).days
                >= 0
                and (
                    (business_date(line.created_at) - requirement.requirement_date).days
                    <= creation_days
                    if settings["creation_window_inclusive"]
                    else (business_date(line.created_at) - requirement.requirement_date).days
                    < creation_days
                )
                and (
                    abs((line.need_by_date - requirement.need_by_date).days) <= need_by_days
                    if settings["need_by_window_inclusive"]
                    else abs((line.need_by_date - requirement.need_by_date).days) < need_by_days
                )
            ]
            if candidates:
                selected = min(candidates, key=lambda requirement: requirement.ordinal)

        if selected is None:
            requirement = Requirement(
                ordinal=len(requirements) + 1,
                lines=[line],
                item=item,
                plant_code=plant_code,
                match=match,
                requirement_date=business_date(line.created_at),
                first_created_at=line.created_at,
                need_by_date=line.need_by_date,
                quantity=line.quantity,
                is_emergency=(
                    line.urgency.casefold()
                    == str(settings["emergency_urgency_value"]).casefold()
                ),
            )
            requirements.append(requirement)
            selected = requirement
        else:
            selected.lines.append(line)
            selected.quantity += line.quantity
            selected.is_emergency = selected.is_emergency or (
                line.urgency.casefold()
                == str(settings["emergency_urgency_value"]).casefold()
            )

        if plant_code is None:
            selected.non_processed_reason = f"Unsupported plant {line.plant_code!r}."
        elif match.method == "unknown_item_code":
            selected.non_processed_reason = (
                f"Supplied item code {line.supplied_item_code!r} does not exist in the item master."
            )
        elif item is not None and not matching_uom:
            selected.non_processed_reason = (
                f"Unit of measure {line.uom!r} does not match item "
                f"{item.item_code!r} unit {item.uom!r}."
            )
        elif match.method == "ambiguous":
            selected.non_processed_reason = (
                f"Item description is ambiguous; candidates: "
                f"{', '.join(code for code, _ in match.candidates[:3])}."
            )

    return requirements
