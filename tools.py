"""
Tool definitions and implementations for Ryven.
Includes filesystem operations, web search (Gemini Google Search grounding, DuckDuckGo, Tavily).
"""

import os
import re
import logging
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

RELATIVE_DATE_PATTERN = re.compile(
    r"\b("
    r"today|yesterday|tomorrow|tonight|last\s+night|this\s+morning|this\s+afternoon|this\s+evening|"
    r"currently|right\s+now|now|latest|recent|as\s+of|"
    r"last\s+week|this\s+week|next\s+week|last\s+month|this\s+month|next\s+month|"
    r"last\s+year|this\s+year|next\s+year"
    r")\b",
    re.IGNORECASE,
)


def _anchor_query_with_current_date(query: str) -> str:
    """
    Add a concrete date/time anchor when the query uses relative time terms.
    This helps search providers resolve terms like "yesterday" consistently.
    """
    if not RELATIVE_DATE_PATTERN.search(query or ""):
        return query
    now = datetime.now().astimezone()
    tz_name = str(now.tzinfo) if now.tzinfo else "local"
    return (
        f"{query}\n\n"
        f"Date context: Current local date is {now.strftime('%Y-%m-%d')} "
        f"and local time is {now.strftime('%H:%M:%S')} ({tz_name}). "
        "Resolve all relative terms (e.g., yesterday/today/latest) using this date context."
    )


async def search_project_knowledge(query: str) -> str:
    from knowledge import search_project_knowledge_tool

    return await search_project_knowledge_tool(query)

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
        "name": "count_files",
        "description": "Count files in a directory (recursive) using an optional glob pattern. Returns an exact count.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to count files in"},
                "file_glob": {"type": "string", "description": "Optional glob filter, e.g. '*.json' or '**/*.json'. Defaults to '*'"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_files",
        "description": "List files in a directory (recursive) with pagination. Returns structured JSON with total_count, returned_count, and has_more.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to list files from"},
                "file_glob": {"type": "string", "description": "Optional glob filter, e.g. '*.json' or '**/*.json'. Defaults to '*'"},
                "offset": {"type": "integer", "description": "Pagination offset (default 0)"},
                "limit": {"type": "integer", "description": "Max files to return (default 200, max 1000)"}
            },
            "required": ["path"]
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
        "description": (
            "Search the web: combines Gemini (Google Search grounding, if GEMINI_API_KEY is set), "
            "DuckDuckGo link snippets, and Tavily (if TAVILY_API_KEY is set). Independent of the chat model."
        ),
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
    },
    {
        "name": "get_system_datetime",
        "description": (
            "Get the current date and time from the Ryven server system clock. "
            "Use this for 'what time is it', today's date, or any question needing real current time — "
            "do not use web search for that. Optional IANA timezone (e.g. America/New_York); omit for the server's local timezone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Optional IANA timezone name, e.g. Europe/London. If omitted, uses the server's local timezone.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_weather",
        "description": (
            "Get current weather conditions from the Open-Meteo API (live forecast data). "
            "Provide either latitude and longitude, or a location name (city/region) to geocode. "
            "Use for weather questions instead of web search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "Latitude in decimal degrees (e.g. 40.71)"},
                "longitude": {"type": "number", "description": "Longitude in decimal degrees (e.g. -74.01)"},
                "location": {
                    "type": "string",
                    "description": "Place name to look up (e.g. 'Tokyo', 'Austin TX') if lat/lon are not known",
                },
            },
            "required": [],
        },
    },
]

# Appended dynamically per chat when a project is active (see agent.get_all_tools).
PROJECT_KB_TOOL_SCHEMA = {
    "name": "search_project_knowledge",
    "description": (
        "Search the active project's knowledge base plus any global knowledge (notes, uploads, snippets, repo summaries). "
        "Use when the user asks about stored documents or saved context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for in the knowledge base"},
        },
        "required": ["query"],
    },
}


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


def _iter_visible_files(root: Path, file_glob: str):
    for filepath in root.rglob(file_glob):
        if not filepath.is_file():
            continue
        if any(part.startswith(".") for part in filepath.parts):
            continue
        yield filepath


async def count_files(path: str, file_glob: str = "*") -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists() or not p.is_dir():
            return f"Error: Invalid directory: {path}"

        total = sum(1 for _ in _iter_visible_files(p, file_glob))
        return str(total)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error counting files: {e}"


