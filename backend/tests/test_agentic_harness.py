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
