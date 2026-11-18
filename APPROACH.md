# Proposed implementation approach

## 1. Architecture and technology

Use the requested stack:

- React + TypeScript + Vite + Tailwind CSS for employee chat, policy administration and buyer workspace.
- Python + FastAPI for APIs and orchestration.
- SQLite + SQLAlchemy for persistent, relational application data and an auditable decision ledger.
- Pandas for batch CSV ingestion/export and Pydantic for request, policy and row validation.
- Pytest for policy-engine, API and integration tests.

Keep the core decision engine independent of the UI, chat model and CSV shape. Convert each intake mode into the same validated PR-line model, then run one deterministic pipeline. Use versioned policy data and stable identifiers rather than sample PR-number branches.

## 2. Policy-first implementation

1. Encode v5.0 from the actual policy clauses into a validated, ordered policy version with explicit references to clauses 1.1-8.1.
2. Build the policy context and generic condition evaluator before implementing decisions.
3. Implement item matching, non-clubbing new items, clubbing, requirement valuation, contract selection/ceiling, supplier ranking, ordered routes and PO consolidation in separate testable services.
4. Store complete run inputs, selected policy version, decision evidence, supplier evidence and contract allocations.
5. Add policy upload/amendment as a draft/validate/explain/approve workflow. Never execute arbitrary uploaded text or accept a silently incomplete parse.
6. Validate expected behavior against all 14 worked decisions and three supplier lists, then test boundaries and data cases not represented in those examples.

## 3. Planned AI usage

AI is optional assistance, never the source of truth for procurement outcomes.

- **Employee chat:** use a model through a replaceable provider adapter to extract structured candidate fields from free text and draft concise follow-up questions. Validate all outputs with Pydantic and require requester confirmation before PR creation.
- **Item match assistance:** use normalized catalog matching and deterministic scores first. AI may suggest/rerank catalog candidates for ambiguous descriptions, but must return existing item codes only and show the evidence; user/buyer confirmation resolves uncertain matches. An unmatched request remains a new item.
- **Policy amendment reading:** use a model to extract a proposed structured diff from uploaded policy text or a procurement chat description. Validate every rule against a safe typed schema, show source text and plain-language diff, and require Procurement approval. The deterministic evaluator applies the approved data.
- **Contract PDF reading:** use local text extraction for the supplied searchable PDFs and validate extracted fields against the register, contract lines and supplier/item masters. AI may assist extraction of future documents but cannot activate a contract without human verification.
- **Not AI:** valuation, ceiling allocation, policy ordering, PO/RFQ/RFP routing, preferred-supplier score calculation, blocked-supplier checks, consolidation and export generation are deterministic backend functions.

No model provider, deployment region or data-retention agreement is specified. Decide this before enabling any external model call. Keep the provider replaceable, keep credentials out of the repository, minimize employee data sent for extraction, retain the original human-confirmed fields, and provide a non-AI/manual review path. The fictional challenge inputs are not a reason to hard-code their answers.

## 4. User experience

- Employee flow: chat -> ask for missing details -> show catalog match and parsed summary -> requester confirms -> receive PR reference.
- Buyer flow: prioritized requirement queue -> expandable source lines/evidence -> approve PO or sourcing event, resolve uncertainty, or override with a reason.
- Procurement flow: upload/describe amendment -> view extracted change and validation warnings -> approve with effective date -> inspect immutable version history.
- Batch flow: upload -> see row-level validation and processing summary -> inspect decisions -> download exact required CSV.
- Use clear status and reason text. Do not present low-confidence matching or unsupported data as a successful decision.

## 5. Delivery principles

- Public repository; hosted app remains available for review.
- One-command local setup, preferably Docker Compose, with seeded fictional data and no committed secrets.
- Preserve a clean distinction among source data, derived decisions, human approvals and overrides.
- Store policy and ranking rationale in visible decision evidence so the application can be explained and changed during the blind test.
- Keep original runs and exports reproducible when a policy is amended or a batch is rerun.

