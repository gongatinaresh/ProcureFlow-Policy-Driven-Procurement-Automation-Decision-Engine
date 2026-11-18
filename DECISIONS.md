# Ambiguities, assumptions and proposed business decisions
This document records explicit requirements from Corvo Procurement Policy v5.0, source-data reconciliation findings, and implementation decisions made where the source kit is silent. It distinguishes policy requirements from engineering assumptions and documents the rationale behind deterministic procurement decisions, supplier ranking, contract handling, edge cases, and known implementation limitations.

- Use a supplied valid item code; otherwise match the description to the item master (1.1).
- Unmatched items are never clubbed, use the requester's estimate, and always go to `NEW_ITEM_SOURCING` without supplier suggestions (1.2).
- Club same-item/same-plant lines within 14 calendar days of a group's first line and within +/-7 days of its first need-by date. Use earliest qualifying group; process creation time, PR number, line number in order (2.1-2.3).
- Requirement date is the first line's creation date. Contract and history checks use that date. Any Emergency line makes the requirement Emergency (2.3).
- Value whole requirement quantity using contract rate, otherwise recent PO price, otherwise standard price; when all applicable contracts are out of ceiling, use the lowest applicable contract rate for valuation (3.1).
- Contract eligibility, ceilings, lowest-rate selection and PDF-only signed-contract treatment follow 4.1-4.4.
- First matching decision rule wins, in policy order 5.1 through 5.6. Blocked/Inactive suppliers are never used or suggested (5.7).
- Preferred-list membership and reference price use the 548-day and contract rules in 6.1-6.3; sourcing suppliers are topped up from active category suppliers per 6.4.
- Consolidate only POs sharing supplier, plant and route within 24 hours of the first requirement on the PO; use earliest qualifying open PO. Never consolidate sourcing requests (7.1-7.2).
- Record policy clause and contract/PO evidence; buyer overrides require written reasons and retain the original decision (8.1).

## 2. Data reconciliation findings

### Contracts

- The contract register contains 10 contracts and `contract_lines.csv` contains 26 lines. The signed PDF folder contains 12 contracts.
- The 10 registered contracts' supplier IDs, dates, ceilings, covered items, UOMs and rates match their signed PDFs and line CSVs; no field or line-rate mismatch was found.
- `RC-2026-031` is a signed PDF only: supplier S021 Pneumatic Point (Active), valid 15 Nov 2026-14 Nov 2027, ceiling INR 2,000,000, covering ITM-1036, ITM-1037 and ITM-1039. It applies from its inclusive start date, and its initial consumption is zero under 4.2 because it was signed after the 31 Oct register update.
- `RC-2026-033` is a signed PDF only: supplier S035 Infoline Systems (Active), valid 20 Nov 2026-19 Nov 2027, ceiling INR 5,000,000, covering ITM-1056 and ITM-1057. It applies from 20 Nov inclusive and starts at zero consumption.
- Registered `RC-2026-005` covers ITM-1006 and ITM-1007 but its supplier S004 Metalink Alloys is Blocked in `supplier_master.csv`. Under policy 4.1/5.7 it is not usable and must not be suggested, despite its signed terms and remaining registered ceiling.
- Do not infer that the register is complete; ingest and reconcile all signed PDFs. Record an explicit verified status and source provenance for PDF-only contracts.
- The consumed values in the register cannot be independently reconciled against PO history because the history CSV has no contract ID. Treat register values as authoritative baseline, as required by policy 4.2.

### Master and batch data

- Master data has 73 items, 48 suppliers (43 Active, 3 Blocked, 2 Inactive), 2 plants and 468 historical PO rows/PO numbers. PO history spans 1 Jun 2025-28 Oct 2026, has valid item/supplier references and PUN1/CHK1 plants.
- `plants.csv` and policy v5.0 define only PUN1 and CHK1. One sample line (PR-26-00173) uses PUN2.
- The batch has 186 lines across 175 PR numbers; six PRs contain multiple lines. There are 39 blank item codes and 60 blank estimated prices. No invalid item-master codes, nonpositive quantities, or need-by dates before creation were found.
- Two no-code descriptions have no catalog match: “Drinking water purifier 50 LPH” and “Ultrasonic thickness gauge”. The first is also a worked example of a new item; both should use the new-item path.
- Four rows contain “urgent” in the description while the structured urgency is `Normal`; six rows are explicitly marked `Emergency`.
- The input uses `delivered_on_time` in PO history as well as supplier-master `on_time_pct`/`quality_rating`. Worked ranking examples display the supplier-master on-time percentage and quality rating.

