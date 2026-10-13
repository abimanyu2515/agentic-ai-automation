# 0001. Enforce project specific testing configuration

**Date**: 2026-10-12
**Status**: In Progress

## Summary

Each project will own the target application URL and automation framework used by its tests. New projects will collect these values during creation, and older incomplete projects will remain editable but cannot generate or execute tests until they are configured. Generation and execution will use the selected project directly, with no demo application fallback or first project fallback.

## Context

The application already stores a target URL, framework, and project credentials, but the setup flow does not make the URL required when a project is created. The frontend can show an Adactin URL when a project has no URL, and the backend can resolve a project indirectly or use global configuration. These behaviors make a project such as CricScore appear configured when it is not, and can cause testing to use the wrong application.

The application supports both a JSON repository and a PostgreSQL repository. It also supports asynchronous generation and execution, project scoped permissions, encrypted credentials, and multiple automation frameworks. The change must preserve these contracts while making project configuration explicit, validating it consistently, and preventing configuration changes from altering an active operation.

> Premise note: Removing hardcoded values alone would not solve the reliability problem. A blank or invalid project URL could still reach generation or execution, and a downstream component could still select the wrong project. This spec therefore treats setup validation and explicit runtime project context as part of the same decision.

## Requirements

**User stories**:

* As a project manager, I want to provide the target URL and framework when I create a project so that the project is ready for testing.
* As a tester, I want to see whether the selected project is ready and what is missing so that I can fix setup before starting a workflow.
* As a tester, I want generation and execution to use the selected project URL so that tests never run against another project or a demo application.
* As an administrator, I want project configuration changes protected during active operations so that a running test remains reproducible.

**Acceptance criteria**:

* **AC-1**: New project creation requires a valid absolute HTTP or HTTPS `target_url` and a supported `framework`. Invalid data returns HTTP 422 and does not create the project.
* **AC-2**: Project responses include derived readiness with `is_ready`, `missing_fields`, `invalid_fields`, and a safe target URL value. Passwords and other secrets are never returned.
* **AC-3**: Existing projects without valid configuration remain viewable and editable, but generation and execution return HTTP 422 with `PROJECT_NOT_READY`, exact missing or invalid fields, and a settings action hint.
* **AC-4**: The selected project id, normalized target URL, framework, and credential reference are passed explicitly through generation and execution. Playwright receives `TARGET_URL` from the selected project. No global URL, first project fallback, Adactin default, or misleading frontend fallback can be used.
* **AC-5**: The frontend collects `target_url` and `framework` during project creation, shows a readiness banner and missing settings near the selected project, disables blocked actions, and links to Project Settings.
* **AC-6**: The frontend uses an empty value and generic guidance instead of an Adactin URL. Generic examples are never submitted as configuration.
* **AC-7**: Project `target_url` values use normalized full URL identity and are unique within the owning user. Existing owner scoped duplicates are reported without silent data changes, and new conflicting projects for the same owner are blocked. Different owners may use the same target URL.
* **AC-8**: Project `target_url` and `framework` edits are blocked while any generation, batch, or execution operation for that project is queued or running. Operations use a captured project configuration and later edits cannot change an active operation.
* **AC-9**: Existing role permissions and project scoping remain in force. Project administrators and managers configure projects. Assigned testers generate and execute within their project.
* **AC-10**: Configuration changes, readiness failures, generation blocks, and execution blocks produce structured logs with project id and target URL, but never secrets.
* **AC-11**: Navigation or connection failures are execution failures, not configuration fallbacks. They are classified separately and include the safe target URL in diagnostics.
* **AC-12**: The shared validation service behaves consistently for JSON and PostgreSQL repositories, with atomic duplicate protection appropriate to each repository.

## Options considered

### Option 1: Remove visible defaults only

This option removes the Adactin values from the frontend and leaves the existing project setup and execution flow mostly unchanged.

**Pros**:

* Smallest code change.
* Low migration effort.

**Cons**:

* Incomplete projects could still reach generation or execution.
* Runtime project selection would remain indirect.
* It would not provide a reliable fix for CricScore or other dynamic projects.

### Option 2: Keep settings separate and validate at execution

This option keeps the current project creation form and validates the target URL only when a test is generated or executed.

**Pros**:

* Existing project creation behavior changes less.
* Configuration remains in the current settings area.

