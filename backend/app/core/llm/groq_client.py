"""Groq API client for DealForge AI (F-030)."""
from typing import Dict, Any, Optional, List
import structlog
import os

logger = structlog.get_logger(__name__)

class GroqClient:
    """Client for Groq models (F-030) - optimized for low latency."""

    def __init__(self, api_key: str, model: str = "llama3-70b-8192"):
        self.api_key = api_key
        self.model = model
        try:
            from groq import AsyncGroq
            self.client = AsyncGroq(api_key=api_key, timeout=60.0, max_retries=0)
        except ImportError:
            self.client = None
            logger.warning("groq_sdk_not_installed")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate response via Groq."""
        if not self.client:
            raise ImportError("Please install 'groq' package.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return {
            "content": response.choices[0].message.content,
            "raw": response,
            "usage": {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens
            },
            "model": self.model,
            "provider": "groq"
        }
