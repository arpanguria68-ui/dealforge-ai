"""RAG v2 + Laya integration tests (offline, no torch/network required).

All Laya paths are fail-soft: with laya uninstalled (or LAYA_MODE=off)
every decision helper returns None and callers use legacy behavior.
These tests pin that contract plus the RAG upgrades:

- chunking: overlap, heading-aware titles, no mid-sentence splits
- embeddings: dense rank puts the relevant doc first
- local_pageindex: tree-search recursion fix (no duplicate chunk_ids),
  citation metadata present
- hybrid_search: 3-way fusion works with dense on + rerank on, Laya absent
- laya client: fail-soft contract under LAYA_MODE=off
- confidence_gate: high-confidence output needs no validation (laya off)
"""
import os

import pytest

os.environ.setdefault("LAYA_MODE", "off")

from app.core.memory.chunking import chunk_text, summarize  # noqa: E402
from app.core.memory.embeddings import get_embedder, cosine_top_k  # noqa: E402


def test_chunking_overlap_and_headings():
    body = (
        "# Revenue Analysis\n\n" + ("Revenue grew 12% year over year. " * 120)
        + "\n\n# Risk Factors\n\n" + ("Customer concentration remains elevated. " * 120)
    )
    chunks = chunk_text(body, max_chars=1500, overlap_chars=200, source_title="memo.md")
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c.text) <= 1500 + 220  # overlap tail may round up slightly
        assert c.text.strip()
    titles = {c.title for c in chunks}
    assert "Revenue Analysis" in titles or "Risk Factors" in titles
    # overlap: consecutive chunks share text
    assert chunks[0].text[-100:] in chunks[1].text or chunks[1].text[:300] in chunks[0].text


def test_chunking_no_sentence_split_for_short_text():
    text = "First sentence. Second sentence. Third sentence."
    chunks = chunk_text(text, max_chars=1500, source_title="t")
    assert len(chunks) == 1
    # sentences are re-joined with blank lines; all three must survive intact
    assert "First sentence." in chunks[0].text
    assert "Second sentence." in chunks[0].text
    assert "Third sentence." in chunks[0].text


def test_summarize_prefers_sentences():
    s = summarize("Alpha. Beta. " + "Gamma filler. " * 100, limit=60)
    assert len(s) <= 65
    assert "Alpha" in s


def test_embedder_ranks_relevant_first():
    corpus = [
        "DCF valuation uses free cash flow and WACC discount rate",
        "The quick brown fox jumps over the lazy dog repeatedly",
        "EBITDA margin expansion driven by cost discipline",
    ]
    emb = get_embedder()
    vecs = emb.embed(corpus)
    q = emb.embed(["discounted cash flow WACC valuation"])[0]
    top = cosine_top_k(q, vecs, 3)
    assert top, "expected non-empty ranking"
    assert top[0][0] == 0, f"finance doc should rank first, got {top}"


@pytest.mark.asyncio
async def test_pageindex_index_query_citations_no_dupes(tmp_path):
    from app.core.memory.local_pageindex import LocalPageIndexService

    svc = LocalPageIndexService(storage_dir=str(tmp_path / "pageindex"))
    content = (
        "# Revenue\n\n" + ("Acme revenue grew 12% with EBITDA margin 24%. " * 40)
        + "\n\n# Risks\n\n" + ("Churn risk from customer concentration. " * 40)
    )
    doc = await svc.index_text(content, title="acme", metadata={"deal_id": "d1"})
    assert doc.total_nodes >= 2

    results = await svc.query("Acme revenue EBITDA margin", deal_id="d1", top_k=5)
    assert results, "keyword query should hit revenue chunk"
    ids = [r.chunk_id for r in results]
    assert len(ids) == len(set(ids)), "duplicate chunk_ids = tree-walk bug regressed"
    first = results[0]
    assert "citation" in (first.metadata or {}), "citation metadata missing"
    assert "chunk" in first.metadata["citation"]

    all_chunks = svc.get_all_chunks(deal_id="d1")
    assert len(all_chunks) == doc.total_nodes, "get_all_chunks must return each node once"


