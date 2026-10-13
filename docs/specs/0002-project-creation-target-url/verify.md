# Verify: Project creation with target URL, spec 0002

## API and persistence

- [ ] Submit a valid project with an absolute HTTP URL and supported framework, then confirm `201`, the normalized `target_url`, project identity, Jira key, and `readiness.is_ready: true`. AC-1, AC-2, AC-3, AC-4.
- [ ] Submit a valid project with an HTTPS URL containing a default port and root slash, then confirm the response stores the normalized URL without the default port or root slash. AC-3.
- [ ] Submit a missing URL, relative URL, unsupported scheme, URL with userinfo, or URL with a fragment, then confirm `422` with `ProjectConfigurationError.as_detail()` fields and no project created. AC-2, AC-3, AC-6.
- [ ] Submit `target_username`, `target_password`, `owner_id`, or another undeclared field, then confirm `422` with `invalid_fields.<field>: field_not_permitted` and no vault write. AC-8.
- [ ] Submit the same normalized URL twice for one owner, then confirm one `201` and one exact `409 PROJECT_URL_CONFLICT` response with `conflicting_project_id`. AC-5.
- [ ] Submit the same normalized URL for two different owners through the JSON repository, then confirm both projects are accepted. AC-5.
- [ ] Submit the same normalized URL concurrently for one owner through the JSON and PostgreSQL providers, then confirm one success and one conflict with no partial project. AC-5.
- [ ] Create a project and read it through the list and detail paths, then confirm the readiness object is present and credentials are not returned. AC-4.
- [ ] Verify readiness for a project with a valid URL and framework but no credentials, then confirm `is_ready` is true. AC-4.
- [ ] Force a PostgreSQL persistence error, then confirm rollback and session cleanup leave no project record. AC-8.

## Authorization and logging

- [ ] Create with an authenticated user who has `Projects:create`, then confirm both `owner_id` and `created_by` equal the authenticated user id. AC-7.
- [ ] Create without authentication, then confirm `401` and no repository write. AC-7.
- [ ] Create without the `Projects:create` permission, then confirm `403` and no repository write. AC-7.
- [ ] Review success, validation, and conflict logs, then confirm they contain project context without passwords, URL userinfo, or other sensitive URL parts. AC-9.

## Frontend

- [ ] Open the create project modal and confirm the target URL input has a label, required state, URL type, keyboard access, and visible error output. AC-1, AC-10.
- [ ] Submit the form with a valid URL, then confirm the API payload includes `target_url` and the created project is selected. AC-1, AC-4.
- [ ] Submit an invalid or duplicate URL, then confirm the modal stays open, entered values remain, and the target URL field shows the backend error. AC-6, AC-10.
- [ ] Open Project Settings for a legacy project without a URL, then confirm no demo Adactin URL is inserted. AC-3, AC-4.

## Value sourcing checks

- [ ] Confirm the create form values come from the named form fields, including target URL and Jira key. AC-1, AC-4.
- [ ] Confirm the create request sends the target URL from the form as `target_url`. AC-4.
- [ ] Confirm owner and creator values come from `get_current_user().id`, never from request data. AC-7.
- [ ] Confirm normalized URL values come from the shared project configuration normalizer before duplicate checks and persistence. AC-2, AC-3, AC-5.
- [ ] Confirm duplicate detection compares the authenticated owner and normalized stored URL. AC-5.
- [ ] Confirm the response readiness comes from the stored project fields and owner scoped project list. AC-4.
- [ ] Confirm credential storage is not invoked during creation and remains a Project Settings operation. AC-8.
- [ ] Confirm validation and conflict messages come from the structured backend error contracts and render on the relevant form field. AC-6, AC-10.