**Cons**:

* Testers discover missing setup late.
* Generation and execution can fail after significant workflow work.
* The frontend still needs to explain readiness and remove misleading defaults.

### Option 3: Enforce project configuration at the project boundary

This option collects URL and framework during creation, derives readiness from project data, blocks operations for incomplete projects, and passes a captured project configuration through generation and execution.

**Pros**:

* New projects are clear and testable from the start.
* Existing incomplete projects remain recoverable without silently changing data.
* The selected project remains the source of truth across the full workflow.
* The design fits the current repositories, services, agents, and frontend patterns.

**Cons**:

* Project creation and update contracts become stricter.
* Existing duplicate URLs require an explicit review path.
* Active operation tracking must cover generation, batch, and execution work.

## Decision

**Chosen option**: Option 3: Enforce project configuration at the project boundary.

Project configuration will be validated and exposed as a derived readiness result, then captured and passed explicitly through every generation and execution operation. The existing application stack will be extended in place rather than replaced.

**Implementation skills**: none detected

## Rationale

The current failure is a boundary problem, not only a string replacement problem. A project URL can be absent, a frontend fallback can imply the wrong application, and the Playwright agent can identify a project indirectly. Option 3 fixes the boundary where the project becomes testable and carries that identity through the workflow.

The engineer chose a normalized URL unique within the owning user. This prevents one owner from creating ambiguous project identities while allowing separate owners to test the same application. Existing owner scoped duplicates will not be changed automatically, which protects historical data and gives operators a deliberate recovery path.

## Feature design

**Data model sketch**:

| Entity | Fields and rules | Relationships |
|---|---|---|
| Project | Existing project id as primary key. `name` remains required. `framework` is required for new projects and must be one of `playwright`, `selenium`, or `cucumber`. `target_url` is required for new API projects, nullable for legacy records until repaired, and stores a normalized absolute HTTP or HTTPS URL. `target_username` remains optional. The encrypted password remains in VaultService. | Existing one to many relationships to Requirement, Scenario, TestCase, and Execution remain unchanged. A normalized non null target URL is unique within `owner_id` for non deleted projects. |
| Operation context | Existing workflow and batch records gain a nullable project configuration snapshot containing project id, normalized target URL, framework, and a credential reference. The snapshot is captured when work is accepted into the queue and is used by retries and resumed workers. It is not a user facing entity. | Each generation, batch, and execution operation has one snapshot for its project. Each item in a mixed batch uses its own project snapshot if mixed project batches are supported; otherwise mixed project batches are rejected. |

No setup table or persisted readiness column is added. Readiness is derived from the project fields and validation rules.

**State transitions**:

* `unready`: `target_url` or `framework` is missing or invalid.
* `ready`: `target_url` and `framework` are valid.
* `operation_locked`: a generation, batch, or execution operation for the project is queued or running. This is a derived lock, not a stored project state.
* A project can move from `unready` to `ready` through a valid update.
* A project cannot change `target_url` or `framework` while it is `operation_locked`.
* An operation captures project configuration at start and uses that snapshot until it reaches a terminal state.

**API surface**:

| Endpoint or action | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| Create project | `POST /api/v1/projects` | `name`, `description`, `framework`, `target_url`, optional `jira_project_key`. Credentials and owner fields are not accepted. | Project data and derived readiness. Credentials are configured later in Project Settings. | `get_current_user` plus `Projects:create` permission | `401`, `403`, `409` for an owner scoped duplicate URL, `422` for invalid configuration or forbidden fields |
| Read project | `GET /api/v1/projects/{project_id}` | Project id | Project data and `readiness` with `is_ready`, `missing_fields`, `invalid_fields`, and safe URL | Existing project access | `401`, `403`, `404` |
| Update project | `PUT /api/v1/projects/{project_id}` | Editable project fields, including optional target URL and framework | Updated project data and derived readiness | Existing project administration permission | `401`, `403`, `404`, `409`, `422`, `423` when configuration is locked by an active operation |
| Generate script | Existing project scoped generation route | Project id, requirement or test case inputs | Existing generation result or stream | Existing project generation permission | `401`, `403`, `404`, `422` with `PROJECT_NOT_READY`, `423` when locked as applicable |
| Execute test | Existing project scoped execution route | Project id, test or batch inputs | Existing execution result or stream | Existing project execution permission | `401`, `403`, `404`, `422` with `PROJECT_NOT_READY` |
| Project readiness presentation | Frontend action using project response | Selected project id | Banner, missing settings, disabled actions, settings link | Existing authenticated session | Safe inline error when project data cannot load |

