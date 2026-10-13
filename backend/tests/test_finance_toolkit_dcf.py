import asyncio

from app.core.tools import finance_toolkit_tool as module


class _Frame:
    def to_dict(self):
        return {"AAPL": {"Intrinsic Value": 1.0}}


def _run(monkeypatch, **kwargs):
    captured = {}

    class FakeModels:
        def get_intrinsic_valuation(self, growth_rate, perpetual_growth_rate, weighted_average_cost_of_capital):
            captured.update(growth=growth_rate, perpetual=perpetual_growth_rate, wacc=weighted_average_cost_of_capital)
            return _Frame()

    class FakeToolkit:
        def __init__(self, **_):
            self.models = FakeModels()

    monkeypatch.setattr(module, "_try_import_toolkit", lambda: FakeToolkit)
    tool = module.FinanceAnalysisTool()
    result = asyncio.run(tool.execute("AAPL", analysis_type="dcf", **kwargs))
    return result, captured


def test_dcf_supplies_the_inputs_the_library_requires_and_reports_assumptions(monkeypatch):
    result, captured = _run(monkeypatch)
    assert result.success, result.error
    assert captured == {"growth": 0.08, "perpetual": 0.025, "wacc": 0.10}
    assert set(result.data["_inputs"]["assumed"]) == {"growth_rate", "perpetual_growth_rate", "wacc"}


def test_dcf_uses_caller_inputs_without_marking_them_assumed(monkeypatch):
    result, captured = _run(monkeypatch, growth_rate=0.12, perpetual_growth_rate=0.03, wacc=0.096)
    assert captured == {"growth": 0.12, "perpetual": 0.03, "wacc": 0.096}
    assert result.data["_inputs"]["assumed"] == {}
