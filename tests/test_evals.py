from agentwatch.evals import evaluate, grade_for


def span(kind="tool", name="t", input="x", status="ok", latency=10, cost=0.0):
    return {"kind": kind, "name": name, "input": input, "status": status,
            "latency_ms": latency, "cost_usd": cost}


def trace(spans, answer="done", latency=100.0):
    return {"spans": spans, "final_answer": answer, "latency_ms": latency}


def test_clean_trace_gets_A():
    card = evaluate(trace([span(name="a"), span(name="b", input="y")]))
    assert card["grade"] == "A" and card["score"] == 100 and card["issues"] == []


def test_loop_detected_and_graded_down():
    spans = [span(name="search", input="same")] * 4
    card = evaluate(trace(spans, answer=""))
    assert card["grade"] in ("D", "F")
    assert any("loop" in i["title"].lower() for i in card["issues"])


def test_high_error_rate_flagged():
    spans = [span(status="error")] * 2 + [span()]
    card = evaluate(trace(spans))
    assert any("failure" in i["title"].lower() for i in card["issues"])
    assert card["score"] < 100


def test_cost_budget_flagged():
    card = evaluate(trace([span(cost=0.2)]), cost_budget=0.1)
    assert any("budget" in i["title"].lower() for i in card["issues"])


def test_activity_without_answer_flagged():
    spans = [span(name=f"t{i}", input=str(i)) for i in range(6)]
    card = evaluate(trace(spans, answer=""))
    assert any("no answer" in i["title"].lower() for i in card["issues"])


def test_grades_boundaries():
    assert grade_for(95) == "A" and grade_for(75) == "B"
    assert grade_for(60) == "C" and grade_for(40) == "D" and grade_for(0) == "F"
