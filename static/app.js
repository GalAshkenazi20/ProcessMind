/* ProcessMind dashboard — vanilla JS, talks to the FastAPI backend. */
"use strict";

const state = { dataset: null, activities: [], resources: [] };
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, html = "") => {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
  if (html) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => (typeof v === "number" ? (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toLocaleString()) : v);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------- tooltip ---------- */
const tooltip = $("#tooltip");
function showTip(evt, title, sub) {
  tooltip.innerHTML = `<div class="t-title">${esc(title)}</div><div class="t-sub">${sub}</div>`;
  tooltip.hidden = false;
  const pad = 14;
  const x = Math.min(evt.clientX + pad, window.innerWidth - 300);
  const y = Math.min(evt.clientY + pad, window.innerHeight - 120);
  tooltip.style.left = x + "px";
  tooltip.style.top = y + "px";
}
function hideTip() { tooltip.hidden = true; }

/* ---------- data loading ---------- */
async function refreshDatasets(selectName) {
  const list = await api("/api/datasets");
  const sel = $("#dataset-select");
  sel.innerHTML = list.map((d) => `<option value="${esc(d.name)}">${esc(d.name)} (${d.cases} cases)</option>`).join("");
  if (selectName) sel.value = selectName;
  state.dataset = sel.value || null;
}

$("#btn-demo").addEventListener("click", async () => {
  const res = await api("/api/demo", { method: "POST" });
  await refreshDatasets(res.dataset);
  activateTab("overview");
});

$("#file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await api("/api/upload", { method: "POST", body: fd });
    await refreshDatasets(res.dataset);
    activateTab("overview");
  } catch (err) {
    alert("Upload failed: " + err.message);
  }
  e.target.value = "";
});

$("#dataset-select").addEventListener("change", (e) => {
  state.dataset = e.target.value;
  activateTab(currentTab);
});

/* ---------- tabs ---------- */
let currentTab = "overview";
const RENDERERS = {};

document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => activateTab(b.dataset.tab))
);

async function activateTab(name) {
  currentTab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = true));
  const section = $(`#tab-${name}`);
  $("#empty-state").hidden = !!state.dataset;
  if (!state.dataset) return;
  section.hidden = false;
  section.innerHTML = `<div class="spinner">Loading…</div>`;
  try {
    await RENDERERS[name](section);
  } catch (err) {
    section.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  }
}

/* ---------- shared chart helpers ---------- */
function hbarChart(rows, { labelKey, valueKey, valueFmt = fmt, colorClass = "", tip }) {
  const max = Math.max(...rows.map((r) => r[valueKey]), 1);
  const wrap = el("div");
  rows.forEach((r) => {
    const row = el("div", { class: "hbar-row" });
    row.appendChild(el("div", { class: "hbar-label", title: esc(r[labelKey]) }, esc(r[labelKey])));
    const track = el("div", { class: "hbar-track" });
    const fill = el("div", { class: `hbar-fill ${colorClass}` });
    fill.style.width = (100 * r[valueKey] / max).toFixed(1) + "%";
    if (tip) {
      fill.addEventListener("mousemove", (e) => showTip(e, r[labelKey], tip(r)));
      fill.addEventListener("mouseleave", hideTip);
    }
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("div", { class: "hbar-value" }, valueFmt(r[valueKey])));
    wrap.appendChild(row);
  });
  return wrap;
}

function tiles(items) {
  const wrap = el("div", { class: "tiles" });
  items.forEach(([label, value, hint]) => {
    const t = el("div", { class: "tile" });
    t.appendChild(el("div", { class: "value" }, esc(fmt(value))));
    t.appendChild(el("div", { class: "label" }, esc(label)));
    if (hint) t.appendChild(el("div", { class: "hint" }, esc(hint)));
    wrap.appendChild(t);
  });
  return wrap;
}

