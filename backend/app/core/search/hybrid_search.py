"""Hybrid Search (Tree + BM25 + Dense) for DealForge.

Upgrade notes (RAG v2):
- Three-way Reciprocal Rank Fusion: PageIndex tree score (keyword/reasoning)
  + BM25 lexical + dense cosine (sentence-transformers when installed,
  otherwise hashed TF-IDF — see ``app.core.memory.embeddings``).
- Optional Laya System-1 rerank on the fused top-N: a single batched
  forward pass (~ms) that scores query/chunk relevance without an LLM call.
  Fail-soft: any Laya failure returns the fused ranking unchanged.
- RRF weights are env-tunable: RAG_W_TREE / RAG_W_BM25 / RAG_W_DENSE.
- Env toggles: RAG_DENSE=true|false (default true), RAG_LAYA_RERANK=true|false
  (default true), RAG_RERANK_N=30, RAG_RERANK_TOP_K=top_k.
"""
import math
import os
import structlog
from typing import List, Dict, Any, Optional
from collections import Counter
from app.core.memory.local_pageindex import SearchResult

logger = structlog.get_logger(__name__)


# Map env var → Settings-store ``rag`` attribute (Settings UI writes these).
_RAG_SETTING_ATTRS = {
    "RAG_W_TREE": "w_tree",
    "RAG_W_BM25": "w_bm25",
    "RAG_W_DENSE": "w_dense",
    "RAG_DENSE": "dense",
    "RAG_LAYA_RERANK": "laya_rerank",
    "RAG_RERANK_N": "rerank_n",
}


def _rag_store(attr: str):
    """Read the Settings-store ``rag`` object. Never raises (None if absent)."""
    try:
        from app.core.settings_service import SettingsService

        rag = SettingsService.get_instance().get("rag", {}) or {}
        return rag.get(attr, None)
    except Exception:
        return None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, None)
    if raw in (None, ""):
        stored = _rag_store(_RAG_SETTING_ATTRS.get(name, ""))
        raw = stored if stored is not None else default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, None)
    if raw in (None, ""):
        stored = _rag_store(_RAG_SETTING_ATTRS.get(name, ""))
        raw = stored if stored is not None else ("true" if default else "false")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").lower() in ("1", "true", "yes", "on")


class BM25Matcher:
    """Lightweight BM25-style keyword matcher."""
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.avg_dl = 0
        self.doc_lengths = []
        self.doc_freqs = Counter()
        self.corpus_size = 0
        self.corpus_tokens = []

    def fit(self, corpus: List[str]):
        self.corpus_size = len(corpus)
        if self.corpus_size == 0: return
        
        tokenized_corpus = [doc.lower().split() for doc in corpus]
        total_len = 0
        for tokens in tokenized_corpus:
            total_len += len(tokens)
            self.doc_lengths.append(len(tokens))
            unique_tokens = set(tokens)
            for token in unique_tokens:
                self.doc_freqs[token] += 1
        
        self.avg_dl = total_len / self.corpus_size
        self.corpus_tokens = tokenized_corpus

    def score(self, query: str) -> List[float]:
        query_tokens = query.lower().split()
        scores = []
        
        for i, doc_tokens in enumerate(self.corpus_tokens):
            score = 0.0
            doc_len = self.doc_lengths[i]
            doc_counts = Counter(doc_tokens)
            
            for token in query_tokens:
                if token not in self.doc_freqs: continue
                
                # IDF
                idf = math.log((self.corpus_size - self.doc_freqs[token] + 0.5) / (self.doc_freqs[token] + 0.5) + 1.0)
                # Term Frequency component
                tf = doc_counts[token]
                score += idf * (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_dl))
            scores.append(score)
        return scores


