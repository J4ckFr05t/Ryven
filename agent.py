"""
Ryven Agent — agentic loop with tool calling.
Takes user messages, reasons with LLM, calls tools, and loops until done.
Supports both local tools and MCP server tools (GitHub, etc.).
"""

import asyncio
import logging
from llm_providers import get_provider, LLMResponse
from tools import TOOL_DEFINITIONS, PROJECT_KB_TOOL_SCHEMA, execute_tool
from mcp_manager import mcp_manager
from project_context import current_project_id

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Ryven, a highly capable personal AI assistant. You help your user understand their projects, codebase, and answer questions with precision.

## Your Capabilities
- **Read files** from the user's project directories to understand code, configs, and documentation
- **Read structured tables** (`read_table`) for CSV/TSV/JSON/Excel previews
- **List directories** to explore project structure
- **Search files** for specific patterns, functions, or text across codebases
- **Web search** (`web_search`) always aggregates Gemini Google Search grounding (if `GEMINI_API_KEY` is set), DuckDuckGo, and Tavily when configured — independent of the chat model
- **Deep web search** (`tavily_search`) uses Tavily first, with DuckDuckGo fallback
- **System date & time** — `get_system_datetime` reads the server's real clock (never guess or web-search "what time is it")
- **Weather** — `get_weather` uses the Open-Meteo API from coordinates or a place name
- **GitHub** — browse repositories, issues, pull requests, search code, and more via GitHub MCP

## Guidelines
1. When asked about code or projects, USE YOUR TOOLS to read actual files. Don't guess.
2. When exploring a codebase, start by listing the root directory to understand the structure.
3. For the current date, time, or "today", call `get_system_datetime` (optionally with an IANA timezone). Do not use web search for that.
4. For queries with relative date words (e.g., "yesterday", "today", "last week"), fetch `get_system_datetime` first unless the user already gave a concrete date.
5. For current weather or forecasts, call `get_weather` with a `location` or lat/lon — do not rely on web search for live conditions.
6. For web searches, use `web_search` for a combined snapshot (Gemini + DDG + Tavily); use `tavily_search` when you want Tavily-first research with DDG fallback only.
7. For GitHub questions, use the GitHub tools (prefixed with `github__`) to get real data.
8. Format your responses in clean Markdown with code blocks, headers, and lists.
9. Be direct and helpful. If you don't know something, say so and offer to search.
10. When analyzing code, provide concrete insights — don't just describe what you see.
11. If a tool returns an error, explain it clearly and suggest alternatives.
12. When reporting counts (files, matches, etc.), verify exact numbers with tools and clearly state when results are partial/paginated.
13. For GitHub repositories, prefer full `owner/repo` format. If the user gives only a repo name, first search/disambiguate the repository and confirm the exact full name before declaring that it does not exist.
14. Do not claim a branch/repo is missing unless you verified with a direct tool call for that exact repository. If results are partial (pagination), explicitly say so and continue fetching more pages before concluding.
15. For lists (branches, files, PRs, etc.), never provide only a sample unless the user asked for a sample. Fetch all pages (or say exactly which page/limit is shown), and include total counts when available.
16. Do not call `read_file` unless it is required to answer the user's request. For "what files do I have" style questions, use directory/file listing tools only.
17. Some files are not meaningfully readable as text (images, audio, video, archives, many binaries). Avoid `read_file` on those unless the user explicitly asks; prefer listing/metadata tools.
18. For tabular/structured data (CSV, TSV, JSON, JSONL, Excel), prefer `read_table` instead of `read_file`.

