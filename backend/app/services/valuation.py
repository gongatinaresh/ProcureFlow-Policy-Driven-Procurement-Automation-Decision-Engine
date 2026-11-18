from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


def recent_purchase_history(
    item_code: str,
    requirement_date: date,
    history_rows: Sequence[Any],
    window_days: int,
    *,
    start_inclusive: bool,
    end_inclusive: bool,
) -> list[Any]:
    first_date = requirement_date - timedelta(days=window_days)
    rows = [
        row
        for row in history_rows
        if row.item_code == item_code
        and (
            first_date <= row.po_date if start_inclusive else first_date < row.po_date
        )
        and (row.po_date <= requirement_date if end_inclusive else row.po_date < requirement_date)
    ]
    return sorted(rows, key=lambda row: (row.po_date, row.po_number), reverse=True)


def value_requirement(
    requirement: Any,
    settings: Mapping[str, Any],
    *,
    selected_contract_line: Any | None,
    fallback_contract_line: Any | None,
    recent_po: Any | None,
    item_standard_price: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    scale = int(settings.get("currency_scale", 2))
    quantum = Decimal(1).scaleb(-scale)
    if requirement.item is None:
        if any(line.estimated_unit_price is None for line in requirement.lines):
            return None, None, None
        line_value = sum(
            (line.quantity * line.estimated_unit_price for line in requirement.lines),
            Decimal("0"),
        )
        unit_price = line_value / requirement.quantity
        return unit_price.quantize(quantum), line_value.quantize(quantum), "requester_estimate"

    if selected_contract_line is not None:
        price = Decimal(selected_contract_line.rate)
        source = "contract_rate"
    elif fallback_contract_line is not None:
        price = Decimal(fallback_contract_line.rate)
        source = "lowest_applicable_contract_rate_out_of_ceiling"
    elif recent_po is not None:
        price = Decimal(recent_po.unit_price)
        source = "recent_purchase_order"
    elif item_standard_price is not None:
        price = Decimal(item_standard_price)
        source = "item_standard_price"
    else:
        return None, None, None
    return price, (requirement.quantity * price).quantize(quantum), source