/* ---------- Overview ---------- */
RENDERERS.overview = async (root) => {
  const data = await api(`/api/overview?dataset=${encodeURIComponent(state.dataset)}`);
  const s = data.summary, d = s.duration_hours;
  root.innerHTML = "";
  root.appendChild(tiles([
    ["Cases", s.cases], ["Events", s.events], ["Process steps", s.activities],
    ["People / systems", s.resources],
    ["Median case time", d.median + " h", `mean ${d.mean} h · slowest 10% > ${d.p90} h`],
    ["Handoffs per case", s.mean_handoffs],
    ["Cases with rework", s.rework_case_pct + "%"],
  ]));

  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "How cases actually flow"));
  card.appendChild(el("p", { class: "sub" },
    `${data.variants.total_variants} distinct paths observed — only ${data.variants.happy_path_share_pct}% of cases follow the most common one. Share of cases per path:`));
  card.appendChild(hbarChart(
    data.variants.variants.map((v) => ({ label: v.path.join(" → "), pct: v.share_pct, v })),
    {
      labelKey: "label", valueKey: "pct", valueFmt: (x) => x + "%",
      tip: (r) => `${r.v.cases} cases · avg ${r.v.mean_duration_hours} h · ${r.v.mean_handoffs} handoffs`,
    }
  ));
  root.appendChild(card);
};

/* ---------- Process map ---------- */
const EDGE_RAMP_LIGHT = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"];
const EDGE_RAMP_DARK = ["#2a78d6", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"];

RENDERERS.map = async (root) => {
  const dfg = await api(`/api/process-map?dataset=${encodeURIComponent(state.dataset)}`);
  state.activities = dfg.nodes.map((n) => n.activity);
  root.innerHTML = "";
  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "The real process map"));
  card.appendChild(el("p", { class: "sub" }, "Discovered from the event log — including loops and detours that are not in the official procedure. Edge thickness = how often the transition happens; edge color = average waiting time on it. Hover for details."));
  const wrap = el("div", { id: "map-svg-wrap" });
  wrap.appendChild(buildMapSVG(dfg));
  card.appendChild(wrap);

  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const ramp = dark ? EDGE_RAMP_DARK : EDGE_RAMP_LIGHT;
  const legend = el("div", { class: "legend-row" });
  legend.appendChild(el("span", {}, "Avg wait:"));
  ["shortest", "", "", "", "longest"].forEach((lbl, i) => {
    legend.appendChild(el("span", {}, `<span class="legend-swatch" style="background:${ramp[i]}"></span>${lbl}`));
  });
  card.appendChild(legend);
  root.appendChild(card);
};

