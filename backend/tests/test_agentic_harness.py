"""Production-readiness harness for the agent tool loop, tool router and Laya client.

Covers the contracts every tool must satisfy to be callable by an agent, the
router's failure isolation (timeouts, bad arguments, sync tools), the agent
loop's call budget, and the Laya client's cache / coalescing / breaker.
"""

import asyncio
import inspect
import re
from types import SimpleNamespace

import pytest

from app.core.tools.base_tool import BaseTool, ToolResult


# ── Tool contracts ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def registry():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_default_tools(pageindex_client=object())
    return router


def test_every_registered_tool_has_a_callable_contract(registry):
    problems = []
    for name, tool in registry.tools.items():
        schema = tool.get_parameters_schema() or {}
        props = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []
        sig = inspect.signature(tool.execute)
        accepts_any = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name):
            problems.append(f"{name}: tool name is not snake_case")
        if schema.get("type") != "object":
            problems.append(f"{name}: parameters schema must be an object")
        if set(required) - set(props):
            problems.append(f"{name}: required args missing from properties {set(required) - set(props)}")
        if not accepts_any and [p for p in props if p not in sig.parameters]:
            problems.append(f"{name}: execute() cannot accept schema args")
        hard_required = {
            p.name for p in sig.parameters.values()
            if p.default is p.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }
        if hard_required - set(required):
            problems.append(f"{name}: execute() requires {hard_required - set(required)} not marked required")
        if len(tool.description or "") < 20:
            problems.append(f"{name}: description too short for model tool selection")
    assert not problems, "\n".join(problems)


def test_agent_allow_lists_and_laya_families_reference_real_tools(registry):
    from app.core.tools.tool_router import AGENT_TOOL_MAP

    known = set(registry.tools)
    unknown = {
        agent: sorted(set(tools) - known)
        for agent, tools in AGENT_TOOL_MAP.items()
        if set(tools) - known
    }
    assert not unknown, f"allow-lists reference unregistered tools: {unknown}"
    family_unknown = {
        family: sorted(set(tools) - known)
        for family, tools in registry.LAYA_FAMILY_TOOLS.items()
        if set(tools) - known
    }
    assert not family_unknown, f"Laya families reference unregistered tools: {family_unknown}"
    in_family = {t for tools in registry.LAYA_FAMILY_TOOLS.values() for t in tools}
    assert known <= in_family, f"tools unreachable via Laya shortlist: {sorted(known - in_family)}"


# ── Router failure isolation ──────────────────────────────────────────


class _SyncTool(BaseTool):
    def __init__(self):
        super().__init__("sync_tool", "Synchronous tool used by the harness tests")

    def get_parameters_schema(self):
        return {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}

    def execute(self, x: int) -> ToolResult:
        return ToolResult(True, {"x": x * 2})


class _SlowTool(BaseTool):
    timeout_seconds = 0.05

    def __init__(self, name="slow_tool", delay=1.0):
        super().__init__(name, "Slow tool used by the harness timeout tests")
        self.delay = delay

    def get_parameters_schema(self):
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> ToolResult:
        await asyncio.sleep(self.delay)
        return ToolResult(True, {"slept": self.delay})


class _ConcurrencyTool(BaseTool):
    def __init__(self, name, tracker):
        super().__init__(name, "Records concurrent executions for harness tests")
        self.tracker = tracker

    def get_parameters_schema(self):
        return {"type": "object", "properties": {"i": {"type": "integer"}}}

    async def execute(self, i: int = 0) -> ToolResult:
        self.tracker["active"] += 1
        self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
        await asyncio.sleep(0.02)
        self.tracker["active"] -= 1
        return ToolResult(True, {"i": i})


@pytest.mark.asyncio
async def test_router_runs_sync_tools_off_loop_and_validates_args():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_tool(_SyncTool())

    ok = await router.execute("sync_tool", {"x": 4})
    assert ok.success and ok.data == {"x": 8}
    assert ok.execution_time_ms is not None

    missing = await router.execute("sync_tool", {})
    assert not missing.success and "Missing required argument" in missing.error

    # Unknown args are dropped for tools without **kwargs instead of crashing.
    extra = await router.execute("sync_tool", {"x": 1, "hallucinated": True})
    assert extra.success and extra.data == {"x": 2}

    as_string = await router.execute("sync_tool", '{"x": 3}')
    assert as_string.success and as_string.data == {"x": 6}

    not_object = await router.execute("sync_tool", [1, 2])
    assert not not_object.success and "JSON object" in not_object.error


