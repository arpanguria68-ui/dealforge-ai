"""Shared offline fixtures for legacy evals updated to current contracts."""
from unittest.mock import Mock

import pytest
import pytest_asyncio


@pytest.fixture
def mock_llm_client():
    client = Mock()
    client.generate.return_value = {"content": "Deterministic test response"}
    return client


@pytest_asyncio.fixture
async def mock_financial_analyst(mock_llm_client):
    from app.agents.financial_analyst import FinancialAnalystAgent

    return FinancialAnalystAgent(llm_client=mock_llm_client)