function buildMapSVG(dfg) {
  const nodes = dfg.nodes;
  const edges = dfg.edges.filter((e) => e.source !== "__start__" && e.target !== "__end__");
  // layered layout: BFS levels from the start edges
  const level = {};
  dfg.edges.filter((e) => e.source === "__start__").forEach((e) => (level[e.target] = 0));
  let changed = true, guard = 0;
  while (changed && guard++ < 50) {
    changed = false;
    for (const e of edges) {
      if (level[e.source] !== undefined && level[e.target] === undefined) {
        level[e.target] = level[e.source] + 1;
        changed = true;
      }
    }
  }
  nodes.forEach((n) => { if (level[n.activity] === undefined) level[n.activity] = 0; });

  const byLevel = {};
  nodes.forEach((n) => (byLevel[level[n.activity]] ||= []).push(n));
  const levels = Object.keys(byLevel).map(Number).sort((a, b) => a - b);
  const NW = 178, NH = 46, VGAP = 96, HGAP = 26;
  const maxRow = Math.max(...levels.map((l) => byLevel[l].length));
  const width = Math.max(720, maxRow * (NW + HGAP) + 60);
  const height = levels.length * (NH + VGAP) + 40;

  const pos = {};
  levels.forEach((l) => {
    const row = byLevel[l];
    const rowW = row.length * NW + (row.length - 1) * HGAP;
    row.forEach((n, i) => {
      pos[n.activity] = { x: (width - rowW) / 2 + i * (NW + HGAP), y: 20 + l * (NH + VGAP) };
    });
  });

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Directly-follows process graph");

  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const ramp = dark ? EDGE_RAMP_DARK : EDGE_RAMP_LIGHT;
  const maxFreq = Math.max(...edges.map((e) => e.frequency));
  const waits = edges.map((e) => e.wait_hours.mean).sort((a, b) => a - b);
  const q = (p) => waits[Math.min(waits.length - 1, Math.floor(p * waits.length))] || 0;
  const waitColor = (w) => (w <= q(0.2) ? ramp[0] : w <= q(0.4) ? ramp[1] : w <= q(0.6) ? ramp[2] : w <= q(0.8) ? ramp[3] : ramp[4]);

  for (const e of edges) {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) continue;
    const x1 = a.x + NW / 2, y1 = a.y + NH, x2 = b.x + NW / 2, y2 = b.y;
    const path = document.createElementNS(svgNS, "path");
    let d;
    if (e.source === e.target) {
      d = `M ${x1 + NW / 2 - 8} ${a.y + NH / 2} C ${x1 + NW} ${a.y - 22}, ${x1 + NW} ${a.y + NH + 22}, ${x1 + NW / 2 - 8} ${a.y + NH / 2 + 6}`;
    } else if (y2 <= y1) { // back edge (loop) — bow out to the side
      const side = x1 > width / 2 ? 1 : -1;
      const bow = 70 + 40 * Math.random() * 0; // deterministic bow
      d = `M ${x1} ${y1 - NH / 2 + (side > 0 ? 0 : 0)} C ${x1 + side * (NW / 2 + bow)} ${y1}, ${x2 + side * (NW / 2 + bow)} ${y2 + NH}, ${x2 + side * 6} ${y2 + NH / 2}`;
    } else {
      d = `M ${x1} ${y1} C ${x1} ${y1 + VGAP / 2}, ${x2} ${y2 - VGAP / 2}, ${x2} ${y2 - 4}`;
    }
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", waitColor(e.wait_hours.mean));
    path.setAttribute("stroke-width", (1.2 + 5 * e.frequency / maxFreq).toFixed(1));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("opacity", "0.85");
    path.style.cursor = "pointer";
    path.addEventListener("mousemove", (ev) =>
      showTip(ev, `${e.source} → ${e.target}`,
        `${e.frequency} times · ${e.case_coverage}% of cases<br>avg wait ${e.wait_hours.mean} h · p90 ${e.wait_hours.p90} h`));
    path.addEventListener("mouseleave", hideTip);
    svg.appendChild(path);
  }

  for (const n of nodes) {
    const p = pos[n.activity];
    const g = document.createElementNS(svgNS, "g");
    g.setAttribute("class", "map-node");
    g.style.cursor = "pointer";
    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", p.x); rect.setAttribute("y", p.y);
    rect.setAttribute("width", NW); rect.setAttribute("height", NH);
    rect.setAttribute("rx", 8);
    rect.setAttribute("stroke-width", "1.2");
    g.appendChild(rect);
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", p.x + NW / 2); label.setAttribute("y", p.y + 19);
    label.setAttribute("text-anchor", "middle");
    label.textContent = n.activity.length > 24 ? n.activity.slice(0, 23) + "…" : n.activity;
    g.appendChild(label);
    const freq = document.createElementNS(svgNS, "text");
    freq.setAttribute("x", p.x + NW / 2); freq.setAttribute("y", p.y + 35);
    freq.setAttribute("text-anchor", "middle");
    freq.setAttribute("class", "freq");
    freq.textContent = `${n.frequency}× · ${n.case_coverage}% of cases`;
    g.appendChild(freq);
    g.addEventListener("mousemove", (ev) =>
      showTip(ev, n.activity,
        `${n.frequency} executions in ${n.case_coverage}% of cases<br>avg wait before: ${n.mean_wait_before_hours} h` +
        `<br>${n.resources} performer(s) · top: ${esc(n.top_resource)} (${n.top_resource_share}%)`));
    g.addEventListener("mouseleave", hideTip);
    svg.appendChild(g);
  }
  return svg;
}

