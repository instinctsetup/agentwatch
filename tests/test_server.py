import pytest
from fastapi.testclient import TestClient

from agentwatch.demo_agent import DemoAgent, SCENARIOS
from agentwatch.sdk import Tracer
from agentwatch.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "srv.db"))
    with TestClient(app) as c:
        yield c


def post_trace(client, tid="x1"):
    body = {"traces": [{"id": tid, "agent": "a", "task": "t", "metadata": {},
                        "started_at": 1.0, "ended_at": 2.0, "latency_ms": 1000,
                        "final_answer": "ans",
                        "totals": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "errors": 0},
                        "spans": []}]}
    return client.post("/api/traces", json=body)


def test_ingest_list_detail_stats(client):
    assert post_trace(client).json()["accepted"] == 1
    listing = client.get("/api/traces").json()
    assert listing["total"] == 1
    detail = client.get("/api/traces/x1").json()
    assert detail["task"] == "t" and detail["eval"]["grade"] == "A"
    stats = client.get("/api/stats").json()
    assert stats["traces"] == 1


def test_unknown_trace_404(client):
    assert client.get("/api/traces/nope").status_code == 404


def test_reevaluate_endpoint(client):
    post_trace(client)
    card = client.post("/api/traces/x1/evaluate").json()
    assert card["grade"] == "A"


def test_bad_rows_skipped(client):
    r = client.post("/api/traces", json={"traces": [{"agent": "a"}]})
    assert r.json()["accepted"] == 0


def test_end_to_end_demo_agent_catches_loop(client):
    """The real demo agent runs all 3 scenarios against the live API."""
    tracer = Tracer("demo-agent", server_url="", flush_interval=60)
    # point the SDK at the TestClient by monkeypatching _post
    def post(batch):
        resp = client.post("/api/traces", json={"traces": batch})
        return resp.status_code == 200
    tracer._post = post
    for task, plan in SCENARIOS:
        DemoAgent(tracer).run(task, plan)
    tracer.close()

    listing = client.get("/api/traces").json()
    assert listing["total"] == 3
    by_task = {t["task"]: t for t in listing["traces"]}
    stuck = next(t for t in listing["traces"] if "stuck" in t["task"])
    assert stuck["grade"] in ("D", "F")
    detail = client.get(f"/api/traces/{stuck['id']}").json()
    titles = [i["title"] for i in detail["eval"]["issues"]]
    assert "Repeated-action loop" in titles
    clean = next(t for t in listing["traces"] if "Price out" in t["task"])
    assert clean["grade"] == "A"
