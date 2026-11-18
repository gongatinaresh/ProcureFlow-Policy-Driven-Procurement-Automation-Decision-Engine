# Cantill.ai procurement PR-to-PO application requirements

## 1. Purpose and source of truth

Build a web application for Corvo Engineering that accepts employee purchase requests, applies an effective-dated procurement policy, and guides a buyer to either a purchase order (PO) or a sourcing event. The procurement rules are policy data, not item- or sample-specific application logic.

The requirements below are derived from the supplied source kit:

- [Task brief](./Cantill_FullStack_Task_Kit/Cantill_FullStack_Task_Brief.pdf)
- [Procurement Policy v5.0](./Cantill_FullStack_Task_Kit/Corvo_Procurement_Policy.pdf)
- [Worked examples](./Cantill_FullStack_Task_Kit/Worked_Examples.pdf)
- [Source README](./Cantill_FullStack_Task_Kit/README.txt)
- CSVs under `master_data/`, `contracts/`, and `pr_batches/`, plus every signed PDF under `contracts/pdf/`

The policy applies to PUN1 (Bhosari) and CHK1 (Chakan), values are INR excluding GST, and v5.0 is effective 1 April 2026. A policy amendment must be able to change limits, conditions, rule order and plant/category-specific behavior without a code change, subject to the supported policy schema and approval workflow.

## 2. Functional requirements

### 2.1 Employee chat and PR creation

- Provide conversational intake, not a conventional purchase-request form as the primary intake.
- Extract the requested item/description, quantity, plant, need-by date and urgency from the conversation.
- Ask follow-up questions for required or ambiguous information; show an item candidate for confirmation when confidence is not adequate.
- Do not fabricate an item. Support unknown/new-item requests and route them according to policy.
- Summarize the interpreted request and obtain confirmation before creating the PR.
- Retain requester, department, plant, conversation and created PR lines.
- Support one PR containing multiple lines.

### 2.2 Item identification

- If a PR line contains an item code, use that code as required by policy 1.1; validate that the code exists.
- If no code is supplied, match the description to the item master, including common abbreviations and case/format variation.
- Require human confirmation when multiple catalog items are plausible or the match is below a configurable confidence threshold.
- A line that matches no item is a new item: it is not clubbed, is valued at quantity times the requester's estimated unit price, and uses `NEW_ITEM_SOURCING` with no supplier suggestions (policy 1.2).
- Preserve the original description and the resolved item code separately.
- Validate unit of measure and quantities; do not silently reinterpret a unit mismatch.

### 2.3 PR batch intake

- Accept the supplied CSV format through the API and buyer workspace.
- Validate required headers, row structure, dates, numeric fields and references.
- Process valid lines and report invalid lines explicitly; do not turn invalid input into a successful-looking decision.
- Preserve `pr_no` and `pr_line` from the input.
- Permit reruns under a selected approved policy version and preserve each run's inputs, decisions and exports.

### 2.4 Requirement clubbing

Implement policy clauses 2.1-2.3 exactly:

- Sort lines by creation timestamp, then PR number, then line number.
- A line may join an existing requirement only when it has the same resolved item, the same plant, is within 14 calendar days of the requirement's first line, and its need-by date is within 7 days either side of the first line's need-by date.
- If multiple requirements qualify, join the earliest one.
- Otherwise start a new requirement. Apply value limits to the total requirement; do not split requirements to evade limits.
- Use the first line's creation date as the requirement date. Use that date for contract validity and purchase-history lookups.
- If any line is marked Emergency, mark the requirement Emergency.
- Never club new-item lines.

### 2.5 Valuation

Implement policy 3.1 on the whole requirement quantity:

1. If a usable applicable contract is selected, use its rate.
2. If applicable contracts exist but all are out of ceiling, use the lowest applicable contract rate for valuation.
3. Otherwise use the most recent PO unit price for the item in the 365-day pre-requirement-date window.
4. Otherwise use the item-master standard price.
5. For a new item, use the requester's estimated unit price.

Store the selected valuation source, unit price, quantity and calculated INR value so a buyer can see and audit the calculation. Define currency precision and rounding once and apply it consistently.

### 2.6 Contract validation and ceiling

