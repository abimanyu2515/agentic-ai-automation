# 0002. Require target URL during project creation

**Date**: 2026-10-13
**Status**: In Progress

## Summary

Project creation will collect the target application URL in the existing create form. The API will treat the URL as required for new projects, normalize it, validate it, and store it with the project owner. Invalid or duplicate input will leave no project behind and will return an error the form can show clearly.

This closes the gap identified by the current PostgreSQL failure, where the frontend sends no `target_url` and the database rejects the resulting null value.

## Requirements

**User stories**:

- As an internal QA user, I want to provide the target application URL while creating a project so that the project is ready for testing.
- As a tester, I want invalid or duplicate URLs rejected before creation so that no unusable project is saved.
- As an administrator, I want project ownership and URL uniqueness enforced by the server so that concurrent requests cannot create ambiguous project configuration.

**Acceptance criteria**:

- **AC-1**: The create project form collects name, description, line of business, framework, and a required target URL in one submission.
- **AC-2**: A new project request requires a valid absolute HTTP or HTTPS URL and a supported framework. The backend is authoritative even when browser validation is bypassed.
- **AC-3**: Valid URLs are normalized before persistence and response. URL userinfo and fragments are rejected. Query text remains allowed.
- **AC-4**: The create request sends `target_url` and may send `jira_project_key` to `POST /api/v1/projects`. A successful `201` response contains the stored normalized URL, the project identity, the preserved Jira key, and the readiness object with `is_ready`, `missing_fields`, and `invalid_fields`, without exposing passwords.
- **AC-5**: A normalized target URL is unique within its authenticated owner. Concurrent duplicates result in one successful project and one structured `409` response, with no partial project.
- **AC-6**: Missing or invalid input returns the existing `ProjectConfigurationError.as_detail()` shape with `code`, `message`, `missing_fields`, `invalid_fields`, and the Project Settings action hint. The form stays open, preserves entered values where practical, and creates no project.
- **AC-7**: `POST /api/v1/projects` uses `get_current_user` and `has_permission("Projects", "create")`. The `Projects` module and `create` action are seeded for the existing RBAC system and granted to Project Manager, Admin, and Superadmin. The authenticated user's `id` is written to both `owner_id` and `created_by`. A request without an authenticated owner or permission is rejected before persistence, and the server ignores or rejects any client supplied owner identity.
- **AC-8**: Unexpected database failures roll back the transaction and return an explicit server error without leaving a project record behind. Project creation accepts no `target_username`, `target_password`, or `owner_id` fields, and rejects those fields with structured `422` details using `field_not_permitted`; it never creates a vault record. Credentials are added later through Project Settings.
- **AC-9**: Creation outcomes are logged through the existing application or audit logging path without passwords or sensitive URL parts.
- **AC-10**: The create form remains usable with keyboard navigation, associated labels, required state, and visible validation messages.

## Decision

**Chosen option**: Option 1: Extend the existing creation flow

Extend the existing project creation form and endpoint, use the existing URL configuration service as the canonical validator and normalizer, add a dedicated project creation permission to the existing RBAC seed, persist authenticated ownership in both repositories, enforce owner scoped uniqueness in both providers, and preserve legacy incomplete records until they are repaired. This spec applies the owner scoped uniqueness rule defined by spec 0001.

The implementation uses the existing FastAPI, Pydantic, repository, SQLAlchemy, PostgreSQL, JSON store, vanilla HTML, JavaScript, and RBAC conventions. No new provider, framework, library, environment variable, or community skill is needed.

## Feature design

**Data model sketch**:

| Entity | Key fields | Required or nullable | Relationships and constraints |
|---|---|---|---|
| Project | `id`, `owner_id`, `created_by`, `name`, `description`, `line_of_business`, `framework`, `target_url`, `target_username`, `target_password_enc` | `id`, owner identity, creator identity, name, description, framework, and target URL are required for new API projects. Legacy owner and target URL values may remain nullable until repaired. Credential columns remain legacy compatibility fields and are not written by project creation. | One owner has many projects. `owner_id` and `created_by` reference the authenticated user. Add owner scoped uniqueness for normalized non null `target_url` on non deleted projects. Never persist a raw password. |

**State transitions**:

