"""Report-card engine: rule-based checks that grade one agent trace.

Every check is deterministic and explainable - the card always says exactly
which behaviour cost points. An optional LLM judge can add a qualitative note
when an OpenAI-compatible endpoint is configured, but the grade always comes
from the rules, never from a model's mood.
"""

from __future__ import annotations

DEFAULT_COST_BUDGET_USD = 0.10
LOOP_THRESHOLD = 3          # identical tool calls in a row
ERROR_RATE_FLAG = 0.30      # >30% of spans failing
MIN_TOOL_CALLS_PROGRESS = 5 # this many tools but no answer = no progress

SEVERITY = {"critical": 40, "major": 20, "minor": 10}


def grade_for(score):
    return ("A" if score >= 90 else "B" if score >= 75 else
            "C" if score >= 60 else "D" if score >= 40 else "F")


def _find_loops(spans):
    """Consecutive identical (tool name + input) runs of length >= LOOP_THRESHOLD."""
    loops = []
    run_key, run_len, run_start = None, 0, 0
    tools = [s for s in spans if s["kind"] == "tool"]
    for i, s in enumerate(tools):
        key = (s["name"], s["input"])
        if key == run_key:
            run_len += 1
        else:
            run_key, run_len, run_start = key, 1, i
        if run_len == LOOP_THRESHOLD:
            loops.append({"tool": s["name"], "input": s["input"], "times": run_len})
        elif run_len > LOOP_THRESHOLD and loops:
            loops[-1]["times"] = run_len
    return loops


def evaluate(trace: dict, cost_budget=DEFAULT_COST_BUDGET_USD) -> dict:
    spans = trace.get("spans", [])
    issues = []
    score = 100

    def dock(severity, title, detail):
        nonlocal score
        issues.append({"severity": severity, "title": title, "detail": detail})
        score -= SEVERITY[severity]

    # 1. repeated-action loops
    loops = _find_loops(spans)
    tool_calls_total = sum(1 for s in spans if s["kind"] == "tool")
    for loop in loops:
        dock("critical", "Repeated-action loop",
             f"Agent called '{loop['tool']}' with identical input "
             f"{loop['times']} times in a row without changing strategy.")
    looped_calls = sum(l["times"] for l in loops)
    if loops and tool_calls_total and looped_calls >= 0.5 * tool_calls_total:
        dock("major", "Loop dominated the run",
             f"{looped_calls} of {tool_calls_total} tool calls were part of a "
             f"repeated-action loop, so most of the run made no progress.")

    # 2. error rate
    total = len(spans)
    errors = sum(1 for s in spans if s.get("status") == "error")
    if total and errors / total > ERROR_RATE_FLAG:
        dock("major", "High failure rate",
             f"{errors}/{total} steps failed ({errors / total:.0%}); "
             f"threshold is {ERROR_RATE_FLAG:.0%}.")

    # 3. cost budget
    cost = sum(s.get("cost_usd", 0.0) for s in spans)
    if cost > cost_budget:
        dock("major", "Over cost budget",
             f"Trace cost ${cost:.4f}, budget is ${cost_budget:.4f}.")

    # 4. no progress: many tool calls, empty final answer
    tool_calls = sum(1 for s in spans if s["kind"] == "tool")
    if tool_calls >= MIN_TOOL_CALLS_PROGRESS and not (trace.get("final_answer") or "").strip():
        dock("major", "Lots of activity, no answer",
             f"{tool_calls} tool calls but the agent produced no final answer.")

    # 5. latency hog (informational)
    lat = [(s["latency_ms"] or 0, s["name"]) for s in spans]
    trace_lat = trace.get("latency_ms") or 0
    if trace_lat > 0 and lat:
        slowest, name = max(lat)
        if slowest > 0.6 * trace_lat and len(spans) > 1:
            dock("minor", "One step dominated the runtime",
                 f"'{name}' took {slowest:.0f} ms of {trace_lat:.0f} ms total "
                 f"({slowest / trace_lat:.0%}).")

    score = max(0, score)
    grade = grade_for(score)
    if not issues:
        summary = "Clean run: no loops, failures, budget breaches or stalls detected."
    else:
        top = issues[0]
        summary = f"{len(issues)} issue(s) found. Most severe: {top['title']}."
    return {"grade": grade, "score": score, "issues": issues,
            "summary": summary, "judge": "rules"}
