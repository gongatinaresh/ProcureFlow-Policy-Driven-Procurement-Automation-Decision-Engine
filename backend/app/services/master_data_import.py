import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Contract,
    ContractLine,
    Item,
    Plant,
    PurchaseHistory,
    Supplier,
)
from app.schemas.import_data import (
    ContractLineRow,
    ContractPDFImportSummary,
    ContractPDFResult,
    ContractRegisterRow,
    ImportFileSummary,
    ItemMasterRow,
    MasterDataImportSummary,
    PlantMasterRow,
    PurchaseHistoryRow,
    SupplierMasterRow,
)

logger = logging.getLogger(__name__)
RowSchema = TypeVar("RowSchema", bound=BaseModel)

CSV_LAYOUTS: dict[str, tuple[str, ...]] = {
    "master_data/item_master.csv": (
        "item_code",
        "description",
        "uom",
        "category",
        "item_type",
        "standard_price",
    ),
    "master_data/supplier_master.csv": (
        "supplier_id",
        "name",
        "city",
        "categories",
        "status",
        "on_time_pct",
        "quality_rating",
    ),
    "master_data/plants.csv": ("plant", "name"),
    "master_data/po_history.csv": (
        "po_number",
        "po_date",
        "supplier_id",
        "item_code",
        "quantity",
        "uom",
        "unit_price",
        "plant",
        "delivered_on_time",
    ),
    "contracts/contract_register.csv": (
        "contract_id",
        "supplier_id",
        "supplier_name",
        "start_date",
        "end_date",
        "value_cap",
        "consumed_value",
    ),
    "contracts/contract_lines.csv": (
        "contract_id",
        "item_code",
        "description",
        "uom",
        "rate",
    ),
}


class ImportSourceError(ValueError):
    """The source path or its CSV structure cannot be processed safely."""


class RowImportError(ValueError):
    """A source row refers to missing or inconsistent master data."""


def _row_error_text(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()
    )


def _read_csv(path: Path, expected_headers: Sequence[str]) -> list[dict[str, str]]:
    try:
        frame = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=False,
            encoding="utf-8-sig",
        )
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise ImportSourceError(f"Could not read {path.name}: {error}") from error

    actual_headers = list(frame.columns)
    if len(actual_headers) != len(set(actual_headers)):
        raise ImportSourceError(f"{path.name} contains duplicate column names.")
    if tuple(actual_headers) != tuple(expected_headers):
        missing = [header for header in expected_headers if header not in actual_headers]
        unexpected = [header for header in actual_headers if header not in expected_headers]
        order_only = not missing and not unexpected
        detail = (
            "columns are not in the expected order"
            if order_only
            else f"missing columns={missing}; unexpected columns={unexpected}"
        )
        raise ImportSourceError(f"{path.name} has an invalid header: {detail}.")
    return frame.to_dict(orient="records")


def _upsert(
    session: Session,
    model: type[Any],
    lookup: dict[str, Any],
    values: dict[str, Any],
    *,
    on_update: dict[str, Any] | None = None,
) -> str:
    existing = session.scalar(select(model).filter_by(**lookup))
    if existing is None:
        session.add(model(**lookup, **values))
        session.flush()
        return "imported"

    changed = any(getattr(existing, field) != value for field, value in values.items())
    if changed:
        for field, value in values.items():
            setattr(existing, field, value)
        if on_update:
            for field, value in on_update.items():
                setattr(existing, field, value)
        session.flush()
        return "updated"
    return "skipped"


