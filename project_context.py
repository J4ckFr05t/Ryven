"""Async-safe current project id for tool handlers during an agent run."""

from contextvars import ContextVar

current_project_id: ContextVar[str | None] = ContextVar("current_project_id", default=None)
