from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from idreader.api.events import diff_jobs, event_stream, format_event
from idreader.core.db import make_engine
from idreader.worker import queue
from tests.conftest import login, make_user
from tests.helpers import make_batch_with_file


def _job(jid: str, status: str = "queued", progress: float = 0.0) -> dict:
    return {"job_id": jid, "status": status, "progress": progress}


def test_diff_jobs_reports_only_changes() -> None:
    state, changed = diff_jobs({}, [_job("a"), _job("b")])
    assert [j["job_id"] for j in changed] == ["a", "b"]
    state, changed = diff_jobs(state, [_job("a"), _job("b", "running", 0.5)])
    assert changed == [_job("b", "running", 0.5)]
    _, changed = diff_jobs(state, [_job("a"), _job("b", "running", 0.5)])
    assert changed == []


def test_format_event() -> None:
    assert format_event("job", {"a": 1}) == 'event: job\ndata: {"a":1}\n\n'


def _parse(chunks: list[str]) -> list[dict]:
    return [json.loads(c.split("data: ", 1)[1]) for c in chunks if c.startswith("event: job")]


def test_event_stream_follows_job_progress(app, migrated) -> None:
    factory = app.state.read_session_factory
    batch_id, file_id, job_id = make_batch_with_file(app.state.session_factory, migrated)
    engine = make_engine(migrated)

    async def scenario() -> list[str]:
        chunks: list[str] = []
        polls = 0

        async def disconnected() -> bool:
            nonlocal polls
            polls += 1
            if polls == 2:
                claimed = queue.claim_next(
                    engine, "w", stale_after=timedelta(seconds=60), max_attempts=3
                )
                assert claimed is not None
                queue.set_progress(engine, job_id, "w", 0.5, "dummy")
            if polls == 3:
                queue.finish(engine, job_id, "w")
            return polls > 4

        async for chunk in event_stream(disconnected, factory, batch_id, 0.01, 30):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(scenario())
    engine.dispose()
    assert chunks[0].startswith("retry:")
    events = _parse(chunks)
    assert [e["status"] for e in events] == ["queued", "running", "done"]
    assert events[1]["progress"] == 0.5 and events[1]["stage"] == "dummy"
    assert all(e["file_id"] == file_id for e in events)
    assert set(events[0]) == {
        "job_id",
        "file_id",
        "type",
        "status",
        "progress",
        "stage",
        "error_code",
        "attempts",
    }


def test_events_require_auth_and_known_batch(app, client) -> None:
    assert client.get("/api/events?batch=x").status_code == 401
    make_user(app)
    login(client)
    assert client.get("/api/events?batch=inconnu").status_code == 404
