from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from app.services.procurement_types import Requirement


def consolidate_purchase_orders(
    requirements: list[Requirement],
    settings: Mapping[str, Any],
    run_key: str,
    open_purchase_orders: list[Any] | None = None,
) -> dict[str, list[Requirement]]:
    window = timedelta(hours=int(settings["window_hours"]))
    groups: dict[str, list[Requirement]] = {}
    anchors: dict[str, Requirement] = {}
    sequence = 1
    ordered = sorted(
        requirements,
        key=lambda requirement: (
            requirement.first_created_at,
            requirement.lines[0].pr_no,
            requirement.lines[0].pr_line,
        ),
    )
    existing_pos = [
        purchase_order
        for purchase_order in (open_purchase_orders or [])
        if purchase_order.status in settings["open_purchase_order_statuses"]
    ]
    for requirement in ordered:
        if requirement.decision != "PO" or requirement.supplier is None:
            continue
        candidates: list[tuple[Any, str]] = []
        for purchase_order in existing_pos:
            anchor_time = purchase_order.created_at
            if anchor_time.tzinfo is None and requirement.first_created_at.tzinfo is not None:
                anchor_time = anchor_time.replace(tzinfo=requirement.first_created_at.tzinfo)
            delta = requirement.first_created_at - anchor_time
            if (
                purchase_order.supplier_id == requirement.supplier.supplier_id
                and purchase_order.plant_code == requirement.plant_code
                and purchase_order.route == requirement.route
                and delta.total_seconds() >= 0
                and (delta <= window if settings["window_inclusive"] else delta < window)
            ):
                candidates.append((anchor_time, purchase_order.po_number))
        for group_id, anchor in anchors.items():
            delta = requirement.first_created_at - anchor.first_created_at
            if (
                anchor.supplier.supplier_id == requirement.supplier.supplier_id
                and anchor.plant_code == requirement.plant_code
                and anchor.route == requirement.route
                and delta.total_seconds() >= 0
                and (delta <= window if settings["window_inclusive"] else delta < window)
            ):
                candidates.append((anchor.first_created_at, group_id))
        if candidates:
            _, group_id = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
            groups.setdefault(group_id, [])
        else:
            group_id = f"PO-{run_key[:8]}-{sequence:04d}"
            sequence += 1
            groups[group_id] = []
            anchors[group_id] = requirement
        groups[group_id].append(requirement)
        requirement.purchase_order_group = group_id
    return groups