async def list_files(path: str, file_glob: str = "*", offset: int = 0, limit: int = 200) -> str:
    try:
        safe_path = _validate_path(path)
        p = Path(safe_path)
        if not p.exists() or not p.is_dir():
            return f"Error: Invalid directory: {path}"

        offset = max(offset, 0)
        limit = min(max(limit, 1), 1000)

        all_files = sorted(
            (fp.relative_to(p).as_posix() for fp in _iter_visible_files(p, file_glob)),
            key=str.lower
        )

        total_count = len(all_files)
        chunk = all_files[offset: offset + limit]
        next_offset = offset + len(chunk)
        payload = {
            "path": safe_path,
            "file_glob": file_glob,
            "total_count": total_count,
            "offset": offset,
            "limit": limit,
            "returned_count": len(chunk),
            "has_more": next_offset < total_count,
            "next_offset": next_offset if next_offset < total_count else None,
            "files": chunk,
        }
        return json.dumps(payload, indent=2)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing files: {e}"


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


async def _duckduckgo_markdown(query: str, num_results: int = 5) -> str:
    num_results = min(max(num_results, 1), 10)
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=num_results))
    if not results:
        return f"No DuckDuckGo results for: {query}"
    output = []
    for i, r in enumerate(results, 1):
        output.append(f"{i}. **{r.get('title', 'N/A')}**\n   {r.get('href', '')}\n   {r.get('body', '')}")
    return "\n\n".join(output)


async def _tavily_markdown(query: str, search_depth: str = "basic") -> str | None:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, search_depth=search_depth, max_results=5)
        output = []
        if response.get("answer"):
            output.append(f"**Summary:** {response['answer']}\n")
        for i, r in enumerate(response.get("results", []), 1):
            output.append(f"{i}. **{r.get('title', 'N/A')}**\n   {r.get('url', '')}\n   {r.get('content', '')}")
        return "\n\n".join(output) if output else None
    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return None


async def _gemini_google_search_grounding(query: str) -> str | None:
    """Uses Gemini + Google Search tool; separate from the user's selected chat model."""
    if not os.getenv("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types as genai_types

        model = (os.getenv("GEMINI_WEB_SEARCH_MODEL") or "gemini-2.5-flash").strip()
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
        )
        prompt = (
            "Answer using web search. Be concise and factual. "
            "End with a short 'Sources:' line listing the most important URLs you relied on.\n\n"
            f"Query: {query}"
        )
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        lines = []
        text = getattr(response, "text", None) or ""
        if text.strip():
            lines.append(text.strip())

        gm = None
        try:
            if response.candidates:
                gm = getattr(response.candidates[0], "grounding_metadata", None)
        except (IndexError, AttributeError):
            pass
        if gm:
            chunks = getattr(gm, "grounding_chunks", None) or []
            urls = []
            for ch in chunks:
                web = getattr(ch, "web", None)
                if not web:
                    continue
                uri = getattr(web, "uri", None) or ""
                title = getattr(web, "title", None) or ""
                if uri:
                    urls.append(f"- {title}: {uri}" if title else f"- {uri}")
            if urls:
                lines.append("**Grounding sources (API):**\n" + "\n".join(urls[:12]))
        return "\n\n".join(lines) if lines else None
    except Exception as e:
        logger.warning("Gemini Google Search grounding failed: %s", e)
        return None


async def web_search(query: str, num_results: int = 5) -> str:
    sections: list[str] = []
    anchored_query = _anchor_query_with_current_date(query)

    gemini_block = await _gemini_google_search_grounding(anchored_query)
    if gemini_block:
        sections.append("### Gemini (Google Search grounding)\n\n" + gemini_block)

    try:
        ddg_block = await _duckduckgo_markdown(anchored_query, num_results)
    except Exception as e:
        ddg_block = f"DuckDuckGo error: {e}"
    sections.append("### DuckDuckGo\n\n" + ddg_block)

    tavily_block = await _tavily_markdown(anchored_query)
    if tavily_block:
        sections.append("### Tavily\n\n" + tavily_block)
    elif os.getenv("TAVILY_API_KEY"):
        sections.append("### Tavily\n\n*(No Tavily results or request failed; use DuckDuckGo / Gemini sections above.)*")

    body = "\n\n---\n\n".join(sections)
    if (
        not gemini_block
        and "No DuckDuckGo results" in ddg_block
        and not tavily_block
        and not os.getenv("TAVILY_API_KEY")
    ):
        return f"No web results for: {query}"
    return body


async def tavily_search(query: str, search_depth: str = "basic") -> str:
    anchored_query = _anchor_query_with_current_date(query)
    body = await _tavily_markdown(anchored_query, search_depth=search_depth)
    if body:
        return body
    try:
        ddg = await _duckduckgo_markdown(anchored_query)
    except Exception as e:
        return f"Tavily unavailable and DuckDuckGo error: {e}"
    if not os.getenv("TAVILY_API_KEY"):
        return "Tavily API key not configured. Using DuckDuckGo instead.\n\n" + ddg
    return f"Tavily error or empty results. Falling back to DuckDuckGo.\n\n" + ddg


