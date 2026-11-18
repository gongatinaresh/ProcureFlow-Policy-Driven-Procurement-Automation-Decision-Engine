\# Cantill Procurement Intelligence Platform



A policy-driven procurement decision system for \*\*Corvo Engineering\*\* that transforms employee purchase requests and batch PR data into explainable, auditable procurement decisions.



The platform combines conversational intake, CSV processing, policy version management, contract evaluation, supplier ranking, procurement routing, and a buyer workspace using a deterministic decision engine.



\---



\## Overview



Procurement decisions often depend on multiple factors: item availability, purchasing history, supplier eligibility, contract pricing, spending ceilings, urgency, policy rules, and consolidation opportunities.



Cantill centralizes these checks in a policy-driven backend so that procurement decisions are consistent, explainable, and reproducible.



\### Key capabilities



\- Conversational employee purchase-request intake

\- Catalog item matching and request clarification

\- CSV batch processing with row-level outcomes

\- Deterministic policy evaluation

\- Requirement clubbing and consolidation

\- Contract validation and ceiling management

\- Preferred supplier ranking

\- PO, RFQ, and RFP routing

\- Buyer decision workspace

\- Policy amendment and version management

\- Contract PDF upload and text extraction

\- Decision evidence and audit records

\- CSV exports



\---



\## Application Features



\### 1. Employee Request Intake



Employees can submit purchase requirements through the chat interface.



The intake workflow collects relevant information, identifies missing fields, presents catalog matches, and allows the requester to review and confirm the requirement before processing.



The system supports:

\- Item identification and catalog matching

\- Quantity collection

\- Plant selection

\- Required delivery date

\- Urgency

\- Clarification of incomplete requests

\- Confirmation before submission

\- New-item handling when a catalog match is unavailable



The application uses implemented application logic rather than an external LLM for chat intake and catalog matching.



\### 2. Batch PR Processing



Upload procurement requests through CSV.



The batch pipeline validates input, processes eligible rows through the procurement decision engine, and preserves row-level errors and processing outcomes.



Features:

\- CSV schema validation

\- Row-level validation

\- Duplicate request handling

\- Item validation

\- Plant validation

\- Missing information checks

\- Policy-based processing

\- Batch decision persistence

\- Exportable results



Invalid or unsupported rows are reported with explicit reasons instead of silently being treated as successful procurement decisions.



\### 3. Policy-Driven Decision Engine



The decision engine evaluates requirements using structured policy data instead of hardcoded sample outcomes.



The engine handles:



\- Item matching

\- Requirement clubbing

\- Requirement valuation

\- Contract eligibility

\- Contract price selection

\- Contract ceiling checks

\- Supplier eligibility

\- Preferred supplier ranking

\- Procurement route selection

\- PO consolidation

\- Policy clause evidence



Decision logic is implemented in backend services and evaluated against the applicable policy version.



\### 4. Buyer Workspace



The buyer workspace provides visibility into processed procurement requirements and their decision evidence.



Buyers can review:

\- Requirement details

\- Source PR lines

\- Quantities and values

\- Selected suppliers

\- Contract information

\- Procurement route

\- Applicable policy clauses

\- Decision explanations

\- Supporting historical evidence



The workspace supports buyer decision workflows, including approvals and overrides where implemented, with override reasons retained as evidence.



### 5. Supplier Ranking

Supplier ranking uses a deterministic, policy-configured weighted scoring
system based on procurement history and supplier performance.

#### Preferred Supplier Scoring

| Factor | Weight | Scoring Method |
|---|---:|---|
| Purchase frequency | 35% | Relative purchase count for the item |
| Purchase recency | 25% | Score based on age of the latest PO |
| Contract history | 20% | Matching qualifying contract evidence |
| On-time delivery | 10% | Supplier on-time percentage normalized to 0–1 |
| Quality rating | 10% | Quality rating normalized against a maximum of 5 |

#### Purchase Recency Scoring

| Latest Purchase Age | Score |
|---|---:|
| 0–90 days | 1.0 |
| 91–365 days | 0.5 |
| 366–548 days | 0.0 |

Purchase history is evaluated within a configurable 548-day lookback
window, including the configured date boundaries.

#### Supplier Eligibility

- Only eligible supplier statuses are considered.
- Blocked and inactive suppliers are excluded.
- Suppliers with qualifying item-level contract history can be considered
  even without purchase history.
- Ranking ties are resolved using purchase count, latest purchase date,
  and supplier ID.

#### Supplier Suggestions

When additional suppliers are required, category-matching eligible
suppliers can be added using a separate scoring mechanism based on:

- On-time delivery: 50%
- Quality rating: 50%

Preferred suppliers and category-based supplier suggestions are kept
distinct so that suggestions are not misrepresented as having purchase
history.

Ranking weights, recency buckets, and supplier eligibility are
configured through the policy configuration rather than being
arbitrarily hardcoded in the ranking function.


\### 6. Contract Management



The contract register supports contract-related procurement evaluation.



Capabilities include:

\- Contract records

\- Contract line items

