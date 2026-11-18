from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any


def contract_is_applicable(
    contract: Any,
    contract_line: Any,
    requirement_date: date,
    active_supplier_ids: set[str],
    settings: Mapping[str, Any],
) -> bool:
    if contract_line.item_code is None or contract.supplier_id not in active_supplier_ids:
        return False
    start_ok = (
        contract.start_date <= requirement_date
        if settings["start_date_inclusive"]
        else contract.start_date < requirement_date
    )
    end_ok = (
        requirement_date <= contract.end_date
        if settings["end_date_inclusive"]
        else requirement_date < contract.end_date
    )
    return start_ok and end_ok


def applicable_contracts(
    item_code: str,
    requirement_date: date,
    contracts: Sequence[Any],
    contract_lines_by_contract_item: Mapping[tuple[int, str], Any],
    active_supplier_ids: set[str],
    settings: Mapping[str, Any],
) -> list[tuple[Any, Any]]:
    applicable: list[tuple[Any, Any]] = []
    for contract in contracts:
        contract_line = contract_lines_by_contract_item.get((contract.id, item_code))
        if (
            contract_line is not None
            and contract_is_applicable(
                contract, contract_line, requirement_date, active_supplier_ids, settings
            )
        ):
            applicable.append((contract, contract_line))
    return sorted(
        applicable,
        key=lambda pair: (Decimal(pair[1].rate), pair[0].start_date, pair[0].contract_id),
    )


def select_contract_with_capacity(
    applicable: Sequence[tuple[Any, Any]],
    requirement_value_at_rate: Mapping[int, Decimal],
    available_capacity: Mapping[int, Decimal],
) -> tuple[Any, Any] | None:
    for contract, contract_line in applicable:
        required = requirement_value_at_rate[contract.id]
        if required <= available_capacity.get(contract.id, Decimal("0")):
            return contract, contract_line
    return None


def contract_for_fallback_valuation(
    applicable: Sequence[tuple[Any, Any]],
) -> tuple[Any, Any] | None:
    return applicable[0] if applicable else None
