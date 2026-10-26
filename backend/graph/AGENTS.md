# Workflow graph

## Overview

The graph builds the LangGraph workflow that connects agents and routes work based on operation and workflow state. Keep orchestration order and conditional routing in this area.

## Key files

| File | Owns |
|---|---|
| `backend/graph/workflow.py` | Graph construction, node registration, edges, and conditional routing |

## Conventions

* Keep agent and runner dependencies injectable so workflow tests do not need live LLM or browser calls.
* Use `WorkflowState` as the workflow contract and route between agents through the graph.
* Keep business behavior in services, agents, repositories, and domain models rather than graph routing code.

## Gotchas

* Agent source files and registered graph nodes are not the same count. Check node registration and edges before changing workflow order.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
