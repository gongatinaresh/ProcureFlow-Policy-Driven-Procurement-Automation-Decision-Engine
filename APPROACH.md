
# Implementation Approach

## 1. Architecture and Technology

ProcureFlow is implemented as a policy-driven procurement decision platform with a frontend, backend API, persistent database, and deterministic procurement evaluation engine.

### Technology Stack

- **React + TypeScript + Vite:** Frontend application and interactive procurement workflows.
- **Python + FastAPI:** REST API and backend business logic.
- **SQLite + SQLAlchemy:** Persistent storage and relational data management.
- **Pandas:** CSV processing and data handling.
- **Pydantic:** Request validation and structured data validation.
- **Pytest:** Automated backend testing.
- **Alembic:** Database schema migration management.
- **Docker Compose:** Containerized application setup and execution.
- **Nginx:** Frontend serving and application routing within the containerized deployment.

The frontend and backend communicate through HTTP APIs. Procurement decisions are evaluated by backend logic rather than being calculated in the frontend.

---

## 2. Policy-Driven Decision Engine

The procurement engine is designed to evaluate requests consistently against procurement policy, master data, historical purchase information, contracts, supplier eligibility, and procurement thresholds.

The decision process follows a structured pipeline:

1. Validate incoming procurement request data.
2. Resolve item codes or match item descriptions against the item master.
3. Identify eligible requirement-clubbing opportunities.
4. Calculate requirement quantity and valuation.
5. Evaluate available contracts and applicable contract rates.
6. Check supplier eligibility and purchasing restrictions.
7. Apply procurement policy rules in the defined order.
8. Determine the applicable procurement route.
9. Evaluate supplier selection and sourcing requirements where applicable.
10. Produce the decision together with supporting evidence.

The engine uses deterministic logic so that the same validated inputs and policy version produce reproducible outcomes.

---

## 3. Policy as Data

Procurement policy is separated from the decision execution logic wherever supported by the application.

Policy versions and rule definitions are used to control procurement evaluation. The policy management workflow supports working with policy changes without treating every amendment as a software code change.

Policy amendments must be validated and reviewed before they are relied upon for procurement decisions.

The decision engine uses policy rule ordering and clause references to explain why a procurement request receives a particular route.

---

## 4. Conversational Procurement Intake

The Employee Procurement Chat provides a guided conversational interface for creating purchase requests.

The workflow collects the required procurement information through sequential questions, validates user responses, and presents a review step before submission.

The chat collects information such as:

- Requester details
- Department
- Plant
- Item description or item code
- Quantity
- Unit of measurement
- Required delivery date
- Urgency
- Estimated unit price, when available

The chat uses a deterministic, step-based workflow. It does not depend on an external generative AI model to make procurement decisions.

After confirmation, the request is submitted to the backend API and evaluated using the procurement decision engine. The resulting decision and procurement reference are displayed to the user.

---

## 5. Batch Processing

The application supports procurement batch processing through CSV input.

The batch workflow is intended to:

1. Accept procurement request data.
2. Validate the incoming records.
3. Process procurement lines through the backend decision engine.
4. Apply the configured procurement policy.
5. Generate row-level decision results.
6. Make processed results available for review and export.

Batch processing and conversational intake use backend procurement logic rather than separate decision implementations.

---

## 6. Contracts, Suppliers, and Procurement Decisions

Supplier and contract information are maintained as procurement data used during evaluation.

The engine considers relevant procurement inputs, including:

- Item master data
- Supplier eligibility and status
- Historical purchase information
- Contract coverage
- Contract rates
- Contract ceilings and consumption
- Plant and procurement requirements
- Policy-defined procurement thresholds and routing rules

Contract and supplier information contributes to procurement decisions and the supporting decision evidence.

Blocked or otherwise ineligible suppliers must not be treated as valid purchasing options.

---

## 7. Explainability and Decision Evidence

Procurement decisions should be understandable to the requester and procurement buyer.

The backend decision process produces structured decision information, including the procurement route and relevant policy rationale.

The application separates procurement input data from evaluated decision results, allowing decisions to be inspected rather than represented only as a final status.

The objective is to make procurement outcomes traceable to the applicable business rules and inputs.

---

## 8. Testing and Validation

The backend is tested using Pytest.

Validation includes:

- API health and endpoint tests
- Request and data validation
- Database behavior
- Master data import
- Policy management
- Procurement engine decision logic
- Workspace API behavior

The frontend is validated through the production build process.

The conversational procurement workflow has also been manually tested through request intake, validation, review, submission, and procurement decision response.

---

## 9. Deployment and Execution

The application can be run using Docker Compose to bring up the frontend and backend services.

The containerized workflow simplifies local setup and provides a consistent environment for running the application.

The deployment configuration includes frontend serving, backend API execution, and database integration.

---

## 10. Design Principles and Limitations

- Procurement outcomes are determined by backend business logic, not by an external AI model.
- Procurement policy is treated as a business rule rather than hard-coded to individual sample request identifiers.
- Input validation is performed before procurement evaluation.
- Existing procurement records and master data are used as decision inputs.
- Procurement decisions should be explainable through their applicable rules and data.
- Unsupported or invalid inputs should not be silently treated as valid procurement requests.
- Policy changes should be handled through the supported policy workflow and validated before use.
- The application is designed to demonstrate procurement automation, policy-driven routing, and decision consistency.

The conversational interface is a guided deterministic workflow rather than an open-ended AI chatbot. This keeps request collection predictable, validated, and aligned with the procurement decision process.
