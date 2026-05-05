"""
Ryven Agent — agentic loop with tool calling.
Takes user messages, reasons with LLM, calls tools, and loops until done.
Supports both local tools and MCP server tools (GitHub, etc.).
"""

import logging
from llm_providers import get_provider, LLMResponse
from tools import TOOL_DEFINITIONS, PROJECT_KB_TOOL_SCHEMA, execute_tool
from mcp_manager import mcp_manager
from project_context import current_project_id

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Ryven, a highly capable personal AI assistant. You help your user understand their projects, codebase, and answer questions with precision.

## Your Capabilities
- **Read files** from the user's project directories to understand code, configs, and documentation
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

## Personality
You're smart, efficient, and slightly witty — professional but personable.
"""

MAX_TOOL_ITERATIONS = 15


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
        provider = get_provider(model)
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
        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response: LLMResponse = await provider.chat(messages, all_tools)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
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