/* ---------- Findings ---------- */
RENDERERS.findings = async (root) => {
  const findings = await api(`/api/findings?dataset=${encodeURIComponent(state.dataset)}`);
  root.innerHTML = "";
  if (!findings.length) {
    root.appendChild(el("div", { class: "card" }, "<h3>No significant problems detected</h3>"));
    return;
  }
  const intro = el("p", { class: "mode-note" },
    `${findings.length} evidence-backed findings, ordered by severity and estimated impact.`);
  root.appendChild(intro);
  findings.forEach((f) => {
    const card = el("div", { class: "card finding" });
    card.appendChild(el("span", { class: `badge ${f.severity}` }, `${f.severity === "high" ? "▲" : f.severity === "medium" ? "◆" : "●"} ${f.severity}`));
    const body = el("div");
    body.appendChild(el("h3", {}, esc(f.title)));
    body.appendChild(el("p", { class: "sub" }, esc(typeLabel(f.type)) + ` · impact score ${f.impact_score}`));
    const dl = el("table");
    dl.innerHTML = Object.entries(f.evidence)
      .filter(([, v]) => v !== null && typeof v !== "object")
      .map(([k, v]) => `<tr><td style="color:var(--text-muted)">${esc(k.replaceAll("_", " "))}</td><td>${esc(fmt(v))}</td></tr>`)
      .join("");
    body.appendChild(dl);
    card.appendChild(body);
    root.appendChild(card);
  });
};

function typeLabel(t) {
  return {
    bottleneck: "Bottleneck — cases queue here",
    rework: "Rework — the process loops back",
    ping_pong: "Ping-pong — unclear ownership",
    single_person_dependency: "Key-person risk",
    duration_outliers: "Outliers — a slow tail of cases",
    attribute_delay_driver: "Delay driver — a case property predicts slowness",
  }[t] || t;
}

/* ---------- Prediction ---------- */
RENDERERS.prediction = async (root) => {
  const p = await api(`/api/predictions?dataset=${encodeURIComponent(state.dataset)}`);
  root.innerHTML = "";
  root.appendChild(tiles([
    ["Delay threshold", p.model.delayed_threshold_hours + " h", "cases slower than this count as delayed"],
    ["Delayed cases", p.model.delayed_share_pct + "%"],
    ["Model accuracy", p.model.accuracy_pct + "%"],
    ["Recall on delays", (p.model.recall_pct ?? "—") + "%"],
  ]));

  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "What drives delays"));
  card.appendChild(el("p", { class: "sub" }, "Learned from historical cases — larger weight = stronger link to delay. Use these to flag risky cases the day they are opened."));
  card.appendChild(hbarChart(p.risk_factors, {
    labelKey: "factor", valueKey: "weight",
    valueFmt: (x) => x.toFixed(2),
    tip: (r) => `model weight ${r.weight}`,
  }));
  root.appendChild(card);

  const card2 = el("div", { class: "card" });
  card2.appendChild(el("h3", {}, "Highest-risk cases"));
  card2.appendChild(el("p", { class: "sub" }, "Historical cases the model scores as most likely to be delayed (validation view)."));
  const t = el("table");
  t.innerHTML = "<tr><th>Case</th><th>Predicted risk</th><th>Actual duration</th><th>Was delayed</th></tr>" +
    p.highest_risk_cases.map((c) =>
      `<tr><td>${esc(c.case_id)}</td><td>${c.risk_pct}%</td><td>${c.duration_hours} h</td><td>${c.delayed ? "yes" : "no"}</td></tr>`).join("");
  card2.appendChild(t);
  root.appendChild(card2);
};

