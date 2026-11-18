# Implementation plan

This is a documentation/planning deliverable only. Do not begin application implementation until the user approves the source findings, proposed decisions and ambiguities in `DECISIONS.md`.

## Phase 1 - Confirm source model and establish project

- Confirm critical decisions: export shape, replay/effective-date behavior, PUN2 treatment, ranking formula, auth and contract reservation lifecycle.
- Create React/TypeScript/Vite/Tailwind frontend and FastAPI backend layout.
- Set up SQLite/SQLAlchemy, configuration, migration strategy, Docker Compose, public-repo hygiene and a one-command README.
- Define Pydantic API models and a stable, typed PR-line/policy schema.
- **Exit:** app boots locally; no business rule is hidden in UI code.

## Phase 2 - Import and reconcile source data

- Load item, supplier, plant, PO history, register and contract-line CSVs with Pandas.
- Extract each signed contract PDF, including the two absent from the register; compare extracted terms/lines to CSV and supplier/item masters.
- Record source provenance, validation errors and supplier statuses; make PDF-only contracts reviewable.
- Explicitly flag PUN2 and the Blocked S004 contract.
- **Exit:** counts, values and known discrepancies match the source audit in `DECISIONS.md`.

## Phase 3 - Policy versioning and administration

- Store policy versions, ordered rules, clause references, effective dates, source document and approvals.
- Load v5.0 as initial approved policy.
- Implement amendment upload/chat proposal, schema validation, human-readable diff, approver action and immutable version history.
- **Exit:** a policy amendment is approved as data and selected by a run without code edits.

## Phase 4 - Core decision engine

- Implement item-code precedence, catalog matching and explicit new-item handling.
- Implement chronological requirement clubbing and requirement evidence.
- Implement date-based contract applicability, usable-rate selection, value computation and ceiling ledger.
- Implement price precedence, supplier ranking/reference prices and sourcing top-up.
- Implement ordered decision rules, global supplier exclusion, and chronological PO consolidation.
- **Exit:** deterministic engine tests cover every policy clause, thresholds, PDF-only contracts and all supplied worked examples.

## Phase 5 - API, audit and CSV exports

- Implement required `POST /api/process-batch`, validation feedback, run persistence and decisions endpoint.
- Implement exact 13-column per-PR-line CSV export and preferred supplier export.
- Implement buyer approve/override actions, policy/contract changes and immutable audit events.
- Add idempotency for uploads/reruns/ceiling allocations.
- **Exit:** API/integration tests verify malformed rows, repeat runs, exact headers, line cardinality and auditable actions.

## Phase 6 - Employee chat and workspaces

- Build conversational PR intake with missing-field follow-ups, candidate confirmation and final PR confirmation.
- Build buyer requirement queue with policy clause, source evidence, price/value, supplier rationale and next action.
- Build procurement policy/contract screens for upload, review, approval and history.
- **Exit:** all routes have a clear next action and uncertainty is visible instead of silently resolved.

## Phase 7 - Validation, deployment and handoff

- Run Pytest and frontend/build checks; test v5.0 and a policy amendment replay against the same batch.
- Verify all 14 worked decisions, three supplier ranking examples, edge cases and exact CSV output fields.
- Deploy and verify the hosted URL; ensure the public repository and setup instructions work from a clean checkout.
- Document AI use, ranking logic, assumptions and remaining limitations.
- **Exit:** hosted app and public repo are reviewable; no PR-specific/sample-specific answer logic exists.

## Smallest high-value test matrix

- Clubbing: same item/plant, different plant, 14-day boundary, 7-day need-by boundary, earliest of two groups, Emergency propagation.
- Valuation: contract usable, competing contracts, all ceilings exhausted, recent PO, standard price, missing new-item estimate.
- Routes: capex before contract, contract before emergency, emergency at/over INR 500,000, spot strictly below INR 200,000, RFQ strictly below INR 1,000,000, RFP at INR 1,000,000, blocked/inactive exclusion.
- Suppliers: 548-day inclusion, expired contract membership, no recent PO reference-rate fallback, tie-breaking, category top-up, insufficient suppliers and new item.
- Consolidation: same and different supplier/plant/route, exact 24-hour boundary, multiple eligible open POs, no sourcing consolidation.
- Policy and audit: amendment validation/approval/effective date, preserved original run, explicit replay, override reason, contract reservation idempotency.
- Data/export: PUN2 row, blank code/estimate, unknown code, ambiguous match, invalid dates/UOM, multiline PR, `NOT_PROCESSED`, exact CSV header and one output row per input line.

