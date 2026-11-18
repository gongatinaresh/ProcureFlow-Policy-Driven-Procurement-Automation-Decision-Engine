# Proposed architecture

## 1. System overview

Use a thin React/TypeScript/Vite/Tailwind client, a FastAPI service, and a SQLite database accessed through SQLAlchemy. Keep intake interpretation, policy management, decision execution, supplier ranking, audit and export as separate backend modules. The decision engine is deterministic and policy-driven; the UI and any AI-assisted extraction do not decide routes.

```text
Employee chat / Buyer workspace / CSV upload
                 |
             React app
                 |
        FastAPI + Pydantic
       /          |           \
 Intake      Policy service    CSV/API
       \          |           /
         Decision orchestration
           |        |        |
      Item match  Contracts  Supplier rank
           \        |        /
             Ordered policy engine
           /          |          \
     SQLite/SQLAlchemy  Audit   CSV exports
```

The policy evaluator must not use `eval` or execute uploaded code. A validated condition/action schema is interpreted by a deterministic evaluator. Unknown rule fields or outcomes are held for review rather than silently skipped.

## 2. Proposed database design

Use integer primary keys or stable UUIDs and foreign keys. Persist monetary values as integer minor units or fixed-precision Decimal values, not binary floating point. Store dates/timestamps in normalized ISO form and use a consistent timezone for creation timestamps.

| Entity | Important fields and relationships |
|---|---|
| `Item` | `item_code`, description, UOM, category, `item_type`, standard price, active flag |
| `Plant` | plant code, display name, active flag |
| `Supplier` | supplier ID, name, city, categories, status, on-time percentage, quality rating |
| `PurchaseHistoryLine` | PO number/date, supplier, item, quantity/UOM, unit price, plant, delivered-on-time; indexed by item/date/supplier |
| `Contract` | contract ID, supplier, start/end dates, ceiling, baseline consumed value, source/provenance, PDF, verified status |
| `ContractLine` | contract, item, description, UOM, rate |
| `ContractAllocation` | contract, requirement, run, amount, status (`reserved`, `committed`, `released`), idempotency key |
| `PolicyVersion` | version, effective-from, status (`draft`, `approved`, `active`, `superseded`), source, proposer/approver and timestamps |
| `PolicyRule` | policy version, explicit order, condition JSON, outcome JSON, clause reference, explanation |
| `PolicyChangeProposal` | uploaded source/chat prompt, parsed draft, diff, validation issues, review and approval details |
| `ChatSession` / `ChatMessage` | requester, messages, extracted fields, candidate matches, confirmation state |
| `PRBatch` | upload name/hash, upload actor/time, selected policy/run, validation summary |
| `PurchaseRequest` / `PRLine` | PR identity, requester, department, plant, created time; item code/description, quantity/UOM, estimate, need-by, urgency |
| `Requirement` / `RequirementLine` | group anchor, resolved item, plant, requirement date, total quantity/value; line-to-PR-line mapping |
| `DecisionRun` | batch or chat source, policy version, evaluation timestamp/as-of date, engine version, input hash, status |
| `Decision` | requirement, clause, route, PO/sourcing outcome, value, price source, contract/history evidence, reason and supplier suggestions |
| `PreferredSupplierSnapshot` / `SupplierRankEvidence` | item/date, rank, supplier, reference price, component scores and evidence |
| `PurchaseOrder` / `PurchaseOrderLine` | PO ID, supplier, plant, route, issue/approval details; requirement lines and contract |
| `SourcingEvent` | route, status, suggested suppliers, approval and event details |
| `BuyerAction` / `AuditEvent` | actor, action, entity, timestamp, reason, before/after decision data |
| `ExportArtifact` | run, export type, generated time, CSV metadata and hash |

### Data integrity and lifecycle

