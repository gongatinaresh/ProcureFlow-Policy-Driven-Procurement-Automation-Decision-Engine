# Backend foundation

## Local setup

From the `backend` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API listens on `http://127.0.0.1:8000`; the liveness/readiness endpoint is
`GET /api/health`. Tests run with `python -m pytest`.

## Configuration

Settings can be provided through environment variables or a `.env` file in the
backend working directory:

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Cantill Procurement API` | FastAPI application title |
| `ENVIRONMENT` | `development` | Deployment environment |
| `LOG_LEVEL` | `INFO` | Structured application log level |
| `DATABASE_URL` | `sqlite:///./data/cantill.db` | SQLAlchemy database URL |
| `BUSINESS_TIMEZONE` | `Asia/Kolkata` | IANA timezone used for policy-effective and requirement calendar dates |
| `POLICY_STORAGE_DIR` | `data/policies` | Local directory for uploaded policy PDFs |
| `AUTO_CREATE_TABLES` | `true` | Convenience schema creation for local development |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON array of allowed frontend origins |
| `API_PREFIX` | `/api` | API route prefix |

Production deployments should set `AUTO_CREATE_TABLES=false` and run
`alembic upgrade head` as a deployment step. Alembic revisions are the
authoritative schema migration strategy; `create_all` is only a local
development convenience and does not migrate existing schemas.

## Database migrations

Create a new revision after changing SQLAlchemy models:

```powershell
alembic revision --autogenerate -m "describe schema change"
```

Review and commit the generated migration, then apply it with `alembic upgrade
head`. Do not edit an already-applied migration to make later schema changes.

The initial migration creates the empty schema only. No task-kit data is copied
or seeded by this backend foundation.

## Master-data import

Keep the source kit outside the public repository. From the `backend` directory,
the importer automatically detects a sibling `task_kit` or
`Cantill_FullStack_Task_Kit` directory; pass `--source` to select another
location:

```powershell
python -m app.cli import-master-data --source ..\Cantill_FullStack_Task_Kit
python -m app.cli import-contract-pdfs --source ..\Cantill_FullStack_Task_Kit
```

Each command applies Alembic migrations before importing. The CSV command
validates required headers, types, duplicate keys and foreign-key references;
its JSON report includes imported, updated, skipped and rejected counts with
row-level errors. Invalid source records are reported, not silently discarded.
Identical records are skipped on subsequent runs.

The PDF command extracts contract identity, supplier, dates, ceiling and rate
lines, and compares registered contracts with the signed PDFs. A PDF-only
contract is stored with zero baseline consumption and `verified=false`,
`review_status=pending`; it must be manually verified before it is treated as
an approved contract. Source PDFs are read in place and are not copied into
this repository.

## PR batch processing and exports

The batch endpoint validates and processes the task-kit CSV schema and retains
one output row per non-empty input row:

```powershell
python -m app.cli process-batch --input ..\Cantill_FullStack_Task_Kit\pr_batches\sample_pr_batch.csv --output .\data\sample_decisions.csv
```

Batch row errors are emitted as `NOT_PROCESSED` with a reason; the CLI still
writes the complete CSV and exits non-zero if any rows need review. The
effective policy is selected by each PR line's `created_at` date. Lines are
processed in policy cohorts and are not clubbed across policy-version
boundaries. API users can retrieve the persisted result at
`GET /api/batches/{batch_id}/export`; preferred suppliers are available as CSV
from `GET /api/preferred-suppliers/export?as_of=YYYY-MM-DD`. That export includes
all ranked suppliers and a `top_three` boolean.

The batch storage migration is `35c9ab7f7a20_add_batch_run_exports`; apply it
with `alembic upgrade head` before deploying this version.

## Policy management

The Procurement policy screen is served by the React/Vite frontend. Policy
PDF uploads are text-extracted with PyMuPDF and saved outside source control.
The extracted text does not create executable rules: Procurement must review
and edit the structured policy JSON, validate it, inspect its diff, then approve
or reject it. Validated amendments activate only once their effective date is
due. See the [policy-management limitations and assumptions](../DECISIONS.md#5-policy-management-implementation-limitations-and-assumptions).

The policy metadata/snapshot migration is
`0f48d8e56843_store_policy_document_and_source_`; apply it with `alembic upgrade
head` before running against an existing database.