/* ---------- Simulation ---------- */
RENDERERS.simulation = async (root) => {
  if (!state.activities.length) {
    const dfg = await api(`/api/process-map?dataset=${encodeURIComponent(state.dataset)}`);
    state.activities = dfg.nodes.map((n) => n.activity);
  }
  root.innerHTML = "";
  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "Test a change before making it"));
  card.appendChild(el("p", { class: "sub" }, "The simulator replays historical traffic under the changed process and compares it with the baseline."));

  const actOptions = state.activities.map((a) => `<option>${esc(a)}</option>`).join("");
  const form = el("div", { class: "form-row" });
  form.innerHTML = `
    <label>Scenario
      <select id="sim-type">
        <option value="automate_activity">Automate a step</option>
        <option value="remove_activity">Remove a step</option>
        <option value="add_capacity">Add a worker</option>
      </select>
    </label>
    <label>Step <select id="sim-activity">${actOptions}</select></label>
    <label id="sim-reduction-wrap">Wait reduction %
      <input id="sim-reduction" type="number" value="80" min="10" max="100" style="width:90px" />
    </label>
    <button class="primary" id="sim-run">Run simulation</button>`;
  card.appendChild(form);
  const out = el("div", { id: "sim-out" });
  card.appendChild(out);
  root.appendChild(card);

  $("#sim-type").addEventListener("change", () => {
    $("#sim-reduction-wrap").style.display = $("#sim-type").value === "automate_activity" ? "" : "none";
    $("#sim-activity").parentElement.style.display = $("#sim-type").value === "add_capacity" ? "none" : "";
  });

  $("#sim-run").addEventListener("click", async () => {
    out.innerHTML = `<div class="spinner">Simulating 2,000 case replays…</div>`;
    const body = { dataset: state.dataset, type: $("#sim-type").value };
    if (body.type !== "add_capacity") body.activity = $("#sim-activity").value;
    if (body.type === "automate_activity") body.reduction_pct = Number($("#sim-reduction").value);
    try {
      const res = await api("/api/simulate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      renderSimResult(out, res);
    } catch (err) {
      out.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  });
};

function renderSimResult(out, res) {
  out.innerHTML = "";
  const r = res.results;
  const rows = [
    ["Mean case time (h)", r.mean_duration_hours],
    ["Median case time (h)", r.median_duration_hours],
    ["Slowest 10% (h)", r.p90_duration_hours],
  ];
  const max = Math.max(...rows.map(([, m]) => Math.max(m.baseline, m.scenario)), 1);
  const legend = el("p", { class: "sub" },
    `<span class="legend-swatch" style="background:var(--series-1)"></span>Baseline&nbsp;&nbsp;
     <span class="legend-swatch" style="background:var(--series-2)"></span>Scenario`);
  out.appendChild(legend);
  rows.forEach(([label, m]) => {
    [["", m.baseline], ["series-2", m.scenario]].forEach(([cls, val], i) => {
      const row = el("div", { class: "hbar-row" });
      row.appendChild(el("div", { class: "hbar-label" }, i === 0 ? esc(label) : ""));
      const track = el("div", { class: "hbar-track" });
      const fill = el("div", { class: `hbar-fill ${cls}` });
      fill.style.width = (100 * val / max).toFixed(1) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("div", { class: "hbar-value" }, fmt(val)));
      out.appendChild(row);
    });
    const deltaRow = el("div", { class: "hbar-row" });
    const cls = m.change_pct <= 0 ? "delta-down" : "delta-up";
    deltaRow.appendChild(el("div"));
    deltaRow.appendChild(el("div", { class: `hbar-label ${cls}` },
      `${m.change_pct <= 0 ? "▼" : "▲"} ${Math.abs(m.change_pct)}% ${m.change_pct <= 0 ? "faster" : "slower"}`));
    deltaRow.appendChild(el("div"));
    out.appendChild(deltaRow);
  });
  out.appendChild(el("p", { class: "mode-note" }, esc(res.note)));
}

/* ---------- Work design ---------- */
const MODE_META = {
  traditional_automation: ["Traditional automation", "var(--series-1)"],
  ai_with_review: ["AI + human review", "var(--series-5)"],
  ai_autonomous: ["Autonomous AI (low risk)", "var(--series-2)"],
  human_only: ["Human only", "var(--series-3)"],
};

RENDERERS.workdesign = async (root) => {
  const design = await api(`/api/work-design?dataset=${encodeURIComponent(state.dataset)}`);
  root.innerHTML = "";
  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "Who should do each step"));
  card.appendChild(el("p", { class: "sub" },
    "ProcessMind does not recommend automating everything — each step is matched to the execution mode its data supports. Sensitive decisions stay with people."));
  const t = el("table");
  t.innerHTML = "<tr><th>Step</th><th>Recommended mode</th><th>Volume</th><th>Rework</th><th>Why</th></tr>" +
    design.map((d) => {
      const [label, color] = MODE_META[d.mode];
      return `<tr>
        <td>${esc(d.activity)}</td>
        <td><span class="mode-badge"><span class="mode-dot" style="background:${color}"></span>${label}</span></td>
        <td>${d.frequency}×</td>
        <td>${d.rework_rate_pct}%</td>
        <td style="color:var(--text-secondary)">${d.rationale.map(esc).join("; ")}</td>
      </tr>`;
    }).join("");
  card.appendChild(t);
  root.appendChild(card);
};

