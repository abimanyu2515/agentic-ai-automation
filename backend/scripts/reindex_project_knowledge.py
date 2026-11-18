"""Index every current source of a project into its knowledge base (spec 0006, AC-16).

It queues, through the same indexer the app uses:
- approved test cases, Jira linked stories, failed executions, and
  object repository pages (an unchanged source is skipped by its hash)
- every existing knowledge entry, so an entry whose source is gone is removed
- the project's `pending` documents

Then it runs the queue on this thread until it is empty, including parse
polling and retries, and prints the counts.

Run with:
    python -m backend.scripts.reindex_project_knowledge --project <project_id>
    python -m backend.scripts.reindex_project_knowledge --all
    python -m backend.scripts.reindex_project_knowledge --pending-deletes [--run]

Run it while the server is stopped, or at a quiet time: it is a second
process writing the same store (spec 0006 supports one server process).

`--pending-deletes` (spec 0007, AC-12) lists every RAGFlow delete still
outstanding, deleted projects included. On PostgreSQL it also makes one
attempt at each. On the JSON store it only lists them unless you pass
`--run`, and you should stop the server first, because both would rewrite
the same file. It exits 1 while any row remains.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from uuid import UUID

from backend.repository.project_repository import JSONProjectRepository, get_project_repository
from backend.services.knowledge_indexer import DELETE_FINISHED, KnowledgeIndexer
from backend.services.knowledge_sources import list_project_sources
from backend.services.retrieval import get_retriever

logger = logging.getLogger("backend.scripts.reindex_project_knowledge")


def queue_project(indexer: KnowledgeIndexer, repo, project) -> int:
    """Queue every source, entry, and pending document of one project. Returns the job count."""
    jobs = 0
    sources = list_project_sources(repo, project)
    for source_type, ref in sources:
        indexer.enqueue_source(project.id, source_type, ref)
        jobs += 1
    current = set(sources)
    for entry in repo.list_knowledge_entries(project.id):
        if (entry.source_type, entry.source_ref) not in current:
            # The worker reads the source again and removes the entry when it is gone.
            indexer.enqueue_source(project.id, entry.source_type, entry.source_ref)
            jobs += 1
    for document in repo.get_documents(project.id):
        if document.embedding_status == "pending":
            indexer.enqueue_document(project.id, document.id)
            jobs += 1
    return jobs


def reindex(project_ids: list[UUID] | None, indexer: KnowledgeIndexer | None = None, repo=None, out=print) -> dict:
    """Reindex the given projects, or every project when project_ids is None.
    Returns the outcome counts: indexed, skipped (unchanged), failed, removed."""
    repo = repo or get_project_repository()
    indexer = indexer or KnowledgeIndexer(repository=repo)
    if project_ids is None:
        projects = repo.list_projects()
    else:
        projects = []
        for project_id in project_ids:
            project = repo.get_project(project_id)
            if project is None:
                raise SystemExit(f"Project {project_id} not found")
            projects.append(project)

    for project in projects:
        jobs = queue_project(indexer, repo, project)
        out(f"Project {project.id} ({project.name}): {jobs} item(s) queued")

    indexer.drain()
    counts = {name: indexer.stats.get(name, 0) for name in ("indexed", "skipped", "failed", "removed")}
    out(
        f"Indexed {counts['indexed']}, skipped (unchanged) {counts['skipped']}, "
        f"failed {counts['failed']}, removed {counts['removed']}"
    )
    return counts


def format_age(seconds: float) -> str:
    """The two largest units: `3d 4h`, `2h 5m`, `5m 3s`, or `40s`."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_pending_delete(row, now: datetime) -> str:
    error = " ".join((row.last_error or "-").split())[:120]
    return (
        f"project={row.project_id} kind={row.kind} dataset={row.dataset_id} "
        f"remote={row.remote_doc_id or '-'} reason={row.reason} attempts={row.attempts} "
        f"age={format_age((now - row.created_at).total_seconds())} last_error={error}"
    )


def pending_deletes(run: bool = False, indexer: KnowledgeIndexer | None = None, repo=None, out=print, now=None) -> int:
    """List every pending RAGFlow delete, then make one attempt at each when
    allowed (spec 0007, AC-12). Returns the exit code: 1 while any row remains."""
    repo = repo or get_project_repository()
    indexer = indexer or KnowledgeIndexer(repository=repo)
    now = now or datetime.now(timezone.utc)
    rows = repo.list_remote_deletes(None)
    for row in rows:
        out(format_pending_delete(row, now))
    if not rows:
        out("deleted 0, still pending 0")
        return 0

    if not indexer.retriever.is_active():
        out("The knowledge base is disabled, so nothing was sent to RAGFlow.")
    elif isinstance(repo, JSONProjectRepository) and not run:
        out("Listed only. On the JSON store, stop the server, then pass --run to delete these.")
    else:
        deleted = 0
        for row in rows:
            try:
                if indexer.run_delete_row_once(row) in DELETE_FINISHED:
                    deleted += 1
            except Exception:
                logger.exception("Pending delete failed unexpectedly: row=%s", row.id)
        remaining = len(repo.list_remote_deletes(None))
        out(f"deleted {deleted}, still pending {remaining}")
        return 1 if remaining else 0
    out(f"deleted 0, still pending {len(rows)}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index a project's sources into its knowledge base (spec 0006).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", type=UUID, help="the project ID to reindex")
    target.add_argument("--all", action="store_true", help="reindex every project")
    target.add_argument(
        "--pending-deletes", action="store_true",
        help="list pending RAGFlow deletes, and run them on PostgreSQL (spec 0007)",
    )
    parser.add_argument(
        "--run", action="store_true",
        help="with --pending-deletes on the JSON store: also run the deletes. Stop the server first.",
    )
    args = parser.parse_args(argv)
    if args.run and not args.pending_deletes:
        parser.error("--run only works with --pending-deletes")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.pending_deletes:
        return pending_deletes(run=args.run)
    if not get_retriever().is_active():
        print("The knowledge base is disabled (set RAG_ENABLED=true and rag.vector_db_provider=ragflow).", file=sys.stderr)
        return 1
    counts = reindex(None if args.all else [args.project])
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