@pytest.mark.asyncio
async def test_router_times_out_hanging_tools():
    from app.core.tools.tool_router import ToolRouter

    router = ToolRouter()
    router.register_tool(_SlowTool())
    result = await router.execute("slow_tool", None)
    assert not result.success and "timed out" in result.error


@pytest.mark.asyncio
async def test_function_calls_run_concurrently_in_order(monkeypatch):
    from app.core.tools.tool_router import ToolRouter

    monkeypatch.setenv("TOOL_MAX_CONCURRENCY", "3")
    tracker = {"active": 0, "peak": 0}
    router = ToolRouter()
    router.register_tool(_ConcurrencyTool("conc_tool", tracker))
    calls = [{"name": "conc_tool", "args": {"i": i}} for i in range(6)]
    calls.append({"name": "not_allowed", "args": {}})

    results = await router.execute_function_calls(calls, allowed_tools=["conc_tool"])

    assert [r.data["i"] for r in results[:6]] == list(range(6))
    assert not results[6].success and "not selected" in results[6].error
    assert tracker["peak"] == 3


# ── Agent tool loop call budget ───────────────────────────────────────


class _ScriptedGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def call(self, **kwargs):
        self.prompts.append(kwargs.get("prompt", ""))
        return self.responses.pop(0)


def _make_agent(monkeypatch, gateway, tool_router):
    from app.agents import base as base_mod
    from app.agents.base import BaseAgent

    class _Agent(BaseAgent):
        name = "market_researcher"

        async def run(self, task, context=None):  # pragma: no cover - unused
            raise NotImplementedError

    agent = _Agent.__new__(_Agent)
    agent.name = "market_researcher"
    agent.tools = tool_router
    agent._current_context = {"routed_provider": "gemini"}
    agent.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    monkeypatch.setattr(base_mod, "get_llm_gateway", lambda: gateway)
    return agent


class _CountingRouter:
    def __init__(self):
        self.executed = []

    def list_tools(self, agent_name=None):
        return [{"function": {"name": "web_search"}}]

    async def execute_function_calls(self, calls, allowed_tools=None):
        self.executed.extend(calls)
        return [ToolResult(True, {"q": c["args"].get("query")}) for c in calls]


@pytest.mark.asyncio
async def test_tool_loop_skips_redundant_final_synthesis(monkeypatch):
    gateway = _ScriptedGateway([
        {"content": "", "function_calls": [{"name": "web_search", "args": {"query": "acme"}}]},
        {"content": '{"answer": "final"}'},
    ])
    tools = _CountingRouter()
    agent = _make_agent(monkeypatch, gateway, tools)

    out = await agent.generate_with_tools("Research Acme", max_tool_rounds=3)

    assert out["content"] == '{"answer": "final"}'
    assert len(gateway.prompts) == 2, "final answer already produced; no extra synthesis call"
    assert out["tool_results"][0]["data"] == {"q": "acme"}
    assert out["tool_calls"] == out["function_calls"]


@pytest.mark.asyncio
async def test_tool_loop_dedupes_repeated_calls_and_synthesizes(monkeypatch):
    call = {"name": "web_search", "args": {"query": "acme"}}
    gateway = _ScriptedGateway([
        {"content": "", "function_calls": [call, dict(call)]},
        {"content": "", "function_calls": [dict(call)]},
        {"content": '{"answer": "synth"}'},
    ])
    tools = _CountingRouter()
    agent = _make_agent(monkeypatch, gateway, tools)

    out = await agent.generate_with_tools("Research Acme", max_tool_rounds=3)

    assert len(tools.executed) == 1, "identical calls execute once"
    assert len(gateway.prompts) == 3, "duplicate-only round stops the loop and synthesizes"
    assert "ALL TOOL RESULTS" in gateway.prompts[-1]
    assert out["content"] == '{"answer": "synth"}'


