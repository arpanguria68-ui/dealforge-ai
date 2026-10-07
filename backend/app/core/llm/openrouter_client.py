"""OpenRouter chat-completions client (OpenAI-compatible API)."""

import json
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI


class OpenRouterClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        from app.config import get_settings

        settings = get_settings()
        self.model = model or settings.OPENROUTER_MODEL
        self.provider = "openrouter"
        self.max_context = 32_768
        self.client = AsyncOpenAI(
            api_key=api_key or settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={"X-OpenRouter-Title": "DealForge AI"},
            timeout=60.0,
            max_retries=0,
        )

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        json_mode: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if json_mode:
            params["response_format"] = {"type": "json_object"}
            if "json" not in prompt.lower() and "json" not in (system_prompt or "").lower():
                messages[-1]["content"] += "\nReturn response in valid JSON format."
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # This selected reasoning model can spend its whole completion budget
        # thinking and return no user-visible text. Keep a modest reasoning
        # budget for report synthesis so output tokens remain for the answer.
        if self.model.startswith("nvidia/nemotron-3-super-"):
            params.setdefault("reasoning_effort", "low")

        response = await self.client.chat.completions.create(**params)
        message = response.choices[0].message
        content = message.content or ""
        if (
            self.model.startswith("nvidia/nemotron-3-super-")
            and not content.strip()
            and not message.tool_calls
            and response.choices[0].finish_reason == "length"
        ):
            # A low reasoning setting can still consume the full budget on a
            # long synthesis prompt. Retry once with minimal reasoning so the
            # caller receives an answer rather than an empty completion.
            retry_params = {**params, "reasoning_effort": "minimal"}
            response = await self.client.chat.completions.create(**retry_params)
            message = response.choices[0].message
            content = message.content or ""
            if (
                not content.strip()
                and not message.tool_calls
                and response.choices[0].finish_reason == "length"
            ):
                raise ValueError(
                    "OpenRouter reasoning consumed the completion budget before a visible answer"
                )
        result = {"content": content, "raw_response": response}
        if message.tool_calls:
            result["function_calls"] = [
                {"name": call.function.name, "args": json.loads(call.function.arguments)}
                for call in message.tool_calls
            ]
        return result
