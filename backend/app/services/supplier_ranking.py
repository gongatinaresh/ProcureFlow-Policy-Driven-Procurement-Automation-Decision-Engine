from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


def supplier_is_eligible(supplier: Any, allowed_statuses: set[str]) -> bool:
    return (
        supplier.status in allowed_statuses
        and supplier.status.casefold() not in {"blocked", "inactive"}
    )


def rank_preferred_suppliers(
    item_code: str,
    item_category: str,
    as_of: date,
    suppliers: Sequence[Any],
    history_rows: Sequence[Any],
    contracts: Sequence[Any],
    contract_lines_by_contract_item: Mapping[tuple[int, str], Any],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    window_days = int(settings["lookback_days"])
    first_date = as_of - timedelta(days=window_days)
    start_inclusive = bool(settings["lookback_start_inclusive"])
    end_inclusive = bool(settings["lookback_end_inclusive"])
    eligible_statuses = set(settings["eligible_statuses"])
    supplier_by_id = {supplier.supplier_id: supplier for supplier in suppliers}
    histories = [
        row
        for row in history_rows
        if row.item_code == item_code
        and (first_date <= row.po_date if start_inclusive else first_date < row.po_date)
        and (row.po_date <= as_of if end_inclusive else row.po_date < as_of)
        and row.supplier_id in supplier_by_id
        and supplier_is_eligible(supplier_by_id[row.supplier_id], eligible_statuses)
    ]
    history_by_supplier: dict[str, list[Any]] = {}
    for row in histories:
        history_by_supplier.setdefault(row.supplier_id, []).append(row)

    contract_by_supplier: dict[str, list[tuple[Any, Any]]] = {}
    for contract in contracts:
        if contract.start_date > as_of or contract.supplier_id not in supplier_by_id:
            continue
        if not supplier_is_eligible(supplier_by_id[contract.supplier_id], eligible_statuses):
            continue
        contract_line = contract_lines_by_contract_item.get((contract.id, item_code))
        if contract_line is not None:
            contract_by_supplier.setdefault(contract.supplier_id, []).append(
                (contract, contract_line)
            )

    counts = {supplier_id: len(rows) for supplier_id, rows in history_by_supplier.items()}
    maximum_count = max(counts.values(), default=0)
    weights = {key: Decimal(str(value)) for key, value in settings["score_weights"].items()}
    recency_buckets = sorted(
        (
            (int(bucket["maximum_age_days"]), Decimal(str(bucket["score"])))
            for bucket in settings["recency_buckets"]
        ),
        key=lambda pair: pair[0],
    )
    quality_maximum = Decimal(str(settings["quality_rating_maximum"]))

    results: list[dict[str, Any]] = []
    member_ids = set(counts) | set(contract_by_supplier)
    for supplier_id in member_ids:
        supplier = supplier_by_id[supplier_id]
        supplier_history = sorted(
            history_by_supplier.get(supplier_id, []),
            key=lambda row: (row.po_date, row.po_number),
            reverse=True,
        )
        supplier_contracts = sorted(
            contract_by_supplier.get(supplier_id, []),
            key=lambda pair: (pair[0].start_date, pair[0].contract_id),
            reverse=True,
        )
        most_recent_po = supplier_history[0] if supplier_history else None
        age_days = (as_of - most_recent_po.po_date).days if most_recent_po else None
        recency = Decimal("0")
        if age_days is not None:
            recency = next(
                (
                    score
                    for maximum_age, score in recency_buckets
                    if age_days <= maximum_age
                ),
                Decimal("0"),
            )
        frequency = (
            Decimal(counts.get(supplier_id, 0)) / Decimal(maximum_count)
            if maximum_count
            else Decimal("0")
        )
        contract_score = Decimal("1") if supplier_contracts else Decimal("0")
        on_time_score = Decimal(supplier.on_time_pct) / Decimal("100")
        quality_score = Decimal(supplier.quality_rating) / quality_maximum
        score = (
            frequency * weights["purchase_frequency"]
            + recency * weights["recency"]
            + contract_score * weights["contract_history"]
            + on_time_score * weights["on_time_delivery"]
            + quality_score * weights["quality_rating"]
        )
        reference_price = (
            Decimal(most_recent_po.unit_price)
            if most_recent_po
            else Decimal(supplier_contracts[0][1].rate)
        )
        results.append(
            {
                "supplier": supplier,
                "score": score,
                "purchase_count": counts.get(supplier_id, 0),
                "most_recent_po_date": most_recent_po.po_date if most_recent_po else None,
                "recency_score": recency,
                "contract_history": bool(supplier_contracts),
                "on_time_score": on_time_score,
                "quality_score": quality_score,
                "reference_price": reference_price,
                "reference_source": (
                    "purchase_order" if most_recent_po else "most_recent_contract"
                ),
            }
        )
    results.sort(
        key=lambda entry: (
            -entry["score"],
            -entry["purchase_count"],
            -(entry["most_recent_po_date"].toordinal() if entry["most_recent_po_date"] else 0),
            entry["supplier"].supplier_id,
        )
    )
    return results


def suggest_suppliers(
    ranked: Sequence[dict[str, Any]],
    suppliers: Sequence[Any],
    item_category: str,
    count: int,
    eligible_statuses: set[str],
    settings: Mapping[str, Any],
) -> list[Any]:
    selected = [entry["supplier"] for entry in ranked[:count]]
    selected_ids = {supplier.supplier_id for supplier in selected}
    eligible = [
        supplier
        for supplier in suppliers
        if supplier_is_eligible(supplier, eligible_statuses)
        and item_category in supplier.categories
        and supplier.supplier_id not in selected_ids
    ]
    weights = {
        key: Decimal(str(value))
        for key, value in settings["suggestion_top_up_weights"].items()
    }
    quality_maximum = Decimal(str(settings["quality_rating_maximum"]))
    eligible.sort(
        key=lambda supplier: (
            -(
                Decimal(supplier.on_time_pct) / Decimal("100")
                * weights["on_time_delivery"]
                + Decimal(supplier.quality_rating)
                / quality_maximum
                * weights["quality_rating"]
            ),
            supplier.supplier_id,
        )
    )
    return (selected + eligible)[:count]
