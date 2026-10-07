"""Finance tool validation with a fake toolkit: deterministic and network-free."""
from types import SimpleNamespace

import pytest

from app.core.tools import finance_toolkit_tool as finance_module
from app.core.tools.finance_toolkit_tool import FinanceAnalysisTool


class _FakeToolkit:
    def __init__(self, **_kwargs):
        self.ratios = SimpleNamespace(
            collect_valuation_ratios=lambda: SimpleNamespace(to_dict=lambda: {"PE": {"EVAL": 12.0}})
        )


@pytest.mark.asyncio
async def test_finance_tool_normalizes_ticker_and_returns_structured_data(monkeypatch):
    monkeypatch.setattr(finance_module, "_try_import_toolkit", lambda: _FakeToolkit)
    result = await FinanceAnalysisTool().execute(tickers="EVAL", analysis_type="ratios", sub_type="valuation")
    assert result.success
    assert result.data == {"PE": {"EVAL": 12.0}}


@pytest.mark.asyncio
async def test_finance_tool_rejects_empty_ticker_list(monkeypatch):
    monkeypatch.setattr(finance_module, "_try_import_toolkit", lambda: _FakeToolkit)
    result = await FinanceAnalysisTool().execute(tickers="  ")
    assert not result.success
    assert result.error == "No valid tickers provided."


@pytest.mark.asyncio
async def test_finance_tool_converts_provider_exceptions_to_tool_result(monkeypatch):
    class BrokenToolkit:
        def __init__(self, **_kwargs):
            self.ratios = SimpleNamespace(collect_valuation_ratios=lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    monkeypatch.setattr(finance_module, "_try_import_toolkit", lambda: BrokenToolkit)
    result = await FinanceAnalysisTool().execute(tickers=["EVAL"])
    assert not result.success
    assert "FinanceToolkit error: offline" in result.error
