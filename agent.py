"""
Ryven Agent — agentic loop with tool calling.
Takes user messages, reasons with LLM, calls tools, and loops until done.
Supports both local tools and MCP server tools (GitHub, etc.).
"""

import logging
from llm_providers import get_provider, LLMResponse
from tools import TOOL_DEFINITIONS, execute_tool
from mcp_manager import mcp_manager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Ryven, a highly capable personal AI assistant. You help your user understand their projects, codebase, and answer questions with precision.

## Your Capabilities
- **Read files** from the user's project directories to understand code, configs, and documentation
- **List directories** to explore project structure
- **Search files** for specific patterns, functions, or text across codebases
- **Web search** (DuckDuckGo) for quick lookups and general information
- **Deep web search** (Tavily) for thorough research and comprehensive answers
- **GitHub** — browse repositories, issues, pull requests, search code, and more via GitHub MCP

## Guidelines
1. When asked about code or projects, USE YOUR TOOLS to read actual files. Don't guess.
2. When exploring a codebase, start by listing the root directory to understand the structure.
3. For web searches, prefer `tavily_search` for research questions and `web_search` for quick lookups.
4. For GitHub questions, use the GitHub tools (prefixed with `github__`) to get real data.
5. Format your responses in clean Markdown with code blocks, headers, and lists.
6. Be direct and helpful. If you don't know something, say so and offer to search.
7. When analyzing code, provide concrete insights — don't just describe what you see.
8. If a tool returns an error, explain it clearly and suggest alternatives.

## Personality
You're smart, efficient, and slightly witty — professional but personable.
"""

MAX_TOOL_ITERATIONS = 15


def get_all_tools() -> list[dict]:
    """Combine local tools with MCP tools."""
    all_tools = list(TOOL_DEFINITIONS)
    all_tools.extend(mcp_manager.get_all_tools())
    return all_tools


async def execute_any_tool(name: str, arguments: dict) -> str:
    """Route tool call to local handler or MCP server."""
    if mcp_manager.is_mcp_tool(name):
        return await mcp_manager.call_tool(name, arguments)
    else:
        return await execute_tool(name, arguments)


async def run_agent(user_message: str, model: str, conversation_history: list[dict], send_event):
    """
    Run the agent loop.

    Args:
        user_message: The user's message
        model: "openai" or "gemini"
        conversation_history: Previous messages in OpenAI format
        send_event: async callable to send events to the UI
    """
    try:
        provider = get_provider(model)
    except ValueError as e:
        await send_event("error", {"message": str(e)})
        return

    await send_event("status", {"status": "thinking"})

    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    all_tools = get_all_tools()

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

                if len(result) > 15000:
                    result = result[:15000] + f"\n\n... (truncated, {len(result):,} chars total)"

                await send_event("tool_result", {
                    "id": tc.id,
                    "name": tc.name,
                    "result": result,
                    "success": not result.startswith("Error")
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
