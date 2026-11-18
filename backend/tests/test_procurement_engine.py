import csv
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, Decision, DecisionLine, PurchaseRequirement, Supplier
from app.services.clubbing import club_lines
from app.services.contract_selection import (
    applicable_contracts,
    select_contract_with_capacity,
)
from app.services.master_data_import import import_contract_pdfs, import_master_data
from app.services.policy_engine import (
    evaluate_condition,
    ordered_rules,
    resolve_policy,
)
from app.services.po_consolidation import consolidate_purchase_orders
from app.services.procurement_engine import (
    get_preferred_supplier_list,
    override_decision,
    process_purchase_requests,
)
from app.services.procurement_types import ItemMatch, RequestLine, Requirement
from app.services.supplier_ranking import rank_preferred_suppliers, suggest_suppliers

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "Cantill_FullStack_Task_Kit"
SAMPLE_BATCH = SOURCE_ROOT / "pr_batches" / "sample_pr_batch.csv"

EXAMPLES = [
    (("PR-26-00019/1",), "PO", "CONTRACT_PO", "5.2", "S001", 1),
    (("PR-26-00045/1",), "PO", "CONTRACT_PO", "5.2", "S021", 1),
    (("PR-26-00004/1", "PR-26-00026/1"), "SOURCING_REQUEST", "RFQ", "5.5", None, 1),
    (("PR-26-00034/1", "PR-26-00032/1"), "PO", "CONTRACT_PO", "5.2", "S007", 1),
    (
        ("PR-26-00146/1", "PR-26-00144/1", "PR-26-00145/1"),
        "PO",
        "SPOT_PO",
        "5.4",
        "S024",
        1,
    ),
    (("PR-26-00106/1", "PR-26-00103/1"), "PO", "SPOT_PO", "5.4", None, 2),
    (("PR-26-00088/1",), "PO", "SPOT_PO", "5.4", "S023", 1),
    (("PR-26-00059/1",), "PO", "EMERGENCY_PO", "5.3", "S014", 1),
    (("PR-26-00089/1",), "SOURCING_REQUEST", "RFQ", "5.5", None, 1),
    (("PR-26-00050/1",), "SOURCING_REQUEST", "RFQ", "5.5", None, 1),
    (("PR-26-00100/1",), "SOURCING_REQUEST", "RFP", "5.6", None, 1),
    (("PR-26-00047/1",), "SOURCING_REQUEST", "CAPEX_SOURCING", "5.1", None, 1),
    (("PR-26-00170/1",), "SOURCING_REQUEST", "NEW_ITEM_SOURCING", "1.2", None, 1),
    (
        (
            "PR-26-00031/1",
            "PR-26-00032/1",
            "PR-26-00034/1",
            "PR-26-00035/1",
        ),
        "PO",
        "CONTRACT_PO",
        "5.2",
        "S007",
        2,
    ),
]


def _source_lines(keys: tuple[str, ...]) -> list[RequestLine]:
    with SAMPLE_BATCH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    selected = {
        f"{row['pr_no']}/{row['pr_line']}": row
        for row in rows
        if f"{row['pr_no']}/{row['pr_line']}" in keys
    }
    assert set(selected) == set(keys)
    return [
        RequestLine(
            pr_no=row["pr_no"],
            pr_line=int(row["pr_line"]),
            created_at=datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M"),
            requester=row["requester"],
            department=row["department"],
            plant_code=row["plant"],
            supplied_item_code=row["item_code"] or None,
            description=row["description"],
            quantity=Decimal(row["quantity"]),
            uom=row["uom"],
            estimated_unit_price=(
                Decimal(row["estimated_unit_price"])
                if row["estimated_unit_price"]
                else None
            ),
            need_by_date=date.fromisoformat(row["need_by_date"]),
            urgency=row["urgency"],
        )
        for key in keys
        for row in [selected[key]]
    ]


def _import_policy_source(session: Session) -> None:
    import_master_data(session, SOURCE_ROOT)
    import_contract_pdfs(session, SOURCE_ROOT)


