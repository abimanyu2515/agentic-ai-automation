# Frontend

## Overview

The frontend is a vanilla HTML, CSS, and JavaScript single page application served by the FastAPI backend. It manages authentication, project selection, requirement and test case workflows, execution streams, dashboards, settings, and artifact links through the API client.

## Key files

| File | Owns |
|---|---|
| `frontend/index.html` | Page layout, navigation, forms, modals, and view containers |
| `frontend/js/app.js` | Main application state, event handlers, project selection, rendering, and workflow actions |
| `frontend/js/api.js` | Authenticated API requests and endpoint wrappers |
| `frontend/js/components.js` | Shared HTML rendering helpers and reusable tables |
| `frontend/css/styles.css` | Global layout, themes, forms, tables, and dashboard styling |

## Conventions

* Keep API access in `frontend/js/api.js` and use the shared `request` helper for authentication and error handling.
* Scope project data requests with the selected project ID.
* Keep escaped user supplied values rendered with `escapeHTML`.
* Store the active project ID in `localStorage` only as a selection hint. Reload the project from the backend before using it.
* Preserve the existing single page view and modal patterns rather than introducing a second frontend framework.
* Treat the backend project target URL as the source of truth. Do not add demo application URLs as frontend defaults.

## Gotchas

* The frontend is served by FastAPI and is not the primary source for the Next.js execution workspace.
* Execution status is streamed and rendered asynchronously. Do not assume a request response contains the final execution result.
* Project settings include credentials, but passwords must remain masked and must not be rendered into normal API responses.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