class HybridSearch:
    """
    Combines PageIndex tree retrieval, BM25 lexical, and dense cosine.
    Merges with weighted Reciprocal Rank Fusion, then optionally reranks
    the fused top-N with the Laya System-1 relevance scorer.
    """
    def __init__(
        self,
        pageindex_service,
        alpha: float = 0.5,
        k: int = 60,
        *,
        w_tree: Optional[float] = None,
        w_bm25: Optional[float] = None,
        w_dense: Optional[float] = None,
        enable_dense: Optional[bool] = None,
        enable_rerank: Optional[bool] = None,
        rerank_n: int = 30,
    ):
        self.pageindex = pageindex_service
        self.alpha = alpha # Legacy weight: PageIndex vs BM25 when dense off
        self.k = k # RRF constant
        self.w_tree = w_tree if w_tree is not None else _env_float("RAG_W_TREE", 0.4)
        self.w_bm25 = w_bm25 if w_bm25 is not None else _env_float("RAG_W_BM25", 0.3)
        self.w_dense = w_dense if w_dense is not None else _env_float("RAG_W_DENSE", 0.3)
        self.enable_dense = enable_dense if enable_dense is not None else _env_bool("RAG_DENSE", True)
        self.enable_rerank = enable_rerank if enable_rerank is not None else _env_bool("RAG_LAYA_RERANK", True)
        self.rerank_n = int(os.environ.get("RAG_RERANK_N", rerank_n) or rerank_n)

    async def search(self, query: str, deal_id: Optional[str] = None, top_k: int = 10) -> List[SearchResult]:
        # 1. Tree / semantic candidates from PageIndex
        semantic_results = await self.pageindex.query(query, deal_id=deal_id, top_k=50)
        
        # 2. All chunks for the deal → BM25 + dense over the same corpus
        all_chunks = self.pageindex.get_all_chunks(deal_id=deal_id)
        if not all_chunks:
            return semantic_results[:top_k]

        corpus = [c.content for c in all_chunks]

        # 3a. BM25 lexical ranking
        bm25 = BM25Matcher()
        bm25.fit(corpus)
        bm25_scores = bm25.score(query)
        
        keyword_results = []
        for i, score in enumerate(bm25_scores):
            if score > 0:
                res = all_chunks[i]
                res.relevance_score = score
                keyword_results.append(res)
        keyword_results.sort(key=lambda x: x.relevance_score, reverse=True)

        # 3b. Dense cosine ranking (fail-soft → empty list)
        dense_results = self._dense_rank(query, all_chunks, corpus, limit=50)

        # 4. Weighted Reciprocal Rank Fusion across the three rankings
        # RRF(d) = sum_sources w_s / (k + rank_s(d))
        rrf_scores: Dict[str, float] = {}

        def _fuse(ranked: List[SearchResult], weight: float) -> None:
            for rank, res in enumerate(ranked):
                rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0) + weight * (1.0 / (self.k + rank + 1))

        if dense_results:
            _fuse(semantic_results, self.w_tree)
            _fuse(keyword_results[:50], self.w_bm25)
            _fuse(dense_results, self.w_dense)
        else:
            # Legacy two-way balance preserved when dense is unavailable
            for rank, res in enumerate(semantic_results):
                rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0) + (1.0 / (self.k + rank + 1)) * self.alpha
            for rank, res in enumerate(keyword_results[:50]):
                rrf_scores[res.chunk_id] = rrf_scores.get(res.chunk_id, 0) + (1.0 / (self.k + rank + 1)) * (1.0 - self.alpha)

        id_to_obj = {res.chunk_id: res for res in semantic_results}
        id_to_obj.update({res.chunk_id: res for res in keyword_results[:50]})
        for res in dense_results:
            id_to_obj.setdefault(res.chunk_id, res)
        
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        fused: List[SearchResult] = []
        for cid in sorted_ids[: max(top_k, self.rerank_n)]:
            res = id_to_obj[cid]
            res.relevance_score = rrf_scores[cid]
            fused.append(res)

        # 5. Laya System-1 rerank of the fused shortlist (fail-soft)
        if self.enable_rerank and fused:
            reranked = await self._laya_rerank(query, fused, top_k)
            if reranked:
                return reranked
        return fused[:top_k]

    # ── helpers ──────────────────────────────────────────────────────
    def _dense_rank(
        self, query: str, chunks: List[SearchResult], corpus: List[str], limit: int = 50
    ) -> List[SearchResult]:
        if not self.enable_dense or not corpus:
            return []
        try:
            from app.core.memory.embeddings import get_embedder, cosine_top_k

            embedder = get_embedder()
            doc_vecs = embedder.embed(corpus)
            q_vec = embedder.embed([query])[0]
            ranked = []
            for idx, score in cosine_top_k(q_vec, doc_vecs, limit):
                res = chunks[idx]
                res.relevance_score = round(float(score), 4)
                ranked.append(res)
            return ranked
        except Exception as e:
            logger.warning("dense_rank_failed", error=str(e))
            return []

    async def _laya_rerank(
        self, query: str, candidates: List[SearchResult], top_k: int
    ) -> Optional[List[SearchResult]]:
        try:
            from app.core.laya.client import get_laya_client

            client = get_laya_client()
            payload = [
                {"content": c.content, "chunk_id": c.chunk_id, "citation": (c.metadata or {}).get("citation", "")}
                for c in candidates[: self.rerank_n]
            ]
            reranked = await client.rerank_chunks(query, payload, top_k=top_k)
            if not reranked:
                return None
            by_id = {c.chunk_id: c for c in candidates}
            out: List[SearchResult] = []
            for item in reranked:
                orig = by_id.get(item["chunk_id"])
                if orig is None:
                    continue
                # Blend: keep fused RRF order stable, boost by Laya relevance
                orig.relevance_score = round(
                    0.6 * float(item.get("laya_relevance", 0.0)) + 0.4 * float(orig.relevance_score or 0.0), 4
                )
                meta = dict(orig.metadata or {})
                meta["laya_relevance"] = item.get("laya_relevance")
                orig.metadata = meta
                out.append(orig)
            # Append any fused candidates beyond rerank_n unchanged
            seen = {c.chunk_id for c in out}
            out.extend([c for c in candidates if c.chunk_id not in seen][: max(0, top_k - len(out))])
            out.sort(key=lambda c: c.relevance_score, reverse=True)
            return out[:top_k]
        except Exception as e:
            logger.warning("laya_rerank_failed", error=str(e))
            return None