def _process_file(
    session: Session,
    source_directory: Path,
    relative_path: str,
    expected_headers: Sequence[str],
    schema: type[RowSchema],
    key_function: Callable[[RowSchema], Any],
    import_function: Callable[[Session, RowSchema, int], str],
) -> ImportFileSummary:
    file_summary = ImportFileSummary(file=relative_path)
    try:
        records = _read_csv(source_directory / relative_path, expected_headers)
    except ImportSourceError as error:
        file_summary.rejected = 1
        file_summary.errors.append(str(error))
        return file_summary

    seen: dict[Any, dict[str, Any]] = {}
    for row_number, raw_record in enumerate(records, start=2):
        file_summary.rows_seen += 1
        if not any(str(value).strip() for value in raw_record.values()):
            file_summary.rejected += 1
            file_summary.errors.append(f"row {row_number}: blank record")
            continue
        try:
            record = schema.model_validate(raw_record)
        except ValidationError as error:
            file_summary.rejected += 1
            file_summary.errors.append(f"row {row_number}: {_row_error_text(error)}")
            continue

        key = key_function(record)
        payload = record.model_dump(mode="python")
        previous = seen.get(key)
        if previous is not None:
            if previous == payload:
                file_summary.skipped += 1
                file_summary.errors.append(
                    f"row {row_number}: duplicate source record for key {key!r}; skipped"
                )
            else:
                file_summary.rejected += 1
                file_summary.errors.append(
                    f"row {row_number}: conflicting duplicate source key {key!r}"
                )
            continue
        seen[key] = payload

        try:
            with session.begin_nested():
                result = import_function(session, record, row_number)
            session.commit()
        except RowImportError as error:
            session.rollback()
            file_summary.rejected += 1
            file_summary.errors.append(f"row {row_number}: {error}")
        except Exception as error:
            session.rollback()
            logger.exception(
                "Master-data row import failed",
                extra={"file": relative_path, "row": row_number},
            )
            file_summary.rejected += 1
            file_summary.errors.append(
                f"row {row_number}: database/import error ({type(error).__name__}): {error}"
            )
        else:
            setattr(file_summary, result, getattr(file_summary, result) + 1)

    return file_summary


def _import_item(session: Session, row: ItemMasterRow, _: int) -> str:
    return _upsert(
        session,
        Item,
        {"item_code": row.item_code},
        {
            "description": row.description,
            "uom": row.uom,
            "category": row.category,
            "item_type": row.item_type,
            "standard_price": row.standard_price,
        },
    )


def _import_supplier(session: Session, row: SupplierMasterRow, _: int) -> str:
    return _upsert(
        session,
        Supplier,
        {"supplier_id": row.supplier_id},
        {
            "name": row.name,
            "city": row.city,
            "categories": row.categories,
            "status": row.status,
            "on_time_pct": row.on_time_pct,
            "quality_rating": row.quality_rating,
        },
    )


def _import_plant(session: Session, row: PlantMasterRow, _: int) -> str:
    return _upsert(
        session,
        Plant,
        {"plant_code": row.plant},
        {"name": row.name},
    )


def _import_purchase_history(session: Session, row: PurchaseHistoryRow, _: int) -> str:
    if session.get(Supplier, row.supplier_id) is None:
        raise RowImportError(f"unknown supplier_id {row.supplier_id!r}")
    if session.get(Item, row.item_code) is None:
        raise RowImportError(f"unknown item_code {row.item_code!r}")
    if session.get(Plant, row.plant) is None:
        raise RowImportError(f"unknown plant {row.plant!r}")
    return _upsert(
        session,
        PurchaseHistory,
        {"po_number": row.po_number, "item_code": row.item_code},
        {
            "po_date": row.po_date,
            "supplier_id": row.supplier_id,
            "quantity": row.quantity,
            "uom": row.uom,
            "unit_price": row.unit_price,
            "plant_code": row.plant,
            "delivered_on_time": row.delivered_on_time,
        },
    )


def _import_contract(session: Session, row: ContractRegisterRow, row_number: int) -> str:
    supplier = session.get(Supplier, row.supplier_id)
    if supplier is None:
        raise RowImportError(f"unknown supplier_id {row.supplier_id!r}")
    if supplier.name.casefold() != row.supplier_name.casefold():
        raise RowImportError(
            f"supplier_name {row.supplier_name!r} does not match supplier master "
            f"name {supplier.name!r} for {row.supplier_id}"
        )

    existing = session.scalar(select(Contract).where(Contract.contract_id == row.contract_id))
    values = {
        "supplier_id": row.supplier_id,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "value_cap": row.value_cap,
        "baseline_consumed_value": row.consumed_value,
    }
    if existing is None:
        session.add(
            Contract(
                contract_id=row.contract_id,
                **values,
                source_type="register",
                source_reference="contracts/contract_register.csv",
                verified=False,
                review_status="pending",
            )
        )
        session.flush()
        return "imported"

    business_fields_changed = any(
        getattr(existing, field) != value for field, value in values.items()
    )
    if business_fields_changed or existing.source_type == "pdf_manual_review":
        metadata_values = {
            "source_type": "register",
            "source_reference": "contracts/contract_register.csv",
        }
        for field, value in {**values, **metadata_values}.items():
            setattr(existing, field, value)
        existing.verified = False
        existing.review_status = "pending"
        existing.source_sha256 = None
        session.flush()
        return "updated"
    return "skipped"