- Load the two plant records and the catalog/supplier/history/contract data as a traceable import. Retain the original row/source for reconciliation.
- Model every signed contract PDF separately from the contract register source. The signed PDF is authoritative for signed terms; buyer verification is required when extracted content conflicts or is incomplete.
- A policy version is immutable after approval. Changes create a new draft/version.
- Decisions and exports reference an immutable `DecisionRun` and its `PolicyVersion`; a rerun creates another run.
- Initialize contract consumption from the register's baseline. Track subsequent allocations in a ledger. Reserve capacity consistently during a run; commit on approval/PO creation, release on cancellation, and make retries idempotent.
- Allocate contract capacity in policy order by requirement date, within a database transaction. A rerun must not mutate historic run results or double-count the same allocation.
- For SQLite, serialize the decision/allocation write transaction (single-writer behavior) and retry or return an explicit conflict if another run changes capacity concurrently.
- Store explicit source IDs (PDF path/hash, CSV/import row, PO number, policy clause) with decisions so buyer explanations are reproducible.

## 3. Policy representation and evaluation order

Represent policy rules as versioned data with ordered conditions, outcomes, and source-clause references. Include typed context fields for item type/category/code, plant, requirement value, urgency, contract availability, supplier status and history. Support common predicates (equality, membership, numeric/date range, boolean, conjunction/disjunction) and outcomes (PO route, sourcing route, supplier count, approval requirement). Keep contract, valuation and supplier-ranking mechanics as explicit typed engine services rather than arbitrary expressions.

An ordered run:

1. Select the approved policy version for the requested evaluation date/run. Validate batch schema and retain the original input.
2. Resolve items. A supplied valid item code takes precedence. Resolve no-code descriptions with deterministic normalization/catalog matching and a confidence threshold; request confirmation when needed. Unmatched items take the new-item path and are isolated from clubbing.
3. Sort PR lines by creation timestamp, PR number, line number and club according to clauses 2.1-2.3. Anchor each requirement on its first line; record why each line joined or began a new group.
4. Sort requirements by first-line creation time (with stable PR/line tie-breaks). Determine requirement date, plant, quantity and emergency flag.
5. Find applicable contracts as of each requirement date: item coverage, inclusive term and Active supplier. Consider register contracts and extracted PDF-only contracts with no detected register discrepancy; retain their source hash and manual-review status in decision evidence.
6. Allocate contract ceiling in requirement order. Select the lowest-rate contract with sufficient capacity. If all applicable contracts are out of ceiling, retain the lowest applicable contract rate for valuation but do not mark it usable.
7. Value the requirement under 3.1. For a non-new item with no usable contract, use the latest PO in the 365-day lookback; otherwise standard price. New items use the requester's estimate.
8. Build the preferred list as of requirement date using 6.1-6.3. Build sourcing suggestions using 6.4, filtering Blocked/Inactive suppliers at every path. For 5.3/5.4 direct POs, use the selected supplier's 6.3 reference price; preserve the pre-selection valuation used for threshold evaluation in evidence.
9. Apply decision rules in exact order: 5.1 capex; 5.2 usable contract; 5.3 emergency PO; 5.4 spot PO; 5.5 RFQ; 5.6 RFP. Apply 5.7 as a global eligibility guard. New-item rule 1.2 is a separate early path.
10. Consolidate only PO requirements using 7.1-7.2. Do not combine sourcing events. Allocate a PO ID only after grouping.
11. Save decision evidence and policy version, present buyer action, then create PO/sourcing event on approval. Require a written reason for any override and append immutable audit history.
12. Export one row per PR line in the exact task-brief shape; all lines in one requirement share its group ID and decision/value, and PO-consolidated requirements share a PO ID.

Every step records intermediate evidence: item match/candidate score; group anchor/window; selected or out-of-ceiling contract and ceiling calculation; price source; preferred-supplier score; policy rule; supplier eligibility; and PO consolidation key.

## 4. API design

All request/response payloads use Pydantic models. CSV endpoints use multipart upload and return stable run IDs and row-level validation details.

| Endpoint | Purpose |
|---|---|
| `POST /api/chat/sessions` | Start employee intake session |
| `POST /api/chat/sessions/{session_id}/messages` | Extract request fields, return follow-up or match confirmation |
| `POST /api/chat/sessions/{session_id}/confirm` | Confirm parsed PR and create request/lines |
| `POST /api/process-batch` | Accept PR-batch CSV, process lines under the policy effective on each requirement date, return batch ID, statistics, errors and decisions |
| `GET /api/batches/{batch_id}/export` | Download the persisted required PR decision CSV |
| `GET /api/preferred-suppliers/export?as_of=YYYY-MM-DD` | Download all ranked eligible supplier rows with a `top_three` indicator |
| `GET /api/buyer/requirements` | Buyer queue with decision, value and next action |
| `POST /api/requirements/{id}/approve` | Approve PO or sourcing action |
| `POST /api/requirements/{id}/override` | Override with mandatory reason and audit record |
| `GET /api/policies` | List policy versions and statuses |
| `POST /api/policies/upload` | Upload policy/amendment and create a draft proposal |
| `POST /api/policies/{id}/validate` | Validate schema and produce plain-language change summary |
| `POST /api/policies/{id}/approve` | Record human approval and effective date |
| `GET /api/contracts` / `POST /api/contracts` | List, add and verify register/PDF-only contracts |
| `GET /api/audit` | Query audit events |