- Load contracts from both the register/line CSVs and signed PDFs. A register-only contract list is insufficient.
- A contract applies only if it covers the item, is valid on the requirement date (inclusive start/end dates), and its supplier is Active (policy 4.1).
- Select the lowest-rate applicable contract that has enough remaining ceiling (policy 4.2-4.3).
- Test remaining ceiling using the requirement's total quantity at that contract's rate.
- Process requirements in requirement-date order for ceiling allocation.
- Treat register consumed values as the baseline at the register's last update. A signed contract after that update starts at zero (policy 4.2).
- Record contract provenance (register, line CSV, signed PDF), consumption baseline, allocation, and resulting remaining balance.
- Do not place a PO with or suggest a Blocked or Inactive supplier (policy 5.7).
- Let a buyer add a PDF-only contract manually or through extraction, with human verification before it becomes usable.

### 2.7 Decision rules and supplier selection

Apply the first matching outcome in policy order (see `DECISIONS.md` and `ARCHITECTURE.md` for the execution sequence):

| Policy rule | Condition | Outcome and route |
|---|---|---|
| 5.1 | Item type is Capex | Sourcing request, `CAPEX_SOURCING`; suggest 5 suppliers; requires capex approval |
| 5.2 | A contract can be used | PO, `CONTRACT_PO`, contract supplier and contract rate |
| 5.3 | Emergency, value <= INR 500,000, and item bought from an Active supplier in the preceding 365 days | PO, `EMERGENCY_PO`, first preferred supplier at its reference price |
| 5.4 | Value < INR 200,000 and item bought from an Active supplier in the preceding 365 days | PO, `SPOT_PO`, first preferred supplier at its reference price |
| 5.5 | Value < INR 1,000,000 and no earlier rule applied | Sourcing request, `RFQ`; suggest 3 suppliers |
| 5.6 | Value >= INR 1,000,000 | Sourcing request, `RFP`; suggest 5 suppliers |
| 5.7 | Supplier eligibility guard | Blocked/Inactive suppliers cannot be used for a PO or suggested for sourcing |

New items always use `NEW_ITEM_SOURCING`, before ordinary valuation/routing. Capex sourcing precedes contracts and value limits. The system must retain the rule, policy version, evidence, and contract/PO history relied upon.

### 2.8 Preferred supplier lists

- Build a preferred list for each catalog item as of a requested date.
- Include every Active supplier with at least one PO for the item in the preceding 548 days, or a contract for the item that started on or before that date, even if expired (policy 6.1).
- Rank consistently and explainably using purchase frequency, recency, contract history, on-time delivery and quality (policy 6.2).
- Provide the first-ranked supplier's reference price: most recent PO for the item in the 548-day period, or that supplier's most recent contract rate if no such PO exists (policy 6.3).
- For sourcing suggestions, take the preferred list in order, then add Active suppliers whose supplier-master categories include the item's category, ordered by delivery and quality record (policy 6.4).
- Suggest 3 suppliers for RFQ and 5 for RFP/capex. New items have no suggestions.
- Never include a Blocked or Inactive supplier.
- Explain each rank and its evidence to the buyer.
- Export the preferred list as CSV for a chosen date. The exact interpretation of “top 3” in the brief's stated export shape needs a documented decision.

### 2.9 PO consolidation

- After requirement decisions, combine PO requirements only when they have the same supplier, plant and route, and each qualifying requirement was raised within 24 hours of the first requirement on that PO (policy 7.1).
- Process requirements in creation order; join the earliest open qualifying PO (policy 7.2).
- Never combine sourcing requests.
- Preserve each requirement as a separate PO line and assign a shared PO ID to every exported PR line belonging to the consolidated PO.

### 2.10 Policy management

- Procurement can upload a policy or amendment document or describe a proposed change in chat.
- Parse changes into a versioned, effective-dated policy draft and show a plain-language change summary/diff before activation.
- Require an authorized human approval; retain uploader, approver, timestamps, source file, version, effective date and prior versions.
- Activate the approved policy on its effective date. Permit explicitly selected reruns under a different approved version while retaining the original run.
- Support updates to limits, windows, conditions (including item type, category, plant, value, urgency, contract/history) and outcomes, and new policy rules expressible in the policy schema.
- Reject or hold unsupported/ambiguous policy constructs for human review; never silently ignore a rule.

### 2.11 Buyer workspace, approvals and audit

- Show each requirement with its PR lines, item match, quantity, group value, decision/route, suggested or selected supplier, price, policy version, clause and evidence.
- Make the next action clear: approve/issue PO, initiate RFQ/RFP/capex sourcing, resolve a match, or reject/override.
- Buyer approval creates the PO or sourcing event.
- An override requires a written reason and retains both original and overridden decisions.
- Record who performed each approval, override, policy approval, contract validation and other consequential action, and when.
- Provide searchable audit history by PR, requirement, PO, sourcing event, policy, supplier and actor.

### 2.12 Exports and required PR decision CSV