No new project state is introduced. A create request either persists a valid project or persists nothing.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects` | POST | `name:string` required, `description:string` required, `line_of_business:string` default `general`, `framework:string` required and normalized against `SUPPORTED_FRAMEWORKS`, `target_url:string` required, optional `jira_project_key`. `target_username`, `target_password`, and `owner_id` are forbidden and rejected. | `201` with the existing project response fields plus preserved `jira_project_key`, normalized `target_url`, and `readiness: {is_ready, missing_fields, invalid_fields}`. Readiness is true when URL and framework are valid. No password or vault data. | `user: UserDB = Depends(get_current_user)` and `authorized: bool = Depends(has_permission("Projects", "create"))`. `owner_id` and `created_by` come from `user.id`. | `401` for missing or invalid authentication, `403` for missing permission, `409` with the exact conflict body below for an owner scoped duplicate, `422` with the exact validation detail shape below, explicit `5xx` for unexpected persistence failure |

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| Create form | Project name, description, line of business, framework, target URL | Existing form fields plus the new target URL input |
| Create request | `target_url` | Form value sent as the `target_url` JSON field |
| Create route | Owner identity and creator identity | `user.id` from `get_current_user`, written to `owner_id` and `created_by`; never a client field |
| URL validation | Normalized URL | Existing project configuration normalization rules applied to the request value |
| Duplicate check | Existing owner and normalized URL | Authenticated owner identity plus stored Project rows |
| Project response | Normalized target URL, project identity, and readiness | Persisted Project columns plus `readiness_for_project(project, projects)` using `target_url`, `framework`, and the owner scoped project list. Add the readiness object to `ProjectResponse` so POST and GET expose the same shape |
| Credential storage | No credential record during creation | Credentials are not create inputs. Project Settings later writes credentials through `VaultService` keyed by project ID |
| Validation message | Field or conflict error | Structured backend validation or conflict response rendered by the create form |

**Create error contracts**:

For validation failures, return the existing `ProjectConfigurationError.as_detail()` shape:

```json
{
  "code": "PROJECT_INVALID_CONFIGURATION",
  "message": "Target URL must use http or https.",
  "missing_fields": [],
  "invalid_fields": {
    "target_url": "invalid_url"
  },
  "action": {
    "label": "Open Project Settings",
    "route": "#/settings?tab=project"
  }
}
```

For forbidden request fields, return HTTP 422 with `invalid_fields` reason `field_not_permitted`. For duplicate URLs, return HTTP 409 with:

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

The conflicting project id is included only when the caller is authorized to see that project. The create form maps `invalid_fields.target_url` to the target URL field and keeps the other entered values. Password fields are never accepted or preserved.

**Key invariants**:

- New project creation cannot persist without a valid absolute HTTP or HTTPS target URL.
- Only the authenticated server context determines project ownership.
- URL userinfo and fragments are rejected. Query text is allowed.
- URL normalization happens before duplicate checks and persistence.
- A normalized target URL is unique within one owner for non null, non deleted rows. Projects owned by different users may use the same normalized URL.
- The create request schema preserves `jira_project_key` and uses an extra field policy that rejects `target_username`, `target_password`, `owner_id`, and any other undeclared create field with HTTP 422.
- PostgreSQL stores the canonical normalized URL directly in `projects.target_url`; no second canonical URL column is introduced.
- JSON persistence uses an in process repository lock and atomic temporary file replacement for create and update. The same owner scoped comparison is applied before replacement.
- A failed database operation is rolled back and the failed session is cleared before another database operation.
- Project creation never writes the vault. Credential setup is a separate operation and cannot make project creation appear successful or failed.
- Readiness is derived only from valid `target_url` and `framework`; credentials are optional and do not affect `is_ready`.
- Passwords and sensitive URL parts are excluded from normal responses and diagnostic logs.

**Security model**:

Authenticated users with the `Projects` create permission may create projects owned by themselves. The existing RBAC seed adds the `Projects` module and `create` action, granted to Project Manager, Admin, and Superadmin. The route uses `get_current_user` and `has_permission("Projects", "create")`; `user.id` is persisted as both `owner_id` and `created_by`. Missing authentication returns `401`; missing permission returns `403`; a missing user ID is treated as an authentication failure and is rejected before repository access. The client cannot assign ownership. Target URLs are project configuration, not secrets, but URL userinfo is rejected and logs must avoid sensitive URL components. Credentials remain in `VaultService`, and passwords never appear in project responses.

**Configuration required**:

None. The change reuses existing application configuration, repository selection, authentication, and vault services.

**Critical test scenarios**:

- Happy path: submit the create form with a valid URL and supported framework, then read the project and verify the normalized URL, verifies **AC-1**, **AC-2**, **AC-3**, and **AC-4**.
- Invalid input: submit a missing, malformed, unsupported scheme, userinfo, or fragment URL, verifies **AC-2**, **AC-3**, and **AC-6**.
- Duplicate URL: submit the same normalized URL for the same owner, verifies **AC-5**.
- Concurrent duplicate: race two same owner requests and verify one succeeds and one receives `409`, verifies **AC-5**.
- Permission: submit without authentication or without `Projects` create permission and verify `401` or `403` with no insert, verifies **AC-7**.
- Persistence failure: force a repository failure and verify rollback and explicit error with no project insert and no vault write, verifies **AC-8**.
- Logging: verify success and rejection logs do not contain passwords or sensitive URL components, verifies **AC-9**.
- Accessibility: operate the create form with keyboard navigation and verify labels, required state, and visible errors, verifies **AC-10**.

## Build plan

The Tracer Bullet approach starts with one thin end to end path, then strengthens the shared validation and persistence boundaries.

1. [x] Add the required target URL field and accessible error surface to the existing create modal, then pass the value through the existing API wrapper, satisfies **AC-1**, **AC-4**, and **AC-10**.
2. [x] Add the `Projects` module and `create` action to the RBAC seed, grant it to Project Manager, Admin, and Superadmin, then wire the create route to `get_current_user` and `has_permission("Projects", "create")`. Pass `user.id` as both owner and creator, validate the supported framework and target URL, normalize the URL, and translate validation failures into the existing structured `422` detail shape, satisfies **AC-2**, **AC-3**, **AC-6**, and **AC-7**.
3. [ ] Extend the Project domain, response schema, and both repository implementations to carry owner and creator identity and the readiness object. Add owner scoped duplicate checks and structured `409` handling, with rollback and session cleanup for PostgreSQL failures and lock plus atomic replacement for JSON failures, satisfies **AC-4**, **AC-5**, **AC-7**, and **AC-8**. The application code is present, but the PostgreSQL migration is not applied or verified.
4. [ ] Reconcile the PostgreSQL schema with the application contract by retaining nullable legacy fields, inventorying existing owner and URL conflicts, and adding a partial unique index on `(owner_id, target_url)` for non deleted rows where both values are non null. Store the normalized URL directly in `target_url`; do not add a global uniqueness constraint or a second canonical URL column, satisfies **AC-2**, **AC-5**, and **AC-8**. Blocked by the missing Alembic revision `fef1658bde83`.
5. [x] Remove the demo URL fallback from project settings and ensure creation and settings use the project value as the only source, satisfies **AC-3**, **AC-4**, and **AC-9**.
6. [ ] Update focused API, repository, configuration, and frontend tests for the happy path, validation, conflict, ownership, rollback, and accessibility scenarios, satisfies **AC-1** through **AC-10**. Focused API and repository coverage is present, while the remaining rollback, logging, and browser accessibility coverage is still open.

## Consequences

**Positive**:

- New projects cannot reach persistence without the target URL required by testing.
- The reported PostgreSQL null insertion is prevented at the request boundary.
- JSON and PostgreSQL repositories share the same project creation contract.
- Concurrent duplicate creation is deterministic and safe.
- The existing vault and project response protections remain in place.

**Negative / tradeoffs**:

- Users must provide a target URL before a project can be created.
- A database migration is needed for owner and creator persistence, owner scoped uniqueness, and environments whose active schema does not match the repository migration history.
- Legacy incomplete projects remain possible until separately repaired, so readiness checks must continue to protect generation and execution.

**Neutral**:

- Project settings remains the place to edit the URL and credentials after creation. Credentials are intentionally not part of project creation.
- No new project state or infrastructure component is introduced.

## Follow-up

- [ ] Reconcile the deployed PostgreSQL schema with the migration history. The reported database has `target_url` as `NOT NULL`, while the repository model and legacy handling expect nullable historical rows.
- [ ] Inventory existing PostgreSQL rows with null owners, duplicate `(owner_id, normalized target_url)` values, malformed URLs, and deleted projects before adding the partial unique index. The migration must report conflicts and stop rather than silently changing ownership or URLs.

## Rationale

See [rationale.md](rationale.md) for the decision context and options considered.

## Migration plan

**Strategy**: strangler

**Phases**:

1. Deploy the RBAC seed, response schema, and end to end form, API, service, and repository path while preserving legacy nullable records and keeping existing settings available for repair.
2. Run a preflight inventory for owner and normalized URL conflicts. Stop the migration if any active duplicate would violate owner scoped uniqueness.
3. Apply the PostgreSQL schema migration for owner and creator fields and the partial unique index on `(owner_id, target_url)` for non deleted rows with non null values, then run focused tests against both repository providers.
4. Remove the legacy demo URL fallback and verify that generation and execution continue to use explicit project configuration.

**Rollback**: Revert the application changes while retaining existing legacy rows. If the uniqueness migration causes a deployment issue, remove only the new index after resolving the deployment error, without deleting project records or vault credentials. Because creation never writes the vault, rollback has no cross store cleanup step.

**Risks**: Existing duplicate or null owner data can block the uniqueness migration. The migration must inventory and report conflicts rather than silently changing project ownership or URLs. JSON repository locking is process local, so deployments that allow multiple writers to the same JSON file must continue using a single writer or move to PostgreSQL.