## 3. Proposed business decisions where policy is silent

| Topic | Explicit source rule / ambiguity | Proposed decision and rationale |
|---|---|---|
| PUN2 input | Policy applies to PUN1 and CHK1; one batch row uses PUN2. | Keep the row visible, set `NOT_PROCESSED`, and explain unsupported plant. Do not silently map it to PUN1 or CHK1. Employee chat should ask for a supported plant. |
| Item-match ambiguity | Descriptions may be vague; no confidence threshold or disambiguation flow is defined. | Normalize text and rank catalog candidates; require user/buyer confirmation below a calibrated threshold or when top candidates are close. Only a confirmed match gets clubbed. |
| Unknown items in CSV | Policy specifies new-item treatment but does not state how to handle a missing estimate. | If unmatched and estimate is missing or invalid, retain the row as `NOT_PROCESSED` pending estimate; do not fabricate a standard price or use unrelated items. |
| Urgency text conflict | Clubbing uses lines “marked Emergency”; sample has narrative “urgent” but field is `Normal`. | Use the structured urgency field for batch decisions. Flag conflicting wording for buyer review; in chat, ask for confirmation. Do not upgrade urgency from a description string. |
| Unsupported/invalid row | Brief requires `NOT_PROCESSED` for lines not processed but does not define schema failure granularity. | Reject a malformed file-level schema with a clear 4xx response. For an identifiable bad row, preserve its input keys and export `NOT_PROCESSED` plus a precise reason. If PR identity is missing, report a row-number validation error rather than emitting a fabricated PR row. |
| Exact date windows | “Within/before” does not fully specify whether lookback endpoints include the event date. | For creation/need-by windows, use inclusive <=14 and <=7 calendar-day differences. For “in the preceding N days”, use PO date >= requirement date minus N days and < requirement date; for contract terms, use inclusive start/end as explicitly stated. Log the dates used. |
| Requirements created at same time | Tie order is PR number then line number; requirements with same first timestamp may compete for ceiling. | Use the same stable PR number/line number tie-break order used for line processing. |
| Contract PO price vs value | Policy 3.1 refers to the “contract that will be used (4.3)”; ceiling testing and value are interdependent. | For each contract candidate, test `consumed + quantity * candidate rate <= cap`; choose the lowest-rate candidate that passes. If none pass, value using the lowest applicable rate but do not route as contract PO. This matches 4.2-4.3 and worked example 3. |
| Recent PO valuation status | Clause 3.1 does not say whether a historical price from a now Blocked/Inactive supplier is excluded. | Use the latest PO for valuation regardless of current status because 3.1 has no status restriction; never use that supplier for a new PO or suggestion. Show the historical source and flag its current status. |
| Historical PO “bought” status | Clauses 5.3/5.4 require a purchase from an Active supplier in the preceding 365 days but do not define cancelled/returned POs. | Count recorded PO-history rows whose supplier is currently Active; there is no cancellation/return field in the source. |
| Supplier ranking weights | Policy leaves weighting to system design. Examples show recency, count, contract, on-time and quality, but no full answer key. | Use a documented normalized score: frequency 35%, recency 25%, contract evidence 20%, on-time performance 10%, quality 10%. Suggested recency buckets: 1.0 for <=90 days, 0.5 for <=365 days, 0 for older; include only policy-eligible suppliers. This reproduces the examples' emphasis on frequency/recent buying/contract history while incorporating delivery and quality. Use supplier-master on-time/quality values as in the example tables; expose PO delivery outcomes as supporting evidence. Break exact ties by latest PO date, then supplier ID. Validate against all three example lists before release. |
| Supplier suggestion top-up | “Best delivery and quality record first” does not specify weights or tie-breaks. | Rank eligible category suppliers using equal weights for normalized on-time percentage and quality rating, then supplier ID. Do not add a supplier already in the preferred list. This reproduces the worked examples' top-ups without making one supplier attribute an absolute priority. |
| Contract rank evidence | A contract that started before the list date can count even after expiry; multiple rates/expired contracts may exist. | For membership, use any contract started by the date. For fallback reference price use the most recently started contract line for that supplier/item with a rate, even if expired, and label its status/date. |
| Preferred export “top 3” | Brief's fifth column is written as `top 3`, without defining whether it is a column or row limit. | Include all ranked suppliers and a boolean `top_three` column (`true` for ranks 1–3) so downstream users can filter while retaining full rank history. |
| PR export null fields | Brief defines fields but not whether non-applicable columns should be omitted or empty. | Always emit all 13 columns in the required order. Leave non-applicable IDs/prices blank. For rejected/invalid rows, set both `decision` and `route` to `NOT_PROCESSED` and put the reason in `reason`. |
| Effective policy for batch requirements | Clubbed requirements can include PR lines created on different dates, while policy versions are date-effective. | Assign the policy effective on each PR line's `created_at` date before clubbing. Do not club across policy-version cohorts; this avoids applying one version to a requirement whose lines span versions. Within a cohort, the engine uses the earliest line date as the requirement date. |
| Ceiling consumption lifecycle | Policy orders requirement consumption, but operational timing (decision vs buyer approval) is not specified. | Allocate/reserve ceiling in the chronological decision run so decisions are internally consistent; commit on PO approval, release on cancellation, and make reruns idempotent. Show pending reservations to buyers. |
| Buyer identity and roles | The brief requires actors and approvals but names no authentication mechanism. | Require distinct Employee, Buyer and Procurement Approver roles. Select the hosted-demo authentication approach before implementation; never rely on a user-editable actor name for audit identity. |
| Policy amendment scope | New rules must be policy data, but arbitrary logic cannot safely be executed from a document. | Support a validated declarative schema with typed fields/operators and reusable outcomes. Hold unsupported condition/action types for human review and implementation rather than pretending they were activated. |
| Price rounding | INR currency, taxes excluded; explicit rounding stage is absent. | Store rates/values at full source precision with Decimal arithmetic; round only for display/export to two decimal places unless a verified business requirement says otherwise. 
|
## 4. Ambiguities, assumptions, and remaining limitations