## Personality
You're smart, efficient, and slightly witty — professional but personable.
"""

MAX_TOOL_ITERATIONS = 15
MAX_LLM_RETRIES = 3
MAX_AUTO_CONTINUES = 3
CONTINUE_PROMPT = "Continue exactly from where you stopped. Do not repeat prior text."


def get_all_tools(project_id: str | None = None) -> list[dict]:
    """Combine local tools with MCP tools. Adds KB search when a project is active."""
    all_tools = list(TOOL_DEFINITIONS)
    if project_id:
        all_tools.append(PROJECT_KB_TOOL_SCHEMA)
    all_tools.extend(mcp_manager.get_all_tools())
    return all_tools


async def execute_any_tool(name: str, arguments: dict) -> str:
    """Route tool call to local handler or MCP server."""
    if mcp_manager.is_mcp_tool(name):
        return await mcp_manager.call_tool(name, arguments)
    else:
        return await execute_tool(name, arguments)


def _error_text(exc: Exception) -> str:
    return str(exc).lower()


def _is_retryable_llm_error(exc: Exception) -> bool:
    msg = _error_text(exc)
    retryable_markers = (
        "timeout",
        "timed out",
        "429",
        "rate limit",
        "temporarily",
        "temporary",
        "connection",
        "network",
        "503",
        "502",
        "504",
        "overloaded",
        "try again",
    )
    return any(marker in msg for marker in retryable_markers)


def _fallback_models(primary_model: str) -> list[str]:
    if primary_model.startswith("openrouter:"):
        return ["gemini:gemini-2.5-flash", "openai:gpt-4.1"]
    if primary_model.startswith("gemini:") or primary_model == "gemini":
        return ["openai:gpt-4.1", "openrouter:auto"]
    if primary_model.startswith("openai:") or primary_model == "openai":
        return ["gemini:gemini-2.5-flash", "openrouter:auto"]
    return ["gemini:gemini-2.5-flash", "openai:gpt-4.1"]


async def _chat_with_retries(provider, messages: list[dict], all_tools: list[dict], send_event):
    last_error = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            return await provider.chat(messages, all_tools)
        except Exception as e:
            last_error = e
            if attempt == MAX_LLM_RETRIES or not _is_retryable_llm_error(e):
                break
            await send_event("status", {"status": f"retrying llm ({attempt}/{MAX_LLM_RETRIES})"})
            backoff_seconds = (0.8 * (2 ** (attempt - 1))) + 0.05 * attempt
            await asyncio.sleep(backoff_seconds)
    raise last_error


async def run_agent(
    user_message: str,
    model: str,
    conversation_history: list[dict],
    send_event,
    extra_system_suffix: str | None = None,
    project_id: str | None = None,
):
    """
    Run the agent loop.

    Args:
        user_message: The user's message
        model: "openai" or "gemini"
        conversation_history: Previous messages in OpenAI format
        send_event: async callable to send events to the UI
        extra_system_suffix: Optional project KB / repo context appended to the system prompt
        project_id: When set, enables project-scoped KB tool and tool routing
    """
    try:
        provider_model = model
        provider = get_provider(provider_model)
    except ValueError as e:
        await send_event("error", {"message": str(e)})
        return

    await send_event("status", {"status": "thinking"})

    system_content = SYSTEM_PROMPT
    if extra_system_suffix:
        system_content = (
            SYSTEM_PROMPT
            + "\n\n## Project context\n"
            + extra_system_suffix.strip()
            + "\n\nWhen numbered sources [1], [2], … appear in the project context above, "
            "cite them in your answer as [1], [2], etc. when you use that information."
        )

    messages = [{"role": "system", "content": system_content}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    ctx_token = current_project_id.set(project_id) if project_id else None
    all_tools = get_all_tools(project_id=project_id)

    try:
        auto_continue_count = 0
        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response: LLMResponse = await _chat_with_retries(provider, messages, all_tools, send_event)
            except Exception as e:
                fallback_error = None
                for fallback_model in _fallback_models(provider_model):
                    if fallback_model == provider_model:
                        continue
                    try:
                        await send_event("status", {"status": f"switching model to {fallback_model}"})
                        provider = get_provider(fallback_model)
                        provider_model = fallback_model
                        response = await _chat_with_retries(provider, messages, all_tools, send_event)
                        break
                    except Exception as fallback_exc:
                        fallback_error = fallback_exc
                        continue
                else:
                    logger.error(f"LLM call failed: {e}")
                    if fallback_error:
                        logger.error(f"Fallback LLM call failed: {fallback_error}")
                        await send_event("error", {"message": f"LLM error: {e}. Fallback error: {fallback_error}"})
                    else:
                        await send_event("error", {"message": f"LLM error: {e}"})
                    return

            if response.tool_calls:
                assistant_msg = provider.format_assistant_tool_calls(response.content, response.tool_calls)
                messages.append(assistant_msg)

                if response.content:
                    await send_event("content", {"text": response.content})

                for tc in response.tool_calls:
                    await send_event("tool_call", {
                        "id": tc.id,
                        "name": tc.name,
                        "args": tc.arguments
                    })

                    result = await execute_any_tool(tc.name, tc.arguments)

                    await send_event("tool_result", {
                        "id": tc.id,
                        "name": tc.name,
                        "result": result,
                        "success": not str(result).startswith("Error")
                    })

                    tool_msg = provider.format_tool_result(tc.id, tc.name, result)
                    messages.append(tool_msg)

                await send_event("status", {"status": "thinking"})
                continue

            if response.is_truncated and auto_continue_count < MAX_AUTO_CONTINUES:
                partial = response.content or ""
                if partial:
                    await send_event("content", {"text": partial})
                messages.append({"role": "assistant", "content": partial})
                messages.append({"role": "user", "content": CONTINUE_PROMPT})
                auto_continue_count += 1
                await send_event("status", {"status": "continuing response"})
                continue

            # No tool calls — final response
            final_content = response.content or "I wasn't able to generate a response. Please try again."
            await send_event("response", {"content": final_content})

            return {
                "user_message": {"role": "user", "content": user_message},
                "assistant_message": {"role": "assistant", "content": final_content}
            }

        await send_event("response", {
            "content": "I've reached my maximum number of tool calls for this question. Here's what I found so far — could you refine your question?"
        })
        return {
            "user_message": {"role": "user", "content": user_message},
            "assistant_message": {"role": "assistant", "content": "(Max tool iterations reached)"}
        }
    finally:
        if ctx_token is not None:
            current_project_id.reset(ctx_token)