The readiness error response uses this stable shape:

```json
{
  "code": "PROJECT_NOT_READY",
  "message": "Project configuration is incomplete",
  "missing_fields": ["target_url"],
  "invalid_fields": {},
  "action": {
    "label": "Open Project Settings",
    "route": "#/settings?tab=project"
  }
}
```

`missing_fields` is a list of field names. `invalid_fields` maps field names to stable reason codes such as `invalid_url`, `unsupported_framework`, or `duplicate_url`. A field appears in only one collection. The action route is a frontend route hint and the project id remains in the current selection context.

Create and update duplicate writes return HTTP 409 with this stable shape:

```json
{
  "code": "PROJECT_URL_CONFLICT",
  "message": "A project with this target URL already exists for the current owner.",
  "invalid_fields": {
    "target_url": "duplicate_url"
  },
  "conflicting_project_id": "uuid"
}
```

The conflicting project id is returned only when the caller is authorized to see that project. Otherwise the field is omitted. Create validation uses the existing `ProjectConfigurationError.as_detail()` shape with `code`, `message`, `missing_fields`, `invalid_fields`, and the Project Settings action hint. Forbidden credential or owner fields use `invalid_fields` reason `field_not_permitted`. Configuration changes blocked by an active operation return HTTP 423 with `code: PROJECT_CONFIGURATION_LOCKED`, `operation_id`, `operation_type`, and `operation_status`.

The existing project update route remains `PUT` for compatibility, but it uses field presence aware partial update semantics. Omitted fields retain their current values. Explicit null is allowed only for optional fields. `target_url` and `framework` are validated as a pair before the write.

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| Create project | Project id | Existing project model id generation |
| Create project | Normalized target URL | `target_url` request field after URL validation and normalization |
| Create project | Framework | `framework` request field validated against existing supported values |
| Create project | Jira project key | Optional `jira_project_key` request field, preserved as project data |
| Read project | Readiness status | Derived from the stored project `target_url` and `framework` |
| Read project | Missing fields | Derived by the shared project readiness validator |
| Read project | Invalid fields | Derived by the shared URL, framework, and uniqueness validation |
| Read project | Safe URL display value | Stored normalized `Project.target_url`, with URL userinfo and fragments rejected at validation time. Query text remains because full URL identity was chosen; callers must not put secrets in query parameters |
| Read project | Credential presence | VaultService lookup result represented only as safe presence or absence, never a secret |
| Generate script | Project id | Existing route input, validated against the requirement, scenario, or test case ownership |
| Generate script | Target URL and framework | Captured from the selected project after readiness validation |
| Execute test | Project id | Existing execution route input and batch item association |
| Execute test | Target URL | Captured project configuration passed to the runner and set as `TARGET_URL` |
| Execute test | Credentials | VaultService lookup by captured project id, passed only as child process environment variables |
| Readiness banner | Missing and invalid settings | Project response readiness object |
| Execution diagnostics | Target URL | Captured project configuration, with secrets excluded |
| Active lock | Active operation status | Existing workflow, batch, and execution operation tracking. `queued`, `starting`, and `running` are active. `passed`, `failed`, `cancelled`, `timed_out`, and `blocked` are terminal |

**Key invariants**:

