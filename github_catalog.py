"""
GitHub REST helpers for listing repos and branches (uses GITHUB_PERSONAL_ACCESS_TOKEN).
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _headers() -> dict | None:
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def list_user_repos(page: int = 1, per_page: int = 100) -> dict:
    """Repositories the token can access (owned, collaborator, org)."""
    headers = _headers()
    if not headers:
        return {
            "configured": False,
            "repos": [],
            "error": "Set GITHUB_PERSONAL_ACCESS_TOKEN to list repositories.",
        }
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GITHUB_API}/user/repos",
                params={
                    "page": page,
                    "per_page": per_page,
                    "affiliation": "owner,collaborator,organization_member",
                    "sort": "full_name",
                    "direction": "asc",
                },
                headers=headers,
                timeout=45.0,
            )
            if r.status_code == 401:
                return {
                    "configured": True,
                    "repos": [],
                    "error": "GitHub rejected the token (401). Check GITHUB_PERSONAL_ACCESS_TOKEN.",
                }
            if r.status_code == 403:
                return {
                    "configured": True,
                    "repos": [],
                    "error": "GitHub API rate limit or forbidden (403).",
                }
            r.raise_for_status()
            rows = r.json()
    except httpx.HTTPError as e:
        logger.warning(f"GitHub list repos failed: {e}")
        return {"configured": True, "repos": [], "error": str(e)}

    repos = []
    for x in rows:
        try:
            repos.append(
                {
                    "full_name": x["full_name"],
                    "owner": x["owner"]["login"],
                    "name": x["name"],
                    "private": bool(x.get("private")),
                    "default_branch": x.get("default_branch") or "main",
                }
            )
        except (KeyError, TypeError):
            continue

    return {
        "configured": True,
        "repos": repos,
        "has_more": len(rows) >= per_page,
        "page": page,
    }


async def list_repo_branches(owner: str, repo: str, page: int = 1, per_page: int = 100) -> dict:
    owner = (owner or "").strip()
    repo = (repo or "").strip()
    if not owner or not repo:
        return {"configured": False, "branches": [], "error": "owner and repo required"}

    headers = _headers()
    if not headers:
        return {
            "configured": False,
            "branches": [],
            "error": "Set GITHUB_PERSONAL_ACCESS_TOKEN to list branches.",
        }

    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/branches",
                params={"page": page, "per_page": per_page},
                headers=headers,
                timeout=45.0,
            )
            if r.status_code == 401:
                return {
                    "configured": True,
                    "branches": [],
                    "error": "GitHub rejected the token (401).",
                }
            if r.status_code == 404:
                return {
                    "configured": True,
                    "branches": [],
                    "error": f"Repository not found or no access: {owner}/{repo}",
                }
            r.raise_for_status()
            rows = r.json()
    except httpx.HTTPError as e:
        logger.warning(f"GitHub list branches failed: {e}")
        return {"configured": True, "branches": [], "error": str(e)}

    branches = []
    for b in rows:
        if isinstance(b, dict) and b.get("name"):
            branches.append({"name": b["name"], "protected": bool(b.get("protected"))})

    return {
        "configured": True,
        "branches": branches,
        "has_more": len(rows) >= per_page,
        "page": page,
    }
