import pytest

from agentwatch.storage import Store, _percentile


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


def sample_trace(tid="t1", cost=0.002, errors=0, latency=120.0):
    return {"id": tid, "agent": "a", "task": "do things", "metadata": {},
            "started_at": 1000.0, "ended_at": 1000.12, "latency_ms": latency,
            "final_answer": "ok",
            "totals": {"tokens_in": 10, "tokens_out": 5, "cost_usd": cost, "errors": errors},
            "spans": [{"id": f"{tid}-s1", "trace_id": tid, "parent_id": None,
                       "kind": "tool", "name": "calc", "started_at": 1000.0,
                       "ended_at": 1000.1, "latency_ms": 100.0, "status": "ok",
                       "input": "", "output": "", "error": "",
                       "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "meta": {}}]}


def test_round_trip(store):
    store.insert_trace(sample_trace())
    got = store.get_trace("t1")
    assert got["task"] == "do things"
    assert len(got["spans"]) == 1
    items, total = store.list_traces()
    assert total == 1 and items[0]["id"] == "t1"


def test_stats_aggregate(store):
    store.insert_trace(sample_trace("t1", cost=0.01, latency=100))
    store.insert_trace(sample_trace("t2", cost=0.03, latency=300))
    s = store.stats()
    assert s["traces"] == 2 and s["spans"] == 2
    assert abs(s["cost_usd"] - 0.04) < 1e-9
    assert s["avg_latency_ms"] == 200
    assert s["p50_latency_ms"] == 200


def test_eval_persisted(store):
    store.insert_trace(sample_trace())
    store.save_eval("t1", {"grade": "B", "score": 80, "issues": [{"severity": "minor", "title": "x", "detail": "y"}], "summary": "s"})
    got = store.get_trace("t1")
    assert got["eval"]["grade"] == "B" and got["eval"]["score"] == 80


def test_percentile():
    assert _percentile([], 50) == 0.0
    assert _percentile([10], 95) == 10
    assert _percentile([1, 2, 3, 4], 50) == 2.5
