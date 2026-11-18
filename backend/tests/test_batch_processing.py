import csv
import io
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Contract,
    ContractAllocation,
    Decision,
    DecisionRun,
    PRLine,
    PolicyRule,
    PolicyVersion,
    Supplier,
)
from app.services.master_data_import import import_contract_pdfs, import_master_data
from app.services.policy_engine import POLICY_DOCUMENT, seed_corvo_v5

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "Cantill_FullStack_Task_Kit"
INPUT_COLUMNS = [
    "pr_no",
    "pr_line",
    "created_at",
    "requester",
    "department",
    "plant",
    "item_code",
    "description",
    "quantity",
    "uom",
    "estimated_unit_price",
    "need_by_date",
    "urgency",
]
OUTPUT_COLUMNS = [
    "pr_no",
    "pr_line",
    "resolved_item_code",
    "group_id",
    "po_id",
    "decision",
    "route",
    "supplier_id",
    "unit_price",
    "group_value_inr",
    "suggested_supplier_ids",
    "policy_version",
    "reason",
]


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=INPUT_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _row(
    pr_no: str,
    line: str = "1",
    *,
    created: str = "2026-11-17 10:00",
    plant: str = "PUN1",
    item: str = "ITM-1005",
    description: str = "MS angle 50x50x5",
    quantity: str = "3000",
    uom: str = "KG",
    estimate: str = "",
) -> dict[str, str]:
    return {
        "pr_no": pr_no,
        "pr_line": line,
        "created_at": created,
        "requester": "Test employee",
        "department": "Stores",
        "plant": plant,
        "item_code": item,
        "description": description,
        "quantity": quantity,
        "uom": uom,
        "estimated_unit_price": estimate,
        "need_by_date": "2026-12-01",
        "urgency": "Normal",
    }


def _import_sources(session: Session) -> None:
    import_master_data(session, SOURCE_ROOT)
    import_contract_pdfs(session, SOURCE_ROOT)
    session.commit()


