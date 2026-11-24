"""Progression des jobs en SSE : GET /api/events?batch=…

L'API et le worker sont deux processus : l'API interroge la table `jobs` toutes les secondes
et n'envoie que les changements (docs/DECISIONS.md). Les événements ne portent que des
identifiants et des états, jamais le nom d'un fichier ni une donnée extraite.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from idreader.api.deps import AuthDep, api_error
from idreader.core import models as m

router = APIRouter(tags=["events"])

PING_SECONDS = 15.0
JobSnapshot = dict[str, Any]


def load_jobs(factory: sessionmaker[Session], batch_id: str) -> list[JobSnapshot]:
    with factory() as db:
        rows = db.execute(
            select(
                m.Job.id,
                m.Job.ref_id,
                m.Job.type,
                m.Job.status,
                m.Job.progress,
                m.Job.stage,
                m.Job.error_code,
                m.Job.attempts,
            )
            .where(m.Job.batch_id == batch_id)
            .order_by(m.Job.created_at)
        ).all()
    return [
        {
            "job_id": r.id,
            "file_id": r.ref_id,
            "type": r.type,
            "status": r.status,
            "progress": round(r.progress, 3),
            "stage": r.stage,
            "error_code": r.error_code,
            "attempts": r.attempts,
        }
        for r in rows
    ]


def diff_jobs(
    previous: dict[str, JobSnapshot], current: list[JobSnapshot]
) -> tuple[dict[str, JobSnapshot], list[JobSnapshot]]:
    """Renvoie (nouvel état, jobs nouveaux ou modifiés depuis `previous`)."""
    state = {j["job_id"]: j for j in current}
    changed = [j for j in current if previous.get(j["job_id"]) != j]
    return state, changed


def format_event(event: str, data: JobSnapshot) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def event_stream(
    is_disconnected: Callable[[], Awaitable[bool]],
    factory: sessionmaker[Session],
    batch_id: str,
    poll_seconds: float,
    max_seconds: float,
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    state: dict[str, JobSnapshot] = {}
    start = last_ping = time.monotonic()
    while time.monotonic() - start < max_seconds:
        if await is_disconnected():
            return
        rows = await asyncio.to_thread(load_jobs, factory, batch_id)
        state, changed = diff_jobs(state, rows)
        for job in changed:
            yield format_event("job", job)
        now = time.monotonic()
        if now - last_ping >= PING_SECONDS:
            last_ping = now
            yield ": ping\n\n"
        await asyncio.sleep(poll_seconds)
    # Le navigateur (EventSource) se reconnecte de lui-même après `retry`.


@router.get("/events")
async def events(
    request: Request, _auth: AuthDep, batch: Annotated[str, Query()]
) -> StreamingResponse:
    factory: sessionmaker[Session] = request.app.state.read_session_factory
    settings = request.app.state.settings

    def batch_exists() -> bool:
        with factory() as db:
            return db.get(m.Batch, batch) is not None

    if not await asyncio.to_thread(batch_exists):
        raise api_error(404, "BATCH_NOT_FOUND", "Lot introuvable.")
    return StreamingResponse(
        event_stream(
            request.is_disconnected,
            factory,
            batch,
            settings.sse_poll_seconds,
            settings.sse_max_seconds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