* Project target URLs are absolute HTTP or HTTPS URLs.
* URL normalization lowercases the scheme and hostname, removes a trailing slash, preserves path, query, and port, and is used for identity checks.
* URL normalization trims surrounding whitespace, removes default ports for the selected scheme, rejects userinfo and fragments, and preserves query ordering and path encoding.
* Normalized full target URLs are unique within the owning user for new and updated projects. Different owners may use the same normalized URL.
* Existing duplicate records are reported and are not silently rewritten.
* New projects cannot be created without valid target URL and framework values.
* Legacy incomplete projects can be viewed and edited but cannot generate or execute.
* No global Playwright URL, first project fallback, Adactin URL, or demo credential can satisfy project readiness.
* The selected project id must remain associated with its requirement, scenario, test case, and execution.
* Target URL and framework updates are rejected while a related generation, batch, or execution operation is queued or running.
* Each operation uses a project configuration snapshot captured at operation start.
* The snapshot is captured when the operation is accepted into the queue, persisted in the existing operation context, and reused by retries, resumed workers, and child processes.
* A project update and operation enqueue use one repository transaction or serialized write boundary so an update cannot pass its lock check while a new operation becomes active.
* JSON repository writes use an interprocess write lock and atomic replace. PostgreSQL stores the normalized URL directly in `target_url` and uses a partial unique index on `(owner_id, target_url)` for non deleted rows where both values are non null.
* Project readiness depends only on valid `target_url` and `framework`. Credentials are optional and are not required for `is_ready: true`.
* Credentials are retrieved by project id from VaultService and are never included in normal API responses or structured logs.
* Navigation and connection failures do not trigger a fallback to another URL.

**Security model**:

Existing authentication, project ownership, and role checks remain authoritative. Project administrators and managers can configure project target URL and framework. Assigned testers can generate and execute within their project. All project data routes remain scoped by project id and ownership checks.

The target URL may point to any valid HTTP or HTTPS host, including localhost and private hosts, because internal quality assurance is an intended use. The runner must never log credentials. Target URL values are treated as project configuration and may appear in structured diagnostics. No regulated data scope was identified for this feature.

**Configuration required**:

No new environment variables are required. Existing repository configuration, VaultService, workflow tracking, and Playwright runner settings remain in use. The runtime contract uses `TARGET_URL`, `TARGET_USERNAME`, and `TARGET_PASSWORD`; the first is always set from the captured project snapshot, while the credential variables are set only when VaultService returns values.

**Critical test scenarios**:

* Happy path: create CricScore with a valid URL and Playwright framework, select it, generate a script, and execute it with `TARGET_URL` set to CricScore, verifies **AC-1**, **AC-4**, and **AC-5**.
* Missing configuration: load a legacy project without a target URL, show the readiness banner, and receive `PROJECT_NOT_READY` from generation and execution, verifies **AC-2**, **AC-3**, and **AC-5**.
* Invalid URL: submit a relative URL, unsupported scheme, or malformed URL and receive HTTP 422 without storing it, verifies **AC-1** and **AC-11**.
* Misleading defaults: load an incomplete project and confirm that no Adactin URL or demo credential appears in the form, response, generated script, or runner environment, verifies **AC-4** and **AC-6**.
* Duplicate URL: create two projects with equivalent normalized URLs and confirm the second operation is rejected without changing the first project, verifies **AC-7** and **AC-12**.
* Legacy duplicate review: load existing duplicate records and confirm they are reported without silent changes, verifies **AC-7**.
* Active edit lock: start a queued or running generation, batch, or execution operation and confirm target URL and framework updates return HTTP 423, verifies **AC-8**.
* Snapshot behavior: change settings while an operation is queued or running and confirm the operation continues with its captured values, verifies **AC-8**.
* Permission: confirm an unassigned tester cannot edit another project or execute against it, verifies **AC-9**.
* Failure classification: use an unreachable but valid URL and confirm the result is an execution navigation failure rather than a configuration fallback, verifies **AC-11**.
* Repository parity: run the same readiness, duplicate, transaction, and concurrent write checks through JSON and PostgreSQL repository implementations, verifies **AC-12**.
* Queue recovery: restart or retry a worker and confirm the persisted project snapshot is reused, verifies **AC-4** and **AC-8**.
* Secret boundary: confirm URLs with userinfo or fragments are rejected and credential values do not appear in API payloads, logs, generated diagnostics, process output, or artifacts, verifies **AC-2**, **AC-4**, and **AC-10**.

## Build plan

No project build approach is recorded. This plan assumes an end to end tracer bullet approach, with a thin path through persistence, API, frontend, generation, and execution first, followed by hardening and migration checks.