def test_sample_batch_returns_one_brief_shaped_row_per_input_line(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)

    sample_path = SOURCE_ROOT / "pr_batches" / "sample_pr_batch.csv"
    response = test_client.post(
        "/api/process-batch",
        files={"file": (sample_path.name, sample_path.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["statistics"]["total_rows"] == 186
    assert result["statistics"]["processed"] == 184
    assert result["statistics"]["not_processed"] == 2
    assert len(result["rows"]) == 186
    assert list(result["rows"][0]) == OUTPUT_COLUMNS
    assert result["statistics"]["policy_versions"] == ["5.0"]
    assert result["statistics"]["errors"] == result["statistics"]["not_processed"]

    rows = result["rows"]
    rejected_reasons = [
        row["reason"] for row in rows if row["decision"] == "NOT_PROCESSED"
    ]
    assert any("quantity must be greater than zero" in reason for reason in rejected_reasons)
    assert any("Unsupported plant" in reason for reason in rejected_reasons)
    groups: dict[str, set[str]] = {}
    po_groups: dict[str, set[str]] = {}
    for row in rows:
        if row["group_id"]:
            groups.setdefault(row["group_id"], set()).add(row["pr_no"] + "/" + str(row["pr_line"]))
        if row["po_id"]:
            po_groups.setdefault(row["po_id"], set()).add(row["group_id"])
        if row["decision"] == "NOT_PROCESSED":
            assert row["reason"]
            assert row["route"] == "NOT_PROCESSED"
    assert all(len(lines) >= 1 for lines in groups.values())
    assert any(len(lines) > 1 for lines in groups.values())
    assert all(groups_in_po for groups_in_po in po_groups.values())

    export = test_client.get(f"/api/batches/{result['batch_id']}/export")
    assert export.status_code == 200
    exported = list(csv.DictReader(io.StringIO(export.text)))
    assert len(exported) == 186
    assert list(exported[0]) == OUTPUT_COLUMNS


def test_batch_duplicate_and_invalid_rows_are_reported_without_losing_valid_lines(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    first = _row("BATCH-DUP-1")
    duplicate = dict(first)
    invalid = _row("BATCH-BAD-1", "1", quantity="zero")
    duplicate_invalid = _row("BATCH-BAD-1")
    response = test_client.post(
        "/api/process-batch",
        files={
            "file": (
                "batch.csv",
                _csv_bytes([first, duplicate, invalid, duplicate_invalid]),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert len(rows) == 4
    assert rows[0]["decision"] != "NOT_PROCESSED"
    assert rows[1]["decision"] == "NOT_PROCESSED"
    assert "Duplicate" in rows[1]["reason"]
    assert rows[2]["decision"] == "NOT_PROCESSED"
    assert "quantity must be a decimal" in rows[2]["reason"]
    assert rows[3]["decision"] == "NOT_PROCESSED"
    assert "Duplicate" in rows[3]["reason"]
    assert response.json()["statistics"]["duplicates"] == 2


def test_reuploading_offset_timestamp_batch_is_idempotent(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    content = _csv_bytes(
        [_row("BATCH-TZ-1", created="2026-11-17T15:30:00+05:30")]
    )
    first = test_client.post(
        "/api/process-batch",
        files={"file": ("dated.csv", content, "text/csv")},
    )
    second = test_client.post(
        "/api/process-batch",
        files={"file": ("dated.csv", content, "text/csv")},
    )
    assert first.status_code == second.status_code == 200
    first_row, second_row = first.json()["rows"][0], second.json()["rows"][0]
    assert first_row["decision"] != "NOT_PROCESSED"
    assert second_row["decision"] == first_row["decision"]
    assert second_row["group_id"] == first_row["group_id"]
    with test_session_factory() as session:
        assert len(list(session.scalars(select(PRLine)))) == 1
        assert len(list(session.scalars(select(DecisionRun)))) == 1
        assert len(list(session.scalars(select(Decision)))) == 1


def test_batch_csv_export_neutralizes_spreadsheet_formulas(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    response = test_client.post(
        "/api/process-batch",
        files={
            "file": (
                "formula.csv",
                _csv_bytes([_row("=1+1")]),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    export = test_client.get(f"/api/batches/{response.json()['batch_id']}/export")
    assert export.status_code == 200
    exported = list(csv.DictReader(io.StringIO(export.text)))
    assert exported[0]["pr_no"] == "'=1+1"


def test_missing_item_code_is_matched_from_description_and_csv_schema_is_checked(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    missing_code = _row(
        "BATCH-MATCH-1",
        item="",
        description="MS angle 50x50x5",
        estimate="",
    )
    response = test_client.post(
        "/api/process-batch",
        files={"file": ("batch.csv", _csv_bytes([missing_code]), "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["rows"][0]["resolved_item_code"] == "ITM-1005"

    missing_column = test_client.post(
        "/api/process-batch",
        files={
            "file": (
                "bad.csv",
                b"pr_no,pr_line\nPR-1,1\n",
                "text/csv",
            )
        },
    )
    assert missing_column.status_code == 422


def test_unknown_explicit_item_code_and_uom_mismatch_are_not_processed(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    response = test_client.post(
        "/api/process-batch",
        files={
            "file": (
                "invalid-items.csv",
                _csv_bytes(
                    [
                        _row("BATCH-UNKNOWN-CODE", item="ITM-NOT-FOUND", estimate="12.50"),
                        _row("BATCH-WRONG-UOM", item="ITM-1005", quantity="10", uom="NOS"),
                    ]
                ),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    results = {row["pr_no"]: row for row in response.json()["rows"]}
    assert results["BATCH-UNKNOWN-CODE"]["decision"] == "NOT_PROCESSED"
    assert "does not exist" in results["BATCH-UNKNOWN-CODE"]["reason"]
    assert results["BATCH-WRONG-UOM"]["decision"] == "NOT_PROCESSED"
    assert "does not match" in results["BATCH-WRONG-UOM"]["reason"]


def test_batch_uses_policy_effective_on_each_requirement_date(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        base = seed_corvo_v5(session)
        document = json.loads(POLICY_DOCUMENT.read_text(encoding="utf-8"))
        document = deepcopy(document)
        document["version"] = "5.1"
        document["effective_from"] = "2026-11-17"
        document["thresholds"]["spot_maximum_value"] = "250000"
        amended = PolicyVersion(
            version="5.1",
            effective_from=date(2026, 11, 17),
            status="approved",
            policy_document=document,
            parent_policy_version_id=base.id,
        )
        session.add(amended)
        session.flush()
        for rule in document["settings"]["decision_rules"]:
            session.add(
                PolicyRule(
                    policy_version_id=amended.id,
                    order=rule["order"],
                    clause=rule["clause"],
                    name=rule["name"],
                    condition=rule["condition"],
                    outcome=rule["outcome"],
                )
            )
        _import_sources(session)

    response = test_client.post(
        "/api/process-batch",
        files={
            "file": (
                "dated.csv",
                _csv_bytes(
                    [
                        _row("BATCH-V50", created="2026-11-10 10:00"),
                        _row("BATCH-V51", created="2026-11-17T00:15:00+05:30"),
                    ]
                ),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200, response.text
    result = {row["pr_no"]: row for row in response.json()["rows"]}
    assert result["BATCH-V50"]["policy_version"] == "5.0"
    assert result["BATCH-V50"]["route"] == "RFQ"
    assert result["BATCH-V51"]["policy_version"] == "5.1"
    assert result["BATCH-V51"]["route"] == "SPOT_PO"


def test_preferred_supplier_csv_contains_rank_and_top_three_indicator(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    response = test_client.get(
        "/api/preferred-suppliers/export", params={"as_of": "2026-11-19"}
    )
    assert response.status_code == 200, response.text
    records = list(csv.DictReader(io.StringIO(response.text)))
    assert records
    assert list(records[0]) == [
        "item_code",
        "rank",
        "supplier_id",
        "reference_price",
        "top_three",
    ]
    for item_code in {row["item_code"] for row in records}:
        item_records = [row for row in records if row["item_code"] == item_code]
        assert [int(row["rank"]) for row in item_records] == list(
            range(1, len(item_records) + 1)
        )
        assert all(
            (row["top_three"] == "true") == (int(row["rank"]) <= 3)
            for row in item_records
        )
    with test_session_factory() as session:
        blocked_ids = {
            supplier.supplier_id
            for supplier in session.scalars(select(Supplier))
            if supplier.status != "Active"
        }
    assert all(row["supplier_id"] not in blocked_ids for row in records)


def test_contract_ceiling_and_po_consolidation_remain_engine_controlled(
    test_session_factory: sessionmaker[Session], test_client
) -> None:
    with test_session_factory() as session:
        _import_sources(session)
    sample_path = SOURCE_ROOT / "pr_batches" / "sample_pr_batch.csv"
    response = test_client.post(
        "/api/process-batch",
        files={"file": ("sample.csv", sample_path.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    contract_lines = [row for row in rows if row["route"] == "CONTRACT_PO"]
    assert contract_lines
    assert all(row["po_id"] for row in contract_lines)
    po_to_requirements: dict[str, set[str]] = {}
    for row in rows:
        if row["po_id"]:
            po_to_requirements.setdefault(row["po_id"], set()).add(row["group_id"])
    assert any(len(requirements) > 1 for requirements in po_to_requirements.values())
    with test_session_factory() as session:
        contract_decisions = list(
            session.scalars(
                select(Decision).where(Decision.route == "CONTRACT_PO")
            )
        )
        assert contract_decisions
        assert all(
            decision.evidence["contract"]["remaining_capacity_before"]
            for decision in contract_decisions
        )
        for contract in session.scalars(select(Contract)):
            allocated = sum(
                (
                    Decimal(allocation.amount)
                    for allocation in session.scalars(
                        select(ContractAllocation).where(
                            ContractAllocation.contract_id == contract.id,
                            ContractAllocation.status.in_(("reserved", "committed")),
                        )
                    )
                ),
                Decimal("0"),
            )
            assert allocated + Decimal(contract.baseline_consumed_value) <= Decimal(
                contract.value_cap
            )
