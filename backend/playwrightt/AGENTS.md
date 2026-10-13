# Playwright execution workspace

## Overview

This workspace is a Next.js application and a local Playwright test runner used to execute generated test scripts and present execution artifacts. The Python backend writes a temporary test specification and configuration here, then invokes Playwright from this workspace.

## Key files

| File | Owns |
|---|---|
| `backend/playwrightt/package.json` | Next.js, React, TypeScript, and Playwright dependencies and scripts |
| `backend/playwrightt/app/` | Next.js pages and API routes for execution and artifacts |
| `backend/playwrightt/lib/execution-engine.ts` | Browser execution coordination |
| `backend/playwrightt/lib/execution-queue.ts` | Execution queue behavior |
| `backend/playwrightt/components/` | Execution results and artifact presentation |
| `backend/services/playwright_runner.py` | Python side that creates and invokes generated Playwright tests |

## Commands

```bash
# Install dependencies
pnpm install

# Start the workspace development server
pnpm dev

# Build the workspace
pnpm build

# Run the workspace linter
pnpm lint
```

## Conventions

* Keep generated test files, reports, videos, traces, and screenshots scoped to their execution ID.
* Preserve the artifact routes because the backend and frontend use them to display reports and browser evidence.
* Keep TypeScript types explicit at API and execution boundaries.
* Treat the Python runner as the owner of subprocess execution. Do not add a second direct subprocess path in the Next.js UI.
* Keep project selection and project target configuration in the backend API. The runner should receive the selected project context rather than infer a demo application.
* The execution queue is process local and allows one active execution at a time. Keep execution metadata scoped by execution ID.

## Gotchas

* `next.config.mjs` currently allows TypeScript build errors. Do not treat a successful Next build as a complete type safety check.
* The workspace uses a `pnpm-lock.yaml`. Prefer pnpm for dependency operations.
* Generated artifacts under `public/artifacts/` can be large and should not be edited as source files.
* The execution API returns before the browser work finishes. Queue metadata is written to `public/artifacts/active_executions.json` for local recovery, and the active index is pruned.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
