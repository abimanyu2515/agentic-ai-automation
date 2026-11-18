"""Retrieval layer (spec 0006): one Retriever interface in front of RAGFlow."""

from backend.services.retrieval.base import (
    PARSE_DONE,
    PARSE_FAILED,
    PARSE_RUNNING,
    Chunk,
    IndexResult,
    NoDatasetError,
    ParseStatus,
    RemoteNotFound,
    Retriever,
    RetrieverError,
)
from backend.services.retrieval.factory import get_retriever, rag_enabled, set_retriever

__all__ = [
    "PARSE_DONE",
    "PARSE_FAILED",
    "PARSE_RUNNING",
    "Chunk",
    "IndexResult",
    "NoDatasetError",
    "ParseStatus",
    "RemoteNotFound",
    "Retriever",
    "RetrieverError",
    "get_retriever",
    "rag_enabled",
    "set_retriever",
]
