import copy
import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import fitz
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditEvent, PolicyVersion
from app.services.master_data_import import import_master_data
from app.services.policy_management import (
    PolicyManagementError,
    activate_due_policies,
    validate_policy_document,
)
from app.services.procurement_engine import process_purchase_requests
from app.services.procurement_types import RequestLine

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "Cantill_FullStack_Task_Kit"


def _pdf_bytes(text: str = "Procurement policy amendment") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _test_request() -> RequestLine:
    return RequestLine(
        pr_no="POLICY-TEST-1",
        pr_line=1,
        created_at=datetime(2026, 11, 17, 10, 0),
        requester="Test employee",
        department="Stores",
        plant_code="PUN1",
        supplied_item_code="ITM-1005",
        description="MS angle 50x50x5",
        quantity=Decimal("3000"),
        uom="KG",
        estimated_unit_price=None,
        need_by_date=date(2026, 12, 1),
        urgency="Normal",
    )


def _upload(client, effective_from: date, *, pdf: bytes | None = None):
    return client.post(
        "/api/policies/upload",
        data={
            "effective_from": effective_from.isoformat(),
            "actor_id": "procurement-user",
        },
        files={
            "file": (
                "amendment.pdf",
                pdf or _pdf_bytes(),
                "application/pdf",
            )
        },
    )


