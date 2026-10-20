"""AgentWatch server: ingestion API, query API, live SSE feed, dashboard."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import evals
from .storage import Store

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class TraceBatch(BaseModel):
    traces: list[dict]


def create_app(db_path="agentwatch.db"):
    app = FastAPI(title="AgentWatch", version="1.0.0")
    store = Store(db_path)
    app.state.store = store
    listeners: set[asyncio.Queue] = set()

    async def broadcast(event):
        dead = []
        for q in listeners:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            listeners.discard(q)

    @app.post("/api/traces")
    async def ingest(batch: TraceBatch):
        accepted = 0
        for trace in batch.traces:
            if not trace.get("id") or not trace.get("task"):
                continue
            store.insert_trace(dict(trace))
            card = evals.evaluate(store.get_trace(trace["id"]))
            store.save_eval(trace["id"], card)
            accepted += 1
            await broadcast({"type": "trace", "trace_id": trace["id"],
                             "agent": trace.get("agent"), "task": trace.get("task"),
                             "grade": card["grade"], "score": card["score"]})
        return {"accepted": accepted}

    @app.get("/api/traces")
    def list_traces(limit: int = 50, offset: int = 0, agent: str | None = None):
        items, total = store.list_traces(min(limit, 200), offset, agent)
        return {"traces": items, "total": total}

    @app.get("/api/traces/{trace_id}")
    def get_trace(trace_id: str):
        trace = store.get_trace(trace_id)
        if trace is None:
            raise HTTPException(404, "trace not found")
        return trace

    @app.post("/api/traces/{trace_id}/evaluate")
    def reevaluate(trace_id: str):
        trace = store.get_trace(trace_id)
        if trace is None:
            raise HTTPException(404, "trace not found")
        card = evals.evaluate(trace)
        store.save_eval(trace_id, card)
        return card

    @app.get("/api/stats")
    def stats():
        return store.stats()

    @app.get("/api/stream")
    async def stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        listeners.add(queue)

        async def events():
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                listeners.discard(queue)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
    return app


app = create_app()
