"""AgentWatch client SDK.

Traces what an AI agent did: every step (span), every tool call, every LLM
call - with tokens, cost and latency - and ships the trace to an AgentWatch
server. If the server is down, traces fall back to a local JSONL file so no
recording is ever silently lost.

Dependency-free on purpose (stdlib only): an observability SDK must never be
the thing that breaks your agent.
"""

from __future__ import annotations

import functools
import json
import threading
import time
import traceback
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_FIELD = 500  # chars kept per input/output summary


def _clip(value, limit=MAX_FIELD):
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _now():
    return time.time()


class Span:
    """One recorded step inside a trace."""

    def __init__(self, trace_id, kind, name, parent_id=None):
        self.id = uuid.uuid4().hex[:12]
        self.trace_id = trace_id
        self.parent_id = parent_id
        self.kind = kind  # "llm" | "tool" | "decision" | "custom"
        self.name = name
        self.started_at = _now()
        self.ended_at = None
        self.status = "ok"
        self.input = ""
        self.output = ""
        self.error = ""
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.meta = {}

    @property
    def latency_ms(self):
        end = self.ended_at if self.ended_at is not None else _now()
        return round((end - self.started_at) * 1000, 3)

    def finish(self):
        if self.ended_at is None:
            self.ended_at = _now()

    def to_dict(self):
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "kind": self.kind,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "meta": self.meta,
        }


class Trace:
    def __init__(self, tracer, task, metadata=None):
        self.id = uuid.uuid4().hex[:16]
        self.tracer = tracer
        self.agent = tracer.agent_name
        self.task = task
        self.metadata = metadata or {}
        self.started_at = _now()
        self.ended_at = None
        self.final_answer = ""
        self.spans: list[Span] = []

    @property
    def latency_ms(self):
        end = self.ended_at if self.ended_at is not None else _now()
        return round((end - self.started_at) * 1000, 3)

    @property
    def totals(self):
        return {
            "tokens_in": sum(s.tokens_in for s in self.spans),
            "tokens_out": sum(s.tokens_out for s in self.spans),
            "cost_usd": round(sum(s.cost_usd for s in self.spans), 8),
            "errors": sum(1 for s in self.spans if s.status == "error"),
        }

    def finish(self, final_answer=""):
        self.ended_at = _now()
        self.final_answer = _clip(final_answer, 2000)
        for s in self.spans:
            s.finish()

    def to_dict(self):
        return {
            "id": self.id,
            "agent": self.agent,
            "task": self.task,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "latency_ms": self.latency_ms,
            "final_answer": self.final_answer,
            "totals": self.totals,
            "spans": [s.to_dict() for s in self.spans],
        }


class Tracer:
    """Create one per agent. Thread-safe; flushes in the background."""

    def __init__(self, agent_name, server_url="http://localhost:8000",
                 flush_interval=0.5, fallback_path="agentwatch-fallback.jsonl",
                 enabled=True):
        self.agent_name = agent_name
        self.server_url = server_url.rstrip("/")
        self.flush_interval = flush_interval
        self.fallback_path = Path(fallback_path)
        self.enabled = enabled
        self._queue: list[dict] = []
        self._lock = threading.Lock()
        self._span_stack = threading.local()
        self.fell_back = False
        self._stop = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._flusher.start()

    # -- recording ------------------------------------------------------

    def start_trace(self, task, metadata=None) -> Trace:
        return Trace(self, task, metadata)

    def _current_span(self):
        stack = getattr(self._span_stack, "items", [])
        return stack[-1] if stack else None

    @contextmanager
    def span(self, kind, name, input=None):
        trace = getattr(self._span_stack, "trace", None)
        if trace is None or not self.enabled:
            yield None
            return
        parent = self._current_span()
        sp = Span(trace.id, kind, name, parent.id if parent else None)
        if input is not None:
            sp.input = _clip(input)
        stack = getattr(self._span_stack, "items", None)
        if stack is None:
            stack = self._span_stack.items = []
        stack.append(sp)
        try:
            yield sp
        except Exception as exc:
            sp.status = "error"
            sp.error = _clip(f"{type(exc).__name__}: {exc}")
            sp.meta["traceback"] = _clip(traceback.format_exc(), 1500)
            raise
        finally:
            stack.pop()
            sp.finish()
            trace.spans.append(sp)

    def tool(self, fn=None, *, name=None):
        """Decorator: record a function call as a tool span."""
        def wrap(f):
            @functools.wraps(f)
            def inner(*args, **kwargs):
                label = name or f.__name__
                arg_str = ", ".join([*(repr(a) for a in args),
                                     *(f"{k}={v!r}" for k, v in kwargs.items())])
                with self.span("tool", label, input=arg_str) as sp:
                    result = f(*args, **kwargs)
                    if sp is not None:
                        sp.output = _clip(result)
                    return result
            return inner
        return wrap(fn) if fn is not None else wrap

    def record_llm(self, sp, model, tokens_in, tokens_out, cost_usd, output=None):
        """Fill an open 'llm' span with model accounting."""
        if sp is None:
            return
        sp.meta["model"] = model
        sp.tokens_in = int(tokens_in)
        sp.tokens_out = int(tokens_out)
        sp.cost_usd = float(cost_usd)
        if output is not None:
            sp.output = _clip(output)

    @contextmanager
    def trace(self, task, metadata=None):
        """Context manager: run a whole agent task as one trace."""
        tr = self.start_trace(task, metadata)
        self._span_stack.trace = tr
        try:
            yield tr
        finally:
            tr.finish(getattr(tr, "_answer", ""))
            self._span_stack.trace = None
            self.submit(tr)

    # -- shipping -------------------------------------------------------

    def submit(self, trace: Trace):
        if not self.enabled:
            return
        with self._lock:
            self._queue.append(trace.to_dict())

    def _flush_loop(self):
        while not self._stop.is_set():
            self.flush()
            self._stop.wait(self.flush_interval)

    def flush(self):
        with self._lock:
            batch, self._queue = self._queue, []
        if not batch:
            return True
        ok = self._post(batch)
        if not ok:
            self.fell_back = True
            with self.fallback_path.open("a") as fh:
                for item in batch:
                    fh.write(json.dumps(item) + "\n")
        return ok

    def _post(self, batch):
        body = json.dumps({"traces": batch}).encode()
        req = urllib.request.Request(
            f"{self.server_url}/api/traces", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def close(self):
        self._stop.set()
        self._flusher.join(timeout=2)
        self.flush()
