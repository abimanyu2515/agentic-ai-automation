"""get_retriever(): the process wide retriever (spec 0006).

RAGFlowRetriever when RAG is enabled with the ragflow provider (the same check
the old RAGFlowService.is_active made), otherwise NullRetriever. Tests swap it
with set_retriever(InMemoryRetriever()).
"""

from __future__ import annotations

import threading

from backend.config.settings import get_settings
from backend.services.retrieval.base import Retriever

_retriever: Retriever | None = None
_lock = threading.Lock()


def rag_enabled(settings=None) -> bool:
    rag = (settings or get_settings()).rag
    return bool(rag.enabled) and rag.vector_db_provider == "ragflow"


def build_retriever(settings=None) -> Retriever:
    settings = settings or get_settings()
    if rag_enabled(settings):
        from backend.services.retrieval.ragflow import RAGFlowRetriever

        return RAGFlowRetriever(settings.rag.ragflow_api_base, settings.rag.ragflow_api_key)
    from backend.services.retrieval.fake import NullRetriever

    return NullRetriever()


def get_retriever() -> Retriever:
    global _retriever
    with _lock:
        if _retriever is None:
            _retriever = build_retriever()
        return _retriever


def set_retriever(retriever: Retriever | None) -> None:
    """Swap the process wide retriever; None rebuilds it from settings on next use."""
    global _retriever
    with _lock:
        _retriever = retriever