@pytest.mark.parametrize(
    "keys,outcome,route,clause,supplier_id,requirement_count",
    EXAMPLES,
    ids=[f"worked-example-{number}" for number in range(1, 15)],
)
def test_all_worked_examples(
    test_session_factory: sessionmaker[Session],
    keys: tuple[str, ...],
    outcome: str,
    route: str,
    clause: str,
    supplier_id: str | None,
    requirement_count: int,
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        result = process_purchase_requests(
            session, _source_lines(keys), policy_version="5.0"
        )

        assert len(result.requirements) == requirement_count
        assert {requirement.decision for requirement in result.requirements} == {outcome}
        assert {requirement.route for requirement in result.requirements} == {route}
        assert {requirement.clause for requirement in result.requirements} == {clause}
        if supplier_id is not None:
            assert {requirement.supplier.supplier_id for requirement in result.requirements} == {
                supplier_id
            }
        assert all(requirement.reason for requirement in result.requirements)
        assert all(
            requirement.evidence["policy_version"] == "5.0"
            for requirement in result.requirements
        )
        if keys == ("PR-26-00170/1",):
            requirement = result.requirements[0]
            assert requirement.price_source == "requester_estimate"
            assert requirement.value == Decimal("141100.00")
            assert requirement.suggested_suppliers == []
        if keys == ("PR-26-00088/1",):
            assert result.requirements[0].item.item_code == "ITM-1035"
            assert result.requirements[0].unit_price == Decimal("391.82")
            assert result.requirements[0].value == Decimal("97955.00")
        if keys == ("PR-26-00059/1",):
            assert result.requirements[0].unit_price == Decimal("106.38")
            assert result.requirements[0].value == Decimal("253184.40")
        if keys == ("PR-26-00146/1", "PR-26-00144/1", "PR-26-00145/1"):
            assert len(result.requirements[0].lines) == 3
            assert result.requirements[0].quantity == Decimal("760")
        if keys == ("PR-26-00106/1", "PR-26-00103/1"):
            assert len({requirement.plant_code for requirement in result.requirements}) == 2
        if keys == ("PR-26-00089/1",):
            assert [
                supplier.supplier_id
                for supplier in result.requirements[0].suggested_suppliers
            ] == ["S043", "S044", "S042"]
        if keys == ("PR-26-00050/1",):
            assert [
                supplier.supplier_id
                for supplier in result.requirements[0].suggested_suppliers
            ] == ["S003", "S002", "S005"]
        if keys == ("PR-26-00100/1",):
            assert [
                supplier.supplier_id
                for supplier in result.requirements[0].suggested_suppliers
            ][:4] == ["S020", "S017", "S019", "S018"]
            assert len(result.requirements[0].suggested_suppliers) == 4
        if keys == ("PR-26-00047/1",):
            assert [
                supplier.supplier_id
                for supplier in result.requirements[0].suggested_suppliers
            ] == ["S045", "S047", "S020", "S048", "S046"]
        if keys == (
            "PR-26-00031/1",
            "PR-26-00032/1",
            "PR-26-00034/1",
            "PR-26-00035/1",
        ):
            assert len(result.po_groups) == 1
            assert sorted(len(requirement.lines) for requirement in result.requirements) == [2, 2]


def test_pdf_only_contract_example_records_pdf_provenance(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        result = process_purchase_requests(
            session, _source_lines(("PR-26-00045/1",)), policy_version="5.0"
        )
        contract = result.requirements[0].contract
        assert contract.contract_id == "RC-2026-031"
        assert contract.source_type == "pdf_manual_review"
        assert result.requirements[0].evidence["contract"]["rate"] == "2439.01"
        assert result.requirements[0].evidence["contract"]["source_sha256"]
        assert "PDF source" in result.requirements[0].reason


def test_worked_preferred_supplier_rankings(
    test_session_factory: sessionmaker[Session],
) -> None:
    expected = {
        "ITM-1048": ["S029", "S030"],
        "ITM-1036": ["S023", "S022", "S024"],
        "ITM-1016": ["S009", "S010"],
    }
    with test_session_factory() as session:
        _import_policy_source(session)
        for item_code, supplier_ids in expected.items():
            rows = get_preferred_supplier_list(
                session, item_code, date(2026, 11, 1), policy_version="5.0"
            )
            assert [row["supplier"].supplier_id for row in rows] == supplier_ids
        inactive = session.get(Supplier, "S031")
        assert inactive is not None and inactive.status == "Inactive"


def test_decision_persistence_is_idempotent_and_records_policy_clause(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        lines = _source_lines(("PR-26-00019/1",))
        first = process_purchase_requests(session, lines, policy_version="5.0")
        second = process_purchase_requests(session, lines, policy_version="5.0")

        assert first.run_id == second.run_id
        decision = session.scalar(select(Decision))
        assert decision is not None
        assert decision.policy_rule_id is not None
        assert decision.clause == "5.2"
        assert decision.evidence["policy_version"] == "5.0"
        assert decision.evidence["policy_rule_id"] == decision.policy_rule_id
        assert session.scalar(select(DecisionLine)) is not None
        assert session.scalar(select(PurchaseRequirement)).decision_run_id is not None


def test_engine_rejects_duplicate_pr_lines_and_identifies_unsupported_plant(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        line = _source_lines(("PR-26-00019/1",))[0]
        with pytest.raises(ValueError, match="Duplicate PR line"):
            process_purchase_requests(session, [line, line], policy_version="5.0")
        unsupported = RequestLine(
            **{**line.__dict__, "pr_no": "PR-UNSUPPORTED", "plant_code": "UNKNOWN"}
        )
        result = process_purchase_requests(session, [unsupported], policy_version="5.0")
        assert result.requirements[0].decision == "NOT_PROCESSED"
        assert "Unsupported plant" in result.requirements[0].reason


def test_generic_condition_evaluator_and_policy_rule_order(
    test_session_factory: sessionmaker[Session],
) -> None:
    context = {
        "value": Decimal("500000"),
        "history": {"recent": True},
        "plant": {"code": "CHK1"},
    }
    assert evaluate_condition(
        {
            "all": [
                {"field": "value", "operator": "lte", "value": "500000"},
                {
                    "any": [
                        {"field": "history.recent", "operator": "eq", "value": True},
                        {"field": "plant.code", "operator": "eq", "value": "PUN1"},
                    ]
                },
            ]
        },
        context,
    )
    assert evaluate_condition(
        {"not": [{"field": "plant.code", "operator": "eq", "value": "PUN1"}]},
        context,
    )
    with test_session_factory() as session:
        policy = resolve_policy(session, "5.0", date(2026, 11, 1))
        assert [rule.clause for rule in ordered_rules(session, policy)] == [
            "5.1",
            "5.2",
            "5.3",
            "5.4",
            "5.5",
            "5.6",
        ]


def test_policy_threshold_order_and_boundaries(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        policy = resolve_policy(session, "5.0", date(2026, 11, 1))
        rules = ordered_rules(session, policy)
        thresholds = {
            "emergency_maximum_value": "500000",
            "spot_maximum_value": "200000",
            "rfp_minimum_value": "1000000",
        }

        def clause_for(value: str, emergency: bool, item_type: str = "Material") -> str:
            context = {
                "item": {"item_type": item_type},
                "contract": {"usable": False},
                "requirement": {
                    "is_emergency": emergency,
                    "value": Decimal(value),
                },
                "history": {"recent_active_purchase": True},
                "thresholds": thresholds,
            }
            return next(
                rule.clause for rule in rules if evaluate_condition(rule.condition, context)
            )

        assert clause_for("500000", True) == "5.3"
        assert clause_for("500001", True) == "5.5"
        assert clause_for("199999.99", False) == "5.4"
        assert clause_for("200000", False) == "5.5"
        assert clause_for("999999.99", False) == "5.5"
        assert clause_for("1000000", False) == "5.6"
        assert clause_for("500000", True, "Capex") == "5.1"


def test_clubbing_earliest_qualifying_requirement_and_14_day_boundary() -> None:
    item = SimpleNamespace(item_code="ITEM-1", uom="NOS")
    settings = {
        "creation_window_days": 14,
        "creation_window_inclusive": True,
        "need_by_window_days": 7,
        "need_by_window_inclusive": True,
        "same_item": True,
        "same_plant": True,
        "unmatched_items_club": False,
        "emergency_urgency_value": "Emergency",
    }

    def line(number: int, created: date, need_by: date) -> RequestLine:
        return RequestLine(
            pr_no=f"PR-{number}",
            pr_line=1,
            created_at=datetime.combine(created, datetime.min.time()),
            requester="requester",
            department="stores",
            plant_code="PUN1",
            supplied_item_code="ITEM-1",
            description="item",
            quantity=Decimal("1"),
            uom="NOS",
            estimated_unit_price=None,
            need_by_date=need_by,
        )

    lines = [
        line(1, date(2026, 1, 1), date(2026, 1, 10)),
        line(2, date(2026, 1, 3), date(2026, 1, 20)),
        line(3, date(2026, 1, 4), date(2026, 1, 17)),
    ]
    matches = {
        request.key: ItemMatch(item, Decimal("1"), [], "item_code")
        for request in lines
    }
    requirements = club_lines(lines, matches, settings, {"PUN1"})
    assert [len(requirement.lines) for requirement in requirements] == [2, 1]
    assert requirements[0].lines[-1].pr_no == "PR-3"

    at_boundary = [
        line(4, date(2026, 1, 1), date(2026, 1, 10)),
        line(5, date(2026, 1, 15), date(2026, 1, 17)),
    ]
    at_boundary_matches = {
        request.key: ItemMatch(item, Decimal("1"), [], "item_code")
        for request in at_boundary
    }
    assert len(club_lines(at_boundary, at_boundary_matches, settings, {"PUN1"})) == 1


def test_clubbing_inclusive_14_day_and_plus_or_minus_7_day_boundaries() -> None:
    item = SimpleNamespace(item_code="ITEM-1", uom="NOS")
    settings = {
        "creation_window_days": 14,
        "creation_window_inclusive": True,
        "need_by_window_days": 7,
        "need_by_window_inclusive": True,
        "same_item": True,
        "same_plant": True,
        "unmatched_items_club": False,
        "emergency_urgency_value": "Emergency",
    }

    def line(number: int, created: date, need_by: date, plant: str = "PUN1") -> RequestLine:
        return RequestLine(
            pr_no=f"BOUNDARY-{number}",
            pr_line=1,
            created_at=datetime.combine(created, datetime.min.time()),
            requester="requester",
            department="stores",
            plant_code=plant,
            supplied_item_code="ITEM-1",
            description="item",
            quantity=Decimal("1"),
            uom="NOS",
            estimated_unit_price=None,
            need_by_date=need_by,
        )

    first = line(1, date(2026, 1, 1), date(2026, 1, 10))
    cases = (
        (line(2, date(2026, 1, 15), date(2026, 1, 17)), 1),
        (line(3, date(2026, 1, 16), date(2026, 1, 17)), 2),
        (line(4, date(2026, 1, 15), date(2026, 1, 18)), 2),
        (line(5, date(2026, 1, 15), date(2026, 1, 3)), 1),
        (line(6, date(2026, 1, 15), date(2026, 1, 2)), 2),
        (line(7, date(2026, 1, 2), date(2026, 1, 10), plant="CHK1"), 2),
    )
    for candidate, expected_group_count in cases:
        lines = [first, candidate]
        matches = {
            request.key: ItemMatch(item, Decimal("1"), [], "item_code")
            for request in lines
        }
        requirements = club_lines(lines, matches, settings, {"PUN1", "CHK1"})
        assert len(requirements) == expected_group_count
        if expected_group_count == 1:
            assert len(requirements[0].lines) == 2


def test_contract_selection_uses_matching_item_line_and_ceiling() -> None:
    contract = SimpleNamespace(
        id=1,
        contract_id="RC-1",
        supplier_id="S1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )
    lines = {
        (1, "ITEM-A"): SimpleNamespace(item_code="ITEM-A", rate=Decimal("10")),
        (1, "ITEM-B"): SimpleNamespace(item_code="ITEM-B", rate=Decimal("20")),
    }
    applicable = applicable_contracts(
        "ITEM-B",
        date(2026, 12, 31),
        [contract],
        lines,
        {"S1"},
        {"start_date_inclusive": True, "end_date_inclusive": True},
    )
    assert applicable[0][1].rate == Decimal("20")
    assert select_contract_with_capacity(
        applicable, {1: Decimal("60")}, {1: Decimal("59")}
    ) is None
    assert select_contract_with_capacity(
        applicable, {1: Decimal("60")}, {1: Decimal("60")}
    ) == applicable[0]


def test_contract_selection_uses_cheapest_rate_that_fits_capacity() -> None:
    cheap = SimpleNamespace(
        id=1,
        contract_id="RC-CHEAP",
        start_date=date(2026, 1, 1),
    )
    alternate = SimpleNamespace(
        id=2,
        contract_id="RC-ALTERNATE",
        start_date=date(2026, 1, 1),
    )
    applicable = [
        (cheap, SimpleNamespace(rate=Decimal("10"))),
        (alternate, SimpleNamespace(rate=Decimal("12"))),
    ]
    assert select_contract_with_capacity(
        applicable,
        {1: Decimal("100"), 2: Decimal("120")},
        {1: Decimal("99"), 2: Decimal("120")},
    ) == applicable[1]
    assert select_contract_with_capacity(
        applicable,
        {1: Decimal("100"), 2: Decimal("120")},
        {1: Decimal("100"), 2: Decimal("120")},
    ) == applicable[0]


def test_open_purchase_order_consolidation_uses_earliest_qualifying_order() -> None:
    supplier = SimpleNamespace(supplier_id="S001")
    first_time = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    requirements = [
        Requirement(
            ordinal=index,
            lines=[
                RequestLine(
                    pr_no=f"PR-{index}",
                    pr_line=1,
                    created_at=first_time + timedelta(hours=index),
                    requester="buyer",
                    department="stores",
                    plant_code="PUN1",
                    supplied_item_code="ITEM",
                    description="item",
                    quantity=Decimal("1"),
                    uom="NOS",
                    estimated_unit_price=None,
                    need_by_date=date(2026, 11, 20),
                )
            ],
            item=SimpleNamespace(item_code="ITEM"),
            plant_code="PUN1",
            match=None,
            requirement_date=date(2026, 11, 2),
            first_created_at=first_time + timedelta(hours=index),
            need_by_date=date(2026, 11, 20),
            quantity=Decimal("1"),
            is_emergency=False,
            decision="PO",
            route="SPOT_PO",
            supplier=supplier,
        )
        for index in (1, 2)
    ]
    open_pos = [
        SimpleNamespace(
            po_number="PO-LATER",
            supplier_id="S001",
            plant_code="PUN1",
            route="SPOT_PO",
            status="draft",
            created_at=first_time,
        ),
        SimpleNamespace(
            po_number="PO-EARLIER",
            supplier_id="S001",
            plant_code="PUN1",
            route="SPOT_PO",
            status="pending_approval",
            created_at=first_time - timedelta(minutes=5),
        ),
    ]
    groups = consolidate_purchase_orders(
        requirements,
        {
            "window_hours": 24,
            "window_inclusive": True,
            "open_purchase_order_statuses": ["draft", "pending_approval"],
        },
        "a" * 64,
        open_pos,
    )
    assert list(groups) == ["PO-EARLIER"]
    assert all(
        requirement.purchase_order_group == "PO-EARLIER"
        for requirement in requirements
    )


def test_po_consolidation_includes_exact_24_hour_boundary_only() -> None:
    supplier = SimpleNamespace(supplier_id="S001")
    first_time = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    requirements = [
        Requirement(
            ordinal=index,
            lines=[
                RequestLine(
                    pr_no=f"PO-BOUNDARY-{index}",
                    pr_line=1,
                    created_at=first_time + offset,
                    requester="buyer",
                    department="stores",
                    plant_code="PUN1",
                    supplied_item_code="ITEM",
                    description="item",
                    quantity=Decimal("1"),
                    uom="NOS",
                    estimated_unit_price=None,
                    need_by_date=date(2026, 11, 20),
                )
            ],
            item=SimpleNamespace(item_code="ITEM"),
            plant_code="PUN1",
            match=None,
            requirement_date=(first_time + offset).date(),
            first_created_at=first_time + offset,
            need_by_date=date(2026, 11, 20),
            quantity=Decimal("1"),
            is_emergency=False,
            decision="PO",
            route="SPOT_PO",
            supplier=supplier,
        )
        for index, offset in (
            (1, timedelta(0)),
            (2, timedelta(hours=24)),
            (3, timedelta(hours=24, seconds=1)),
        )
    ]
    groups = consolidate_purchase_orders(
        requirements,
        {
            "window_hours": 24,
            "window_inclusive": True,
            "open_purchase_order_statuses": ["draft", "pending_approval"],
        },
        "b" * 64,
    )
    assert len(groups) == 2
    assert requirements[0].purchase_order_group == requirements[1].purchase_order_group
    assert requirements[2].purchase_order_group != requirements[0].purchase_order_group


def test_supplier_ranking_never_selects_blocked_or_inactive() -> None:
    suppliers = [
        SimpleNamespace(
            supplier_id="S1",
            status="Active",
            categories=["C"],
            on_time_pct=90,
            quality_rating=4,
        ),
        SimpleNamespace(
            supplier_id="S2",
            status="Blocked",
            categories=["C"],
            on_time_pct=100,
            quality_rating=5,
        ),
        SimpleNamespace(
            supplier_id="S3",
            status="Inactive",
            categories=["C"],
            on_time_pct=100,
            quality_rating=5,
        ),
    ]
    ranked = rank_preferred_suppliers(
        "I1",
        "C",
        date(2026, 11, 1),
        suppliers,
        [],
        [],
        {},
        {
            "lookback_days": 548,
            "lookback_start_inclusive": True,
            "lookback_end_inclusive": True,
            "eligible_statuses": ["Active"],
            "score_weights": {
                "purchase_frequency": "0.35",
                "recency": "0.25",
                "contract_history": "0.20",
                "on_time_delivery": "0.10",
                "quality_rating": "0.10",
            },
            "recency_buckets": [],
            "quality_rating_maximum": "5",
        },
    )
    suggested = suggest_suppliers(
        ranked,
        suppliers,
        "C",
        3,
        {"Active"},
        {
            "suggestion_top_up_weights": {
                "on_time_delivery": "0.5",
                "quality_rating": "0.5",
            },
            "quality_rating_maximum": "5",
        },
    )
    assert [supplier.supplier_id for supplier in suggested] == ["S1"]


def test_supplier_suggestions_exclude_blocked_and_inactive_under_amended_eligibility() -> None:
    suppliers = [
        SimpleNamespace(
            supplier_id=supplier_id,
            status=status,
            categories=["C"],
            on_time_pct=100,
            quality_rating=5,
        )
        for supplier_id, status in (
            ("S1", "Active"),
            ("S2", "Blocked"),
            ("S3", "Inactive"),
        )
    ]
    suggestions = suggest_suppliers(
        [],
        suppliers,
        "C",
        3,
        {"Active", "Blocked", "Inactive"},
        {
            "suggestion_top_up_weights": {
                "on_time_delivery": "0.5",
                "quality_rating": "0.5",
            },
            "quality_rating_maximum": "5",
        },
    )
    assert [supplier.supplier_id for supplier in suggestions] == ["S1"]


def test_policy_eligibility_amendment_cannot_make_blocked_contract_supplier_usable(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        policy = resolve_policy(session, "5.0", date(2026, 11, 1))
        document = deepcopy(policy.policy_document)
        document["settings"]["supplier_eligibility"]["allowed_statuses"] = [
            "Active",
            "Blocked",
            "Inactive",
        ]
        document["settings"]["preferred_suppliers"]["eligible_statuses"] = [
            "Active",
            "Blocked",
            "Inactive",
        ]
        policy.policy_document = document
        supplier = session.get(Supplier, "S021")
        assert supplier is not None
        supplier.status = "Blocked"
        session.flush()

        result = process_purchase_requests(
            session, _source_lines(("PR-26-00045/1",)), policy_version="5.0"
        )
        requirement = result.requirements[0]
        assert requirement.contract is None
        assert requirement.supplier is None or requirement.supplier.status == "Active"
        assert all(
            entry.status == "Active" for entry in requirement.suggested_suppliers
        )


def test_decision_override_requires_reason_and_audits_actor(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        _import_policy_source(session)
        process_purchase_requests(
            session, _source_lines(("PR-26-00050/1",)), policy_version="5.0"
        )
        decision = session.scalar(select(Decision))
        assert decision is not None
        with pytest.raises(ValueError, match="written reason"):
            override_decision(
                session,
                decision.id,
                route="RFQ",
                reason=" ",
                actor_id="buyer-1",
            )
        updated = override_decision(
            session,
            decision.id,
            route="RFP",
            reason="Need formal proposal due technical complexity.",
            actor_id="buyer-1",
        )
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "decision_overridden")
        )
        assert updated.overridden
        assert updated.override_reason == "Need formal proposal due technical complexity."
        assert event is not None and event.actor_id == "buyer-1"
