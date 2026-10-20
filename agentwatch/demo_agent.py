"""A real instrumented demo agent, so the dashboard shows genuine traces.

It is a small tool-using loop agent with a deterministic planner (no paid API
needed) and four tools. Three scripted scenarios exercise the interesting
paths: a clean run, a repeated-action loop the report card must catch, and a
tool failure the agent recovers from.
"""

from __future__ import annotations

import json
import time

from .sdk import Tracer

PRICE_TABLE = {"apple": 1.2, "banana": 0.5, "server": 450.0, "gpu": 1200.0}


class DemoAgent:
    def __init__(self, tracer: Tracer):
        self.tracer = tracer
        self.tools = {
            "lookup_price": self._lookup_price,
            "calculate": self._calculate,
            "count_words": self._count_words,
            "search_notes": self._search_notes,
        }
        for name, fn in list(self.tools.items()):
            self.tools[name] = tracer.tool(fn, name=name)

    # -- tools (each one is traced) -------------------------------------

    def _lookup_price(self, item):
        time.sleep(0.01)
        if item not in PRICE_TABLE:
            raise KeyError(f"no price for '{item}'")
        return PRICE_TABLE[item]

    def _calculate(self, expression):
        time.sleep(0.005)
        if "boom" in expression:
            raise ValueError("invalid expression")
        return eval(expression, {"__builtins__": {}}, {})  # noqa: S307 - demo only

    def _count_words(self, text):
        return len(str(text).split())

    def _search_notes(self, query):
        time.sleep(0.01)
        notes = {"deployment": "deploy on Friday, freeze on Thursday",
                 "budget": "q4 budget approved at 12k"}
        return notes.get(query, "no notes found")

    # -- the agent loop --------------------------------------------------

    def run(self, task, plan):
        """Execute a scripted plan of (tool, args) steps, then answer."""
        with self.tracer.trace(task) as tr:
            results = []
            for tool_name, args in plan:
                with self.tracer.span("decision", f"choose {tool_name}",
                                      input=json.dumps(args)):
                    pass
                with self.tracer.span("llm", "planner", input=task) as sp:
                    time.sleep(0.02)  # stand-in for model latency
                    self.tracer.record_llm(sp, model="demo-planner-v1",
                                           tokens_in=120, tokens_out=45,
                                           cost_usd=0.00021,
                                           output=f"call {tool_name}")
                try:
                    results.append(self.tools[tool_name](**args))
                except Exception as exc:  # recorded by the span, agent keeps going
                    results.append(f"error: {exc}")
            answer = f"Done: {task} -> {results[-1] if results else 'nothing'}"
            tr._answer = answer  # picked up by Tracer.trace on exit
            return answer


SCENARIOS = [
    ("Price out 2 servers and 1 gpu",
     [("lookup_price", {"item": "server"}),
      ("lookup_price", {"item": "gpu"}),
      ("calculate", {"expression": "2*450.0 + 1200.0"})]),
    ("Research the deployment plan (agent gets stuck)",
     [("search_notes", {"query": "deployment"}),
      ("search_notes", {"query": "deployment"}),
      ("search_notes", {"query": "deployment"}),
      ("search_notes", {"query": "deployment"})]),
    ("Compute launch metric (tool fails, agent recovers)",
     [("calculate", {"expression": "boom"}),
      ("calculate", {"expression": "42 * 1.5"}),
      ("count_words", {"text": "launch went well for the team"})]),
]


def run_scenarios(server_url="http://localhost:8000"):
    tracer = Tracer("demo-agent", server_url=server_url, flush_interval=0.2)
    for task, plan in SCENARIOS:
        DemoAgent(tracer).run(task, plan)
    tracer.close()
    return len(SCENARIOS)


if __name__ == "__main__":
    import sys
    n = run_scenarios(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000")
    print(f"sent {n} scenario traces" + (" (fell back to file)" if False else ""))
