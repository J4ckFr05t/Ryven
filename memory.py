"""
Conversation memory — SQLite-backed persistence for chat history.
"""

import json
import sqlite3
import aiosqlite
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "ryven.db"


DEFAULT_PROJECT_ID = "default"


async def _upgrade_project_github_repos_branch(db):
    """Migrate legacy project_github_repos (no branch) to schema with branch."""
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_github_repos'"
    )
    if not await cur.fetchone():
        return
    cur = await db.execute("PRAGMA table_info(project_github_repos)")
    col_names = {row[1] for row in await cur.fetchall()}
    if "branch" in col_names:
        return
    logger.info("Migrating project_github_repos: adding branch column")
    await db.execute("""
        CREATE TABLE project_github_repos__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT 'main',
            created_at TEXT NOT NULL,
            UNIQUE(project_id, owner, repo, branch)
        )
    """)
    await db.execute("""
        INSERT INTO project_github_repos__new (project_id, owner, repo, branch, created_at)
        SELECT project_id, owner, repo, 'main', created_at FROM project_github_repos
    """)
    await db.execute("DROP TABLE project_github_repos")
    await db.execute("ALTER TABLE project_github_repos__new RENAME TO project_github_repos")


async def _migrate_schema(db):
    """Add new columns/tables for existing deployments."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS project_github_repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT 'main',
            created_at TEXT NOT NULL,
            UNIQUE(project_id, owner, repo, branch)
        )
    """)
    await _upgrade_project_github_repos_branch(db)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kb_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            source_label TEXT NOT NULL,
            body_text TEXT,
            rel_path TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            kb_item_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding TEXT
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_chunks_project ON kb_chunks(project_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_items_project ON kb_items(project_id)"
    )

    cursor = await db.execute("PRAGMA table_info(conversations)")
    conv_cols = {row[1] for row in await cursor.fetchall()}
    if "project_id" not in conv_cols:
        await db.execute("ALTER TABLE conversations ADD COLUMN project_id TEXT")

    await _recreate_empty_projects_if_integer_pk(db)
    await _rewrite_conversations_if_project_id_integer(db)


def _column_declares_integer(decl: str) -> bool:
    d = (decl or "").strip().upper()
    if d in ("INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT"):
        return True
    return d.startswith("INT") and "POINT" not in d


