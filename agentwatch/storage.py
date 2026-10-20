"""SQLite storage for AgentWatch. WAL mode; stdlib sqlite3 only."""

from __future__ import annotations

import json
import time
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    task TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    started_at REAL NOT NULL,
    ended_at REAL,
    latency_ms REAL,
    final_answer TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    received_at REAL NOT NULL DEFAULT (strftime('%s','now') + strftime('%f','now') % 1)
);
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    parent_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    started_at REAL,
    ended_at REAL,
    latency_ms REAL,
    status TEXT NOT NULL DEFAULT 'ok',
    input TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_traces_received ON traces(received_at DESC);
CREATE TABLE IF NOT EXISTS evals (
    trace_id TEXT PRIMARY KEY REFERENCES traces(id) ON DELETE CASCADE,
    grade TEXT NOT NULL,
    score INTEGER NOT NULL,
    issues TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    judge TEXT NOT NULL DEFAULT 'rules',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now') + strftime('%f','now') % 1)
);
"""


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 3)


class Store:
    def __init__(self, path="agentwatch.db"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        with self._lock, self.db:
            self.db.executescript(SCHEMA)

    # -- writes ---------------------------------------------------------

    def insert_trace(self, trace: dict):
        totals = trace.get("totals") or {}
        spans = trace.pop("spans", [])
        with self._lock, self.db:
            self.db.execute(
                """INSERT OR REPLACE INTO traces
                   (id, agent, task, metadata, started_at, ended_at, latency_ms,
                    final_answer, tokens_in, tokens_out, cost_usd, errors, received_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trace["id"], trace["agent"], trace["task"],
                 json.dumps(trace.get("metadata") or {}), trace["started_at"],
                 trace.get("ended_at"), trace.get("latency_ms"),
                 trace.get("final_answer", ""), totals.get("tokens_in", 0),
                 totals.get("tokens_out", 0), totals.get("cost_usd", 0.0),
                 totals.get("errors", 0), time.time()))  # SQL default truncates %f to whole seconds
            for s in spans:
                self.db.execute(
                    """INSERT OR REPLACE INTO spans
                       (id, trace_id, parent_id, kind, name, started_at, ended_at,
                        latency_ms, status, input, output, error,
                        tokens_in, tokens_out, cost_usd, meta)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (s["id"], trace["id"], s.get("parent_id"), s["kind"], s["name"],
                     s.get("started_at"), s.get("ended_at"), s.get("latency_ms"),
                     s.get("status", "ok"), s.get("input", ""), s.get("output", ""),
                     s.get("error", ""), s.get("tokens_in", 0), s.get("tokens_out", 0),
                     s.get("cost_usd", 0.0), json.dumps(s.get("meta") or {})))
        trace["spans"] = spans

    def save_eval(self, trace_id, card: dict):
        with self._lock, self.db:
            self.db.execute(
                """INSERT OR REPLACE INTO evals
                   (trace_id, grade, score, issues, summary, judge, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (trace_id, card["grade"], card["score"],
                 json.dumps(card["issues"]), card["summary"], card.get("judge", "rules"),
                 time.time()))

    # -- reads ----------------------------------------------------------

    def list_traces(self, limit=50, offset=0, agent=None):
        sql = """SELECT t.*, e.grade, e.score FROM traces t
                 LEFT JOIN evals e ON e.trace_id = t.id"""
        args: list = []
        if agent:
            sql += " WHERE t.agent = ?"
            args.append(agent)
        sql += " ORDER BY t.received_at DESC, t.rowid DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._lock:
            rows = self.db.execute(sql, args).fetchall()
            total = self.db.execute("SELECT COUNT(*) c FROM traces").fetchone()["c"]
        return [self._trace_row(r) for r in rows], total

    def get_trace(self, trace_id):
        with self._lock:
            row = self.db.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
            if row is None:
                return None
            spans = self.db.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY started_at", (trace_id,)).fetchall()
            ev = self.db.execute("SELECT * FROM evals WHERE trace_id=?", (trace_id,)).fetchone()
        out = self._trace_row(row)
        out["spans"] = [self._span_row(s) for s in spans]
        out["eval"] = self._eval_row(ev) if ev else None
        return out

    def stats(self):
        with self._lock:
            agg = self.db.execute(
                """SELECT COUNT(*) traces, COALESCE(SUM(tokens_in),0) tokens_in,
                          COALESCE(SUM(tokens_out),0) tokens_out,
                          COALESCE(SUM(cost_usd),0) cost_usd,
                          COALESCE(SUM(errors),0) errors,
                          COALESCE(AVG(latency_ms),0) avg_latency_ms
                   FROM traces""").fetchone()
            spans = self.db.execute("SELECT COUNT(*) c FROM spans").fetchone()["c"]
            err_spans = self.db.execute(
                "SELECT COUNT(*) c FROM spans WHERE status='error'").fetchone()["c"]
            lats = [r[0] for r in self.db.execute(
                "SELECT latency_ms FROM traces WHERE latency_ms IS NOT NULL ORDER BY latency_ms")]
            grades = {r["grade"]: r["c"] for r in self.db.execute(
                "SELECT grade, COUNT(*) c FROM evals GROUP BY grade")}
            agents = [r["agent"] for r in self.db.execute(
                "SELECT DISTINCT agent FROM traces ORDER BY agent")]
        return {
            "traces": agg["traces"], "spans": spans,
            "tokens_in": agg["tokens_in"], "tokens_out": agg["tokens_out"],
            "cost_usd": round(agg["cost_usd"], 6),
            "error_spans": err_spans,
            "span_error_rate": round(err_spans / spans, 4) if spans else 0.0,
            "avg_latency_ms": round(agg["avg_latency_ms"], 2),
            "p50_latency_ms": _percentile(lats, 50),
            "p95_latency_ms": _percentile(lats, 95),
            "grades": grades, "agents": agents,
        }

    def _trace_row(self, r):
        return {
            "id": r["id"], "agent": r["agent"], "task": r["task"],
            "metadata": json.loads(r["metadata"]), "started_at": r["started_at"],
            "ended_at": r["ended_at"], "latency_ms": r["latency_ms"],
            "final_answer": r["final_answer"], "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"], "cost_usd": r["cost_usd"],
            "errors": r["errors"], "received_at": r["received_at"],
            **({"grade": r["grade"], "score": r["score"]} if "grade" in r.keys() else {}),
        }

    def _span_row(self, r):
        return {k: (json.loads(r[k]) if k == "meta" else r[k]) for k in r.keys()}

    def _eval_row(self, r):
        return {"grade": r["grade"], "score": r["score"],
                "issues": json.loads(r["issues"]), "summary": r["summary"],
                "judge": r["judge"], "created_at": r["created_at"]}

    def close(self):
        self.db.close()
