# Rationale: Project creation with target URL

## Context

The create form omitted `target_url`, while the backend and database expected project configuration to carry it. The settings screen was only available after creation and supplied a legacy demo URL for incomplete projects. The application supports both JSON and PostgreSQL repositories, authenticated users, RBAC, and a separate file based credential vault.

## Options considered

### Extend the existing creation flow

Add the URL field to the current form, reuse the shared configuration validator, and extend the existing endpoint and repositories.

This was selected because it fixes the failure with the smallest compatible change and keeps one end to end path.

### Add a separate setup flow

Keep creation incomplete and require another setup step. This was rejected because it preserves the current database failure and adds an unnecessary user journey.

### Replace project creation

Rewrite the modal, API wrapper, and route. This was rejected because the problem is a missing field and contract, not a need for a new workflow.

## Rationale

The root cause is at the frontend request boundary. The selected design adds the required value there, validates it authoritatively in the backend, normalizes it before persistence, and protects owner scoped uniqueness in each repository.

Readiness depends only on valid target URL and framework, so credentials remain optional and can continue through Project Settings. Creation never writes the separate vault, avoiding a false promise of cross store transactionality.
