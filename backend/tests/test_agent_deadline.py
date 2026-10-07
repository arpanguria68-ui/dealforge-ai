import asyncio

import pytest

from app.main import _await_agent_execution


@pytest.mark.asyncio
async def test_agent_deadline_cancels_hung_execution_with_clear_error():
    async def slow_agent():
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError, match="financial_analyst exceeded the 0-second execution deadline"):
        await _await_agent_execution(slow_agent(), "financial_analyst", 0)