async def _recreate_empty_projects_if_integer_pk(db):
    """
    Older or hand-edited DBs may have projects.id as INTEGER while the app uses TEXT ids.
    SQLite then raises IntegrityError/datatype mismatch on INSERT ('default', ...).
    Recreate an empty mis-typed table only.
    """
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
    )
    if not await cur.fetchone():
        return
    cur = await db.execute("PRAGMA table_info(projects)")
    cols = await cur.fetchall()
    id_row = next((r for r in cols if r[1] == "id"), None)
    if not id_row:
        return
    if not _column_declares_integer(id_row[2]):
        return
    cnt = await db.execute("SELECT COUNT(*) FROM projects")
    n = (await cnt.fetchone())[0]
    if n > 0:
        logger.warning(
            "projects.id is INTEGER-like but the table is not empty; "
            "leave schema unchanged or migrate manually"
        )
        return
    logger.info("Recreating empty projects table (TEXT id) to fix schema mismatch")
    await db.execute("DROP TABLE projects")
    await db.execute("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


async def _rewrite_conversations_if_project_id_integer(db):
    """If conversations.project_id was created INTEGER, rewrite table so TEXT ids work."""
    cur = await db.execute("PRAGMA table_info(conversations)")
    cols = await cur.fetchall()
    pid_row = next((r for r in cols if r[1] == "project_id"), None)
    if not pid_row:
        return
    if not _column_declares_integer(pid_row[2]):
        return
    logger.info("Rewriting conversations table: project_id INTEGER -> TEXT")
    await db.execute("PRAGMA foreign_keys=OFF")
    try:
        await db.execute("ALTER TABLE conversations RENAME TO conversations_legacy")
        await db.execute("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                model TEXT DEFAULT 'openai',
                project_id TEXT
            )
        """)
        await db.execute("""
            INSERT INTO conversations (id, title, created_at, updated_at, model, project_id)
            SELECT id, title, created_at, updated_at, model,
                   CASE WHEN project_id IS NULL THEN NULL ELSE CAST(project_id AS TEXT) END
            FROM conversations_legacy
        """)
        await db.execute("DROP TABLE conversations_legacy")
    finally:
        await db.execute("PRAGMA foreign_keys=ON")


async def _ensure_default_project(db):
    cursor = await db.execute("SELECT COUNT(*) FROM projects")
    count = (await cursor.fetchone())[0]
    if count > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            """INSERT INTO projects (id, name, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (DEFAULT_PROJECT_ID, "General", "Default project for chats and knowledge.", now, now),
        )
    except sqlite3.IntegrityError as e:
        err = str(e).lower()
        if "datatype mismatch" in err:
            await _recreate_empty_projects_if_integer_pk(db)
            await db.execute(
                """INSERT INTO projects (id, name, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (DEFAULT_PROJECT_ID, "General", "Default project for chats and knowledge.", now, now),
            )
        else:
            raise


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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await _migrate_schema(db)
        await _ensure_default_project(db)
        try:
            await db.execute(
                "UPDATE conversations SET project_id = ? WHERE project_id IS NULL OR project_id = ''",
                (DEFAULT_PROJECT_ID,),
            )
        except sqlite3.IntegrityError as e:
            if "datatype mismatch" in str(e).lower():
                await _rewrite_conversations_if_project_id_integer(db)
                await db.execute(
                    "UPDATE conversations SET project_id = ? WHERE project_id IS NULL OR project_id = ''",
                    (DEFAULT_PROJECT_ID,),
                )
            else:
                raise
        await db.commit()
    logger.info(f"Database initialized at {DB_PATH}")


async def create_conversation(
    conv_id: str,
    title: str = "New Chat",
    model: str = "openai",
    project_id: str | None = None,
) -> dict:
    """Create a new conversation."""
    now = datetime.now(timezone.utc).isoformat()
    pid = project_id or DEFAULT_PROJECT_ID
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO conversations (id, title, created_at, updated_at, model, project_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, title, now, now, model, pid),
        )
        await db.commit()
    return {
        "id": conv_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "model": model,
        "project_id": pid,
    }


async def update_conversation_title(conv_id: str, title: str):
    """Update conversation title."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conv_id)
        )
        await db.commit()


async def list_conversations(limit: int = 50, project_id: str | None = None) -> list[dict]:
    """List recent conversations, optionally filtered by project."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if project_id:
            cursor = await db.execute(
                """SELECT id, title, created_at, updated_at, model, project_id
                   FROM conversations WHERE project_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (project_id, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT id, title, created_at, updated_at, model, project_id
                   FROM conversations ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_conversation(conv_id: str) -> dict | None:
    """Return conversation row or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title, created_at, updated_at, model, project_id FROM conversations WHERE id = ?",
            (conv_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


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


async def get_setting(key: str) -> str | None:
    """Get a single app setting value by key."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_setting(key: str, value: str):
    """Insert or update a single app setting."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )
        await db.commit()


# ── Projects ───────────────────────────────────────────────────────────────


async def list_projects() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, description, created_at, updated_at FROM projects ORDER BY name ASC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def create_project(project_id: str, name: str, description: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO projects (id, name, description, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, name, description.strip(), now, now),
        )
        await db.commit()
    return {"id": project_id, "name": name, "description": description.strip(), "created_at": now, "updated_at": now}


async def update_project(project_id: str, name: str | None = None, description: str | None = None) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if name is not None and description is not None:
            await db.execute(
                "UPDATE projects SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                (name, description, now, project_id),
            )
        elif name is not None:
            await db.execute(
                "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, project_id),
            )
        elif description is not None:
            await db.execute(
                "UPDATE projects SET description = ?, updated_at = ? WHERE id = ?",
                (description, now, project_id),
            )
        else:
            return False
        await db.commit()
        return True


async def delete_project(project_id: str) -> bool:
    if project_id == DEFAULT_PROJECT_ID:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET project_id = ? WHERE project_id = ?",
            (DEFAULT_PROJECT_ID, project_id),
        )
        await db.execute("DELETE FROM kb_chunks WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM kb_items WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM project_github_repos WHERE project_id = ?", (project_id,))
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
    return True


async def list_github_repos(project_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, owner, repo, branch, created_at FROM project_github_repos
               WHERE project_id = ? ORDER BY owner, repo, branch""",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def add_github_repo(
    project_id: str, owner: str, repo: str, branch: str = "main"
) -> dict:
    owner = owner.strip()
    repo = repo.strip().strip("/")
    branch = (branch or "main").strip() or "main"
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO project_github_repos (project_id, owner, repo, branch, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(project_id, owner, repo, branch) DO NOTHING""",
            (project_id, owner, repo, branch, now),
        )
        await db.commit()
    return {"owner": owner, "repo": repo, "branch": branch}


async def remove_github_repo(project_id: str, owner: str, repo: str, branch: str = "main") -> bool:
    branch = (branch or "main").strip() or "main"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """DELETE FROM project_github_repos
               WHERE project_id = ? AND owner = ? AND repo = ? AND branch = ?""",
            (project_id, owner, repo, branch),
        )
        await db.commit()
        return True


async def get_kb_item(item_id: str, project_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, project_id, kind, title, source_label, rel_path, metadata, body_text
               FROM kb_items WHERE id = ? AND project_id = ?""",
            (item_id, project_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_kb_items(project_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, project_id, kind, title, source_label, rel_path, metadata, created_at, updated_at
               FROM kb_items WHERE project_id = ? ORDER BY updated_at DESC""",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def insert_kb_item(
    item_id: str,
    project_id: str,
    kind: str,
    title: str,
    source_label: str,
    body_text: str | None,
    rel_path: str | None,
    metadata: dict | None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata) if metadata else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO kb_items
               (id, project_id, kind, title, source_label, body_text, rel_path, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                project_id,
                kind,
                title,
                source_label,
                body_text,
                rel_path,
                meta_json,
                now,
                now,
            ),
        )
        await db.commit()
    return {"id": item_id, "project_id": project_id, "kind": kind, "title": title}


async def delete_kb_item(item_id: str, project_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM kb_chunks WHERE kb_item_id = ? AND project_id = ?",
            (item_id, project_id),
        )
        cur = await db.execute(
            "DELETE FROM kb_items WHERE id = ? AND project_id = ?",
            (item_id, project_id),
        )
        deleted = getattr(cur, "rowcount", 0) or 0
        await db.commit()
        return deleted > 0


async def delete_chunks_for_item(kb_item_id: str, project_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM kb_chunks WHERE kb_item_id = ? AND project_id = ?",
            (kb_item_id, project_id),
        )
        await db.commit()


async def insert_kb_chunk(
    project_id: str,
    kb_item_id: str,
    chunk_index: int,
    text: str,
    embedding_json: str | None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO kb_chunks (project_id, kb_item_id, chunk_index, text, embedding)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, kb_item_id, chunk_index, text, embedding_json),
        )
        await db.commit()


async def fetch_chunks_for_project(project_id: str) -> list[dict]:
    """All chunks with optional embeddings for lexical or hybrid search."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.id, c.kb_item_id, c.chunk_index, c.text, c.embedding, i.source_label, i.kind
               FROM kb_chunks c
               JOIN kb_items i ON i.id = c.kb_item_id AND i.project_id = c.project_id
               WHERE c.project_id = ?""",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_project(project_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, name, description FROM projects WHERE id = ?", (project_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
