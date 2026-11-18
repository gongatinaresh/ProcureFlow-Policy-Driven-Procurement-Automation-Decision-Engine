import csv
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Contract, ContractLine, Item, Plant, PurchaseHistory, Supplier
from app.services.master_data_import import import_contract_pdfs, import_master_data


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


def make_source_directory(root: Path) -> Path:
    _write_csv(
        root / "master_data" / "item_master.csv",
        ["item_code", "description", "uom", "category", "item_type", "standard_price"],
        [
            ["ITM-1", "Test item", "NOS", "Testing", "Material", "12.50"],
            ["ITM-2", "Second item", "KG", "Testing", "Material", "2"],
        ],
    )
    _write_csv(
        root / "master_data" / "supplier_master.csv",
        [
            "supplier_id",
            "name",
            "city",
            "categories",
            "status",
            "on_time_pct",
            "quality_rating",
        ],
        [
            ["S001", "Supplier One", "Pune", "Testing; Other", "Active", "95", "4.5"],
            ["S002", "Supplier Two", "Pune", "Testing", "Blocked", "80", "3.2"],
        ],
    )
    _write_csv(
        root / "master_data" / "plants.csv",
        ["plant", "name"],
        [["PUN1", "Pune"]],
    )
    _write_csv(
        root / "master_data" / "po_history.csv",
        [
            "po_number",
            "po_date",
            "supplier_id",
            "item_code",
            "quantity",
            "uom",
            "unit_price",
            "plant",
            "delivered_on_time",
        ],
        [["PO-1", "2026-10-01", "S001", "ITM-1", "2", "NOS", "11.25", "PUN1", "True"]],
    )
    _write_csv(
        root / "contracts" / "contract_register.csv",
        [
            "contract_id",
            "supplier_id",
            "supplier_name",
            "start_date",
            "end_date",
            "value_cap",
            "consumed_value",
        ],
        [["RC-1", "S001", "Supplier One", "2026-01-01", "2026-12-31", "1000", "100"]],
    )
    _write_csv(
        root / "contracts" / "contract_lines.csv",
        ["contract_id", "item_code", "description", "uom", "rate"],
        [["RC-1", "ITM-1", "Test item", "NOS", "10.75"]],
    )
    (root / "contracts" / "pdf").mkdir(parents=True, exist_ok=True)
    return root


def test_master_data_import_is_idempotent(
    tmp_path: Path, test_session_factory: sessionmaker[Session]
) -> None:
    source = make_source_directory(tmp_path / "source")
    with test_session_factory() as session:
        first = import_master_data(session, source)
        second = import_master_data(session, source)

        assert [(file.imported, file.rejected) for file in first.files] == [
            (2, 0),
            (2, 0),
            (1, 0),
            (1, 0),
            (1, 0),
            (1, 0),
        ]
        assert all(file.skipped == file.rows_seen for file in second.files)
        assert all(file.imported == 0 and file.updated == 0 for file in second.files)
        assert session.scalar(select(func.count()).select_from(Item)) == 2
        assert session.scalar(select(func.count()).select_from(Supplier)) == 2
        assert session.scalar(select(func.count()).select_from(Plant)) == 1
        assert session.scalar(select(func.count()).select_from(PurchaseHistory)) == 1
        assert session.scalar(select(func.count()).select_from(Contract)) == 1
        assert session.scalar(select(func.count()).select_from(ContractLine)) == 1


def test_import_reports_invalid_types_foreign_keys_and_duplicate_rows(
    tmp_path: Path, test_session_factory: sessionmaker[Session]
) -> None:
    source = make_source_directory(tmp_path / "source")
    _write_csv(
        source / "master_data" / "supplier_master.csv",
        [
            "supplier_id",
            "name",
            "city",
            "categories",
            "status",
            "on_time_pct",
            "quality_rating",
        ],
        [
            ["S001", "Supplier One", "Pune", "Testing", "Active", "95", "4.5"],
            ["S002", "Supplier Two", "Pune", "Testing", "Unknown", "80", "3.2"],
            ["S003", "Supplier Three", "Pune", "Testing", "Inactive", "80", "3.2"],
        ],
    )
    _write_csv(
        source / "master_data" / "po_history.csv",
        [
            "po_number",
            "po_date",
            "supplier_id",
            "item_code",
            "quantity",
            "uom",
            "unit_price",
            "plant",
            "delivered_on_time",
        ],
        [
            ["PO-1", "2026-10-01", "S001", "ITM-1", "2", "NOS", "11.25", "PUN1", "True"],
            ["PO-2", "not-a-date", "S001", "ITM-1", "2", "NOS", "11.25", "PUN1", "True"],
            ["PO-3", "2026-10-01", "S404", "ITM-1", "2", "NOS", "11.25", "PUN1", "True"],
            ["PO-1", "2026-10-01", "S001", "ITM-1", "2", "NOS", "11.25", "PUN1", "True"],
            ["PO-1", "2026-10-01", "S001", "ITM-1", "2", "NOS", "12.25", "PUN1", "True"],
        ],
    )

    with test_session_factory() as session:
        summary = import_master_data(session, source)

        suppliers = next(file for file in summary.files if file.file.endswith("supplier_master.csv"))
        history = next(file for file in summary.files if file.file.endswith("po_history.csv"))
        assert (suppliers.imported, suppliers.rejected) == (2, 1)
        assert (history.imported, history.skipped, history.rejected) == (1, 1, 3)
        assert any("status" in error for error in suppliers.errors)
        assert any("unknown supplier_id" in error for error in history.errors)
        assert any("duplicate source record" in error for error in history.errors)
        assert any("conflicting duplicate source key" in error for error in history.errors)


