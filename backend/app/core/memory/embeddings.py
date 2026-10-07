"""Pluggable dense embeddings for hybrid RAG (optional, fail-soft).

Priority:
1. ``sentence-transformers`` (all-MiniLM-L6-v2 default) when installed —
   real dense vectors, cached per model in the storage dir.
2. TF-IDF hashed vectors (stdlib + math only) — always available, better
   than pure keyword overlap for paraphrase-ish queries.

Both expose ``embed(texts) -> List[List[float]]`` with L2-normalized rows
so cosine similarity is a dot product. Callers must treat this module as
optional: ``get_embedder()`` never raises, and ``cosine_top_k`` is pure
python (fast enough for the typical <5k chunks/deal corpus).
"""

import hashlib
import math
import os
from typing import Any, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

_ST_MODEL_NAME = os.environ.get("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")
_ST_AVAILABLE: Optional[bool] = None


def sentence_transformers_available() -> bool:
    global _ST_AVAILABLE
    if _ST_AVAILABLE is not None:
        return _ST_AVAILABLE
    try:
        import importlib.util as _ilu

        _ST_AVAILABLE = _ilu.find_spec("sentence_transformers") is not None
    except Exception:
        _ST_AVAILABLE = False
    return _ST_AVAILABLE


class HashTfIdfEmbedder:
    """Deterministic hashed TF-IDF vectors (dim=512). No dependencies."""

    DIM = 512

    def __init__(self) -> None:
        self._idf: dict = {}
        self._fitted = False

    @staticmethod
    def _tokens(text: str) -> List[str]:
        return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]

    @staticmethod
    def _bucket(token: str) -> int:
        return int(hashlib.md5(token.encode()).hexdigest(), 16) % HashTfIdfEmbedder.DIM

    def fit(self, corpus: List[str]) -> None:
        df: dict = {}
        for doc in corpus:
            for tok in set(self._tokens(doc)):
                df[tok] = df.get(tok, 0) + 1
        n = max(1, len(corpus))
        self._idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self._fitted = True

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._fitted:
            self.fit(texts)
        out = []
        for text in texts:
            vec = [0.0] * self.DIM
            toks = self._tokens(text)
            if not toks:
                out.append(vec)
                continue
            tf: dict = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            total = len(toks)
            for tok, c in tf.items():
                idf = self._idf.get(tok, 1.0)
                vec[self._bucket(tok)] += (c / total) * idf
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SentenceTransformerEmbedder:
    """Thin wrapper; model loads lazily on first embed."""

    def __init__(self, model_name: str = _ST_MODEL_NAME) -> None:
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            logger.info("st_embedder_loaded", model=self.model_name)
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]


def get_embedder() -> Any:
    """Return best available embedder; never raises."""
    if sentence_transformers_available():
        try:
            return SentenceTransformerEmbedder()
        except Exception as e:
            logger.warning("st_embedder_init_failed", error=str(e))
    return HashTfIdfEmbedder()


def cosine_top_k(
    query_vec: List[float], doc_vecs: List[List[float]], top_k: int
) -> List[Tuple[int, float]]:
    """Dot-product top-k over L2-normalized rows. Returns (idx, score)."""
    scored = []
    for i, dv in enumerate(doc_vecs):
        s = sum(q * d for q, d in zip(query_vec, dv))
        if s > 0:
            scored.append((i, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
