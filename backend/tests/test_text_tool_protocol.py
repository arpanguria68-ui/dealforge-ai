import asyncio

from app.agents.base import BaseAgent
from app.agents.business_analyst import BusinessAnalystAgent

BLOCK = '```json\n{"command": "finnhub_data", "args": {"symbol": "SPT", "endpoint": "quote"}}\n```'


def test_parses_single_multiple_and_list_calls():
    parse = BaseAgent._parse_text_tool_calls
    assert parse(BLOCK) == [{"name": "finnhub_data", "args": {"symbol": "SPT", "endpoint": "quote"}}]
    two = BLOCK + "\n" + BLOCK.replace("quote", "metric")
    assert [c["args"]["endpoint"] for c in parse(two)] == ["quote", "metric"]
    as_list = '```json\n[{"command": "a", "args": {}}, {"command": "b"}]\n```'
    assert [c["name"] for c in parse(as_list)] == ["a", "b"]
    assert parse('{"command": "bare", "args": {"x": 1}}') == [{"name": "bare", "args": {"x": 1}}]


def test_prose_and_final_answers_are_not_tool_calls():
    parse = BaseAgent._parse_text_tool_calls
    assert parse("") == [] and parse("The command line tool is great.") == []
    assert parse('```json\n{"revenue": 1, "growth": 0.1}\n```') == []
    assert parse('```json\n{"command": 5}\n```') == []


def test_custom_endpoint_uses_text_protocol_and_executes_tools(monkeypatch):
    import app.agents.base as base_module

    seen = []

    class FakeGateway:
        async def call(self, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                return {"content": BLOCK, "provider_used": "custom_endpoint"}
            return {"content": '{"price": 11.05}', "provider_used": "custom_endpoint"}

    class FakeResult:
        success, data, error = True, {"c": 11.05}, None

    class FakeTools:
        executed = []

        def list_tools(self, agent_name=None):
            return [{"type": "function", "function": {"name": "finnhub_data", "description": "quote",
                                                      "parameters": {"type": "object"}}}]

        async def execute_function_calls(self, calls, allowed_tools=None):
            self.executed.extend(calls)
            return [FakeResult() for _ in calls]

    class FakeRouter:
        async def get_model_route_for_text(self, *a, **k):
            return "custom_endpoint", "gemini-x", False

    monkeypatch.setattr(base_module, "get_llm_gateway", lambda: FakeGateway())
    monkeypatch.setattr("app.core.llm.model_router.get_model_router", lambda: FakeRouter())

    agent = object.__new__(BusinessAnalystAgent)
    agent.name = "business_analyst"
    agent.tools = FakeTools()
    agent._current_context = {}
    agent.logger = type("L", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})()
    result = asyncio.run(agent.generate_with_tools("What is SPT trading at?", system_prompt="sys"))

    assert seen[0]["tools"] is None                      # no native function calling
    assert "ONLY fenced json blocks" in seen[0]["system_prompt"]
    assert agent.tools.executed == [{"name": "finnhub_data", "args": {"symbol": "SPT", "endpoint": "quote"}}]
    assert result["tool_results"][0]["success"] and "11.05" in result["tool_results"][0]["data"].__str__()
    assert "TOOL EXECUTION RESULTS" in seen[1]["prompt"]