def _wmo_weather_label(code: int | None) -> str:
    if code is None:
        return "unknown"
    labels = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return labels.get(int(code), f"Code {code}")


async def get_system_datetime(timezone: str | None = None) -> str:
    try:
        if timezone and str(timezone).strip():
            tz = ZoneInfo(str(timezone).strip())
            now = datetime.now(tz)
            tz_name = str(timezone).strip()
        else:
            now = datetime.now().astimezone()
            tz_name = str(now.tzinfo) if now.tzinfo else "local"
        utc = datetime.now(ZoneInfo("UTC"))
        return (
            f"**System time ({tz_name})**\n"
            f"- ISO local: {now.isoformat(timespec='seconds')}\n"
            f"- Date: {now.strftime('%A, %Y-%m-%d')}\n"
            f"- Time: {now.strftime('%H:%M:%S')} (24h) / {now.strftime('%I:%M %p')} (12h)\n"
            f"- UTC (reference): {utc.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"- Unix timestamp (local instant): {int(now.timestamp())}"
        )
    except ZoneInfoNotFoundError:
        return f"Error: Unknown IANA timezone '{timezone}'. Use a name like 'America/Los_Angeles' or 'UTC'."


async def get_weather(
    latitude: float | None = None,
    longitude: float | None = None,
    location: str | None = None,
) -> str:
    if (latitude is not None) ^ (longitude is not None):
        return "Error: Pass both `latitude` and `longitude`, or use `location` alone."

    lat_f: float | None = float(latitude) if latitude is not None else None
    lon_f: float | None = float(longitude) if longitude is not None else None
    loc = (location or "").strip()

    if (lat_f is None or lon_f is None) and loc:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                g = await client.get(geo_url, params={"name": loc, "count": 1, "language": "en", "format": "json"})
                g.raise_for_status()
                results = g.json().get("results") or []
                if not results:
                    return f"No geocoding results for location: {loc!r}. Try a different spelling or pass latitude/longitude."
                r0 = results[0]
                lat_f = float(r0["latitude"])
                lon_f = float(r0["longitude"])
                place = r0.get("name", loc)
                admin = r0.get("admin1")
                country = r0.get("country")
                place_line = ", ".join(p for p in (place, admin, country) if p)
        except httpx.HTTPError as e:
            return f"Weather geocoding request failed: {e}"

    if lat_f is None or lon_f is None:
        return (
            "Error: Provide either `location` (place name) or both `latitude` and `longitude` "
            "(decimal degrees)."
        )

    forecast_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat_f,
        "longitude": lon_f,
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "surface_pressure",
            ]
        ),
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(forecast_url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return f"Weather API request failed: {e}"

    cur = data.get("current") or {}
    tz_used = data.get("timezone", "unknown")
    units = data.get("current_units") or {}

    t = cur.get("temperature_2m")
    t_unit = units.get("temperature_2m", "°C")
    feels = cur.get("apparent_temperature")
    rh = cur.get("relative_humidity_2m")
    code = cur.get("weather_code")
    wind = cur.get("wind_speed_10m")
    wind_u = units.get("wind_speed_10m", "km/h")
    wdir = cur.get("wind_direction_10m")
    press = cur.get("surface_pressure")
    p_u = units.get("surface_pressure", "hPa")
    when = cur.get("time", "")

    lines = [
        f"**Weather** ({lat_f:.4f}, {lon_f:.4f}) — timezone: {tz_used}",
        f"- As of (API): {when}",
        f"- Conditions: {_wmo_weather_label(code)}",
        f"- Temperature: {t}{t_unit}" + (f" (feels like {feels}{t_unit})" if feels is not None else ""),
    ]
    if rh is not None:
        lines.append(f"- Relative humidity: {rh}%")
    if wind is not None:
        wline = f"- Wind: {wind} {wind_u}"
        if wdir is not None:
            wline += f", direction {wdir}°"
        lines.append(wline)
    if press is not None:
        lines.append(f"- Surface pressure: {press} {p_u}")
    lines.append("\n(Data: Open-Meteo forecast API.)")
    return "\n".join(lines)


# ── Tool Router ────────────────────────────────────────────────────────────

TOOL_MAP = {
    "read_file": read_file,
    "list_directory": list_directory,
    "search_files": search_files,
    "count_files": count_files,
    "list_files": list_files,
    "get_file_info": get_file_info,
    "web_search": web_search,
    "tavily_search": tavily_search,
    "get_system_datetime": get_system_datetime,
    "get_weather": get_weather,
    "search_project_knowledge": search_project_knowledge,
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
