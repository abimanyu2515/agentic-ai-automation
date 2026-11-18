"""RAGFlowRetriever: the only RAGFlow HTTP client (spec 0006).

Written against the RAGFlow HTTP API v1. RAGFlow answers most errors with
HTTP 200 and a non zero `code`, so both are checked. Connection errors,
timeouts, and 5xx responses raise a transient RetrieverError.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from backend.services.retrieval.base import (
    PARSE_DONE,
    PARSE_FAILED,
    PARSE_RUNNING,
    Chunk,
    IndexResult,
    ParseStatus,
    RemoteNotFound,
    RetrieverError,
    dataset_name,
    require_dataset,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
UPLOAD_TIMEOUT_SECONDS = 120

# RAGFlow v1.0 requires a parse type on dataset create (1 = BuiltIn parser,
# 2 = ingestion pipeline) and rejects the older `chunk_method` field. A BuiltIn
# parse type needs a `parser_id`; "general" is the default chunker.
PARSE_TYPE_BUILTIN = 1
DEFAULT_PARSER_ID = "general"


# "Already gone" answers, checked against the installed RAGFlow on 2026-11-18
# (spec 0007, AC-13). Each entry is (body code, lowercase message fragment).
# A 404 status or body code 404 ("Resource not found") always means gone.
DATASET_DELETE_MISS = (
    (403, "permission denied"),  # this version: an unknown or foreign dataset
    (102, "lacks permission for dataset"),  # older versions
    (102, "not found"),
)
DOCUMENT_LOOKUP_MISS = (
    (102, "don't own the document"),
    (102, "not found"),
)
# Codes a document delete answers when a document is unknown; confirmed by a lookup.
DOCUMENT_DELETE_MISS_CODES = {102, 404}


def _is_missing_answer(exc: RetrieverError, known: tuple) -> bool:
    if exc.transient:
        return False
    if exc.status_code == 404 or exc.code == 404:
        return True
    message = str(exc).lower()
    return any(exc.code == code and fragment in message for code, fragment in known)


class RAGFlowRetriever:
    def __init__(self, api_base: str, api_key: str | None, session: requests.Session | None = None):
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.http = session or requests.Session()

    def is_active(self) -> bool:
        return bool(self.api_base and self.api_key)

    # ------------------------------------------------------------ http

    def _call(self, method: str, path: str, *, timeout: float = TIMEOUT_SECONDS, **kwargs):
        if not self.is_active():
            raise RetrieverError("RAGFlow is not configured (RAGFLOW_API_BASE and RAGFLOW_API_KEY)")
        url = f"{self.api_base}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            response = self.http.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise RetrieverError(f"RAGFlow unreachable: {exc.__class__.__name__}", transient=True) from exc
        except requests.RequestException as exc:
            raise RetrieverError(f"RAGFlow request failed: {exc.__class__.__name__}") from exc
        status = response.status_code
        if status >= 500:
            raise RetrieverError(f"RAGFlow returned {status}", transient=True, status_code=status)
        try:
            body = response.json()
        except ValueError:
            raise RetrieverError(f"RAGFlow returned {status} without JSON", status_code=status)
        code = body.get("code", 0) if isinstance(body, dict) else 0
        if status >= 400 or code != 0:
            message = (body.get("message") if isinstance(body, dict) else None) or f"HTTP {status}"
            raise RetrieverError(f"RAGFlow: {message}", status_code=status, code=code if isinstance(code, int) else None)
        return body.get("data")

    # ------------------------------------------------------------ contract

    def ensure_dataset(self, project) -> str:
        if getattr(project, "rag_dataset_id", None):
            return project.rag_dataset_id
        data = self._call(
            "POST",
            "/api/v1/datasets",
            json={
                "name": dataset_name(project),
                "parse_type": PARSE_TYPE_BUILTIN,
                "parser_id": DEFAULT_PARSER_ID,
            },
        )
        dataset_id = (data or {}).get("id")
        if not dataset_id:
            raise RetrieverError("RAGFlow created a dataset without an id")
        logger.info("RAGFlow dataset created: project=%s dataset=%s", project.id, dataset_id)
        return dataset_id

    def upload_file(self, project, path: Path, name: str) -> IndexResult:
        dataset_id = require_dataset(project)
        with open(path, "rb") as handle:
            data = self._call(
                "POST",
                f"/api/v1/datasets/{dataset_id}/documents",
                files={"file": (name, handle)},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        return self._start_parse(dataset_id, data)

    def upload_text(self, project, text: str, name: str) -> IndexResult:
        dataset_id = require_dataset(project)
        data = self._call(
            "POST",
            f"/api/v1/datasets/{dataset_id}/documents",
            files={"file": (name, text.encode("utf-8"), "text/plain")},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        return self._start_parse(dataset_id, data)

    def _start_parse(self, dataset_id: str, upload_data) -> IndexResult:
        items = upload_data if isinstance(upload_data, list) else [upload_data or {}]
        remote_id = (items[0] or {}).get("id") if items else None
        if not remote_id:
            raise RetrieverError("RAGFlow upload returned no document id")
        self._call("POST", f"/api/v1/datasets/{dataset_id}/chunks", json={"document_ids": [remote_id]})
        return IndexResult(remote_doc_id=remote_id)

    def parse_status(self, project, remote_id: str) -> ParseStatus:
        dataset_id = require_dataset(project)
        data = self._call("GET", f"/api/v1/datasets/{dataset_id}/documents", params={"id": remote_id}) or {}
        if isinstance(data, dict):
            docs = data.get("docs") or data.get("documents") or []
        else:
            docs = data or []
        doc = next((d for d in docs if d.get("id") == remote_id), None)
        if doc is None:
            return ParseStatus(PARSE_FAILED, "document not found in RAGFlow")
        # RAGFlow v1.0 reports `ingestion_status` (CREATED, SCHEDULED, RUNNING,
        # STOPPING, COMPLETED, FAILED, STOPPED) and no `run`; older builds send `run`.
        ingestion = str(doc.get("ingestion_status") or "").upper()
        if ingestion == "COMPLETED":
            return ParseStatus(PARSE_DONE)
        if ingestion in {"FAILED", "STOPPED"}:
            event = doc.get("latest_ingestion_event") or {}
            message = (event.get("message") if isinstance(event, dict) else None) or doc.get("progress_msg")
            return ParseStatus(PARSE_FAILED, message or f"parsing {ingestion.lower()}")
        if ingestion:
            return ParseStatus(PARSE_RUNNING)
        run = str(doc.get("run") or "").upper()
        if run in {"DONE", "3"}:
            return ParseStatus(PARSE_DONE)
        if run in {"FAIL", "CANCEL", "4", "2"}:
            return ParseStatus(PARSE_FAILED, doc.get("progress_msg") or f"parsing {run.lower()}")
        return ParseStatus(PARSE_RUNNING)

    def delete(self, project, remote_ids: list[str]) -> None:
        ids = [r for r in remote_ids if r]
        if not ids:
            return
        dataset_id = require_dataset(project)
        try:
            data = self._call("DELETE", f"/api/v1/datasets/{dataset_id}/documents", json={"ids": ids})
        except RetrieverError as exc:
            # The installed RAGFlow answers code 102 for an unknown document and
            # code 404 when the dataset is gone. Confirm with a lookup before
            # treating it as gone (spec 0007, AC-13); otherwise keep the error.
            if (exc.code in DOCUMENT_DELETE_MISS_CODES or exc.status_code == 404) and self._documents_absent(dataset_id, ids):
                raise RemoteNotFound(f"RAGFlow documents already gone: {exc}", status_code=exc.status_code, code=exc.code) from exc
            raise
        deleted = data.get("deleted") if isinstance(data, dict) else None
        if deleted is None or deleted >= len(ids):
            return
        # RAGFlow answers code 0 with a short `deleted` count, and removes
        # nothing, while a document is still parsing. Stop the parse so the
        # next try can delete it (AC-5).
        try:
            self._call("DELETE", f"/api/v1/datasets/{dataset_id}/chunks", json={"document_ids": ids})
        except RetrieverError as exc:
            logger.warning("RAGFlow could not stop parsing before delete: dataset=%s ids=%s error=%s", dataset_id, ids, exc)
        # A short count can also mean the documents were already gone (AC-13).
        if self._documents_absent(dataset_id, ids):
            raise RemoteNotFound(f"RAGFlow deleted {deleted} of {len(ids)} documents, the rest are already gone")
        raise RetrieverError(
            f"RAGFlow deleted {deleted} of {len(ids)} documents, still parsing",
            transient=True,
        )

    def _documents_absent(self, dataset_id: str, ids: list[str]) -> bool:
        """True only when a lookup shows that none of the documents exist.
        Any other answer (a document still listed, a transient error, an
        unrecognised error) is False, so a pending delete is kept, not dropped."""
        for remote_id in ids:
            try:
                data = self._call("GET", f"/api/v1/datasets/{dataset_id}/documents", params={"id": remote_id}) or {}
            except RetrieverError as exc:
                if _is_missing_answer(exc, DOCUMENT_LOOKUP_MISS):
                    continue
                return False
            docs = (data.get("docs") or data.get("documents") or []) if isinstance(data, dict) else (data or [])
            if any(d.get("id") == remote_id for d in docs if isinstance(d, dict)):
                return False
        return True

    def delete_dataset(self, project) -> None:
        dataset_id = require_dataset(project)
        try:
            self._call("DELETE", "/api/v1/datasets", json={"ids": [dataset_id]})
        except RetrieverError as exc:
            if _is_missing_answer(exc, DATASET_DELETE_MISS):
                raise RemoteNotFound(f"RAGFlow dataset already gone: {exc}", status_code=exc.status_code, code=exc.code) from exc
            raise

    def search(self, project, query: str, top_k: int) -> list[Chunk]:
        dataset_id = require_dataset(project)
        data = self._call(
            "POST",
            "/api/v1/retrieval",
            json={"question": query, "dataset_ids": [dataset_id], "top_k": top_k},
        )
        raw_chunks = data.get("chunks", []) if isinstance(data, dict) else (data or [])
        chunks = []
        for raw in raw_chunks:
            text = raw.get("content") or raw.get("content_with_weight") or ""
            remote_doc_id = raw.get("document_id") or raw.get("doc_id") or ""
            if not text or not remote_doc_id:
                continue
            chunks.append(
                Chunk(
                    chunk_id=str(raw.get("id") or f"{remote_doc_id}:{len(chunks)}"),
                    text=text,
                    score=float(raw.get("similarity") or 0.0),
                    remote_doc_id=remote_doc_id,
                    title=raw.get("document_keyword") or raw.get("docnm_kwd") or "",
                )
            )
        return chunks[:top_k] if top_k else chunks
