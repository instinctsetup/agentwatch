const $ = (s) => document.querySelector(s);
const fmtCost = (c) => c === 0 ? "$0" : c < 0.01 ? `$${c.toFixed(5)}` : `$${c.toFixed(3)}`;
const fmtMs = (m) => m == null ? "–" : m >= 1000 ? `${(m / 1000).toFixed(2)}s` : `${Math.round(m)}ms`;
const fmtWhen = (t) => new Date(t * 1000).toLocaleTimeString();

async function loadStats() {
  const s = await (await fetch("/api/stats")).json();
  $("#s-traces").textContent = s.traces;
  $("#s-spans").textContent = s.spans;
  $("#s-cost").textContent = fmtCost(s.cost_usd);
  $("#s-err").textContent = `${(s.span_error_rate * 100).toFixed(1)}%`;
  $("#s-p95").textContent = fmtMs(s.p95_latency_ms);
  $("#s-grades").innerHTML = ["A","B","C","D","F"]
    .filter(g => s.grades[g])
    .map(g => `<span class="gchip g-${g}">${g} × ${s.grades[g]}</span>`).join("") || '<span class="muted">no grades yet</span>';
}

let selected = null;
async function loadFeed() {
  const data = await (await fetch("/api/traces?limit=40")).json();
  const body = $("#feed-body");
  if (!data.traces.length) { body.innerHTML = '<tr><td colspan="7" class="empty">waiting for traces…</td></tr>'; return; }
  body.innerHTML = "";
  for (const t of data.traces) {
    const tr = document.createElement("tr");
    tr.dataset.id = t.id;
    if (t.id === selected) tr.classList.add("selected");
    tr.innerHTML = `<td><span class="grade g-${t.grade || "A"}">${t.grade || "…"}</span></td>
      <td>${esc(t.agent)}</td><td>${esc(t.task)}</td><td>${t.errors ? t.errors + " err" : ""}</td>
      <td>${fmtMs(t.latency_ms)}</td><td>${fmtCost(t.cost_usd)}</td><td>${fmtWhen(t.received_at)}</td>`;
    tr.onclick = () => selectTrace(t.id);
    body.appendChild(tr);
  }
}

async function selectTrace(id) {
  selected = id;
  document.querySelectorAll("#feed-body tr").forEach(r => r.classList.toggle("selected", r.dataset.id === id));
  const t = await (await fetch(`/api/traces/${id}`)).json();
  $("#detail-title").textContent = t.task;
  const t0 = t.started_at, total = Math.max(t.latency_ms || 1, 1);
  const rows = t.spans.map(s => {
    const left = Math.max(0, ((s.started_at - t0) * 1000) / total * 100);
    const width = Math.max(1.5, (s.latency_ms || 0) / total * 100);
    return `<div class="wf-row">
      <div class="wf-name" title="${esc(s.input)}"><span class="kind">${s.kind}</span>${esc(s.name)}${s.status === "error" ? " ⚠" : ""}</div>
      <div class="wf-bar-wrap"><div class="wf-bar ${s.kind} ${s.status}" style="left:${left}%;width:${Math.min(width, 100 - left)}%"></div></div>
      <div class="wf-ms">${fmtMs(s.latency_ms)}</div></div>`;
  }).join("");
  const ev = t.eval;
  const card = ev ? `<div class="card">
      <div class="card-head"><span class="grade g-${ev.grade}">${ev.grade}</span><div><b>Report card · ${ev.score}/100</b><div class="summary">${esc(ev.summary)}</div></div></div>
      ${ev.issues.map(i => `<div class="issue ${i.severity}"><b>${esc(i.title)}</b><span>${esc(i.detail)}</span></div>`).join("")}
    </div>` : "";
  const errSpans = t.spans.filter(s => s.status === "error" && s.error)
    .map(s => `<div class="issue critical"><b>Error in ${esc(s.name)}</b><span>${esc(s.error)}</span></div>`).join("");
  $("#detail-body").innerHTML = `
    <div class="muted">${esc(t.agent)} · ${t.spans.length} steps · ${fmtMs(t.latency_ms)} · ${t.totals ? "" : ""}${fmtCost(t.cost_usd)} · ${t.tokens_in + t.tokens_out} tokens</div>
    <div class="waterfall">${rows}</div>${errSpans}${card}
    ${t.final_answer ? `<div class="answer"><b>Final answer</b>${esc(t.final_answer)}</div>` : ""}`;
  $("#detail-body").classList.remove("muted");
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function connectStream() {
  const es = new EventSource("/api/stream");
  es.onopen = () => { $("#live-dot").classList.add("on"); $("#live-label").textContent = "live"; };
  es.onerror = () => { $("#live-dot").classList.remove("on"); $("#live-label").textContent = "reconnecting…"; };
  es.onmessage = (m) => {
    if (!m.data || m.data === "{}") return;
    loadStats(); loadFeed();
    const ev = JSON.parse(m.data);
    if (ev.trace_id === selected || !selected) selectTrace(ev.trace_id);
  };
}

loadStats(); loadFeed(); connectStream();
setInterval(loadStats, 15000);
