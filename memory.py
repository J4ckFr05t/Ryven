"""
Conversation memory — SQLite-backed persistence for chat history.
"""

import json
import aiosqlite
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "ryven.db"


async def init_db():
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                model TEXT DEFAULT 'openai'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                tool_name TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conv 
            ON messages(conversation_id, id)
        """)
        await db.commit()
    logger.info(f"Database initialized at {DB_PATH}")


async def create_conversation(conv_id: str, title: str = "New Chat", model: str = "openai") -> dict:
    """Create a new conversation."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at, model) VALUES (?, ?, ?, ?, ?)",
            (conv_id, title, now, now, model)
        )
        await db.commit()
    return {"id": conv_id, "title": title, "created_at": now, "updated_at": now, "model": model}


async def update_conversation_title(conv_id: str, title: str):
    """Update conversation title."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conv_id)
        )
        await db.commit()


async def list_conversations(limit: int = 50) -> list[dict]:
    """List recent conversations."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title, created_at, updated_at, model FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()


async def add_message(conv_id: str, role: str, content: str | None = None,
                      tool_calls: list | None = None, tool_call_id: str | None = None,
                      tool_name: str | None = None):
    """Add a message to a conversation."""
    now = datetime.now(timezone.utc).isoformat()
    tc_json = json.dumps(tool_calls) if tool_calls else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO messages (conversation_id, role, content, tool_calls, tool_call_id, tool_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, role, content, tc_json, tool_call_id, tool_name, now)
        )
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conv_id)
        )
        await db.commit()


async def get_messages(conv_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation in OpenAI format."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT role, content, tool_calls, tool_call_id, tool_name 
               FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?""",
            (conv_id, limit)
        )
        rows = await cursor.fetchall()

    # Reverse to get chronological order
    rows = list(reversed(rows))

    messages = []
    for row in rows:
        msg = {"role": row["role"]}
        if row["content"] is not None:
            msg["content"] = row["content"]
        if row["tool_calls"]:
            msg["tool_calls"] = json.loads(row["tool_calls"])
            if "content" not in msg:
                msg["content"] = None
        if row["tool_call_id"]:
            msg["tool_call_id"] = row["tool_call_id"]
        if row["tool_name"]:
            msg["name"] = row["tool_name"]
        messages.append(msg)

    return messages


async def generate_title(first_message: str) -> str:
    """Generate a short title from the first user message."""
    title = first_message.strip()
    # Truncate to ~50 chars at a word boundary
    if len(title) > 50:
        title = title[:50].rsplit(' ', 1)[0] + '...'
    return title
