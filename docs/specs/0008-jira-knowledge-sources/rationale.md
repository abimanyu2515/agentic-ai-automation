# 0008. Rationale: Jira knowledge sources

## Context

Spec 0006 (Agentic RAG) indexes six kinds of project knowledge. Two of them, Jira stories and Jira defects, can only be checked against a live Jira site, because the app keeps only the issue keys locally and reads everything else from Jira.

On 2026-11-18, `/check verify agentic RAG` passed every step it could run. The 13 open steps all needed Jira, and the configured Jira site answered "Site temporarily unavailable" to every call, including read only ones. The engineer chose not to wait for Jira.

The forces: the rest of Agentic RAG is built and verified and should be able to close; the Jira sources are built and unit tested with a fake Jira, but no one has seen them work against a real one; and a verified spec should not claim criteria that were never checked.

## Options considered

### Option 1: A separate spec and scope feature (chosen)

Move the Jira criteria and their verify steps into a new spec 0008 with its own scope feature. Spec 0006 keeps the design text, marked as governed by 0008.

**Pros**:
- Agentic RAG closes honestly, and the Jira work keeps an owner and a verify box.

**Cons**:
- One more spec and scope row; the design text lives in a different spec from its criteria.

### Option 2: A deferred note inside spec 0006

Mark the two criteria in 0006 as "verification deferred" and add a line to the scope Deferred list.

**Pros**:
- Lightest change.

**Cons**:
- Spec 0006 would become `Accepted` while still claiming criteria nobody checked.

### Option 3: Drop the Jira sources

Remove `jira_story` and `defect` from the spec as unwanted.

**Pros**:
- Simplest spec.

**Cons**:
- The built code would become unspecced, and the knowledge base would lose two sources the engineer still wants.

## Rationale

The engineer wants Agentic RAG verified now and the Jira sources kept. Option 1 is the only one that does both without a spec claiming unverified behavior: 0006 can reach `Accepted` on what was actually checked, and 0008 carries exactly the Jira dependent criteria until a working Jira is available. The original reasoning for how the Jira sources work (bug keys as links, reading from Jira at job time, mock mode skipping) is unchanged and stays in [spec 0006's rationale](../0006-agentic-rag/rationale.md).