1. Add shared project URL normalization, supported framework validation, derived readiness, stable error structures, and URL redaction rules without changing existing storage shape, satisfies **AC-1**, **AC-2**, **AC-3**, and **AC-11**.
2. Update project create and update schemas, routes, service validation, role checks, and repository operations. Add canonical URL storage, atomic normalized URL duplicate protection for JSON and PostgreSQL, and duplicate reporting for legacy data, satisfies **AC-1**, **AC-7**, **AC-9**, and **AC-12**.
3. Add the project configuration snapshot to existing workflow and batch operation context. Capture it at queue acceptance, persist it for retries and worker recovery, and define the project ownership checks for every associated requirement, scenario, test case, and batch item, satisfies **AC-3**, **AC-4**, and **AC-8**.
4. Thread the snapshot through WorkflowService, WorkflowState, PlaywrightAgent, ExecutionAgent, BatchQueueManager, and PlaywrightRunner. Set `TARGET_URL` from the snapshot and remove indirect first project and global URL fallbacks, satisfies **AC-3**, **AC-4**, and **AC-11**.
5. Add the project operation lock using existing generation, batch, and execution tracking. Make the lock check and project update or operation enqueue atomic, reject target URL and framework updates while related operations are queued or running, and release the derived lock after terminal states, satisfies **AC-8** and **AC-12**.
6. Update the project creation form and project settings to collect URL and framework, replace the Adactin placeholder and fallback with empty generic guidance, render readiness, disable blocked actions in loading and error states, and link to settings, satisfies **AC-5** and **AC-6**.
7. Add structured events for configuration changes, readiness failures, generation blocks, and execution blocks with event name, project id, operation id when available, normalized target URL without userinfo, and correlation id. Add redaction tests for credentials and URL userinfo, satisfies **AC-10** and **AC-11**.
8. Add focused backend tests for validation, repository parity, duplicate handling, readiness errors, operation locks, concurrent writes, snapshot persistence, project context propagation, worker retry, and runner environment setup. Add frontend tests for project creation, readiness presentation, blocked actions, and removal of the Adactin fallback, satisfies **AC-1** through **AC-12**.
9. Update the related API and workflow documentation, inventory existing duplicate canonical URLs, resolve them through an operator workflow, and only then enable the PostgreSQL unique index, satisfies **AC-7** and **AC-12**.

## Consequences

**Positive**:

* Testers can see whether a project is ready before investing time in generation.
* CricScore and other projects use their own target applications throughout the workflow.
* Incomplete legacy records are recoverable without silently assigning a new application.
* Runtime failures are easier to distinguish from configuration failures.
* The change preserves the existing FastAPI, repository, VaultService, LangGraph, Playwright, and vanilla frontend architecture.

**Negative and tradeoffs**:

* New project creation becomes stricter and may reject callers that relied on implicit defaults.
* Existing duplicate URLs need operator review before the database uniqueness constraint can be fully enforced.
* Locking settings during active work can delay a correction until the operation finishes or is cancelled.
* More project context must be carried through workflow state and runner boundaries.
* Allowing private hosts preserves internal testing capability but requires deployment owners to treat the runner as a trusted network actor.

**Neutral**:

* Existing historical Adactin documentation and clearly labeled fixtures may remain, but they are not runtime sources of truth.
* Existing projects with valid settings continue to use their stored values.

## Follow-up

* [ ] Define the operator workflow for resolving legacy duplicate normalized URLs before enabling a database unique constraint in an existing PostgreSQL deployment.
* [ ] Run the full backend and Playwright test suites in an environment with all development dependencies installed.

## Migration plan

**Strategy**: feature flagged repository and API rollout

**Phases**:

1. Deploy shared validation, canonical URL calculation, readiness reporting, and snapshot fields in compatibility mode. Existing records remain readable, and duplicate records are reported without being changed.
2. Deploy the new project creation contract, explicit project context, runner URL propagation, and frontend readiness presentation. Keep old workers compatible with absent snapshots during the transition.
3. Enable blocking for generation and execution of incomplete projects, then enable atomic update locks for active operations after all workers understand snapshots.
4. Review and resolve existing duplicate canonical URLs, add the PostgreSQL uniqueness constraint, and enable atomic duplicate protection for all writes.

**Rollback**: Disable the blocking feature flag and stop accepting new snapshot based operations if needed, while retaining readiness reporting and logs. Keep the nullable snapshot fields during rollback so old and new workers can coexist. Revert the explicit runtime propagation only after active operations have drained, because in flight operations use captured configuration.

**Risks**: Existing clients may omit required create fields. Existing PostgreSQL data may contain duplicate normalized URLs. Operation status names may differ across workflow and execution components. A lock implemented against incomplete status coverage could permit a configuration change during an active operation.
