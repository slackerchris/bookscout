"""n8n execution history proxy."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from config import get_config

router = APIRouter(prefix="/n8n", tags=["n8n"])


def _extract_items(run_data: dict) -> list[dict]:
    """Pull book-level results from Success Summary / Failure Summary node outputs."""
    items: list[dict] = []
    for node_name in ("Success Summary", "Failure Summary"):
        node_runs = run_data.get(node_name)
        if not node_runs:
            continue
        for run in node_runs:
            outputs = run.get("data", {}).get("main", [[]])
            for output_branch in outputs:
                for item in output_branch or []:
                    j = item.get("json", {})
                    if j:
                        items.append({
                            "name": j.get("name", ""),
                            "book_id": j.get("bookId"),
                            "result": j.get("result", "unknown"),
                            "content_path": j.get("contentPath", ""),
                        })
    return items


@router.get("/executions")
async def get_executions(
    workflow_id: str = Query(..., description="n8n workflow ID"),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    """
    Proxy the last N executions for a given n8n workflow.
    Returns a simplified list with status, timestamps, and any book-level results.
    """
    config = get_config()
    n8n_cfg = getattr(config, "n8n", None)
    n8n_url = (getattr(n8n_cfg, "url", "") or "").rstrip("/")
    api_key = getattr(n8n_cfg, "api_key", "") or ""

    if not n8n_url:
        raise HTTPException(status_code=503, detail="n8n is not configured")

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["X-N8N-API-KEY"] = api_key

    url = f"{n8n_url}/api/v1/executions"
    params = {
        "workflowId": workflow_id,
        "limit": limit,
        "includeData": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Could not reach n8n: {exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="n8n rejected the API key")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"n8n returned {resp.status_code}")

    payload = resp.json()
    raw_executions: list[dict] = payload.get("data", payload) if isinstance(payload, dict) else payload

    results: list[dict] = []
    for ex in raw_executions:
        run_data: dict = {}
        try:
            run_data = ex.get("data", {}).get("resultData", {}).get("runData", {})
        except AttributeError:
            pass

        results.append({
            "id": ex.get("id"),
            "status": ex.get("status", "unknown"),
            "started_at": ex.get("startedAt"),
            "stopped_at": ex.get("stoppedAt"),
            "items": _extract_items(run_data),
        })

    return results
