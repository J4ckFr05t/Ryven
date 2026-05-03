"""
Project knowledge base: chunking, embeddings (optional), lexical fallback, search.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from pathlib import Path

from openai import AsyncOpenAI

import memory
from project_context import current_project_id

logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).parent / "data"
PROJECTS_ROOT = DATA_ROOT / "projects"

_MAX_CHARS = 1400
_OVERLAP = 200


def project_upload_dir(project_id: str) -> Path:
    p = PROJECTS_ROOT / project_id / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def chunk_text(text: str, max_chars: int = _MAX_CHARS, overlap: int = _OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+", s.lower()))


def lexical_score(query: str, text: str) -> float:
    qt = _tokens(query)
    tt = _tokens(text)
    if not qt or not tt:
        return 0.0
    inter = len(qt & tt)
    return inter / (math.sqrt(len(qt)) * math.sqrt(len(tt)) + 1e-9)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_embedding_client: AsyncOpenAI | None = None


def _get_embed_client() -> AsyncOpenAI | None:
    global _embedding_client
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if _embedding_client is None:
        _embedding_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _embedding_client


async def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """Embed with OpenAI; returns Nones if API unavailable."""
    client = _get_embed_client()
    if not client or not texts:
        return [None] * len(texts)
    try:
        resp = await client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )
        out: list[list[float] | None] = [None] * len(texts)
        for item in resp.data:
            if item.index < len(out):
                out[item.index] = list(item.embedding)
        return out
    except Exception as e:
        logger.warning(f"Embedding failed, using lexical search only: {e}")
        return [None] * len(texts)


async def embed_query(q: str) -> list[float] | None:
    vecs = await embed_texts([q])
    return vecs[0] if vecs else None


async def index_kb_text(project_id: str, kb_item_id: str, full_text: str):
    """Replace chunks for an item and store embeddings when possible."""
    await memory.delete_chunks_for_item(kb_item_id, project_id)
    chunks = chunk_text(full_text)
    if not chunks:
        return
    embeddings = await embed_texts(chunks)
    for i, ch in enumerate(chunks):
        emb = embeddings[i] if i < len(embeddings) else None
        emb_json = json.dumps(emb) if emb else None
        await memory.insert_kb_chunk(project_id, kb_item_id, i, ch, emb_json)


async def search_kb(
    project_id: str,
    query: str,
    top_k: int = 8,
) -> list[dict]:
    rows = await memory.fetch_chunks_for_project(project_id)
    if not rows:
        return []
    q_emb = await embed_query(query.strip())

    scored: list[tuple[float, dict]] = []
    for row in rows:
        emb = None
        if row.get("embedding"):
            try:
                emb = json.loads(row["embedding"])
            except (json.JSONDecodeError, TypeError):
                emb = None
        text = row["text"] or ""
        lex = lexical_score(query, text)
        if q_emb and emb and len(q_emb) == len(emb):
            cos = cosine_similarity(q_emb, emb)
            score = 0.85 * cos + 0.15 * min(lex, 1.0)
        else:
            score = lex
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, row in scored[:top_k]:
        results.append(
            {
                "score": score,
                "source_label": row.get("source_label", ""),
                "kind": row.get("kind", ""),
                "kb_item_id": row.get("kb_item_id", ""),
                "chunk_index": row.get("chunk_index", 0),
                "text": row.get("text", "")[:2000],
            }
        )
    return results


def format_kb_results_for_prompt(results: list[dict]) -> tuple[str, list[dict]]:
    """Return system suffix text and citation metadata."""
    if not results:
        return "", []
    lines = []
    citations = []
    for i, r in enumerate(results, start=1):
        label = r.get("source_label") or r.get("kind") or "source"
        snippet = (r.get("text") or "").strip().replace("\n", " ")
        if len(snippet) > 700:
            snippet = snippet[:697] + "..."
        lines.append(f"[{i}] **{label}** — {snippet}")
        citations.append(
            {
                "ref": i,
                "source_label": label,
                "kb_item_id": r.get("kb_item_id"),
                "chunk_index": r.get("chunk_index"),
            }
        )
    body = "## Retrieved project knowledge\nUse these numbered sources in your answer when relevant. Cite as [1], [2], etc.\n\n" + "\n\n".join(
        lines
    )
    return body, citations


async def build_kb_context(project_id: str, user_message: str) -> tuple[str, list[dict]]:
    hits = await search_kb(project_id, user_message, top_k=8)
    text, cites = format_kb_results_for_prompt(hits)
    return text, cites


def read_uploaded_file_bytes(path: Path, max_bytes: int = 400_000) -> str:
    raw = path.read_bytes()[:max_bytes]
    if b"\x00" in raw[:8000]:
        return ""
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def search_project_knowledge_tool(query: str) -> str:
    pid = current_project_id.get()
    if not pid:
        return "Error: No project is active for this chat."
    results = await search_kb(pid, query, top_k=10)
    if not results:
        return "No matching passages found in this project's knowledge base."
    out = []
    for i, r in enumerate(results, start=1):
        lab = r.get("source_label", "")
        snippet = (r.get("text") or "").strip()
        if len(snippet) > 1200:
            snippet = snippet[:1197] + "..."
        out.append(f"[{i}] {lab}\n{snippet}")
    return "\n\n".join(out)


async def add_note(project_id: str, title: str, body: str) -> dict:
    item_id = str(uuid.uuid4())[:12]
    title = title.strip() or "Note"
    source_label = f"note:{title}"
    await memory.insert_kb_item(
        item_id=item_id,
        project_id=project_id,
        kind="note",
        title=title,
        source_label=source_label,
        body_text=body,
        rel_path=None,
        metadata=None,
    )
    await index_kb_text(project_id, item_id, body)
    return {"id": item_id, "title": title}


async def add_snippet(project_id: str, title: str, code: str) -> dict:
    item_id = str(uuid.uuid4())[:12]
    title = title.strip() or "Snippet"
    source_label = f"snippet:{title}"
    text = code.strip()
    await memory.insert_kb_item(
        item_id=item_id,
        project_id=project_id,
        kind="snippet",
        title=title,
        source_label=source_label,
        body_text=text,
        rel_path=None,
        metadata=None,
    )
    await index_kb_text(project_id, item_id, text)
    return {"id": item_id, "title": title}


def _github_kb_body(owner: str, repo: str, branch: str) -> str:
    full = f"{owner}/{repo}"
    branch = (branch or "main").strip() or "main"
    return (
        f"Linked GitHub repository: {full} on branch `{branch}`. "
        f"Use GitHub MCP tools to browse files at ref `{branch}` or compare against this branch."
    )


async def add_upload(project_id: str, filename: str, content: bytes) -> dict:
    item_id = str(uuid.uuid4())[:12]
    safe_name = re.sub(r"[^\w.\-]", "_", filename)[:180]
    upload_dir = project_upload_dir(project_id)
    rel_name = f"{item_id}_{safe_name}"
    dest = upload_dir / rel_name
    dest.write_bytes(content[:2_000_000])
    text = read_uploaded_file_bytes(dest)
    if not text.strip():
        text = f"(Binary or empty file uploaded as {safe_name}; no text extracted for search.)"
    await memory.insert_kb_item(
        item_id=item_id,
        project_id=project_id,
        kind="file",
        title=safe_name,
        source_label=f"file:{safe_name}",
        body_text=text[:500_000],
        rel_path=rel_name,
        metadata={"filename": safe_name},
    )
    await index_kb_text(project_id, item_id, text)
    return {"id": item_id, "title": safe_name}


async def add_github_kb_item(
    project_id: str, owner: str, repo: str, branch: str = "main"
) -> dict:
    item_id = str(uuid.uuid4())[:12]
    branch = (branch or "main").strip() or "main"
    full = f"{owner}/{repo}"
    display = f"{full} @ {branch}"
    body = _github_kb_body(owner, repo, branch)
    await memory.insert_kb_item(
        item_id=item_id,
        project_id=project_id,
        kind="github_repo",
        title=display,
        source_label=f"github:{display}",
        body_text=body,
        rel_path=None,
        metadata={"owner": owner, "repo": repo, "branch": branch},
    )
    await index_kb_text(project_id, item_id, body)
    return {"id": item_id, "title": display}


def _metadata_dict(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            return None
    return None


async def _find_github_repo_kb_item_id(
    project_id: str, owner: str, repo: str, branch: str
) -> str | None:
    branch = (branch or "main").strip() or "main"
    for it in await memory.list_kb_items(project_id):
        if it.get("kind") != "github_repo":
            continue
        meta = _metadata_dict(it.get("metadata")) or {}
        if (
            meta.get("owner") == owner
            and meta.get("repo") == repo
            and (meta.get("branch") or "main") == branch
        ):
            return it["id"]
    return None


async def update_note(project_id: str, item_id: str, title: str, body: str) -> dict | None:
    item = await memory.get_kb_item(item_id, project_id)
    if not item or item.get("kind") != "note":
        return None
    title = title.strip() or "Note"
    source_label = f"note:{title}"
    ok = await memory.update_kb_item(
        item_id,
        project_id,
        title=title,
        source_label=source_label,
        body_text=body,
        rel_path=item.get("rel_path"),
        metadata=_metadata_dict(item.get("metadata")),
    )
    if not ok:
        return None
    await index_kb_text(project_id, item_id, body)
    return {"id": item_id, "title": title}


async def update_snippet(project_id: str, item_id: str, title: str, code: str) -> dict | None:
    item = await memory.get_kb_item(item_id, project_id)
    if not item or item.get("kind") != "snippet":
        return None
    title = title.strip() or "Snippet"
    source_label = f"snippet:{title}"
    text = code.strip()
    ok = await memory.update_kb_item(
        item_id,
        project_id,
        title=title,
        source_label=source_label,
        body_text=text,
        rel_path=item.get("rel_path"),
        metadata=_metadata_dict(item.get("metadata")),
    )
    if not ok:
        return None
    await index_kb_text(project_id, item_id, text)
    return {"id": item_id, "title": title}


async def update_github_repo_branch(
    project_id: str, owner: str, repo: str, old_branch: str, new_branch: str
) -> tuple[dict | None, str | None]:
    owner = owner.strip()
    repo = repo.strip()
    old_branch = (old_branch or "main").strip() or "main"
    new_branch = (new_branch or "main").strip() or "main"

    item_id = await _find_github_repo_kb_item_id(project_id, owner, repo, old_branch)
    ok, err = await memory.replace_github_repo_branch(project_id, owner, repo, old_branch, new_branch)
    if not ok:
        return None, err or "replace_failed"

    if not item_id:
        return None, "kb_item_missing"

    display = f"{owner}/{repo} @ {new_branch}"
    body = _github_kb_body(owner, repo, new_branch)
    await memory.update_kb_item(
        item_id,
        project_id,
        title=display,
        source_label=f"github:{display}",
        body_text=body,
        rel_path=None,
        metadata={"owner": owner, "repo": repo, "branch": new_branch},
    )
    await index_kb_text(project_id, item_id, body)
    return {"id": item_id, "title": display}, None


async def remove_kb_item(project_id: str, item_id: str) -> bool:
    item = await memory.get_kb_item(item_id, project_id)
    if not item:
        return False
    if item.get("kind") == "file" and item.get("rel_path"):
        try:
            (project_upload_dir(project_id) / item["rel_path"]).unlink(missing_ok=True)
        except Exception:
            pass
    return await memory.delete_kb_item(item_id, project_id)
