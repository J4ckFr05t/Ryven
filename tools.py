"""
Tool definitions and implementations for Jarvis.
Includes filesystem operations, web search (DuckDuckGo + Tavily).
"""

import os
import re
import logging
from pathlib import Path

from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

ALLOWED_DIRS = []


def init_allowed_dirs():
    global ALLOWED_DIRS
    dirs_str = os.getenv("ALLOWED_DIRECTORIES", "")
    ALLOWED_DIRS = [d.strip() for d in dirs_str.split(",") if d.strip()]
    logger.info(f"Allowed directories: {ALLOWED_DIRS}")


def _validate_path(path: str) -> str:
    """Ensure path is within allowed directories. Returns resolved path."""
    resolved = str(Path(path).resolve())
    for allowed in ALLOWED_DIRS:
        if resolved.startswith(str(Path(allowed).resolve())):
            return resolved
    raise PermissionError(f"Access denied: {path} is not within allowed directories")


# ── Tool Definitions (OpenAI function-calling schema) ──────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Use this to examine code, configs, docs, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and subdirectories in a directory. Returns names with [DIR] or [FILE] prefix and file sizes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the directory"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "search_files",
        "description": "Search for a text pattern across files in a directory (recursive). Returns matching lines with file paths and line numbers.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to search in"},
                "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                "file_glob": {"type": "string", "description": "Optional glob to filter files, e.g. '*.py'. Defaults to all files."}
            },
            "required": ["path", "pattern"]
        }
    },
    {
        "name": "get_file_info",
        "description": "Get metadata about a file: size, type, last modified time.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for information. Returns top results with titles, URLs, and snippets.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "description": "Number of results (default 5, max 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "tavily_search",
        "description": "Deep web search using Tavily for comprehensive, AI-optimized results. Better for research questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "search_depth": {"type": "string", "description": "'basic' or 'advanced' (default 'basic')"}
            },
            "required": ["query"]
        }
    }
]


# ── Tool Implementations ───────────────────────────────────────────────────

async def read_file(path: str) -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists():
            return f"Error: File not found: {path}"
        if not p.is_file():
            return f"Error: Not a file: {path}"
        size = p.stat().st_size
        if size > 500_000:
            return f"Error: File too large ({size:,} bytes). Max 500KB."
        return p.read_text(errors="replace")
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error reading file: {e}"


async def list_directory(path: str) -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists():
            return f"Error: Directory not found: {path}"
        if not p.is_dir():
            return f"Error: Not a directory: {path}"

        entries = []
        for item in sorted(p.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                count = sum(1 for _ in item.rglob("*") if _.is_file())
                entries.append(f"[DIR]  {item.name}/ ({count} files)")
            else:
                size = item.stat().st_size
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024*1024):.1f} MB"
                entries.append(f"[FILE] {item.name} ({size_str})")

        if not entries:
            return f"Directory is empty: {path}"
        return "\n".join(entries)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing directory: {e}"


async def search_files(path: str, pattern: str, file_glob: str = "*") -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists() or not p.is_dir():
            return f"Error: Invalid directory: {path}"

        matches = []
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        for filepath in p.rglob(file_glob):
            if not filepath.is_file():
                continue
            if filepath.stat().st_size > 500_000:
                continue
            if any(part.startswith(".") for part in filepath.parts):
                continue
            try:
                content = filepath.read_text(errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        rel = filepath.relative_to(p)
                        matches.append(f"{rel}:{i}: {line.strip()}")
                        if len(matches) >= 50:
                            break
            except Exception:
                continue
            if len(matches) >= 50:
                break

        if not matches:
            return f"No matches found for '{pattern}' in {path}"
        header = f"Found {len(matches)} matches for '{pattern}':\n"
        return header + "\n".join(matches)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error searching: {e}"


async def get_file_info(path: str) -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists():
            return f"Error: Not found: {path}"
        stat = p.stat()
        import datetime
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
        return (
            f"Path: {safe_path}\n"
            f"Type: {'directory' if p.is_dir() else 'file'}\n"
            f"Size: {stat.st_size:,} bytes\n"
            f"Modified: {mtime}\n"
            f"Suffix: {p.suffix or 'none'}"
        )
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"


async def web_search(query: str, num_results: int = 5) -> str:
    try:
        num_results = min(max(num_results, 1), 10)
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
        if not results:
            return f"No results found for: {query}"
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"{i}. **{r.get('title', 'N/A')}**\n   {r.get('href', '')}\n   {r.get('body', '')}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error searching web: {e}"


async def tavily_search(query: str, search_depth: str = "basic") -> str:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Tavily API key not configured. Using DuckDuckGo instead.\n\n" + await web_search(query)
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, search_depth=search_depth, max_results=5)
        output = []
        if response.get("answer"):
            output.append(f"**Summary:** {response['answer']}\n")
        for i, r in enumerate(response.get("results", []), 1):
            output.append(f"{i}. **{r.get('title', 'N/A')}**\n   {r.get('url', '')}\n   {r.get('content', '')}")
        return "\n\n".join(output) if output else f"No results for: {query}"
    except Exception as e:
        return f"Tavily error: {e}. Falling back to DuckDuckGo.\n\n" + await web_search(query)


# ── Tool Router ────────────────────────────────────────────────────────────

TOOL_MAP = {
    "read_file": read_file,
    "list_directory": list_directory,
    "search_files": search_files,
    "get_file_info": get_file_info,
    "web_search": web_search,
    "tavily_search": tavily_search,
}


async def execute_tool(name: str, arguments: dict) -> str:
    func = TOOL_MAP.get(name)
    if not func:
        return f"Error: Unknown tool '{name}'"
    try:
        return await func(**arguments)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return f"Error executing {name}: {e}"