def test_tool_context_is_bounded():
    from app.agents.base import BaseAgent

    huge = [{"name": "web_search", "success": True, "data": "x" * 50_000, "error": None}] * 10
    rendered = BaseAgent._format_tool_results(huge)
    assert len(rendered) <= BaseAgent._TOOL_CONTEXT_CHAR_LIMIT + 100
    assert "truncated" in rendered


# ── Laya client hot-path protections ──────────────────────────────────


@pytest.fixture
def remote_laya(monkeypatch):
    from app.core.laya.client import LayaDecisionClient

    monkeypatch.setenv("LAYA_ENABLED", "true")
    monkeypatch.setenv("LAYA_MODE", "remote")
    monkeypatch.setenv("LAYA_BASE_URL", "http://laya.test")
    monkeypatch.setenv("LAYA_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("LAYA_BREAKER_COOLDOWN_SECONDS", "60")
    return LayaDecisionClient()


QUESTIONS = {"q": {"type": "noul", "instructions": "Is it?"}}


@pytest.mark.asyncio
async def test_laya_caches_and_coalesces_identical_decisions(remote_laya, monkeypatch):
    calls = []

    async def fake_remote(state, questions, timeout_seconds=None):
        calls.append(state)
        await asyncio.sleep(0.02)
        return {"q": {"noul": 0.9}}

    monkeypatch.setattr(remote_laya, "_predict_remote", fake_remote)
    first, second = await asyncio.gather(
        remote_laya.apredict({"body": "same"}, QUESTIONS),
        remote_laya.apredict({"body": "same"}, QUESTIONS),
    )
    third = await remote_laya.apredict({"body": "same"}, QUESTIONS)

    assert first == second == third == {"q": {"noul": 0.9}}
    assert len(calls) == 1
    stats = remote_laya.stats()
    assert stats["cache_hits"] == 1 and stats["inflight_joins"] == 1

    # Callers mutating the result must not poison the cache.
    third["q"]["noul"] = 0.0
    assert (await remote_laya.apredict({"body": "same"}, QUESTIONS))["q"]["noul"] == 0.9


@pytest.mark.asyncio
async def test_laya_breaker_short_circuits_dead_backend(remote_laya, monkeypatch):
    calls = []

    async def failing_remote(state, questions, timeout_seconds=None):
        calls.append(state)
        raise ConnectionError("laya down")

    monkeypatch.setattr(remote_laya, "_predict_remote", failing_remote)
    for i in range(5):
        assert await remote_laya.apredict({"body": f"b{i}"}, QUESTIONS) is None

    assert len(calls) == 2, "breaker opens after threshold failures"
    assert remote_laya.stats()["breaker_short_circuits"] == 3
    assert remote_laya.stats()["breakers"]["remote"]["open_for_s"] > 0


@pytest.mark.asyncio
async def test_laya_batch_serves_cached_items_and_only_sends_misses(remote_laya, monkeypatch):
    sent = []

    async def fake_remote(state, questions, timeout_seconds=None):
        sent.append(state["body"])
        return {"q": {"noul": 0.7}}

    monkeypatch.setattr(remote_laya, "_predict_remote", fake_remote)
    reqs = [{"state": {"body": b}, "questions": QUESTIONS} for b in ("a", "b")]
    await remote_laya.apredict_batch(reqs)
    reqs.append({"state": {"body": "c"}, "questions": QUESTIONS})
    out = await remote_laya.apredict_batch(reqs)

    assert sent == ["a", "b", "c"]
    assert all(o == {"q": {"noul": 0.7}} for o in out)


def test_laya_noul_confidence_is_distance_from_coin_flip():
    from app.core.laya.client import LayaDecisionClient

    confident_no = LayaDecisionClient._parse_noul({"x": {"noul": 0.05}}, "x")
    assert confident_no.answer == 0.05
    assert confident_no.confidence == pytest.approx(0.95)


# ── Agent readiness: confidence, failure handling, compliance, tool labels ──


def _bare_agent(cls, name, **attrs):
    agent = cls.__new__(cls)
    agent.name = name
    agent.logger = SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None
    )
    agent._current_context = {}
    for key, value in attrs.items():
        setattr(agent, key, value)
    return agent