The task brief requires one output row per input PR line with these columns, in this order:

1. `pr_no`
2. `pr_line`
3. `resolved_item_code`
4. `group_id`
5. `po_id`
6. `decision`
7. `route`
8. `supplier_id`
9. `unit_price`
10. `group_value_inr`
11. `suggested_supplier_ids`
12. `policy_version`
13. `reason`

Requirements for the output:

- Preserve the input PR keys; clubbed lines share `group_id`.
- PO-bound lines carry the consolidated `po_id`; non-PO lines leave it empty.
- `decision` is `PO` or `SOURCING_REQUEST`; routes include `CONTRACT_PO`, `EMERGENCY_PO`, `SPOT_PO`, `RFQ`, `RFP`, `CAPEX_SOURCING`, and `NEW_ITEM_SOURCING`.
- For unprocessed lines use route `NOT_PROCESSED` and provide a reason.
- Include policy version and the clause/evidence in `reason`; identify the contract or PO relied upon where applicable.
- For sourcing, provide ranked IDs separated by semicolons; for new items, leave suggestions empty.
- Leave non-applicable fields empty rather than manufacturing a value.
- Support CSV upload/download and the preferred-supplier CSV export.

### 2.13 Technical and delivery requirements

- Frontend: React, TypeScript, Vite and Tailwind CSS.
- Backend: Python and FastAPI.
- Persistence: SQLite and SQLAlchemy.
- Data handling/validation: Pandas and Pydantic.
- Automated backend tests: Pytest.
- Provide a public GitHub repository and a hosted application that remains available for review.
- Provide a README with a one-command local setup/run (Docker Compose is preferred by the brief).
- Keep policy decisions in a testable backend engine, separate from UI and chat interpretation.
- Do not hard-code answers for the supplied PR numbers or items.

## 3. Source-kit inventory and observed data

The source README says the contract CSVs are current as of 31 October 2026 and that the signed PDF folder contains every signed contract. The inspected kit contains:

- 73 catalog items, 48 suppliers, 2 plant records and 468 PO-history rows (468 unique PO numbers, dated 1 June 2025 through 28 October 2026).
- Supplier status counts: 43 Active, 3 Blocked and 2 Inactive.
- 10 contract-register entries, 26 registered contract-line rows and 12 signed contract PDFs.
- 186 batch lines across 175 PR numbers; 39 lines have no item code, 60 have no estimated unit price, and 6 lines are marked Emergency.
- Six PR numbers have multiple lines (17 lines total across those PRs).
- One batch line references PUN2 although the plant master and policy recognize only PUN1 and CHK1.
- Four descriptions contain “urgent” while the structured urgency is `Normal`.
- Two descriptions do not correspond to a catalog item: “Drinking water purifier 50 LPH” and “Ultrasonic thickness gauge”. The former is also used in a worked example.
- Purchase-history supplier and item references resolve to the current supplier/item masters; its plant values are PUN1/CHK1.

## 4. Important input and policy edge cases

- Exact threshold equality: policy 5.4 is strictly below INR 200,000; policy 5.3 includes INR 500,000; policy 5.5 is below INR 1,000,000; policy 5.6 starts at INR 1,000,000.
- Emergency above INR 500,000 falls through to later rules; Emergency alone does not bypass a contract or capex precedence.
- No Active-supplier purchase history means no emergency/spot PO under 5.3/5.4, even at a small value.
- A valid contract can be unusable because its supplier is not Active or its ceiling is insufficient.
- Several contracts may cover the same item with different rates and remaining ceilings.
- PDF-only contracts, expired contracts in preferred-list membership, inclusive contract dates, and contract ceilings across multiple requirements must be handled.
- The source CSV can have blank code/estimate, abbreviations, multiple lines per PR, free-text “urgent” conflicting with the urgency column, and unsupported plant codes.
- Item-master standard price, requester's estimate, PO price and contract rate may disagree; the correct source depends on the explicit valuation order.
- Similar requests can fall just outside the creation or need-by window, be for different plants, or qualify for multiple existing groups; preserve earliest-group behavior.
- Requirement processing order and PO consolidation are distinct: requirements use chronological ceiling allocation and PO grouping uses an earliest open PO within the 24-hour window.
- Supplier is no longer Active at decision time despite historical POs; historical evidence must not override the supplier eligibility guard.
- Empty preferred list, too few category suppliers, ties in ranking, no usable reference price, missing contract extraction fields and malformed CSV rows need explicit outcomes.
- Reprocessing the same batch, approving/rejecting/overriding a decision, and changing a policy must not double-count contract consumption or erase historical decisions.

