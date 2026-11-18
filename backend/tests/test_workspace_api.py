from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, PurchaseRequirement
from app.services.master_data_import import import_contract_pdfs, import_master_data

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "Cantill_FullStack_Task_Kit"


def _import_source_data(session: Session) -> None:
    import_master_data(session, SOURCE_ROOT)
    import_contract_pdfs(session, SOURCE_ROOT)
    session.commit()


def test_dashboard_supplier_directory_and_contract_register_are_backend_data(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_source_data(session)

    dashboard = test_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["active_policy_version"] == "5.0"
    assert dashboard.json()["purchase_requests"] == 0

    suppliers = test_client.get(
        "/api/suppliers", params={"as_of": "2026-11-19", "search": "S001"}
    )
    assert suppliers.status_code == 200, suppliers.text
    assert len(suppliers.json()) == 1
    supplier = suppliers.json()[0]
    assert supplier["supplier_id"] == "S001"
    assert supplier["purchase_count"] > 0
    assert supplier["recent_purchase_history"]
    assert supplier["contracts"]

    contracts = test_client.get("/api/contracts")
    assert contracts.status_code == 200, contracts.text
    pdf_only = next(
        item for item in contracts.json() if item["contract_id"] == "RC-2026-031"
    )
    assert pdf_only["review_status"] == "pending"
    assert pdf_only["lines"]
    reviewed = test_client.post(
        f"/api/contracts/{pdf_only['id']}/review",
        json={
            "actor_id": "buyer-1",
            "verified": True,
            "reason": "Signed document checked against imported contract lines.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["verified"] is True
    with test_session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "contract_reviewed")
        )
        assert event is not None
        assert event.actor_id == "buyer-1"


def test_employee_chat_submits_a_request_to_the_policy_engine(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_source_data(session)
    response = test_client.post(
        "/api/chat/requests",
        json={
            "requester": "Test employee",
            "department": "Stores",
            "plant": "PUN1",
            "item": "ITM-1005",
            "quantity": "3000",
            "uom": "KG",
            "need_by_date": "2026-12-01",
            "urgency": "Normal",
            "estimated_unit_price": None,
            "message": "Need 3000 kg MS angle 50x50x5 for Pune by 1 December.",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["pr_no"].startswith("CHAT-")
    assert result["resolved_item_code"] == "ITM-1005"
    assert result["policy_version"] == "5.0"
    assert result["route"] in {
        "CONTRACT_PO",
        "EMERGENCY_PO",
        "SPOT_PO",
        "RFQ",
        "RFP",
        "CAPEX_SOURCING",
        "NEW_ITEM_SOURCING",
        "NOT_PROCESSED",
    }


def test_employee_chat_matches_the_item_field_not_only_the_message(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_source_data(session)
    response = test_client.post(
        "/api/chat/requests",
        json={
            "requester": "Test employee",
            "department": "Stores",
            "plant": "PUN1",
            "item": "MS angle 50x50x5",
            "quantity": "10",
            "uom": "KG",
            "need_by_date": "2026-12-01",
            "urgency": "Normal",
            "estimated_unit_price": None,
            "message": "Need 10 kg by 1 December.",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["resolved_item_code"] == "ITM-1005"
    assert result["decision"] != "NOT_PROCESSED"


def test_buyer_approval_requires_reason_and_updates_audit_and_queue(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_source_data(session)
    request = test_client.post(
        "/api/chat/requests",
        json={
            "requester": "Test employee",
            "department": "Stores",
            "plant": "PUN1",
            "item": "ITM-1005",
            "quantity": "10",
            "uom": "KG",
            "need_by_date": "2026-12-01",
            "urgency": "Normal",
            "estimated_unit_price": None,
            "message": "Need 10 kg MS angle.",
        },
    )
    assert request.status_code == 200, request.text
    with test_session_factory() as session:
        requirement = session.scalar(
            select(PurchaseRequirement).where(
                PurchaseRequirement.group_id == request.json()["group_id"]
            )
        )
        assert requirement is not None
        requirement.status = "decided"
        session.commit()
    queue = test_client.get("/api/buyer/requirements")
    assert queue.status_code == 200
    decision = next(item for item in queue.json() if item["group_id"] == request.json()["group_id"])
    approved = test_client.post(
        f"/api/buyer/decisions/{decision['id']}/approve",
        json={"actor_id": "buyer-1", "reason": "Requirement checked and approved."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert test_client.get("/api/buyer/requirements").json() == []
    with test_session_factory() as session:
        assert session.scalar(
            select(AuditEvent).where(AuditEvent.action == "buyer_decision_approved")
        ) is not None


def test_buyer_route_override_requires_compatible_route_and_reason(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_source_data(session)
    request = test_client.post(
        "/api/chat/requests",
        json={
            "requester": "Test employee",
            "department": "Stores",
            "plant": "PUN1",
            "item": "ITM-1005",
            "quantity": "10",
            "uom": "KG",
            "need_by_date": "2026-12-01",
            "urgency": "Normal",
            "estimated_unit_price": None,
            "message": "Need 10 kg MS angle.",
        },
    )
    assert request.status_code == 200
    queue = test_client.get("/api/buyer/requirements").json()
    decision = next(item for item in queue if item["group_id"] == request.json()["group_id"])
    invalid = test_client.post(
        f"/api/buyer/decisions/{decision['id']}/override",
        json={"actor_id": "buyer-1", "route": "NOT_PROCESSED", "reason": "Override."},
    )
    assert invalid.status_code == 422
    incompatible_route = (
        "RFQ" if decision["decision"] == "PO" else "SPOT_PO"
    )
    incompatible = test_client.post(
        f"/api/buyer/decisions/{decision['id']}/override",
        json={
            "actor_id": "buyer-1",
            "route": incompatible_route,
            "reason": "Attempt to change decision class.",
        },
    )
    assert incompatible.status_code == 409