def test_contract_pdf_ingestion_matches_register_and_keeps_pdf_only_pending(
    tmp_path: Path,
    test_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    from app.services import master_data_import

    source = make_source_directory(tmp_path / "source")
    with test_session_factory() as session:
        import_master_data(session, source)

        registered_pdf = source / "contracts" / "pdf" / "RC-1.pdf"
        pdf_only = source / "contracts" / "pdf" / "RC-2.pdf"
        registered_pdf.touch()
        pdf_only.touch()

        pdf_values = {
            "contract_id": "RC-1",
            "supplier_id": "S001",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "value_cap": Decimal("1000"),
            "lines": {
                "ITM-1": {
                    "item_code": "ITM-1",
                    "description": "Test item",
                    "uom": "NOS",
                    "rate": Decimal("10.75"),
                }
            },
        }
        pdf_only_values = {
            **pdf_values,
            "contract_id": "RC-2",
            "start_date": "2026-11-15",
            "end_date": "2027-11-14",
            "value_cap": Decimal("2500"),
            "lines": {
                "ITM-2": {
                    "item_code": "ITM-2",
                    "description": "Second item",
                    "uom": "KG",
                    "rate": Decimal("20"),
                }
            },
        }
        monkeypatch.setattr(
            master_data_import,
            "_extract_pdf",
            lambda path: pdf_values if path.name == "RC-1.pdf" else pdf_only_values,
        )

        first = import_contract_pdfs(session, source)
        second = import_contract_pdfs(session, source)
        after_pdf = import_master_data(session, source)

        assert first.pdfs_seen == 2
        assert first.skipped == 1
        assert first.imported == 1
        assert first.rejected == 0
        assert first.contracts_missing_from_register == ["RC-2"]
        pdf_only_contract = session.scalar(
            select(Contract).where(Contract.contract_id == "RC-2")
        )
        assert pdf_only_contract is not None
        assert pdf_only_contract.review_status == "pending"
        assert pdf_only_contract.verified is False
        assert pdf_only_contract.baseline_consumed_value == Decimal("0")
        assert len(pdf_only_contract.lines) == 1
        assert second.imported == 0
        assert second.skipped == 2
        contracts = next(
            file for file in after_pdf.files if file.file.endswith("contract_register.csv")
        )
        assert contracts.imported == 0
        assert contracts.updated == 0
        assert contracts.skipped == 1
        assert session.scalar(
            select(func.count()).select_from(Contract).where(Contract.contract_id == "RC-2")
        ) == 1


def test_missing_or_unexpected_csv_columns_are_reported(
    tmp_path: Path, test_session_factory: sessionmaker[Session]
) -> None:
    source = make_source_directory(tmp_path / "source")
    _write_csv(
        source / "master_data" / "plants.csv",
        ["plant"],
        [["PUN1"]],
    )
    with test_session_factory() as session:
        summary = import_master_data(session, source)
        plants = next(file for file in summary.files if file.file.endswith("plants.csv"))

        assert plants.rejected == 1
        assert "invalid header" in plants.errors[0]
        assert session.get(Plant, "PUN1") is None


def test_contract_pdf_discrepancy_is_flagged_for_manual_review(
    tmp_path: Path,
    test_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    from app.services import master_data_import

    source = make_source_directory(tmp_path / "source")
    with test_session_factory() as session:
        import_master_data(session, source)
        (source / "contracts" / "pdf" / "RC-1.pdf").touch()
        monkeypatch.setattr(
            master_data_import,
            "_extract_pdf",
            lambda _: {
                "contract_id": "RC-1",
                "supplier_id": "S001",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "value_cap": Decimal("1000"),
                "lines": {
                    "ITM-1": {
                        "item_code": "ITM-1",
                        "description": "Test item",
                        "uom": "NOS",
                        "rate": Decimal("11.00"),
                    }
                },
            },
        )

        report = import_contract_pdfs(session, source)

        assert report.rejected == 0
        assert report.skipped == 1
        assert report.documents[0].review_status == "mismatch"
        assert any("item ITM-1" in value for value in report.documents[0].discrepancies)
        contract = session.scalar(select(Contract).where(Contract.contract_id == "RC-1"))
        assert contract is not None
        assert contract.verified is False
        assert contract.review_status == "mismatch"