`POST /api/process-batch` returns a persisted batch identifier, per-policy version
list, processed/unprocessed counts, row errors and line decisions. A malformed
file-level schema returns a client error; a line that can be identified but
cannot safely be processed is retained as `NOT_PROCESSED` with a reason. Do not
accept client-supplied supplier, route, or price values as authoritative
decisions.

## 5. Data flow

### Employee chat

1. User sends natural language.
2. Intake extracts candidate item, quantity, plant, need-by date and urgency.
3. Catalog matcher resolves a code or asks the user to confirm; missing fields prompt follow-up.
4. User confirms a summary; API persists PR and lines.
5. Decision orchestration applies the selected policy version and returns a traceable buyer decision.

### Batch processing

1. Upload UTF-8 CSV; validate required headers and types and retain each source row.
2. Detect duplicate PR keys and reject invalid rows individually as `NOT_PROCESSED`;
   malformed file headers fail with HTTP 422.
3. Select the approved/effective policy for each PR creation date and process
   each policy cohort in chronological order. Clubbing does not cross policy
   version boundaries.
4. The engine resolves items, clubs requirements, allocates contract ceiling in
   requirement-date order, values each requirement, applies ordered rules,
   ranks eligible suppliers and consolidates PO outcomes.
5. Persist each policy-engine run and the complete line-level batch result,
   including unprocessed rows, for deterministic export.
6. Download the required output columns from the persisted batch result without
   recalculating decisions.

The CLI equivalent is `python -m app.cli process-batch --input PATH --output PATH`.
It writes the CSV even when some rows are unprocessed and returns a non-zero
status to make those errors visible to automation.

### Policy amendment

1. Upload the policy PDF; PyMuPDF extracts selectable text and the application stores
   the source hash, text, and a structured draft cloned from the effective version.
2. Procurement edits the structured JSON policy document and validates it against
   the bounded declarative rule schema. Unsupported operators/fields and executable
   content are rejected; no code from a PDF is executed.
3. Procurement reviews the old-vs-new diff, effective date, and extracted text,
   then approves or rejects the validated version. Every lifecycle action is audited.
4. An approved policy is activated when its effective date arrives and policy
   resolution runs. Runs continue to record the exact policy version used.

The API is `POST /api/policies/upload`, `POST /api/policies/{id}/validate`,
`GET /api/policies/{id}/diff`, `POST /api/policies/{id}/approve`,
`POST /api/policies/{id}/reject`, `GET /api/policies`, and
`GET /api/policies/active`.

### Contract import

1. Parse searchable PDF text (use OCR only if a future contract is image-only).
2. Extract contract ID, supplier, term, ceiling, item lines, rates and source references.
3. Compare against register/line CSV and supplier/item masters.
4. Require buyer verification before activation; initialize post-register contracts with zero consumption as stated in policy 4.2.

## 6. Quality and operational concerns

- Unit tests for policy boundaries, window inclusivity, earliest-club behavior, contract ceiling selection/consumption, supplier eligibility, price precedence and PO consolidation.
- Regression tests for every worked example and tests for uncovered cases; examples validate behavior but must not be encoded as PR-specific rules.
- API tests for CSV errors, deterministic reruns, effective policy selection, approval/override audit, and stable exports.
- Integration test that processes the same batch under v5.0 and an approved amendment without mutating the original run.
- Apply explicit role checks for Employee, Buyer and Procurement approver. Authentication provider is unspecified; choose a minimal safe hosted-demo mechanism before implementation.
- Keep secrets and any model credentials outside source control. Log identifiers and outcomes, not unnecessary employee chat content.
