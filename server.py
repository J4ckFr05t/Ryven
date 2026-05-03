"""
Ryven — FastAPI server with WebSocket chat, conversation memory, and MCP integration.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
import base64
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from tools import init_allowed_dirs
from agent import run_agent
from mcp_manager import mcp_manager
import memory
import knowledge
import github_catalog

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

AUTH_COOKIE_NAME = "ryven_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
AUTH_PASSWORD_KEY = "auth_password_hash"
DISPLAY_NAME_KEY = "display_name"
PASSWORD_HASH_ITERATIONS = 240_000


def _auth_signing_key() -> str:
    key = os.getenv("AUTH_SIGNING_KEY", "").strip()
    if not key:
        raise RuntimeError("AUTH_SIGNING_KEY is required. Set it in your environment before starting Ryven.")
    return key


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    password_bytes = (password + _auth_signing_key()).encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, PASSWORD_HASH_ITERATIONS)
    payload = {
        "salt": base64.b64encode(salt).decode("utf-8"),
        "hash": base64.b64encode(digest).decode("utf-8"),
        "iterations": PASSWORD_HASH_ITERATIONS,
    }
    return json.dumps(payload)


def _verify_password(password: str, password_blob: str) -> bool:
    try:
        payload = json.loads(password_blob)
        salt = base64.b64decode(payload["salt"])
        expected_hash = base64.b64decode(payload["hash"])
        iterations = int(payload.get("iterations", PASSWORD_HASH_ITERATIONS))
    except Exception:
        return False

    password_bytes = (password + _auth_signing_key()).encode("utf-8")
    actual_hash = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)
    return hmac.compare_digest(actual_hash, expected_hash)


def _build_auth_token(password_blob: str) -> str:
    return hmac.new(
        _auth_signing_key().encode("utf-8"),
        password_blob.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


async def _get_password_blob() -> str | None:
    return await memory.get_setting(AUTH_PASSWORD_KEY)


async def _get_display_name() -> str:
    return (await memory.get_setting(DISPLAY_NAME_KEY)) or ""


async def _is_auth_configured() -> bool:
    return bool(await _get_password_blob())


async def _is_request_authorized(request: Request) -> bool:
    password_blob = await _get_password_blob()
    if not password_blob:
        return False
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "")
    expected_token = _build_auth_token(password_blob)
    return hmac.compare_digest(cookie_token, expected_token)


async def _is_websocket_authorized(ws: WebSocket) -> bool:
    password_blob = await _get_password_blob()
    if not password_blob:
        return False
    cookie_token = ws.cookies.get(AUTH_COOKIE_NAME, "")
    expected_token = _build_auth_token(password_blob)
    return hmac.compare_digest(cookie_token, expected_token)


async def _require_auth(request: Request) -> None:
    if not await _is_request_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    _auth_signing_key()
    init_allowed_dirs()
    await memory.init_db()

    # Start MCP servers (GitHub, etc.)
    logger.info("Starting MCP servers...")
    try:
        await mcp_manager.start()
        mcp_tools = mcp_manager.get_all_tools()
        logger.info(f"MCP ready — {len(mcp_tools)} tools from {len(mcp_manager.connections)} servers")
    except Exception as e:
        logger.warning(f"MCP startup error (non-fatal): {e}")

    logger.info("🤖 Ryven is online")
    yield

    # Shutdown
    logger.info("Shutting down MCP servers...")
    await mcp_manager.shutdown()
    logger.info("Ryven shut down")


app = FastAPI(title="Ryven", lifespan=lifespan)

# Serve frontend
FRONTEND_DIR = Path(__file__).parent / "frontend"


@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/health")
async def health():
    auth_configured = await _is_auth_configured()
    return {
        "status": "ok",
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
        "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
        "tavily": bool(os.getenv("TAVILY_API_KEY")),
        "mcp_servers": list(mcp_manager.connections.keys()),
        "mcp_tools_count": len(mcp_manager.get_all_tools()),
        "auth_configured": auth_configured,
    }


# ── REST API for conversation management ───────────────────────────────────

@app.get("/api/auth/status")
async def auth_status(request: Request):
    auth_configured = await _is_auth_configured()
    authenticated = await _is_request_authorized(request) if auth_configured else False
    return {
        "auth_configured": auth_configured,
        "authenticated": authenticated,
        "requires_setup": not auth_configured,
        "display_name": await _get_display_name(),
    }


@app.post("/api/auth/setup")
async def auth_setup(payload: dict, response: Response):
    password_blob = await _get_password_blob()
    if password_blob:
        return {"ok": False, "message": "Password is already configured"}

    password = str(payload.get("password", "")).strip()
    display_name = str(payload.get("display_name", "")).strip()
    if len(password) < 6:
        return {"ok": False, "message": "Password must be at least 6 characters"}
    if len(display_name) < 2:
        return {"ok": False, "message": "Name must be at least 2 characters"}

    new_blob = _hash_password(password)
    await memory.set_setting(AUTH_PASSWORD_KEY, new_blob)
    await memory.set_setting(DISPLAY_NAME_KEY, display_name)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=_build_auth_token(new_blob),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {"ok": True}


@app.post("/api/auth/login")
async def auth_login(payload: dict, response: Response):
    password_blob = await _get_password_blob()
    if not password_blob:
        return {"ok": False, "message": "Password is not configured yet"}

    password = str(payload.get("password", ""))
    if not _verify_password(password, password_blob):
        return {"ok": False, "message": "Invalid password"}

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=_build_auth_token(password_blob),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {"ok": True}


@app.post("/api/auth/change-password")
async def change_password(payload: dict, request: Request, response: Response):
    await _require_auth(request)
    current_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", "")).strip()

    if len(new_password) < 6:
        return {"ok": False, "message": "New password must be at least 6 characters"}

    password_blob = await _get_password_blob()
    if not password_blob:
        return {"ok": False, "message": "Password is not configured yet"}
    if not _verify_password(current_password, password_blob):
        return {"ok": False, "message": "Current password is incorrect"}

    new_blob = _hash_password(new_password)
    await memory.set_setting(AUTH_PASSWORD_KEY, new_blob)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=_build_auth_token(new_blob),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"ok": True}


async def _project_context_suffix(project_id: str, user_message: str) -> str | None:
    """KB retrieval + linked repos for system prompt."""
    parts = []
    kb_text, _ = await knowledge.build_kb_context(project_id, user_message)
    if kb_text:
        parts.append(kb_text)
    repos = await memory.list_github_repos(project_id)
    if repos:
        lines = [
            "## Linked GitHub repositories",
            "Use GitHub MCP tools (`github__*`) for these repositories when relevant:",
        ]
        for r in repos:
            br = r.get("branch") or "main"
            lines.append(f"- `{r['owner']}/{r['repo']}` — branch `{br}`")
        parts.append("\n".join(lines))
    if not parts:
        return None
    return "\n\n".join(parts)


@app.get("/api/github/repos")
async def api_github_repos(request: Request, page: int = 1):
    await _require_auth(request)
    return await github_catalog.list_user_repos(page=page)


@app.get("/api/github/branches")
async def api_github_branches(request: Request, owner: str, repo: str, page: int = 1):
    await _require_auth(request)
    return await github_catalog.list_repo_branches(owner, repo, page=page)


@app.get("/api/projects")
async def api_list_projects(request: Request):
    await _require_auth(request)
    projects = await memory.list_projects()
    return {"projects": projects}


@app.post("/api/projects")
async def api_create_project(request: Request, payload: dict):
    await _require_auth(request)
    name = str(payload.get("name", "")).strip()
    if len(name) < 1:
        raise HTTPException(status_code=400, detail="Name required")
    description = str(payload.get("description", "")).strip()
    project_id = str(uuid.uuid4())[:8]
    proj = await memory.create_project(project_id, name, description)
    return {"project": proj}


@app.patch("/api/projects/{project_id}")
async def api_update_project(project_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if "name" not in payload and "description" not in payload:
        raise HTTPException(status_code=400, detail="Nothing to update")
    name = payload.get("name")
    description = payload.get("description")
    await memory.update_project(
        project_id,
        name=str(name).strip() if name is not None else None,
        description=str(description).strip() if description is not None else None,
    )
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str, request: Request):
    await _require_auth(request)
    ok = await memory.delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete this project")
    return {"ok": True}


@app.get("/api/projects/{project_id}/kb")
async def api_list_kb(project_id: str, request: Request):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    items = await memory.list_kb_items(project_id)
    repos = await memory.list_github_repos(project_id)
    return {"items": items, "github_repos": repos}


@app.post("/api/projects/{project_id}/kb/note")
async def api_kb_note(project_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    title = str(payload.get("title", "Note")).strip()
    body = str(payload.get("body", ""))
    item = await knowledge.add_note(project_id, title, body)
    return {"ok": True, "item": item}


@app.post("/api/projects/{project_id}/kb/snippet")
async def api_kb_snippet(project_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    title = str(payload.get("title", "Snippet")).strip()
    code = str(payload.get("code", ""))
    item = await knowledge.add_snippet(project_id, title, code)
    return {"ok": True, "item": item}


@app.post("/api/projects/{project_id}/kb/repo")
async def api_kb_repo(project_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    owner = str(payload.get("owner", "")).strip()
    repo = str(payload.get("repo", "")).strip()
    branch = str(payload.get("branch", "main")).strip() or "main"
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="owner and repo required")
    existing = await memory.list_github_repos(project_id)
    if any(
        r["owner"] == owner and r["repo"] == repo and (r.get("branch") or "main") == branch
        for r in existing
    ):
        return {"ok": True, "duplicate": True}
    await memory.add_github_repo(project_id, owner, repo, branch)
    item = await knowledge.add_github_kb_item(project_id, owner, repo, branch)
    return {"ok": True, "item": item}


@app.delete("/api/projects/{project_id}/kb/repo")
async def api_kb_repo_delete(
    project_id: str, request: Request, owner: str, repo: str, branch: str = "main"
):
    await _require_auth(request)
    owner = owner.strip()
    repo = repo.strip()
    branch = (branch or "main").strip() or "main"
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="owner and repo required")
    await memory.remove_github_repo(project_id, owner, repo, branch)
    items = await memory.list_kb_items(project_id)
    for it in items:
        if it.get("kind") != "github_repo":
            continue
        raw = it.get("metadata")
        meta = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if (
            meta.get("owner") == owner
            and meta.get("repo") == repo
            and (meta.get("branch") or "main") == branch
        ):
            await knowledge.remove_kb_item(project_id, it["id"])
            break
    return {"ok": True}


@app.post("/api/projects/{project_id}/kb/upload")
async def api_kb_upload(project_id: str, request: Request, file: UploadFile = File(...)):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    raw = await file.read()
    filename = file.filename or "upload"
    item = await knowledge.add_upload(project_id, filename, raw)
    return {"ok": True, "item": item}


@app.delete("/api/projects/{project_id}/kb/{item_id}")
async def api_kb_delete_item(project_id: str, item_id: str, request: Request):
    await _require_auth(request)
    ok = await knowledge.remove_kb_item(project_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}


def _kb_metadata_for_json(raw):
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


@app.get("/api/projects/{project_id}/kb/items/{item_id}")
async def api_kb_get_item(project_id: str, item_id: str, request: Request):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    row = await memory.get_kb_item(item_id, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    item = dict(row)
    item["metadata"] = _kb_metadata_for_json(item.get("metadata"))
    return {"item": item}


@app.patch("/api/projects/{project_id}/kb/items/{item_id}")
async def api_kb_patch_item(project_id: str, item_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    row = await memory.get_kb_item(item_id, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    kind = row.get("kind")
    if kind == "github_repo":
        raise HTTPException(
            status_code=400,
            detail="GitHub links are updated from the GitHub tab (branch update) or remove and re-link.",
        )
    if kind not in ("note", "snippet"):
        raise HTTPException(
            status_code=400,
            detail="Only notes and code snippets can be updated with this endpoint.",
        )
    if kind == "note":
        title = str(payload.get("title", row.get("title") or "Note")).strip() or "Note"
        body = str(payload.get("body", row.get("body_text") or ""))
        item = await knowledge.update_note(project_id, item_id, title, body)
    else:
        title = str(payload.get("title", row.get("title") or "Snippet")).strip() or "Snippet"
        code = str(payload.get("code", row.get("body_text") or ""))
        item = await knowledge.update_snippet(project_id, item_id, title, code)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True, "item": item}


@app.patch("/api/projects/{project_id}/kb/repo")
async def api_kb_patch_repo(project_id: str, request: Request, payload: dict):
    await _require_auth(request)
    if not await memory.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    owner = str(payload.get("owner", "")).strip()
    repo = str(payload.get("repo", "")).strip()
    branch = str(payload.get("branch", "main")).strip() or "main"
    new_branch = str(payload.get("new_branch", "main")).strip() or "main"
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="owner and repo required")

    item, err = await knowledge.update_github_repo_branch(project_id, owner, repo, branch, new_branch)
    if err == "not_found":
        raise HTTPException(status_code=404, detail="Repository link not found for that branch")
    if err == "branch_exists":
        raise HTTPException(
            status_code=409,
            detail="That branch is already linked for this repository.",
        )
    if err == "kb_item_missing":
        raise HTTPException(
            status_code=500,
            detail="Linked repo was updated but the knowledge item is missing; remove and re-link the repository.",
        )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "item": item}


@app.get("/api/conversations")
async def list_conversations(request: Request, project_id: str | None = None):
    await _require_auth(request)
    convs = await memory.list_conversations(project_id=project_id)
    return {"conversations": convs}


@app.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str, request: Request):
    await _require_auth(request)
    msgs = await memory.get_messages(conv_id)
    return {"messages": msgs}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str, request: Request):
    await _require_auth(request)
    await memory.delete_conversation(conv_id)
    return {"ok": True}


# ── WebSocket chat endpoint ───────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
    if not await _is_websocket_authorized(ws):
        await ws.send_json({"type": "error", "message": "Unauthorized. Please login first."})
        await ws.close(code=1008)
        return

    current_conv_id = None
    pending_project_id = memory.DEFAULT_PROJECT_ID
    logger.info("Client connected")

    incoming: asyncio.Queue = asyncio.Queue(maxsize=512)
    agent_task: asyncio.Task | None = None

    async def send_event(event_type: str, data: dict):
        await ws.send_json({"type": event_type, **data})

    async def pump_incoming():
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await incoming.put({"type": "invalid_json"})
                else:
                    await incoming.put(msg)
        except WebSocketDisconnect:
            await incoming.put({"type": "_disconnect"})
        except Exception as e:
            logger.debug("WS pump receive ended: %s", e)
            await incoming.put({"type": "_disconnect"})

    pump_task = asyncio.create_task(pump_incoming())

    async def cancel_agent_task():
        nonlocal agent_task
        if agent_task is None or agent_task.done():
            return
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        agent_task = None

    try:
        while True:
            msg = await incoming.get()
            msg_type = msg.get("type")

            if msg_type == "_disconnect":
                logger.info("Client disconnected")
                break

            if msg_type == "invalid_json":
                await send_event("error", {"message": "Invalid JSON"})
                continue

            busy = agent_task is not None and not agent_task.done()
            if busy:
                if msg_type == "cancel_generation":
                    await cancel_agent_task()
                    continue
                if msg_type in ("load_conversation", "new_conversation"):
                    await cancel_agent_task()
                elif msg_type == "chat":
                    await send_event("error", {"message": "Already working on a reply. Stop it first or wait."})
                    continue
                else:
                    await send_event("error", {"message": "Busy. Stop the current reply or wait."})
                    continue

            # ── Load conversation ──────────────────────────────────────
            if msg_type == "load_conversation":
                conv_id = msg.get("conversation_id")
                if conv_id:
                    current_conv_id = conv_id
                    msgs = await memory.get_messages(conv_id)
                    conv_row = await memory.get_conversation(conv_id)
                    pid = (
                        (conv_row or {}).get("project_id")
                        or memory.DEFAULT_PROJECT_ID
                    )
                    pending_project_id = pid
                    await send_event("conversation_loaded", {
                        "conversation_id": conv_id,
                        "messages": msgs,
                        "project_id": pid,
                    })
                continue

            # ── New conversation ───────────────────────────────────────
            if msg_type == "new_conversation":
                current_conv_id = None
                pending_project_id = msg.get("project_id") or memory.DEFAULT_PROJECT_ID
                await send_event("conversation_cleared", {"project_id": pending_project_id})
                continue

            # ── Chat message ───────────────────────────────────────────
            if msg_type != "chat":
                continue

            user_text = msg.get("message", "").strip()
            model = msg.get("model", "openai")

            if not user_text:
                continue

            project_id = memory.DEFAULT_PROJECT_ID
            if current_conv_id:
                conv_row = await memory.get_conversation(current_conv_id)
                if conv_row and conv_row.get("project_id"):
                    project_id = conv_row["project_id"]
            else:
                project_id = msg.get("project_id") or pending_project_id or memory.DEFAULT_PROJECT_ID

            # Create conversation if needed
            if not current_conv_id:
                current_conv_id = str(uuid.uuid4())[:8]
                title = await memory.generate_title(user_text)
                await memory.create_conversation(
                    current_conv_id, title, model, project_id=project_id
                )
                pending_project_id = project_id
                await send_event("conversation_created", {
                    "conversation_id": current_conv_id,
                    "title": title,
                    "project_id": project_id,
                })

            logger.info(f"User [{model}] (proj:{project_id} conv:{current_conv_id}): {user_text[:80]}...")

            history = await memory.get_messages(current_conv_id, limit=30)
            extra_suffix = await _project_context_suffix(project_id, user_text)

            async def run_agent_job():
                nonlocal agent_task
                try:
                    result = await run_agent(
                        user_message=user_text,
                        model=model,
                        conversation_history=history,
                        send_event=send_event,
                        extra_system_suffix=extra_suffix,
                        project_id=project_id,
                    )
                    if result:
                        await memory.add_message(current_conv_id, "user", user_text)
                        await memory.add_message(
                            current_conv_id, "assistant",
                            result["assistant_message"]["content"],
                        )
                except asyncio.CancelledError:
                    try:
                        await send_event("generation_stopped", {})
                    except Exception:
                        pass
                    raise
                except Exception as e:
                    logger.exception("WebSocket agent error")
                    try:
                        await send_event("error", {"message": f"Server error: {e}"})
                    except Exception:
                        pass
                finally:
                    agent_task = None

            agent_task = asyncio.create_task(run_agent_job())

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await cancel_agent_task()
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
