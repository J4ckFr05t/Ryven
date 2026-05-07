"""
Runtime app configuration backed by persisted settings.
Only AUTH_SIGNING_KEY stays in environment variables.
"""

from __future__ import annotations

import os

import memory

KEY_OPENAI_API_KEY = "openai_api_key"
KEY_GEMINI_API_KEY = "gemini_api_key"
KEY_OPENROUTER_API_KEY = "openrouter_api_key"
KEY_TAVILY_API_KEY = "tavily_api_key"
KEY_GITHUB_PAT = "github_personal_access_token"
KEY_GEMINI_WEB_SEARCH_MODEL = "gemini_web_search_model"
KEY_LLM_MAX_TOKENS = "llm_max_tokens"
KEY_LLM_TIMEOUT_SECONDS = "llm_timeout_seconds"
KEY_GEMINI_MAX_OUTPUT_TOKENS = "gemini_max_output_tokens"


def get_setting(key: str, env_fallback: str | None = None) -> str:
    value = (memory.get_setting_sync(key) or "").strip()
    if value:
        return value
    if env_fallback:
        return (os.getenv(env_fallback) or "").strip()
    return ""