\- Supplier and item associations

\- Contract validity checks

\- Contract price evaluation

\- Contract ceiling evaluation

\- PDF upload

\- Local PDF text extraction

\- Contract evidence in procurement decisions



Uploaded contract documents are processed using local text extraction. Extracted information requires review and validation; the application does not use AI to automatically approve or activate contracts.



\### 7. Policy Management



Policy behavior is stored as structured, versioned data.



The policy management workflow supports:



\- Policy versions

\- Effective dates

\- Policy amendments

\- Draft and validation workflow

\- Structured rule editing

\- Validation against supported conditions and outcomes

\- Approval workflow

\- Policy history

\- Policy evidence in decision outputs



Policy changes can alter procurement decisions without changing the decision-engine source code when the amendment uses supported policy constructs.



Unsupported rule types require deliberate implementation rather than silently being treated as executable.



\---



\## Technology Stack



| Component | Technology | Purpose |

|---|---|---|

| Frontend | React | User interface |

| Language | TypeScript | Type-safe frontend |

| Build tooling | Vite | Frontend development and production build |

| Backend | Python, FastAPI | APIs and application services |

| Database | SQLite | Persistent application data |

| ORM | SQLAlchemy | Database interaction |

| Migrations | Alembic | Database schema migrations |

| Validation | Pydantic | Input and structured policy validation |

| CSV processing | Pandas | Batch processing and export |

| PDF extraction | PyMuPDF | Local contract and policy text extraction |

| Testing | Pytest | Backend testing |

| Containerization | Docker | Application packaging |

| Orchestration | Docker Compose | Local multi-service deployment |

| Web server | Nginx | Frontend hosting and API proxying |



\---



\## Architecture



```text

&#x20;                 EMPLOYEE / BUYER

&#x20;                        |

&#x20;                        v

&#x20;             React + TypeScript Frontend

&#x20;                        |

&#x20;                        v

&#x20;                  Nginx Gateway

&#x20;                        |

&#x20;               +--------+--------+

&#x20;               |                 |

&#x20;               v                 v

&#x20;         Frontend Assets    FastAPI Backend

&#x20;                                 |

&#x20;             +-------------------+-------------------+

&#x20;             |                   |                   |

&#x20;             v                   v                   v

&#x20;       Chat / Intake       Batch Processing     Buyer Workspace

&#x20;             |                   |                   |

&#x20;             +-------------------+-------------------+

&#x20;                                 |

&#x20;                                 v

&#x20;                      Procurement Decision Engine

&#x20;                                 |

&#x20;            +--------------------+--------------------+

&#x20;            |                    |                    |

&#x20;            v                    v                    v

&#x20;       Policy Engine       Supplier Ranking      Contract Engine

&#x20;            |                    |                    |

&#x20;            +--------------------+--------------------+

&#x20;                                 |

&#x20;                                 v

&#x20;                       SQLAlchemy Persistence

&#x20;                                 |

&#x20;                                 v

&#x20;                             SQLite

```



The frontend communicates with the FastAPI backend through API endpoints. In Docker deployment, Nginx serves the frontend and proxies API requests to the backend service.



The decision engine remains independent of the frontend and intake format, allowing chat and batch requests to use the same procurement decision pipeline.



\---



\## Procurement Decision Flow



```text

Request Intake

&#x20;    |

&#x20;    v

Input Validation

&#x20;    |

&#x20;    v

Catalog Item Identification

&#x20;    |

&#x20;    v

Requirement Grouping / Clubbing

&#x20;    |

&#x20;    v

Requirement Valuation

&#x20;    |

&#x20;    v

Contract Eligibility \& Ceiling Evaluation

&#x20;    |

&#x20;    v

Supplier Eligibility \& Ranking

&#x20;    |

&#x20;    v

Policy Rule Evaluation

&#x20;    |

&#x20;    v

PO / RFQ / RFP / Sourcing Decision

&#x20;    |

&#x20;    v

PO Consolidation Check

&#x20;    |

&#x20;    v

Persist Decision \& Evidence

&#x20;    |

&#x20;    v

Buyer Review / Export

```



Each decision is evaluated using structured inputs, an applicable policy version, and supporting procurement evidence.



\---



\## Project Structure



```text

Cantill\_FullStack\_Task\_Kit\_vf/

│

├── backend/

│   ├── app/

│   │   ├── main.py

│   │   ├── policies/

│   │   ├── models/

│   │   ├── schemas/

│   │   ├── services/

│   │   └── ...

│   ├── tests/

│   ├── alembic/

│   ├── requirements.txt

│   └── ...

│

├── frontend/

│   ├── src/

│   ├── public/

│   ├── package.json

│   └── ...

│

├── docker-compose.yml

├── .gitignore

├── README.md

├── APPROACH.md

├── ARCHITECTURE.md

├── DECISIONS.md

├── IMPLEMENTATION\_PLAN.md

└── REQUIREMENTS.md

```



\*The tree is simplified for readability.\*



\---



\## Getting Started



\### Prerequisites



\- Docker Desktop