def _import_contract_line(session: Session, row: ContractLineRow, _: int) -> str:
    contract = session.scalar(select(Contract).where(Contract.contract_id == row.contract_id))
    if contract is None:
        raise RowImportError(f"unknown contract_id {row.contract_id!r}")
    if session.get(Item, row.item_code) is None:
        raise RowImportError(f"unknown item_code {row.item_code!r}")
    return _upsert(
        session,
        ContractLine,
        {"contract_id": contract.id, "item_code": row.item_code},
        {
            "description": row.description,
            "uom": row.uom,
            "rate": row.rate,
        },
    )


def import_master_data(session: Session, source_directory: Path) -> MasterDataImportSummary:
    root = source_directory.expanduser().resolve()
    if not root.is_dir():
        raise ImportSourceError(f"Source directory does not exist: {root}")

    import_specs: list[
        tuple[
            str,
            type[BaseModel],
            Callable[[Any], Any],
            Callable[[Session, Any, int], str],
        ]
    ] = [
        ("master_data/item_master.csv", ItemMasterRow, lambda row: row.item_code, _import_item),
        (
            "master_data/supplier_master.csv",
            SupplierMasterRow,
            lambda row: row.supplier_id,
            _import_supplier,
        ),
        ("master_data/plants.csv", PlantMasterRow, lambda row: row.plant, _import_plant),
        (
            "master_data/po_history.csv",
            PurchaseHistoryRow,
            lambda row: (row.po_number, row.item_code),
            _import_purchase_history,
        ),
        (
            "contracts/contract_register.csv",
            ContractRegisterRow,
            lambda row: row.contract_id,
            _import_contract,
        ),
        (
            "contracts/contract_lines.csv",
            ContractLineRow,
            lambda row: (row.contract_id, row.item_code),
            _import_contract_line,
        ),
    ]

    summaries = [
        _process_file(
            session,
            root,
            relative_path,
            CSV_LAYOUTS[relative_path],
            schema,
            key_function,
            import_function,
        )
        for relative_path, schema, key_function, import_function in import_specs
    ]
    return MasterDataImportSummary(source_directory=str(root), files=summaries)


def _extract_pdf(path: Path) -> dict[str, Any]:
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except Exception as error:
        raise ValueError(f"PDF text extraction failed: {error}") from error

    contract_match = re.search(r"Contract No\.\s*([A-Za-z0-9-]+)", text, re.IGNORECASE)
    supplier_match = re.search(r"Supplier\s+ID\s*(S\d+)", text, re.IGNORECASE)
    term_match = re.search(
        r"valid\s+from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    cap_match = re.search(
        r"shall\s+not\s+exceed\s+INR\s+([\d,]+)(?:\.\d{2})?",
        text,
        re.IGNORECASE,
    )
    missing_fields = [
        field
        for field, match in (
            ("contract number", contract_match),
            ("supplier ID", supplier_match),
            ("contract term", term_match),
            ("value ceiling", cap_match),
        )
        if match is None
    ]
    if missing_fields:
        raise ValueError(f"missing required signed-contract fields: {', '.join(missing_fields)}")

    line_pattern = re.compile(
        r"(ITM-\d+)\s+(.+?)\s+([A-Z]+)\s+([\d,]+\.\d{2})(?=\s|$)"
    )
    lines: dict[str, dict[str, Any]] = {}
    for match in line_pattern.finditer(text):
        item_code, description, uom, raw_rate = match.groups()
        if item_code in lines:
            raise ValueError(f"duplicate item line {item_code} in signed contract")
        lines[item_code] = {
            "item_code": item_code,
            "description": " ".join(description.split()),
            "uom": uom,
            "rate": Decimal(raw_rate.replace(",", "")),
        }
    if not lines:
        raise ValueError("no rate lines could be extracted from the signed contract")

    return {
        "contract_id": contract_match.group(1),
        "supplier_id": supplier_match.group(1),
        "start_date": term_match.group(1),
        "end_date": term_match.group(2),
        "value_cap": Decimal(cap_match.group(1).replace(",", "")),
        "lines": lines,
    }


