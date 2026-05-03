"""
Unified LLM provider interface for OpenAI, Gemini, and OpenRouter.
"""

import json
import os
import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class OpenAIProvider:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str = "gpt-4.1", extra_headers: dict | None = None):
        self.client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url,
            default_headers=extra_headers,
        )
        self.model = model

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        result = LLMResponse()
        if choice.message.content:
            result.content = choice.message.content
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                result.tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments)
                ))
        return result

    def format_assistant_tool_calls(self, content, tool_calls: list[ToolCall]) -> dict:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}
                }
                for tc in tool_calls
            ]
        }

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result}


def _sanitize_schema(schema):
    """Recursively strip fields that Gemini doesn't support in tool schemas."""
    if not isinstance(schema, dict):
        return schema

    # Fields that Gemini's API doesn't recognize
    unsupported = {"additionalProperties", "additional_properties", "$schema", "default", "examples"}
    cleaned = {}
    for key, value in schema.items():
        if key in unsupported:
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [_sanitize_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value
    return cleaned


class GeminiProvider:
    def __init__(self, model: str | None = None):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = (model or "gemini-2.5-flash").strip()

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        contents, system_instruction = self._convert_messages(messages)

        config_kwargs = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if tools:
            config_kwargs["tools"] = [genai_types.Tool(
                function_declarations=[
                    genai_types.FunctionDeclaration(
                        name=t["name"],
                        description=t["description"],
                        parameters=_sanitize_schema(t.get("parameters"))
                    ) for t in tools
                ]
            )]

        config = genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        response = await self.client.aio.models.generate_content(
            model=self.model, contents=contents, config=config
        )

        result = LLMResponse()
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.text:
                    result.content = (result.content or "") + part.text
                if part.function_call:
                    args = dict(part.function_call.args) if part.function_call.args else {}
                    result.tool_calls.append(ToolCall(
                        id=f"call_{part.function_call.name}_{id(part)}",
                        name=part.function_call.name,
                        arguments=args
                    ))
        return result

    def _convert_messages(self, messages: list[dict]):
        contents = []
        system_instruction = None

        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_instruction = msg["content"]
            elif role == "user":
                contents.append(genai_types.Content(
                    role="user", parts=[genai_types.Part(text=msg["content"])]
                ))
            elif role == "assistant":
                parts = []
                if msg.get("content"):
                    parts.append(genai_types.Part(text=msg["content"]))
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fn = tc["function"]
                        parts.append(genai_types.Part(
                            function_call=genai_types.FunctionCall(
                                name=fn["name"],
                                args=json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                            )
                        ))
                if parts:
                    contents.append(genai_types.Content(role="model", parts=parts))
            elif role == "tool":
                name = msg.get("name", "tool")
                contents.append(genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=name, response={"result": msg["content"]}
                        )
                    )]
                ))
        return contents, system_instruction

    def format_assistant_tool_calls(self, content, tool_calls: list[ToolCall]) -> dict:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}
                }
                for tc in tool_calls
            ]
        }

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": result}


# ── OpenRouter free-model catalog ──────────────────────────────────────────
# These are popular free models on OpenRouter. The default "openrouter/free"
# auto-router picks the best available free model for each request.
OPENROUTER_FREE_MODELS = {
    "auto":        "openrouter/free",
    "gemma4":      "google/gemma-4-31b-it:free",
    "nemotron":    "nvidia/nemotron-3-super-120b-a12b:free",
    "gpt-oss":     "openai/gpt-oss-120b:free",
    "minimax":     "minimax/minimax-m2.5:free",
    "hy3":         "tencent/hy3-preview:free",
    "laguna":      "poolside/laguna-m.1:free",
    "glm":         "z-ai/glm-4.5-air:free",
    "ling":        "inclusionai/ling-2.6-1t:free",
    "nano-9b":     "nvidia/nemotron-nano-9b-v2:free",
}


def get_provider(name: str = "openai"):
    # ── OpenRouter ──────────────────────────────────────────────────────
    if name.startswith("openrouter"):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in .env")

        # Support "openrouter" (auto) or "openrouter:<model>" syntax
        parts = name.split(":", 1)
        model_key = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "auto"

        # Look up alias first, then use raw model ID
        model_id = OPENROUTER_FREE_MODELS.get(model_key, model_key)

        logger.info(f"Using OpenRouter model: {model_id}")
        return OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            model=model_id,
            extra_headers={"X-OpenRouter-Title": "Ryven"},
        )

    # ── Gemini ──────────────────────────────────────────────────────────
    if name == "gemini" or name.startswith("gemini:"):
        if not os.getenv("GEMINI_API_KEY"):
            raise ValueError("GEMINI_API_KEY not set in .env")
        parts = name.split(":", 1)
        gem_model = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "gemini-2.5-flash"
        return GeminiProvider(model=gem_model)

    # ── OpenAI ──────────────────────────────────────────────────────────
    if name == "openai" or name.startswith("openai:"):
        if not os.getenv("OPENAI_API_KEY"):
            if os.getenv("GEMINI_API_KEY"):
                logger.warning("OpenAI key missing, falling back to Gemini")
                return GeminiProvider()
            raise ValueError("No API keys configured. Set OPENAI_API_KEY or GEMINI_API_KEY in .env")
        parts = name.split(":", 1)
        oa_model = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "gpt-4.1"
        return OpenAIProvider(model=oa_model)

    # Legacy plain "openai" / unknown → OpenAI default model
    if not os.getenv("OPENAI_API_KEY"):
        if os.getenv("GEMINI_API_KEY"):
            logger.warning("OpenAI key missing, falling back to Gemini")
            return GeminiProvider()
        raise ValueError("No API keys configured. Set OPENAI_API_KEY or GEMINI_API_KEY in .env")
    return OpenAIProvider(model="gpt-4.1")