\- Docker Compose

\- Git



Ensure Docker Desktop is running before starting the application.



\### Run with Docker Compose



Clone the repository:



```bash

git clone <YOUR\_PUBLIC\_GITHUB\_REPOSITORY\_URL>

cd <REPOSITORY\_DIRECTORY>

```



Start the application:



```bash

docker compose up --build -d

```



\### Access the Application



| Service | URL |

|---|---|

| Frontend | http://localhost:8081 |

| API health check | http://localhost:8081/api/health |



Verify the backend health endpoint:



```bash

curl http://localhost:8081/api/health

```



Expected response:



```json

{

&#x20; "status": "ok",

&#x20; "database": "ok",

&#x20; "service": "Cantill Procurement API"

}

```



\### Stop the Application



```bash

docker compose down

```



To stop the application and remove its containers while preserving the named persistent data volume:



```bash

docker compose down

```



Application data is stored in the Docker-managed persistent volume configured by the Compose setup.



\---



\## API



\### Health Check



```http

GET /api/health

```



Checks application and database health.



\### Batch Processing



```http

POST /api/process-batch

```



Processes a procurement batch using the configured policy and decision pipeline.



The batch endpoint validates uploaded data and returns processing outcomes, decision information, and row-level errors where applicable.



\### Batch Export



Batch results can be exported through the application's CSV export functionality.



Refer to the backend API implementation for exact request schemas, response fields, and available endpoints.



\---



\## Testing



The backend test suite covers policy evaluation, procurement decision behavior, API processing, and policy management.



Run backend tests locally from the backend directory:



```bash

cd backend

pytest -v

```



\### Verified Development Checks



\- Backend test suite: \*\*59 passed\*\*

\- Frontend TypeScript build: \*\*passed\*\*

\- Frontend production build: \*\*passed\*\*

\- Docker Compose configuration validation: \*\*passed\*\*

\- Docker image build: \*\*passed\*\*

\- Docker services startup: \*\*passed\*\*

\- Frontend HTTP response: \*\*200 OK\*\*

\- API health check: \*\*200 OK\*\*



These checks were verified during development. Re-run the tests against the final commit before submission.



\---



\## Policy and Decision Documentation



Additional project documentation:



| Document | Description |

|---|---|

| `APPROACH.md` | Implementation approach, technology choices, AI usage, boundaries |

| `ARCHITECTURE.md` | System architecture and component responsibilities |

| `DECISIONS.md` | Policy interpretations, assumptions, supplier ranking rationale, edge cases, data reconciliation |

| `IMPLEMENTATION\_PLAN.md` | Implementation planning and work breakdown |

| `REQUIREMENTS.md` | Project requirements and challenge scope |



The procurement decision system is designed to make decisions inspectable, including relevant policy clauses, contract evidence, supplier evidence, and decision rationale.



\---



\## AI Usage Disclosure



AI tools were used as development assistance during implementation, including code generation, debugging, documentation assistance, and review.



The deployed application does not require an external AI provider and has no configured LLM integration.



Procurement outcomes are generated by deterministic backend logic and structured policy data rather than external model-generated decisions.



\---



\## Deployment



The application supports local Docker Compose deployment.



A publicly hosted deployment is intended for reviewer access. Hosting configuration and availability depend on the selected deployment environment.



\*\*Hosted application:\*\* `Pending deployment`



\*\*Public repository:\*\* `Pending repository publication`



Replace the placeholders with verified URLs before submission.



\---



\## Security and Production Considerations



This application was built as a challenge prototype, not a production procurement system.



Known limitations include:



\- Authentication is not implemented.

\- Role enforcement is not implemented.

\- Actor IDs are audit labels supplied by the UI, not verified identities.

\- Contract PDFs use configured local storage.

\- Scanned PDFs do not have OCR support.

\- Policy activation is lazy rather than scheduler-driven.

\- Policy amendments are limited to the supported structured rule schema.

\- External AI integration is not configured.

\- Production deployment requires secure authentication, authorization, durable document storage, backups, monitoring, and access controls.



These limitations are documented rather than presented as implemented capabilities.



\---



\## Design Principles



\- \*\*Policy over hardcoded outcomes:\*\* procurement decisions are driven by structured policy data.

\- \*\*Deterministic decisions:\*\* the same inputs and policy version should produce reproducible decisions.

\- \*\*Explainability:\*\* decisions should expose the evidence and policy rationale.

\- \*\*Validation first:\*\* unsupported and incomplete data should not silently pass.

\- \*\*Separation of concerns:\*\* frontend, intake, policy evaluation, supplier ranking, and persistence are separated.

\- \*\*Auditable changes:\*\* policy versions, approvals, and overrides should retain traceable evidence.

\- \*\*Honest AI boundaries:\*\* external AI is not used to generate procurement outcomes.

\- \*\*Reproducibility:\*\* batch runs and decisions should remain traceable to their inputs and policy versions.



\---



\## License



This repository was created as a full-stack procurement system implementation challenge.



Refer to the repository owner for licensing and reuse permissions.