def _contract_discrepancies(
    session: Session, contract: Contract, extracted: dict[str, Any]
) -> list[str]:
    discrepancies: list[str] = []
    comparisons = {
        "supplier_id": (contract.supplier_id, extracted["supplier_id"]),
        "start_date": (contract.start_date.isoformat(), extracted["start_date"]),
        "end_date": (contract.end_date.isoformat(), extracted["end_date"]),
        "value_cap": (contract.value_cap, extracted["value_cap"]),
    }
    for field, (csv_value, pdf_value) in comparisons.items():
        if csv_value != pdf_value:
            discrepancies.append(f"{field}: register={csv_value}; signed_pdf={pdf_value}")

    csv_lines = {
        line.item_code: {
            "description": " ".join(line.description.split()).casefold(),
            "uom": line.uom,
            "rate": line.rate,
        }
        for line in session.scalars(
            select(ContractLine).where(ContractLine.contract_id == contract.id)
        )
    }
    pdf_lines = extracted["lines"]
    for item_code in sorted(set(csv_lines) | set(pdf_lines)):
        csv_line = csv_lines.get(item_code)
        pdf_line = pdf_lines.get(item_code)
        if csv_line is None:
            discrepancies.append(f"item {item_code}: absent from contract_lines.csv")
        elif pdf_line is None:
            discrepancies.append(f"item {item_code}: absent from signed PDF")
        else:
            pdf_compare = {
                "description": pdf_line["description"].casefold(),
                "uom": pdf_line["uom"],
                "rate": pdf_line["rate"],
            }
            if csv_line != pdf_compare:
                discrepancies.append(
                    f"item {item_code}: register_lines={csv_line}; signed_pdf={pdf_compare}"
                )
    return discrepancies


def _import_pdf_only_contract(
    session: Session,
    extracted: dict[str, Any],
    relative_pdf_path: str,
    digest: str,
) -> tuple[bool, list[str]]:
    discrepancies: list[str] = []
    supplier = session.get(Supplier, extracted["supplier_id"])
    if supplier is None:
        discrepancies.append(f"unknown supplier_id {extracted['supplier_id']!r}")
    for item_code in extracted["lines"]:
        if session.get(Item, item_code) is None:
            discrepancies.append(f"unknown item_code {item_code!r}")
    if discrepancies:
        return False, discrepancies

    contract = Contract(
        contract_id=extracted["contract_id"],
        supplier_id=extracted["supplier_id"],
        start_date=date.fromisoformat(extracted["start_date"]),
        end_date=date.fromisoformat(extracted["end_date"]),
        value_cap=extracted["value_cap"],
        baseline_consumed_value=Decimal("0"),
        source_type="pdf_manual_review",
        source_reference=relative_pdf_path,
        source_sha256=digest,
        verified=False,
        review_status="pending",
    )
    session.add(contract)
    session.flush()
    for line in extracted["lines"].values():
        session.add(
            ContractLine(
                contract_id=contract.id,
                item_code=line["item_code"],
                description=line["description"],
                uom=line["uom"],
                rate=line["rate"],
            )
        )
    session.flush()
    return True, []


