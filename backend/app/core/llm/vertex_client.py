import json
import httpx
import structlog
from typing import List, Dict, Any, Optional, AsyncGenerator
from app.config import get_settings

logger = structlog.get_logger()


def _safe_error_fields(error: Exception) -> Dict[str, Any]:
    """Return diagnostic fields without logging request URLs or response bodies."""
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    fields: Dict[str, Any] = {"error_type": type(error).__name__}
    if status_code is not None:
        fields["status_code"] = status_code
    return fields

class VertexClient:
    """
    Client for Google Vertex AI using direct REST API.
    Supports the streamGenerateContent endpoint with API key authentication.
    """

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        settings = get_settings()
        self.api_key = api_key or settings.VERTEX_API_KEY
        self.model_name = model or settings.VERTEX_MODEL or "gemini-3.8-flash"
        self.project_id = settings.VERTEX_PROJECT_ID
        self.location = settings.VERTEX_LOCATION or "us-central1"
        self.provider = "vertex"
        
        # Use Google AI Studio (GenAI) endpoint as it supports standard API Keys
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}"

    def _build_function_declarations(self, tools: List[Dict]) -> List[Dict]:
        """Convert OpenAI-style tool definitions to Vertex AI function declarations."""
        declarations = []
        for tool in tools:
            if isinstance(tool, dict):
                func = tool.get("function", tool)
                decl: Dict[str, Any] = {"name": func.get("name", ""), "description": func.get("description", "")}
                params = func.get("parameters")
                if params:
                    decl["parameters"] = self._fix_schema(params)
                declarations.append(decl)
        return declarations

    def _fix_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively fix JSON schema for Vertex AI (uppercase types, strip unsupported fields)."""
        if not isinstance(schema, dict):
            return schema
        new_schema = dict(schema)
        if "type" in new_schema and isinstance(new_schema["type"], str):
            new_schema["type"] = new_schema["type"].upper()
        for field in ["default", "title", "examples", "example", "allOf", "anyOf", "oneOf"]:
            new_schema.pop(field, None)
        if "properties" in new_schema and isinstance(new_schema["properties"], dict):
            new_schema["properties"] = {k: self._fix_schema(v) for k, v in new_schema["properties"].items()}
        if "items" in new_schema and isinstance(new_schema["items"], dict):
            new_schema["items"] = self._fix_schema(new_schema["items"])
        return new_schema

    def _validate_api_key(self):
        """Validate the API key format before making any request."""
        if not self.api_key:
            raise ValueError(
                "Vertex AI API key is not configured. "
                "Go to Settings → Google Vertex AI and enter your API key."
            )
        # We no longer strictly enforce 'AIza' prefix or block 'AQ.' prefix 
        # as some API key formats (like OAuth2 or custom project keys) may vary.
        if len(self.api_key) < 10:
             logger.warning("vertex_api_key_suspiciously_short", length=len(self.api_key))

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        json_mode: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate text using Google AI Studio REST API"""
        self._validate_api_key()
        url = f"{self.base_url}:generateContent?key={self.api_key}"

        contents = [{"role": "user", "parts": [{"text": prompt}]}]

        generation_config: Dict[str, Any] = {"maxOutputTokens": max_tokens}
        if not self.model_name.startswith("gemini-3."):
            generation_config["temperature"] = temperature

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }

        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            if "json" not in prompt.lower() and "json" not in (system_prompt or "").lower():
                contents[-1]["parts"][0]["text"] += "\nReturn response in valid JSON format."

        # Use the proper system_instruction field instead of a fake user turn
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        if tools:
            declarations = self._build_function_declarations(tools)
            if declarations:
                payload["tools"] = [{"function_declarations": declarations}]

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                
                # Extract content and function calls from response
                content = ""
                function_calls = []
                if "candidates" in data and data["candidates"]:
                    candidate = data["candidates"][0]
                    parts = (candidate.get("content") or {}).get("parts", [])
                    for part in parts:
                        if "text" in part:
                            content += part["text"]
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            function_calls.append({"name": fc.get("name", ""), "args": fc.get("args", {})})

                result: Dict[str, Any] = {"content": content, "provider": "vertex"}
                if function_calls:
                    result["function_calls"] = function_calls
                return result
            except Exception as e:
                logger.error("vertex_generation_failed", **_safe_error_fields(e))
                raise

    async def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream generation from Google AI Studio REST API"""
        self._validate_api_key()
        url = f"{self.base_url}:streamGenerateContent?key={self.api_key}"

        contents = [{"role": "user", "parts": [{"text": prompt}]}]

        generation_config: Dict[str, Any] = {"maxOutputTokens": 8192}
        if not self.model_name.startswith("gemini-3."):
            generation_config["temperature"] = 0.7

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }

        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line.strip() == "[" or line.strip() == "]":
                            continue
                        
                        # Vertex stream returns list of objects
                        if line.startswith(","):
                            line = line[1:]
                            
                        try:
                            chunk_data = json.loads(line)
                            if "candidates" in chunk_data and chunk_data["candidates"]:
                                candidate = chunk_data["candidates"][0]
                                if "content" in candidate and "parts" in candidate["content"]:
                                    yield candidate["content"]["parts"][0].get("text", "")
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.error("vertex_stream_failed", **_safe_error_fields(e))
                raise
