"""
Lazy-loading singleton wrapper for sentence-transformers.

Model: all-MiniLM-L6-v2 — 384-dimensional embeddings, ~90 MB, CPU-friendly.
Auto-downloads on first encode() call (not on import).
Thread-safe with double-checked locking.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np


# Module-level singleton instance
_model: Optional["EmbeddingModel"] = None
_lock = threading.Lock()


class EmbeddingModel:
    """
    Thread-safe lazy-loading wrapper around SentenceTransformer.

    Usage:
        from embeddings import encode_texts
        vectors = encode_texts(["hello world", "financial report"])
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Optional["_SentenceTransformer"] = None
        self._load_lock = threading.Lock()

    # ── Lazy loading ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Import and initialise the model (once, thread-safe)."""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            # Deferred import — nothing happens until this is called
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)

    # ── Public API ─────────────────────────────────────────────────────────────

    def encode(self, texts: list[str]) -> np.ndarray:
        """
        Encode a list of texts into a numpy array of embedding vectors.
        Returns float32 ndarray of shape (len(texts), 384).
        """
        self._load()
        return self._model.encode(  # type: ignore[union-attr]
            texts,
            show_progress_bar=len(texts) > 20,
            convert_to_numpy=True,
        )

    @property
    def dimension(self) -> int:
        """Embedding dimension (384 for all-MiniLM-L6-v2)."""
        self._load()
        return self._model.get_sentence_embedding_dimension()  # type: ignore[union-attr]


# ── Module-level singleton ───────────────────────────────────────────────────

def get_embedding_model() -> EmbeddingModel:
    """Return the module-level singleton EmbeddingModel instance."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _model = EmbeddingModel()
    return _model


def encode_texts(texts: list[str]) -> np.ndarray:
    """
    Convenience wrapper: encode texts using the singleton model.
    Returns float32 numpy array — directly usable by ChromaDB.
    """
    return get_embedding_model().encode(texts)
