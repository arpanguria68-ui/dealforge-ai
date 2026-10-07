"""Offline contract tests for provider adapters and genuine MCP configuration."""
import pytest

from app.core.mcp import MCPClient


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="Unknown MCP provider"):
        MCPClient("not-a-provider")


@pytest.mark.asyncio
async def test_unconfigured_provider_returns_actionable_result(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    client = MCPClient("finnhub")
    result = await client.query("stock_price", {"symbol": "AAPL"})
    assert result["configured"] is False
    assert result["data"] is None
    assert "FINNHUB_API_KEY" in result["error"]


def test_provider_metadata_is_explicit_and_unique():
    from app.core.mcp import MCP_PROVIDERS

    assert MCP_PROVIDERS
    assert all(provider.get("name") and provider.get("capabilities") for provider in MCP_PROVIDERS.values())
    assert len(MCP_PROVIDERS) == len(set(MCP_PROVIDERS))