/* ---------- Insights ---------- */
RENDERERS.insights = async (root) => {
  root.innerHTML = `<div class="spinner">Analyzing the process and writing the narrative… (first run can take up to a minute)</div>`;
  const res = await api(`/api/insights?dataset=${encodeURIComponent(state.dataset)}`);
  root.innerHTML = "";
  const note = res.mode === "ai"
    ? "Narrative written by Claude from the structured analysis results."
    : "Offline narrative (no AI credentials detected) — generated directly from the analysis data.";
  root.appendChild(el("p", { class: "mode-note" }, esc(note)));
  const card = el("div", { class: "card narrative" });
  card.innerHTML = mdToHtml(res.narrative);
  root.appendChild(card);
};

function mdToHtml(md) {
  const lines = esc(md).split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    const bolded = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    if (/^#{1,3}\s/.test(line)) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<h2>${bolded.replace(/^#+\s*/, "")}</h2>`;
    } else if (/^\s*[-*]\s/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${bolded.replace(/^\s*[-*]\s*/, "")}</li>`;
    } else if (line.trim() === "") {
      if (inList) { html += "</ul>"; inList = false; }
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<p>${bolded}</p>`;
    }
  }
  if (inList) html += "</ul>";
  return html;
}

/* ---------- Impact ---------- */
RENDERERS.impact = async (root) => {
  root.innerHTML = "";
  const card = el("div", { class: "card" });
  card.appendChild(el("h3", {}, "Did the change actually help?"));
  card.appendChild(el("p", { class: "sub" },
    "Pick the date a change went live. Cases opened before it form the baseline; cases after it show the new reality. Outcomes are recorded so ProcessMind learns which change types work."));
  const form = el("div", { class: "form-row" });
  form.innerHTML = `
    <label>Change date <input type="date" id="impact-date" /></label>
    <label>Change type
      <select id="impact-type">
        <option value="intake_form_fix">Intake / form fix</option>
        <option value="automation">Automation</option>
        <option value="added_capacity">Added capacity</option>
        <option value="reassignment">Reassignment</option>
        <option value="other">Other</option>
      </select>
    </label>
    <button class="primary" id="impact-run">Compare before / after</button>`;
  card.appendChild(form);
  const out = el("div");
  card.appendChild(out);
  root.appendChild(card);

  $("#impact-run").addEventListener("click", async () => {
    const date = $("#impact-date").value;
    if (!date) { out.innerHTML = `<div class="error-box">Choose a date first.</div>`; return; }
    out.innerHTML = `<div class="spinner">Comparing…</div>`;
    try {
      const res = await api("/api/impact", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset: state.dataset, split_date: date, change_type: $("#impact-type").value }),
      });
      const t = el("table");
      t.innerHTML = "<tr><th>Metric</th><th>Before</th><th>After</th><th>Change</th></tr>" +
        Object.entries(res.metrics).map(([k, m]) => {
          const cls = m.improved ? "delta-down" : "delta-up";
          return `<tr><td>${esc(k.replaceAll("_", " "))}</td><td>${m.before}</td><td>${m.after}</td>
            <td class="${cls}">${m.change_pct > 0 ? "+" : ""}${m.change_pct}% ${m.improved ? "✓ improved" : ""}</td></tr>`;
        }).join("");
      out.innerHTML = `<p><strong>Verdict: ${esc(res.verdict)}</strong> — ${res.before_cases} cases before vs ${res.after_cases} after.</p>`;
      out.appendChild(t);
    } catch (err) {
      out.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  });

  // knowledge base summary
  const kb = await api("/api/knowledge");
  const kcard = el("div", { class: "card" });
  kcard.appendChild(el("h3", {}, "What ProcessMind has learned"));
  const eff = kb.effectiveness;
  if (!Object.keys(eff.by_change_type).length) {
    kcard.appendChild(el("p", { class: "sub" }, "No measured outcomes recorded yet. Run before/after comparisons to build the knowledge base."));
  } else {
    const t = el("table");
    t.innerHTML = "<tr><th>Change type</th><th>Improved</th><th>Mixed</th><th>Not improved</th></tr>" +
      Object.entries(eff.by_change_type).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${v.improved || 0}</td><td>${v.mixed || 0}</td><td>${v["not improved"] || 0}</td></tr>`).join("");
    kcard.appendChild(t);
  }
  root.appendChild(kcard);
};

/* ---------- boot ---------- */
refreshDatasets().then(() => activateTab("overview"));
