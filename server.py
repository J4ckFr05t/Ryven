"""
Ryven — FastAPI server with WebSocket chat, conversation memory, and MCP integration.
"""

import json
import logging
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
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
    return {
        "status": "ok",
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
        "tavily": bool(os.getenv("TAVILY_API_KEY")),
        "mcp_servers": list(mcp_manager.connections.keys()),
        "mcp_tools_count": len(mcp_manager.get_all_tools()),
    }


# ── REST API for conversation management ───────────────────────────────────

@app.get("/api/conversations")
async def list_conversations():
    convs = await memory.list_conversations()
    return {"conversations": convs}


@app.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str):
    msgs = await memory.get_messages(conv_id)
    return {"messages": msgs}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    await memory.delete_conversation(conv_id)
    return {"ok": True}


# ── WebSocket chat endpoint ───────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    await ws.accept()
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
