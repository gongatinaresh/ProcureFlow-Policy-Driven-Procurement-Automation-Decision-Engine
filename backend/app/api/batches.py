import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import BatchRun
from app.services.batch_processing import (
    MAX_BATCH_BYTES,
    OUTPUT_COLUMNS,
    BatchInputError,
    preferred_supplier_rows,
    process_batch_csv,
)

router = APIRouter()


def _csv_response(filename: str, columns: list[str], rows: list[dict[str, str]]) -> StreamingResponse:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe_row = {
            key: _safe_csv_cell(value)
            for key, value in row.items()
        }
        writer.writerow(safe_row)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_csv_cell(value: object) -> object:
    if not isinstance(value, str):
        return value
    formula_candidate = value.lstrip(" \t\r\n")
    if formula_candidate.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


@router.post("/process-batch")
def process_batch(
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    content = file.file.read(MAX_BATCH_BYTES + 1)
    try:
        batch = process_batch_csv(session, content)
        session.commit()
    except BatchInputError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "batch_id": batch.id,
        "input_sha256": batch.input_sha256,
        "statistics": batch.statistics,
        "rows": batch.result_rows,
    }


@router.get("/batches/{batch_id}/export")
def export_batch(
    batch_id: str,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    batch = session.get(BatchRun, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch result does not exist.")
    return _csv_response(
        f"procurement-decisions-{batch.id}.csv",
        OUTPUT_COLUMNS,
        batch.result_rows,
    )


@router.get("/preferred-suppliers/export")
def export_preferred_suppliers(
    as_of: date,
    session: Session = Depends(get_db),
) -> StreamingResponse:
    try:
        policy_version, rows = preferred_supplier_rows(session, as_of)
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _csv_response(
        f"preferred-suppliers-{as_of.isoformat()}-policy-{policy_version}.csv",
        ["item_code", "rank", "supplier_id", "reference_price", "top_three"],
        rows,
    )
