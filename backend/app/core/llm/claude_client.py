"""Anthropic Claude API client for DealForge AI (F-029)."""
from typing import Dict, Any, Optional, List
import structlog
import asyncio
import os

logger = structlog.get_logger(__name__)

class ClaudeClient:
    """Client for Anthropic Claude models (F-029)."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20240620"):
        self.api_key = api_key
        self.model = model
        # Lazy import to avoid dependency issues if not installed
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(
                api_key=api_key, timeout=60.0, max_retries=0
            )
        except ImportError:
            self.client = None
            logger.warning("anthropic_sdk_not_installed")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate response via Claude."""
        if not self.client:
            raise ImportError("Please install 'anthropic' package.")

        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]
        }
        
        if system_prompt:
            params["system"] = system_prompt

        response = await self.client.messages.create(**params)
        
        # Extract text content
        text = "".join([block.text for block in response.content if hasattr(block, 'text')])
        
        return {
            "content": text,
            "raw": response,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens
            },
            "model": self.model,
            "provider": "anthropic"
        }