def test_evidence_confidence_reflects_output_signals():
    from app.agents.base import BaseAgent

    clean = {"thesis": "x"}
    assert BaseAgent._evidence_confidence(0.95, None, clean) == 0.85, "capped below near-certainty"
    assert clean["confidence_basis"] == "heuristic_signals"

    unparsed = BaseAgent._evidence_confidence(0.85, None, {"raw_reasoning": "text"})
    empty = BaseAgent._evidence_confidence(0.85, None, {})
    tools = {"tool_results": [{"success": True}, {"success": False}]}
    partial_tools = BaseAgent._evidence_confidence(0.85, tools, {"a": 1})
    failed_validation = BaseAgent._evidence_confidence(0.85, None, {"a": 1, "_validation": {"passed": False}})
    assert empty < unparsed < 0.85
    assert partial_tools == pytest.approx(0.85 * 0.8, abs=1e-3)
    assert failed_validation == pytest.approx(0.51, abs=1e-3)
    assert BaseAgent._evidence_confidence(0.85, None, {"a": 1, "_rag_context": {"chunks_used": 0}}) < 0.85


@pytest.mark.asyncio
@pytest.mark.parametrize("module,cls_name,name", [
    ("app.agents.complex_reasoning_agent", "ComplexReasoningAgent", "complex_reasoning_agent"),
    ("app.agents.data_curator_agent", "DataCuratorAgent", "data_curator_agent"),
])
async def test_llm_provider_failure_returns_failed_output(monkeypatch, module, cls_name, name):
    import importlib

    cls = getattr(importlib.import_module(module), cls_name)
    agent = _bare_agent(cls, name)

    async def boom(*a, **k):
        raise RuntimeError("all providers down")

    async def no_context(*a, **k):
        return []

    monkeypatch.setattr(agent, "generate_with_tools", boom)
    monkeypatch.setattr(agent, "retrieve_context", no_context)
    out = await agent.run("task", {"deal_id": "d1"})
    assert out.success is False and out.confidence == 0.0


@pytest.mark.asyncio
async def test_compliance_agent_without_documents_reports_unassessed_not_noncompliant(monkeypatch):
    from app.agents.legal_advisor import ComplianceAgent

    agent = _bare_agent(ComplianceAgent, "compliance_agent")

    async def no_docs(*a, **k):
        return []

    monkeypatch.setattr(agent, "retrieve_context", no_docs)
    out = await agent.run("check", {"industry": "healthcare", "deal_id": "d1"})
    assert out.success
    assert out.data["compliance_score"] is None and out.data["coverage"] == 0.0
    assert out.data["gaps"] == [] and "HIPAA compliance" in out.data["unassessed"]
    assert out.confidence <= 0.2


@pytest.mark.asyncio
async def test_compliance_agent_requires_cited_evidence_per_status(monkeypatch):
    import json as _json

    from app.agents.legal_advisor import ComplianceAgent

    agent = _bare_agent(ComplianceAgent, "compliance_agent")

    async def docs(query, top_k=3):
        if "Data protection" in query:
            return [{"content": "The company has no data processing agreements in place.", "source": "dpa.pdf"}]
        if "Tax compliance" in query:
            return [{"content": "All federal and state tax returns filed through FY2024.", "source": "tax.pdf"}]
        return []

    async def llm(prompt, system_prompt=None):
        assert "[E1]" in prompt and "[E2]" in prompt
        return {"content": _json.dumps({"items": [
            # Excerpts are numbered in checklist order: Tax (E1) before Data protection (E2).
            {"requirement": "Data protection", "status": "non_compliant", "evidence": ["E2"],
             "remediation": "Execute DPAs"},
            {"requirement": "Tax compliance", "status": "compliant", "evidence": ["E1"]},
            {"requirement": "Entity registration", "status": "compliant", "evidence": []},
            {"requirement": "Annual filings", "status": "compliant", "evidence": ["E99"]},
        ]})}

    monkeypatch.setattr(agent, "retrieve_context", docs)
    monkeypatch.setattr(agent, "generate_with_routed_fallback", llm)
    out = await agent.run("check", {"industry": "technology"})

    by_req = {r["requirement"]: r for r in out.data["assessment_results"]}
    assert by_req["Data protection"]["status"] == "non_compliant"
    assert by_req["Data protection"]["evidence"][0]["source"] == "dpa.pdf"
    assert by_req["Tax compliance"]["status"] == "compliant"
    # Uncited or invalidly cited statuses are downgraded to unknown.
    assert by_req["Entity registration"]["status"] == "unknown"
    assert by_req["Annual filings"]["status"] == "unknown"
    assert out.data["compliance_score"] == 0.5
    assert out.data["coverage"] == pytest.approx(0.4)
    assert [g["requirement"] for g in out.data["gaps"]] == ["Data protection"]


