# agentwatch

**A flight recorder and report card for AI agents.** When an agent runs a task, AgentWatch records every step it took - every tool call, every model call, tokens, cost and latency - replays it as a live waterfall, and grades the run with an explainable report card that names exactly what went wrong.

Built for the agent era: coding agents and task agents fail in quiet, expensive ways (repeated-action loops, silent tool failures, runaway cost). AgentWatch makes those failures visible the moment they happen, on a dashboard that updates live over SSE.

## Demo & report

- **Demo video (Drive):** _link added after upload_
- **PDF report (Drive):** _link added after upload_
- Also in-repo: [`docs/report/REPORT.pdf`](docs/report/REPORT.pdf), [`demo/agentwatch-demo.mp4`](demo/agentwatch-demo.mp4)

## What it does

| Capability | Where | Why it matters |
|---|---|---|
| Dependency-free tracing SDK (spans, nesting, tokens, cost) | `agentwatch/sdk.py` | stdlib only - observability must never break the agent it watches |
| One-decorator tool tracing (`@tracer.tool`) | `sdk.py` | two lines to instrument an existing agent |
| Offline fallback to local JSONL when the server is down | `sdk.py` | no recording is ever silently lost |
| Batch ingestion API + SQLite (WAL) storage | `server.py`, `storage.py` | agents ship traces in the background, zero blocking |
| Live dashboard over SSE: feed, stats, grade chips | `frontend/` | watch agents work in real time |
| Waterfall replay of any trace | `frontend/app.js` | see exactly where time and money went |
| **Report card**: loop detection, failure-rate, cost budget, no-progress, latency hog | `evals.py` | deterministic and explainable - every point lost names the behaviour |
| Demo agent with 3 scripted scenarios | `demo_agent.py` | proves the card catches a real loop on a real run, no paid API needed |
| Benchmark harness | `benchmark.py` | the numbers below are measured, not estimated |

## Quick start

```bash
pip install -r requirements.txt
uvicorn agentwatch.server:app --port 8000
# open http://localhost:8000

# in a second shell: send three real demo traces
python -m agentwatch.demo_agent
```

With Docker:

```bash
docker build -t agentwatch . && docker run -p 8000:8000 agentwatch
```

Instrument your own agent:

```python
from agentwatch.sdk import Tracer

tracer = Tracer("my-agent")           # POSTs to localhost:8000

@tracer.tool                          # every call is recorded
def search(query):
    ...

with tracer.trace("research competitors") as t:
    with tracer.span("llm", "gpt-5") as sp:
        ...
        tracer.record_llm(sp, model="gpt-5", tokens_in=812,
                          tokens_out=220, cost_usd=0.0041)
    t._answer = "..."                 # final answer, shown on the card
tracer.close()
```

Run the tests and the benchmark:

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q        # 20 tests: SDK, storage, evals, API, end-to-end
python -m agentwatch.benchmark   # re-measures the numbers below on your machine
```

## Measured performance

From the latest run of `agentwatch/benchmark.py` (see `docs/benchmarks.json` for the raw record, machine and date):

- **~3.9 µs of overhead per recorded step** - tracing a function call costs less than four microseconds beyond running it (0.15 µs untraced vs 4.01 µs traced)
- **~1,400 spans/second end-to-end ingestion**, including SDK batching, HTTP, SQLite writes *and* running the report card on every trace
- **2.4 ms** to compute fleet-wide stats (totals, error rate, p50/p95) over 200 stored traces

## The report card, honestly

The grade always comes from deterministic rules (`evals.py`), never from a model's mood. The demo suite runs three real scenarios through the real pipeline: the clean run and the failure-recovery run grade **A**; the run where the agent searches for the same thing four times grades **D** with "Repeated-action loop" and "Loop dominated the run" on its card. Those grades are asserted in the test suite (`tests/test_server.py::test_end_to_end_demo_agent_catches_loop`).

## Honest engineering notes

- The demo agent's planner is scripted so the whole project runs with zero API keys; the SDK does not care what produced the spans, so pointing it at a real LLM-driven agent is the same two lines.
- Cost figures are whatever the instrumented agent reports; AgentWatch never invents usage.
- SQLite is the right call for a single-operator deployment; the storage layer is one class and can be swapped for Postgres without touching the API.
- Long inputs/outputs are clipped to 500 characters per field - enough to debug a step, small enough to keep the database lean.