1. Preferred supplier export: whether “top 3” means only three rows per item, an additional column, or another shape.
2. Reprocessing an old batch: whether a newly approved amendment intentionally applies retroactively, as the proposed replay behavior assumes.
3. Unsupported plant PUN2: whether to reject the row, add PUN2 to configured plants, or map it; mapping is not supported by source evidence.
4. Supplier ranking weights: no exact formula is supplied; the proposed formula must be checked against the three example rankings.
5. Missing price for an unmatched CSV item: the policy requires the requester's estimate, but the batch has no estimate for every row.
6. Authentication/roles and contract-ceiling reservation timing are not specified.
7. Date-window endpoint inclusivity and a historical price from a currently blocked supplier are not fully specified.
8. Worked example 11 requests five RFP suppliers but lists four; the supplied master data has only four Active suppliers for Electrical Spares. Return the four eligible suppliers and do not suggest a Blocked/Inactive supplier merely to meet the requested count.

## 6. Batch pipeline behavior

- Required CSV schema validation is file-level (HTTP 422); row-level parsing,
  duplicate PR keys, unsupported plants, ambiguous/new-item cases without
  required estimates, and policy gaps are retained as output rows marked
  `NOT_PROCESSED` with an explicit reason.
- When a PR key already exists in storage with different source data, reject
  the uploaded row rather than mutating its recorded source.
- Batch results are persisted separately from decision runs so that error rows
  remain exportable alongside successfully processed decisions.
- Preferred supplier export includes every available ranked supplier, with
  `top_three=true` for the first three ranks. Supplier ranking remains limited
  to active suppliers with purchase or contract history; category fallback
  suppliers remain sourcing suggestions rather than fabricated preferred-list
  history.

## 5. Policy-management implementation limitations and assumptions

- PDF text extraction uses PyMuPDF. Scanned/image-only documents require manual
  structured entry; OCR is not configured.
- No AI provider is configured. Extracted text is for human review, and all
  executable policy behavior comes from validated structured data.
- The structured policy editor uses a bounded DSL: it supports the current
  whitelisted context fields, comparison operators, groups, and outcomes. New
  rules can combine those supported constructs, but new evaluator capabilities
  require a deliberate code change.
- Actor IDs are supplied by the UI and are audit labels only; authentication,
  authorization, and role enforcement are not implemented. Do not treat them as
  verified identities in a production deployment.
- PDFs are stored in the configured local policy-storage directory; the database
  stores the checksum, reference, extracted text, and immutable policy snapshot.
  Production requires managed durable storage, access control, and backups.
- Future policy activation is lazy: the active-policy endpoint or an engine
  evaluation performs effective-date activation; there is no scheduler service.
