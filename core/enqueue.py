"""Enqueue arq jobs with stable, deduplicating job IDs.

A deterministic job ID makes duplicate scan requests (a double-clicked UI
button, the cron firing while a manual scan is running) a no-op while the
job is queued or in progress, instead of two concurrent scans racing on the
same book/author rows.
"""
from __future__ import annotations

from typing import Any

from arq.constants import result_key_prefix


def author_scan_job_id(author_id: int) -> str:
    return f"scan-author-{author_id}"


async def enqueue_unique(pool: Any, task_name: str, *args: Any, job_id: str) -> Any:
    """Enqueue *task_name* under a stable job ID.

    Returns the Job, or None when an identical job is already queued or
    running.  arq also refuses to enqueue while a *finished* run's stored
    result exists under the same ID, so that result is cleared and the
    enqueue retried once — a completed scan must never block a rescan.
    """
    job = await pool.enqueue_job(task_name, *args, _job_id=job_id)
    if job is not None:
        return job
    await pool.delete(result_key_prefix + job_id)
    return await pool.enqueue_job(task_name, *args, _job_id=job_id)
