# Deferred import to prevent startup hang
# import google.generativeai as genai
import os
from typing import List, Dict, Any, Optional, AsyncGenerator
import json
import structlog
from app.config import get_settings

logger = structlog.get_logger()


class GeminiClient:
    """Client for Google's Gemini API (AI Studio or Vertex AI)"""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None, provider: str = "gemini"):
        settings = get_settings()
        self.provider = provider
        
        if self.provider == "vertex":
            self.api_key = api_key or settings.VERTEX_API_KEY
            self.model_name = model or settings.VERTEX_MODEL
            self.project_id = settings.VERTEX_PROJECT_ID
            self.location = settings.VERTEX_LOCATION
            
            # For Vertex AI, we use the vertexai SDK
            import vertexai
            from vertexai.generative_models import GenerativeModel
            
            if self.api_key:
                # If API key is provided for Vertex (less common, usually OAuth/Service Account)
                # but we'll follow user request for "api key input pointy"
                os.environ["GOOGLE_API_KEY"] = self.api_key
            
            vertexai.init(project=self.project_id, location=self.location)
            logger.info("vertex_ai_initialized", project=self.project_id, location=self.location)
        else:
            self.api_key = api_key or settings.GEMINI_API_KEY
            self.model_name = model or settings.GEMINI_MODEL
            self.max_context = 1000000  # Gemini 1.5 context

            import google.generativeai as genai

            if self.api_key:
                genai.configure(api_key=self.api_key)
            else:
                logger.warning("Gemini API key not configured")

    def _get_model(self, tools: Optional[List[Dict]] = None):
        """Get configured model instance"""
        generation_config = {
            "max_output_tokens": 8192,
        }
        if not self.model_name.startswith("gemini-3."):
            generation_config.update(temperature=0.7, top_p=0.95, top_k=40)

        if self.provider == "vertex":
            from vertexai.generative_models import GenerativeModel, Tool as VertexTool, FunctionDeclaration
            
            if tools:
                # Transform OpenAI-style tools to Vertex AI format
                formatted_tools = []
                for tool in tools:
                    # Vertex expects Tool(function_declarations=[...])
                    if isinstance(tool, dict):
                        func = dict(tool.get("function", tool))
                        func.pop("type", None)
                        func.pop("strict", None)
                        if "parameters" in func:
                            func["parameters"] = self._fix_schema(func["parameters"])
                        formatted_tools.append(FunctionDeclaration(**func))
                
                vertex_tool = VertexTool(function_declarations=formatted_tools)
                
                return GenerativeModel(
                    model_name=self.model_name,
                    generation_config=generation_config,
                    tools=[vertex_tool],
                )
            
            return GenerativeModel(
                model_name=self.model_name, generation_config=generation_config
            )

        import google.generativeai as genai

        if tools:
            # Transform OpenAI-style tools to Gemini format
            formatted_tools = []
            for tool in tools:
                if isinstance(tool, dict):
                    if tool.get("type") == "function" and "function" in tool:
                        # Extract the function part
                        func = dict(tool["function"])
                    else:
                        func = dict(tool)

                    # Ensure no invalid fields like 'type' or 'strict' are in the declaration
                    func.pop("type", None)
                    func.pop("strict", None)

                    # Recursively fix types in parameters (Gemini expects uppercase: 'OBJECT', 'STRING')
                    if "parameters" in func and isinstance(func["parameters"], dict):
                        func["parameters"] = self._fix_schema(func["parameters"])

                    formatted_tools.append(func)
                else:
                    formatted_tools.append(tool)

            # Enable function calling
            return genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=generation_config,
                tools=formatted_tools,
            )

        return genai.GenerativeModel(
            model_name=self.model_name, generation_config=generation_config
        )

    def _fix_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively fix JSON schema for Gemini compatibility"""
        if not isinstance(schema, dict):
            return schema

        new_schema = dict(schema)

        # 1. Gemini expects uppercase types (STRING, NUMBER, INTEGER, BOOLEAN, ARRAY, OBJECT)
        if "type" in new_schema and isinstance(new_schema["type"], str):
            new_schema["type"] = new_schema["type"].upper()

        # 2. Remove unsupported fields (Gemini Schema is strict)
        unsupported_fields = [
            "default",
            "title",
            "examples",
            "example",
            "allOf",
            "anyOf",
            "oneOf",
        ]
        for field in unsupported_fields:
            new_schema.pop(field, None)

        # 3. Recursively fix properties
        if "properties" in new_schema and isinstance(new_schema["properties"], dict):
            new_schema["properties"] = {
                k: self._fix_schema(v) for k, v in new_schema["properties"].items()
            }

        # 4. Recursively fix items (for arrays)
        if "items" in new_schema and isinstance(new_schema["items"], dict):
            new_schema["items"] = self._fix_schema(new_schema["items"])

        return new_schema

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
        """
        Generate text using Gemini
        """
        try:
            model = self._get_model(tools)

            # Override generation config with caller-specified values
            import google.generativeai as genai
            generation_config = {"max_output_tokens": max_tokens}
            if not self.model_name.startswith("gemini-3."):
                generation_config.update(
                    temperature=temperature, top_p=0.95, top_k=40
                )
            if json_mode:
                generation_config["response_mime_type"] = "application/json"
            gen_config = genai.GenerationConfig(**generation_config)

            # Build conversation
            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{prompt}"
            else:
                full_prompt = prompt

            logger.debug("Gemini generation request", prompt_length=len(full_prompt))

            response = await model.generate_content_async(
                full_prompt, generation_config=gen_config
            )

            # Extract text content safely
            content = ""
            try:
                content = response.text or ""
            except ValueError:
                pass

            # Check for function calls
            function_calls = []
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "function_call"):
                                function_calls.append(
                                    {
                                        "name": part.function_call.name,
                                        "args": (
                                            dict(part.function_call.args)
                                            if part.function_call.args
                                            else {}
                                        ),
                                    }
                                )

            return {
                "content": content,
                "function_calls": function_calls,
                "raw_response": response,
            }

        except Exception as e:
            logger.error("Gemini generation failed", error_type=type(e).__name__)
            raise

    async def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream generation from Gemini"""
        try:
            model = self._get_model()

            if system_prompt:
                full_prompt = f"{system_prompt}\n\n{prompt}"
            else:
                full_prompt = prompt

            response = await model.generate_content_async(full_prompt, stream=True)

            async for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error("Gemini stream generation failed", error_type=type(e).__name__)
            raise

    async def generate_structured(
        self,
        prompt: str,
        output_schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate structured JSON output"""
        schema_prompt = f"""
{prompt}

You must respond with valid JSON matching this schema:
{json.dumps(output_schema, indent=2)}

Respond ONLY with the JSON, no other text.
"""

        response = await self.generate(schema_prompt, system_prompt)
        content = response["content"]

        # Extract JSON from response
        try:
            # Try to find JSON in code blocks
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content.strip()

            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse structured output",
                error_type=type(e).__name__,
                content_length=len(content or ""),
            )
            raise


class OpenAIClient:
    """Client for OpenAI API (GPT-4, Codex)"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        from openai import AsyncOpenAI

        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            timeout=60.0,
            max_retries=0,
        )
        self.model = model or settings.OPENAI_MODEL
        self.provider = "openai"
        self.max_context = 128000

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
        """Generate using OpenAI"""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        params = {
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

        response = await self.client.chat.completions.create(**params)

        message = response.choices[0].message

        result = {"content": message.content or "", "raw_response": response}

        if message.tool_calls:
            result["function_calls"] = [
                {"name": tc.function.name, "args": json.loads(tc.function.arguments)}
                for tc in message.tool_calls
            ]

        return result

    async def generate_code(self, prompt: str, language: str = "python") -> str:
        """Generate code using Codex model"""
        settings = get_settings()

        code_prompt = f"""Generate {language} code for the following:

{prompt}

Provide only the code, no explanations."""

        response = await self.client.chat.completions.create(
            model=settings.CODEX_MODEL,
            messages=[{"role": "user", "content": code_prompt}],
            temperature=0.2,
            max_tokens=4000,
        )

        return response.choices[0].message.content


class MistralClient:
    """Client for Mistral API using official SDK"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        try:
            from mistralai import Mistral
        except ImportError:
            # Fallback for older SDK versions
            try:
                from mistralai.client import MistralClient as Mistral
            except ImportError:
                logger.error("Mistral library not found or incompatible version")
                raise

        settings = get_settings()
        self.api_key = api_key or settings.MISTRAL_API_KEY
        self.model = model or settings.MISTRAL_MODEL
        self.client = Mistral(api_key=self.api_key)
        self.provider = "mistral"
        self.max_context = 32000

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
        """Generate using Mistral SDK"""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        params = {
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

        response = await self.client.chat.complete_async(**params)

        message = response.choices[0].message

        result = {"content": message.content or "", "raw_response": response}

        if message.tool_calls:
            result["function_calls"] = [
                {
                    "name": tc.function.name,
                    "args": (
                        json.loads(tc.function.arguments)
                        if isinstance(tc.function.arguments, str)
                        else tc.function.arguments
                    ),
                }
                for tc in message.tool_calls
            ]

        return result
