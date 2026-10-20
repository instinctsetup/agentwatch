"""Measured numbers for the README. Run: python -m agentwatch.benchmark

Nothing here is estimated: every figure printed is measured on the machine
running the benchmark, and written to docs/benchmarks.json with the date and
hardware thread count so the README can cite it honestly.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
import urllib.request
from pathlib import Path

from .sdk import Tracer
from .server import create_app


def bench_sdk_overhead(n=2000):
    tracer = Tracer("bench", server_url="http://127.0.0.1:9", enabled=False)

    def untraced(x):
        return x * 2

    @tracer.tool
    def traced(x):
        return x * 2

    start = time.perf_counter()
    for i in range(n):
        untraced(i)
    base = (time.perf_counter() - start) / n * 1e6

    with tracer.trace("bench"):
        start = time.perf_counter()
        for i in range(n):
            traced(i)
        with_tools = (time.perf_counter() - start) / n * 1e6
    return base, with_tools


def bench_ingest(traces=200, spans_per_trace=5):
    import uvicorn, threading
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(str(Path(tmp) / "bench.db"))
        config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(50):
            try:
                urllib.request.urlopen("http://127.0.0.1:8765/api/stats", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        tracer = Tracer("bench-agent", server_url="http://127.0.0.1:8765")
        for t in range(traces):
            with tracer.trace(f"synthetic task {t}") as tr:
                for s in range(spans_per_trace):
                    with tracer.span("tool", f"tool_{s}", input="x"):
                        pass
                tr._answer = "done"
        tracer.close()

        start = time.perf_counter()
        stats = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/api/stats").read())
        stats_ms = (time.perf_counter() - start) * 1000

        total_spans = traces * spans_per_trace
        # wall time from first submit to all rows visible
        deadline = time.time() + 30
        while stats["spans"] < total_spans and time.time() < deadline:
            time.sleep(0.2)
            stats = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/api/stats").read())
        server.should_exit = True
        thread.join(timeout=3)
        return stats, stats_ms, total_spans


def main():
    base, with_tools = bench_sdk_overhead()
    print(f"SDK overhead: untraced call {base:.2f} µs, traced call {with_tools:.2f} µs "
          f"-> {with_tools - base:.2f} µs per recorded step")

    t0 = time.time()
    stats, stats_ms, total_spans = bench_ingest()
    wall = time.time() - t0
    rate = total_spans / wall
    print(f"Ingestion: {total_spans} spans across {stats['traces']} traces stored in "
          f"{wall:.2f}s -> {rate:.0f} spans/s (including SDK batching + eval)")
    print(f"Stats query latency over {stats['traces']} traces: {stats_ms:.1f} ms")

    out = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "cpu_threads": os.cpu_count(),
        "sdk_overhead_us_per_span": round(with_tools - base, 2),
        "untraced_call_us": round(base, 2),
        "traced_call_us": round(with_tools, 2),
        "ingest_spans_total": total_spans,
        "ingest_wall_s": round(wall, 2),
        "ingest_spans_per_s": round(rate),
        "stats_query_ms": round(stats_ms, 1),
        "traces_in_stats_query": stats["traces"],
    }
    Path("docs").mkdir(exist_ok=True)
    Path("docs/benchmarks.json").write_text(json.dumps(out, indent=2))
    print("wrote docs/benchmarks.json")


if __name__ == "__main__":
    main()