def test_spot_threshold_amendment_changes_same_request_without_code_change(
    test_session_factory: sessionmaker[Session],
    test_client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import policy_management

    monkeypatch.setattr(
        policy_management, "_storage_directory", lambda: tmp_path / "policy-files"
    )
    with test_session_factory() as session:
        import_master_data(session, SOURCE_ROOT)
        before = process_purchase_requests(
            session, [_test_request()], policy_version="5.0"
        )
        assert before.requirements[0].route == "RFQ"
        assert before.requirements[0].evidence["threshold_value_inr"] == "223020.00"

    active = test_client.get("/api/policies/active")
    assert active.status_code == 200
    uploaded = _upload(test_client, date(2026, 11, 17), pdf=_pdf_bytes("Spot limit changes"))
    assert uploaded.status_code == 201, uploaded.text
    draft = uploaded.json()
    assert draft["status"] == "draft"
    assert draft["extraction_status"] == "text_extracted_manual_rule_review_required"
    assert "Spot limit changes" in draft["extracted_text"]
    amendment = copy.deepcopy(draft["policy_document"])
    amendment["thresholds"]["spot_maximum_value"] = "250000"

    premature_approval = test_client.post(
        f"/api/policies/{draft['id']}/approve",
        json={"actor_id": "approver", "reason": "Not validated"},
    )
    assert premature_approval.status_code == 409

    validated = test_client.post(
        f"/api/policies/{draft['id']}/validate",
        json={"actor_id": "procurement-user", "policy_document": amendment},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "validated"
    assert validated.json()["valid"] is True
    assert "Spot purchase threshold" in validated.json()["change_summary"]

    diff = test_client.get(f"/api/policies/{draft['id']}/diff")
    assert diff.status_code == 200
    threshold_change = next(
        change
        for change in diff.json()["changes"]
        if change["path"] == "thresholds.spot_maximum_value"
    )
    assert threshold_change["before"] == "200000"
    assert threshold_change["after"] == "250000"

    approval = test_client.post(
        f"/api/policies/{draft['id']}/approve",
        json={"actor_id": "procurement-approver", "reason": "Approved threshold amendment."},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["status"] == "active"

    with test_session_factory() as session:
        diagnostic_policy = session.scalar(
            select(PolicyVersion).where(PolicyVersion.version == draft["version"])
        )
        request_date = _test_request().created_at.date()
        after = process_purchase_requests(
            session, [_test_request()], policy_version=draft["version"]
        )
        assert after.requirements[0].route == "SPOT_PO"
        assert after.requirements[0].evidence["threshold_value_inr"] == "223020.00"
        assert session.scalar(
            select(PolicyVersion).where(PolicyVersion.version == "5.0")
        ).status == "superseded"
        assert session.scalar(
            select(AuditEvent).where(AuditEvent.action == "policy_approved")
        ) is not None

    listed = test_client.get("/api/policies")
    assert listed.status_code == 200
    assert [row["version"] for row in listed.json()][:2] == [draft["version"], "5.0"]
    active_after = test_client.get("/api/policies/active")
    assert active_after.json()["version"] == draft["version"]


def test_custom_plant_and_category_rules_are_executable_policy_data(
    test_session_factory: sessionmaker[Session],
    test_client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import policy_management

    monkeypatch.setattr(
        policy_management, "_storage_directory", lambda: tmp_path / "policy-files"
    )
    with test_session_factory() as session:
        import_master_data(session, SOURCE_ROOT)
    active = test_client.get("/api/policies/active").json()
    uploaded = _upload(test_client, date(2026, 11, 17))
    assert uploaded.status_code == 201
    document = copy.deepcopy(uploaded.json()["policy_document"])
    current_rules = document["settings"]["decision_rules"]
    for rule in current_rules:
        rule["order"] += 2
    document["settings"]["decision_rules"] = [
        {
            "order": 1,
            "clause": "AMENDMENT-PLANT-STEEL",
            "name": "Chakan steel sourcing review",
            "condition": {
                "all": [
                    {"field": "plant.code", "operator": "eq", "value": "CHK1"},
                    {
                        "field": "item.category",
                        "operator": "eq",
                        "value": "Steel & Metals",
                    },
                ]
            },
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "PLANT_REVIEW",
                "supplier_count": 3,
            },
        },
        {
            "order": 2,
            "clause": "AMENDMENT-CATEGORY-STEEL",
            "name": "Pune steel category sourcing",
            "condition": {
                "all": [
                    {"field": "plant.code", "operator": "eq", "value": "PUN1"},
                    {
                        "field": "item.category",
                        "operator": "eq",
                        "value": "Steel & Metals",
                    },
                ]
            },
            "outcome": {
                "decision": "SOURCING_REQUEST",
                "route": "CATEGORY_REVIEW",
                "supplier_count": 3,
            },
        },
        *current_rules,
    ]

    policy_id = uploaded.json()["id"]
    validation = test_client.post(
        f"/api/policies/{policy_id}/validate",
        json={"actor_id": "procurement-user", "policy_document": document},
    )
    assert validation.status_code == 200, validation.text
    approval = test_client.post(
        f"/api/policies/{policy_id}/approve",
        json={"actor_id": "approver", "reason": "Plant/category rules approved."},
    )
    assert approval.status_code == 200

    with test_session_factory() as session:
        for plant, expected_route, expected_clause in (
            ("CHK1", "PLANT_REVIEW", "AMENDMENT-PLANT-STEEL"),
            ("PUN1", "CATEGORY_REVIEW", "AMENDMENT-CATEGORY-STEEL"),
        ):
            line = replace(_test_request(), pr_no=f"PLANT-{plant}", plant_code=plant)
            run = process_purchase_requests(
                session, [line], policy_version=uploaded.json()["version"]
            )
            assert run.requirements[0].route == expected_route
            assert run.requirements[0].clause == expected_clause


def test_approved_future_policy_waits_until_its_effective_date(
    test_session_factory: sessionmaker[Session],
    test_client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from datetime import timedelta
    from app.services import policy_management

    monkeypatch.setattr(
        policy_management, "_storage_directory", lambda: tmp_path / "policy-files"
    )

    # Set the effective date dynamically so the test never becomes outdated.
    future_date = policy_management.business_today() + timedelta(days=2)

    uploaded = _upload(test_client, future_date)
    policy_id = uploaded.json()["id"]
    document = uploaded.json()["policy_document"]

    # Validate the future policy.
    validated = test_client.post(
        f"/api/policies/{policy_id}/validate",
        json={"actor_id": "procurement-user", "policy_document": document},
    )
    assert validated.status_code == 200, validated.text

    # Approve the policy.
    approved = test_client.post(
        f"/api/policies/{policy_id}/approve",
        json={"actor_id": "approver", "reason": "Future effective date approved."},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    # The existing policy should remain active before the future date.
    assert test_client.get("/api/policies/active").json()["version"] == "5.0"

    with test_session_factory() as session:
        future_policy = session.get(PolicyVersion, policy_id)

        # Before the effective date, the future policy must not activate.
        activate_due_policies(session, future_date - timedelta(days=1))

        assert future_policy.status == "approved"
        assert test_client.get("/api/policies/active").json()["version"] == "5.0"

        # On the effective date, the policy should activate.
        activated = activate_due_policies(session, future_date)

        assert activated is not None
        assert activated.id == policy_id
        assert future_policy.status == "active"
def test_policy_validator_rejects_executable_or_unknown_rule_fields() -> None:
    document = json.loads(
        (Path(__file__).resolve().parents[1] / "app" / "policies" / "corvo_v5.json").read_text(
            encoding="utf-8"
        )
    )
    document["version"] = "5.1"
    document["effective_from"] = "2026-12-01"
    document["settings"]["decision_rules"][0]["condition"]["all"][0][
        "field"
    ] = "__import__.os"
    with pytest.raises(PolicyManagementError, match="unsupported field"):
        validate_policy_document(
            document,
            expected_version="5.1",
            expected_effective_from=date(2026, 12, 1),
        )

    document["settings"]["decision_rules"][0]["condition"]["all"][0][
        "field"
    ] = "item.item_type"
    document["settings"]["decision_rules"][0]["python"] = "import os"
    with pytest.raises(PolicyManagementError, match="executable"):
        validate_policy_document(
            document,
            expected_version="5.1",
            expected_effective_from=date(2026, 12, 1),
        )

    invalid_date_document = json.loads(
        (Path(__file__).resolve().parents[1] / "app" / "policies" / "corvo_v5.json").read_text(
            encoding="utf-8"
        )
    )
    invalid_date_document["version"] = "5.1"
    invalid_date_document["effective_from"] = "not-a-date"
    with pytest.raises(PolicyManagementError, match="effective_from"):
        validate_policy_document(
            invalid_date_document,
            expected_version="5.1",
            expected_effective_from=date(2026, 12, 1),
        )


def test_policy_upload_rejects_non_pdf_and_rejection_is_audited(
    test_session_factory: sessionmaker[Session],
    test_client,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import policy_management

    monkeypatch.setattr(
        policy_management, "_storage_directory", lambda: tmp_path / "policy-files"
    )
    invalid = _upload(test_client, date(2026, 11, 17), pdf=b"not a pdf")
    assert invalid.status_code == 422

    uploaded = _upload(test_client, date(2026, 11, 17))
    policy_id = uploaded.json()["id"]
    rejected = test_client.post(
        f"/api/policies/{policy_id}/reject",
        json={"actor_id": "procurement-user", "reason": "Amendment is not approved."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    with test_session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "policy_rejected")
        )
        assert event is not None
        assert event.reason == "Amendment is not approved."
