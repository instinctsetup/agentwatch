import json
import time

from agentwatch.sdk import Tracer


def make_tracer(**kw):
    kw.setdefault("server_url", "http://127.0.0.1:9")  # nothing listening
    kw.setdefault("flush_interval", 60)                 # flush manually in tests
    return Tracer("test-agent", **kw)


def test_span_records_latency_and_nesting(tmp_path):
    tr = make_tracer(fallback_path=tmp_path / "fb.jsonl")
    with tr.trace("nested task") as t:
        with tr.span("tool", "outer"):
            time.sleep(0.01)
            with tr.span("llm", "inner"):
                pass
    tr.close()
    outer = next(s for s in t.spans if s.name == "outer")
    inner = next(s for s in t.spans if s.name == "inner")
    assert inner.parent_id == outer.id
    assert outer.latency_ms >= 10
    assert inner.latency_ms < outer.latency_ms
    assert t.totals["errors"] == 0


def test_tool_decorator_captures_io_and_errors(tmp_path):
    tr = make_tracer(fallback_path=tmp_path / "fb.jsonl")

    @tr.tool
    def add(a, b):
        return a + b

    @tr.tool
    def explode():
        raise ValueError("kaput")

    with tr.trace("io task") as t:
        assert add(2, 3) == 5
        try:
            explode()
        except ValueError:
            pass
    tr.close()
    good, bad = t.spans
    assert good.output == "5"
    assert "2, 3" in good.input
    assert bad.status == "error" and "kaput" in bad.error
    assert t.totals["errors"] == 1


def test_llm_accounting(tmp_path):
    tr = make_tracer(fallback_path=tmp_path / "fb.jsonl")
    with tr.trace("llm task") as t:
        with tr.span("llm", "call") as sp:
            tr.record_llm(sp, model="m", tokens_in=100, tokens_out=50, cost_usd=0.001)
    tr.close()
    assert t.totals["tokens_in"] == 100
    assert t.totals["tokens_out"] == 50
    assert abs(t.totals["cost_usd"] - 0.001) < 1e-9


def test_fallback_to_file_when_server_down(tmp_path):
    fb = tmp_path / "fallback.jsonl"
    tr = make_tracer(fallback_path=fb)
    with tr.trace("offline task") as t:
        with tr.span("tool", "x"):
            pass
    tr.close()
    assert tr.fell_back
    lines = fb.read_text().strip().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["task"] == "offline task" and saved["spans"][0]["name"] == "x"


def test_long_fields_are_clipped(tmp_path):
    tr = make_tracer(fallback_path=tmp_path / "fb.jsonl")

    @tr.tool
    def big():
        return "z" * 5000

    with tr.trace("clip") as t:
        with tr.span("tool", "raw", input="y" * 5000):
            pass
        big()
    tr.close()
    raw, decorated = t.spans
    assert len(raw.input) <= 500
    assert len(decorated.output) <= 500
