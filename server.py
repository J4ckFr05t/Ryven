"""
Ryven — FastAPI server with WebSocket chat, conversation memory, and MCP integration.
"""

import hashlib
import hmac
import json
import logging
import os
import uuid
import base64
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from tools import init_allowed_dirs
from agent import run_agent
from mcp_manager import mcp_manager
import memory

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


@app.get("/api/conversations")
async def list_conversations(request: Request):
    await _require_auth(request)
    convs = await memory.list_conversations()
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
    logger.info("Client connected")

    async def send_event(event_type: str, data: dict):
        await ws.send_json({"type": event_type, **data})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_event("error", {"message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")

            # ── Load conversation ──────────────────────────────────────
            if msg_type == "load_conversation":
                conv_id = msg.get("conversation_id")
                if conv_id:
                    current_conv_id = conv_id
                    msgs = await memory.get_messages(conv_id)
                    await send_event("conversation_loaded", {
                        "conversation_id": conv_id,
                        "messages": msgs
                    })
                continue

            # ── New conversation ───────────────────────────────────────
            if msg_type == "new_conversation":
                current_conv_id = None
                await send_event("conversation_cleared", {})
                continue

            # ── Chat message ───────────────────────────────────────────
            if msg_type != "chat":
                continue

            user_text = msg.get("message", "").strip()
            model = msg.get("model", "openai")

            if not user_text:
                continue

            # Create conversation if needed
            if not current_conv_id:
                current_conv_id = str(uuid.uuid4())[:8]
                title = await memory.generate_title(user_text)
                await memory.create_conversation(current_conv_id, title, model)
                await send_event("conversation_created", {
                    "conversation_id": current_conv_id,
                    "title": title
                })

            logger.info(f"User [{model}] (conv:{current_conv_id}): {user_text[:80]}...")

            # Get conversation history from DB
            history = await memory.get_messages(current_conv_id, limit=30)

            # Run agent
            result = await run_agent(
                user_message=user_text,
                model=model,
                conversation_history=history,
                send_event=send_event
            )

            if result:
                # Save to DB
                await memory.add_message(current_conv_id, "user", user_text)
                await memory.add_message(
                    current_conv_id, "assistant",
                    result["assistant_message"]["content"]
                )

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