def import_contract_pdfs(
    session: Session, source_directory: Path
) -> ContractPDFImportSummary:
    root = source_directory.expanduser().resolve()
    pdf_directory = root / "contracts" / "pdf"
    if not root.is_dir():
        raise ImportSourceError(f"Source directory does not exist: {root}")
    if not pdf_directory.is_dir():
        raise ImportSourceError(f"Contract PDF directory does not exist: {pdf_directory}")

    pdf_paths = sorted(pdf_directory.glob("*.pdf"))
    if not pdf_paths:
        raise ImportSourceError(f"No contract PDFs found in {pdf_directory}")

    summary = ContractPDFImportSummary(
        source_directory=str(root),
        pdfs_seen=len(pdf_paths),
    )
    try:
        register_rows = _read_csv(
            root / "contracts" / "contract_register.csv",
            CSV_LAYOUTS["contracts/contract_register.csv"],
        )
    except ImportSourceError as error:
        raise ImportSourceError(
            f"Cannot compare signed contracts without a valid contract register: {error}"
        ) from error
    register_ids = {row["contract_id"].strip() for row in register_rows}
    discovered_ids: set[str] = set()

    for pdf_path in pdf_paths:
        result = ContractPDFResult(file=pdf_path.name)
        try:
            extracted = _extract_pdf(pdf_path)
            result.contract_id = extracted["contract_id"]
            if result.contract_id in discovered_ids:
                raise ValueError(f"duplicate contract ID {result.contract_id} in PDF folder")
            discovered_ids.add(result.contract_id)
            digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            relative_path = pdf_path.relative_to(root).as_posix()

            contract = session.scalar(
                select(Contract).where(Contract.contract_id == result.contract_id)
            )
            is_pdf_only = result.contract_id not in register_ids
            if is_pdf_only and contract is not None:
                result.missing_from_register = True
                result.review_status = "pending_manual_review"
                result.discrepancies.append(
                    "Signed contract is missing from contract_register.csv; "
                    "consumption starts at zero and the contract requires buyer verification."
                )
                summary.contracts_missing_from_register.append(result.contract_id)
                if contract.source_type != "pdf_manual_review":
                    result.rejected = 1
                    result.errors.append(
                        "Existing contract record is not a PDF-only review record; "
                        "manual reconciliation is required."
                    )
                    summary.rejected += 1
                else:
                    result.skipped = 1
                    summary.skipped += 1
                session.commit()
            elif not is_pdf_only and contract is None:
                result.rejected = 1
                result.review_status = "mismatch"
                result.errors.append(
                    "Contract appears in contract_register.csv but no valid register "
                    "record was imported; fix the CSV row before PDF reconciliation."
                )
                summary.rejected += 1
            elif contract is None:
                with session.begin_nested():
                    imported, errors = _import_pdf_only_contract(
                        session, extracted, relative_path, digest
                    )
                if not imported:
                    result.rejected = 1
                    result.errors.extend(errors)
                    result.review_status = "pending_manual_review"
                    summary.rejected += result.rejected
                else:
                    result.imported = 1
                    result.missing_from_register = True
                    result.review_status = "pending_manual_review"
                    result.discrepancies.append(
                        "Signed contract is missing from contract_register.csv; "
                        "consumption starts at zero and the contract requires buyer verification."
                    )
                    summary.imported += result.imported
                    summary.contracts_missing_from_register.append(result.contract_id)
                session.commit()
            else:
                result.discrepancies.extend(
                    _contract_discrepancies(session, contract, extracted)
                )
                supplier = session.get(Supplier, extracted["supplier_id"])
                if supplier is None:
                    result.discrepancies.append(
                        f"signed PDF supplier {extracted['supplier_id']!r} is missing from supplier master"
                    )
                if result.discrepancies:
                    contract.verified = False
                    contract.review_status = "mismatch"
                    contract.source_type = "register_pdf_mismatch"
                    contract.source_reference = relative_path
                    contract.source_sha256 = digest
                    result.review_status = "mismatch"
                else:
                    contract.verified = True
                    contract.review_status = "verified"
                    contract.source_type = "register_and_pdf"
                    contract.source_reference = relative_path
                    contract.source_sha256 = digest
                    result.review_status = "verified"
                session.commit()
                result.skipped = 1
                summary.skipped += result.skipped
        except Exception as error:
            session.rollback()
            result.rejected = 1
            result.review_status = "pending_manual_review"
            result.errors.append(f"{type(error).__name__}: {error}")
            summary.rejected += 1
            logger.exception(
                "Contract PDF ingestion failed",
                extra={"file": pdf_path.name},
            )
        summary.documents.append(result)

    summary.registered_contracts_without_pdf = sorted(register_ids - discovered_ids)
    return summary