@pytest.mark.asyncio
async def test_pageindex_named_entity_search_does_not_return_other_company(tmp_path):
    from app.core.memory.local_pageindex import LocalPageIndexService

    svc = LocalPageIndexService(storage_dir=str(tmp_path / "pageindex"))
    await svc.index_text(
        "Acme market research indicates strong demand across the sector. " * 8,
        title="Acme market report",
        metadata={"company_name": "Acme"},
    )

    results = await svc.query("Cedarbrook Pumps market research evidence", top_k=5)
    assert results == []
    results = await svc.query("Research Cedarbrook Pumps market evidence", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_pageindex_excludes_provider_failure_artifacts(tmp_path):
    from app.core.memory.local_pageindex import LocalPageIndexService

    svc = LocalPageIndexService(storage_dir=str(tmp_path / "pageindex"))
    await svc.index_text(
        "[financial_analyst] analyze historical income statement and cash flow. "
        "Client error 403 Forbidden for url 'https://generativelanguage.googleapis.com/v1beta/generateContent?key=secret-token-value'",
        title="provider-error-record",
    )

    results = await svc.query("financial analyst historical income statement cash flow", top_k=5)
    assert results == []


def test_secret_sanitizer_redacts_provider_query_parameter():
    from app.core.laya.graph_nodes import sanitize_brief

    text = "Provider error at https://example.test/generate?key=AIzaSy1234567890abcdef&retry=1"
    sanitized = sanitize_brief(text)
    assert "AIzaSy1234567890abcdef" not in sanitized
    assert "key=[REDACTED]" in sanitized


@pytest.mark.asyncio
async def test_hybrid_search_fusion_without_laya(tmp_path):
    from app.core.memory.local_pageindex import LocalPageIndexService
    from app.core.search.hybrid_search import HybridSearch

    svc = LocalPageIndexService(storage_dir=str(tmp_path / "pageindex"))
    await svc.index_text(
        "Acme DCF valuation. " * 30 + "Unrelated filler about office snacks. " * 10,
        title="acme",
        metadata={"deal_id": "d2"},
    )
    hs = HybridSearch(svc, enable_rerank=True)  # laya absent → fused fallback
    results = await hs.search("Acme DCF valuation", deal_id="d2", top_k=3)
    assert results
    assert "DCF" in results[0].content or "Acme" in results[0].content


@pytest.mark.asyncio
async def test_laya_client_fail_soft_off(monkeypatch):
    monkeypatch.setenv("LAYA_MODE", "off")
    from app.core.laya.client import LayaDecisionClient

    client = LayaDecisionClient()
    assert client.backend == "off"
    assert await client.apredict({"body": "hi"}, {"q": {"type": "noul", "instructions": "x"}}) is None
    assert await client.triage_deal("brief") is None
    assert await client.gate_confidence("output") is None
    assert await client.route_complexity("task") is None
    assert await client.rerank_chunks("q", [{"content": "c"}], top_k=1) is None
    assert await client.apredict_batch([{"state": "s", "questions": {}}]) == [None]


@pytest.mark.asyncio
async def test_laya_route_tier_off_returns_none(monkeypatch):
    monkeypatch.setenv("LAYA_MODE", "off")
    from app.core.laya.client import LayaDecisionClient

    assert await LayaDecisionClient().route_tier("value this startup") is None


@pytest.mark.asyncio
async def test_router_multiprovider_tier_routing(monkeypatch):
    """Tier pools route across the fleet without raising (Laya stubbed)."""
    from app.core.llm import model_router as mr_mod
    from app.core.llm.model_router import ModelRouter

    router = ModelRouter()
    assert router._provider_credentialed("ollama") is True
    assert router._provider_credentialed("some_unknown_provider") is True

    class StubLaya:
        def __init__(self, tier):
            self._tier = tier
            self.backend = "test"

        async def route_tier(self, task):
            return self._tier

        async def route_complexity(self, task):
            return None

    for tier in ("simple", "moderate", "complex", None):
        # router imports get_laya_client lazily inside the method, so
        # patching the module attribute takes effect at call time.
        import app.core.laya.client as laya_client_mod

        monkeypatch.setattr(laya_client_mod, "get_laya_client", lambda t=tier: StubLaya(t))
        provider, used_fb = await router.get_provider_for_text("market_researcher", "summarize this")
        assert isinstance(provider, str) and provider
        assert isinstance(used_fb, bool)


def test_lmstudio_prompt_and_normalize():
    from app.core.laya.client import LayaDecisionClient

    qs = {
        "department": {"type": "choice", "instructions": "Pick team.",
                       "criteria": {"billing": "invoices", "tech": "bugs"}},
        "urgency": {"type": "score", "instructions": "Rate.",
                    "criteria": ["low", "high"]},
        "risk": {"type": "noul", "instructions": "Risky?"},
    }
    prompt = LayaDecisionClient._decision_prompt({"body": "billed twice"}, qs)
    assert '"department"' in prompt and '"urgency"' in prompt and '"risk"' in prompt

    good = {"department": {"choice": "billing", "confidence": 0.9},
            "urgency": {"score": 1, "confidence": 0.7},
            "risk": {"noul": 0.2, "confidence": 0.8}}
    out = LayaDecisionClient._normalize_lmstudio_answers(good, qs)
    assert out["department"]["choice"] == "billing"
    assert out["urgency"]["score"] == 1.0
    assert out["risk"]["noul"] == 0.2

    # strict: unknown choice dropped, out-of-range score dropped, bad noul dropped
    bad = {"department": {"choice": "nope", "confidence": 0.9},
           "urgency": {"score": 5, "confidence": 0.9},
           "risk": {"noul": 99, "confidence": 0.9}}
    assert LayaDecisionClient._normalize_lmstudio_answers(bad, qs) is None

    # case-insensitive choice + fenced JSON tolerated
    assert LayaDecisionClient._normalize_lmstudio_answers(
        '```json\n{"department": {"choice": "Billing", "confidence": 0.6}}\n```', qs
    )["department"]["choice"] == "billing"


def test_remote_path_defaults_and_overrides(monkeypatch):
    from app.core.laya.client import LayaDecisionClient

    monkeypatch.delenv("LAYA_REMOTE_PATH", raising=False)
    assert LayaDecisionClient()._remote_path() == "/v1/systemone"
    monkeypatch.setenv("LAYA_REMOTE_PATH", "api/evaluate")
    assert LayaDecisionClient()._remote_path() == "/api/evaluate"


def test_lmstudio_backend_selected(monkeypatch):
    monkeypatch.setenv("LAYA_ENABLED", "true")
    monkeypatch.setenv("LAYA_MODE", "lmstudio")
    for var in ("LAYA_BASE_URL",):
        monkeypatch.delenv(var, raising=False)
    from app.core.laya.client import LayaDecisionClient

    assert LayaDecisionClient().backend == "lmstudio"


def test_lmstudio_model_selection_tracks_loaded_models():
    from app.core.laya.client import LayaDecisionClient

    models = [
        {"key": "prism-ml/bonsai-27b", "type": "llm", "size_bytes": 27_000_000_000,
         "loaded_instances": []},
        {"key": "qwen/qwen3.5-9b", "type": "llm", "size_bytes": 9_000_000_000,
         "loaded_instances": [{"id": "qwen-instance"}]},
        {"key": "nomic-embed-text", "type": "embedding", "size_bytes": 1_000_000_000,
         "loaded_instances": [{"id": "embed-instance"}]},
    ]

    selected = LayaDecisionClient._select_lmstudio_model(
        models, "prism-ml/bonsai-27b", "qwen/qwen3.5-9b"
    )
    assert selected == (
        "qwen/qwen3.5-9b", "dealforge_model_loaded", ["qwen/qwen3.5-9b"]
    )


def test_lmstudio_model_selection_honors_loaded_override_and_falls_back_by_size():
    from app.core.laya.client import LayaDecisionClient

    models = [
        {"key": "qwen/qwen3.5-9b", "type": "llm", "size_bytes": 9_000,
         "loaded_instances": [{"id": "qwen"}]},
        {"key": "small-chat-model", "type": "llm", "size_bytes": 2_000,
         "loaded_instances": [{"id": "small"}]},
        {"key": "bonsai", "type": "llm", "size_bytes": 27_000,
         "loaded_instances": [{"id": "bonsai"}]},
    ]

    override = LayaDecisionClient._select_lmstudio_model(models, "bonsai", "qwen/qwen3.5-9b")
    assert override[:2] == ("bonsai", "laya_override_loaded")

    adaptive = LayaDecisionClient._select_lmstudio_model(models, "missing", "not-loaded")
    assert adaptive[:2] == ("small-chat-model", "smallest_loaded_chat_model")


def test_lmstudio_model_selection_uses_configured_model_if_none_loaded():
    from app.core.laya.client import LayaDecisionClient

    models = [
        {"key": "qwen/qwen3.5-9b", "type": "llm", "loaded_instances": []},
        {"key": "embed", "type": "embedding", "loaded_instances": [{"id": "embed"}]},
    ]
    selected = LayaDecisionClient._select_lmstudio_model(models, "", "qwen/qwen3.5-9b")
    assert selected == ("qwen/qwen3.5-9b", "dealforge_model_available", [])


@pytest.mark.asyncio
async def test_lmstudio_unreachable_is_none(monkeypatch):
    monkeypatch.setenv("LAYA_ENABLED", "true")
    monkeypatch.setenv("LAYA_MODE", "lmstudio")
    monkeypatch.setenv("LAYA_LMSTUDIO_URL", "http://127.0.0.1:9/v1")
    from app.core.laya.client import LayaDecisionClient

    out = await LayaDecisionClient().apredict(
        {"body": "hi"}, {"q": {"type": "noul", "instructions": "x"}},
        timeout_seconds=2)
    assert out is None


@pytest.mark.asyncio
async def test_confidence_gate_no_validation_high_confidence():
    from app.orchestrator.confidence_gate import ConfidenceGate

    gate = ConfidenceGate()
    req = await gate.check_and_validate(
        deal_id="d-gate",
        agent_name="financial_analyst",
        output="Revenue grew 12% with EBITDA margin of 24% supported by audited filings.",
        confidence=0.9,
        context={"deal_id": "d-gate"},
    )
    assert req.status == "no_validation_needed"


@pytest.mark.asyncio
async def test_tool_router_suggest_falls_back():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_default_tools(pageindex_client=None)
    names = await router.suggest_tools("run a DCF valuation", agent_name="financial_analyst")
    assert names, "fallback must return the agent allow-list, never empty"