@pytest.mark.asyncio
async def test_heuristic_tools_are_labelled_to_the_model_and_in_results(registry):
    heuristic = {"model_defensibility_scorer", "ai_value_quantifier", "esg_scorer"}
    extracted = {
        "ai_stack_scanner", "carbon_footprint_extractor", "supply_chain_risk_flagger",
        "cyber_vuln_scanner", "privacy_auditor",
    }
    for name in heuristic:
        assert registry.tools[name].get_schema()["function"]["description"].startswith("[HEURISTIC]")
    for name in extracted:
        assert registry.tools[name].get_schema()["function"]["description"].startswith("[EXTRACTED]")
    assert registry.tools["filing_due_diligence"].output_quality == "synthetic_model"
    assert registry.tools["web_search"].output_quality == "data"
    assert not registry.tools["web_search"].get_schema()["function"]["description"].startswith("[")

    # No LLM in tests (conftest): extraction tools fall back and say so.
    result = await registry.execute("cyber_vuln_scanner", {"security_text": "We suffered a ransomware breach."})
    assert result.success
    assert result.data["data_quality"] == "heuristic" and "[ESTIMATED]" in result.data["method_note"]
    scored = await registry.execute("esg_scorer", {"total_emissions": 5, "supply_chain_severity": "low"})
    assert scored.data["data_quality"] == "heuristic" and "MSCI" not in str(scored.data.get("indicative_rating_band"))


@pytest.mark.asyncio
async def test_churn_simulation_is_reproducible(registry):
    args = {"base_count": 500, "cultural_fit_score": 40}
    first = await registry.execute("churn_monte_carlo", dict(args))
    second = await registry.execute("churn_monte_carlo", dict(args))
    assert first.success and first.data == second.data


def test_default_tools_are_shared_but_document_search_is_per_agent():
    from app.core.tools.tool_router import ToolRouter

    a, b = ToolRouter(), ToolRouter()
    a.register_default_tools(object())
    b.register_default_tools(object())
    assert a.tools["web_search"] is b.tools["web_search"]
    assert a.tools["document_search"] is not b.tools["document_search"]
    a.tools.pop("web_search")
    assert "web_search" in b.tools, "per-router dicts stay independent"


@pytest.mark.asyncio
async def test_laya_warmup_is_opt_out_and_skips_non_model_backends(remote_laya, monkeypatch):
    calls = []

    async def fake_remote(state, questions, timeout_seconds=None):
        calls.append(state)
        return {"warmup": {"noul": 0.9}}

    monkeypatch.setattr(remote_laya, "_predict_remote", fake_remote)
    monkeypatch.setenv("LAYA_WARMUP", "true")
    assert await remote_laya.warmup() is True and len(calls) == 1

    monkeypatch.setenv("LAYA_WARMUP", "false")
    assert await remote_laya.warmup() is False and len(calls) == 1

    monkeypatch.setenv("LAYA_WARMUP", "true")
    monkeypatch.setenv("LAYA_MODE", "lmstudio")
    assert await remote_laya.warmup() is False, "LM Studio decisions are chat calls; nothing to preload"


@pytest.mark.asyncio
async def test_concurrent_runs_on_a_shared_agent_keep_their_own_context():
    """Agent instances are shared; one deal's run must not see another's deal_id."""
    from app.agents.base import BaseAgent

    class _Probe(BaseAgent):
        name = "probe"

        async def run(self, task, context=None):
            self._current_context = context
            await asyncio.sleep(0.01)  # let the other run overwrite, if it could
            return self._current_context["deal_id"]

    agent = _Probe.__new__(_Probe)
    seen = await asyncio.gather(*(agent.run("t", {"deal_id": f"deal-{i}"}) for i in range(5)))
    assert seen == [f"deal-{i}" for i in range(5)]
