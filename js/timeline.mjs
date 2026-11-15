// The Timeline view: H* (8141 full) then, at I*, 8288 full or leanPCS full.
// Every figure is read from data/timeline.json (web-v3/SCHEMA.md); nothing
// is typed here. Chips: "fixture" when the build drew from the fixture,
// "simulated" otherwise; inputs carry their own status from the timeline file.

import { $, el, clear, fmt, fmtSigned, fmtGas, fmtShare, isNum, chip, statusChip,
         segmented, control, details, runSize, intervalText } from "./util.mjs";
import { caveatsWhere, caveatBox, caveatLine } from "./caveats.mjs";

let T = null;
const state = {};

const BRANCH_NAMES = { "8288": "8288 full", leanpcs: "leanPCS full" };
const ROUTE_NAMES = {
  dependency: "dependency",
  pcs_client: "pcs_client",
  pcs_bundler: "pcs_bundler",
};
const ROUTE_TITLES = {
  dependency: "the leanPCS draft default: the signature is an ordinary 8288 dependency",
  pcs_client: "the wallet proves its own signature as a PCS claim in a client STARK",
  pcs_bundler: "a bundler proves many users' signatures in one PCS bundle",
};
const PQ_LABELS = { default: "schedule", low: "low", forced: "all PQ" };
const RESOURCE_NAMES = {
  gas: "gas limit", bytes: "block byte cap", dep_cap: "dependency cap",
  builder_proof: "builder's final proof", prover_pool: "mempool provers",
  uplink_el_dc: "EL uplink (datacentre)", uplink_el_home: "EL uplink (home)",
  uplink_el_builder: "EL uplink (builder)", uplink_cl_dc: "CL uplink (datacentre)",
  uplink_cl_home: "CL uplink (home)", uplink_cl_builder: "CL uplink (builder)",
  downlink_dc: "downlink (datacentre)", downlink_home: "downlink (home)",
  downlink_builder: "downlink (builder)", cpu_dc: "CPU (datacentre)",
  cpu_home: "CPU (home)", cpu_builder: "CPU (builder)",
  propagation_block: "block propagation", propagation_payload: "payload propagation",
  tx_delivery: "tx delivery to builder", mempool: "mempool evictions",
  demand: "demand (nothing binds)",
  none: "nothing (demand below every capacity)",
  pool: "builder had no more txs to add",
  proof_deadline: "builder's final-proof deadline",
  gas_limit: "gas limit",
  unresolved: "unresolved (no resource passed the test)",
};
// a label may name several co-binding resources, joined by "+" or ","
export function resourceName(label) {
  if (label === null || label === undefined) return "n/a";
  return String(label).split(/[+,]/).map((p) => RESOURCE_NAMES[p.trim()] || p.trim()).join(" + ");
}
export const BW_CLASSES = [["dc", "datacentre"], ["home", "home"], ["validator", "validator"],
                           ["supernode", "supernode"], ["builder", "builder"]];

export function keyId(step, branch, cand, gas, pq, route, cap) {
  const s = (v) => (v === null || v === undefined ? "None" : String(v));
  return [step, s(branch), cand, String(gas), pq, s(route), s(cap)].join("|");
}

function stepOf(id) { return T.steps.find((s) => s.id === id); }

function firstValue(leaf) {
  if (!leaf) return null;
  const v = leaf.value;
  if (v && typeof v === "object" && !Array.isArray(v)) return Object.values(v)[0];
  return v;
}

function initState() {
  const H = stepOf("H*"), I = stepOf("I*");
  state.candidate = firstValue(H.candidate) || "P99r";
  state.gas = { "H*": firstValue(H.gas_limit), "I*": firstValue(I.gas_limit) };
  state.pq = { "H*": "default", "I*": "default" };
  state.branch = "8288";
  state.depCap = firstValue(I.branch["8288"].settings["8288.block.dep_cap"]) ?? null;
  state.route = firstValue(I.branch.leanpcs.settings["leanpcs.sig_route"]) || "dependency";
}

export function currentKey(stepId, overrides = {}) {
  const st = { ...state, ...overrides };
  if (stepId === "H*") return keyId("H*", null, st.candidate, st.gas["H*"], st.pq["H*"], null, null);
  const route = st.branch === "leanpcs" ? st.route : null;
  return keyId("I*", st.branch, st.candidate, st.gas["I*"], st.pq["I*"], route, st.depCap);
}

function groupFor(stepId, overrides) { return T.groups[currentKey(stepId, overrides)] || null; }

function evChip(group) {
  if (group && group.fixture) return chip("fixture");
  const s = group.summary;
  const p = group.points[0];
  const size = runSize(s.nodes, s.seeds.length);
  return chip("simulated", `Simulated: v3 network simulator at ${String(p?.code_commit || "").slice(0, 12)}, ${size} `
    + `(seeds ${s.seeds.join(", ")}), ${group.points.length} sampled months; mean over seeds, then month-weighted (About explains)`,
    `sim · ${size}`);
}

let NA_NOTE = null;
let CI_OF = null;    // the group's per-seed intervals   // set per group: columns NA in some seeds or months
function kpi(label, value, unit, sub, chipNode, attrs = {}) {
  const partial = NA_NOTE && attrs.id ? NA_NOTE(attrs.id) : null;
  const interval = CI_OF && attrs.id ? intervalText(CI_OF[attrs.id]) : null;
  return el("div", { class: "kpi" + (attrs.big ? " big" : ""), "data-kpi": attrs.id || null },
    el("dt", {}, label, " ", chipNode),
    el("dd", {}, el("span", { class: isNum(value) ? "v" : "v na", "data-v": attrs.id || null },
      isNum(value) ? fmt(value) : "n/a"),
      unit ? el("span", { class: "unit" }, unit) : null,
      sub ? el("span", { class: "sub" }, sub) : null,
      partial ? el("span", { class: "sub", "data-partial": attrs.id }, partial) : null,
      interval ? el("span", { class: "sub", "data-ci": attrs.id }, interval) : null));
}

function naNote(group) {
  return (column) => {
    const months = group.summary.na_months?.[column] || 0;
    const seeds = group.points.reduce((n, p) => n + (p.na_seeds?.[column] || 0), 0);
    const total = group.points.reduce((n, p) => n + p.seeds.length, 0);
    if (!months && !seeds) return null;
    return `n/a in ${seeds} of ${total} runs; mean of the others`;
  };
}

function latencyClass(stepId) {
  // only pcs_client txs are PCS-claim txs; pcs_bundler users are PQ txs
  return stepId === "I*" && state.branch === "leanpcs" && state.route === "pcs_client" ? "pcs" : "pq1";
}

function utilBars(util) {
  const items = Object.entries(util || {}).filter(([, v]) => isNum(v))
    .sort((a, b) => b[1] - a[1]).slice(0, 4);
  if (!items.length) return el("p", { class: "small dim" }, "no utilisation columns");
  return el("div", { class: "utilbars" }, items.map(([k, v]) =>
    el("div", { class: "utilbar", "data-util": k },
      el("span", {}, RESOURCE_NAMES[k] || k),
      el("span", { class: "t" }, el("span", { style: `width:${Math.min(100, v * 100).toFixed(1)}%` })),
      el("span", { class: "v" }, fmt(v, 2)))));
}

// The step's label has the most month weight; say which other labels the runs had.
function otherBindings(s) {
  const w = s.binding_weights || {};
  const total = Object.values(w).reduce((a, b) => a + b, 0);
  const others = Object.entries(w).filter(([k]) => k !== s.binding).sort((a, b) => b[1] - a[1]);
  if (!others.length || !total) return null;
  return el("p", { class: "small", "data-other-bindings": "1" }, `Runs disagree: ${fmtShare(w[s.binding] / total)} of the month weight says this; `
    + others.map(([k, v]) => `${resourceName(k)} ${fmtShare(v / total)}`).join(", ") + ".");
}

function results(stepId) {
  const group = groupFor(stepId);
  const box = el("div", { class: "stack", "data-results": stepId });
  if (!group) {
    box.append(el("p", { class: "small", "data-missing": currentKey(stepId) },
      "Not run: no precomputed run for this combination. Simulate runs a small network; the command line runs the full one (About)."));
    return box;
  }
  const s = group.summary;
  const c = () => evChip(group);
  const cls = latencyClass(stepId);
  NA_NOTE = naNote(group);
  CI_OF = s.ci || null;
  box.append(el("dl", { class: "kpis" },
    kpi("Included signatures", s.sig_per_s, "sig/s", `PQ tx/s ${fmt(s.pq_tps)}`, c(), { big: true, id: "sig_per_s" }),
    kpi("All transactions", s.all_tps, "tx/s", `ECDSA ${fmt(s.ecdsa_all_tps)} tx/s, background included`, c(), { id: "all_tps" }),
    kpi(cls === "pcs" ? "PCS-claim tx latency p50" : "PQ tx latency p50", s[`incl_latency_${cls}_p50_s`], "s",
        `p95 ${isNum(s[`incl_latency_${cls}_p95_s`]) ? fmt(s[`incl_latency_${cls}_p95_s`]) + " s" : "n/a"}; not included by the end: ${fmtShare(s[`incl_${cls}_censored_share`])}`,
        c(), { id: `incl_latency_${cls}_p50_s` }),
    kpi("Block reaches 90 % of nodes", s.block_reach_t90_p50_ms, "ms",
        `median block; p90 block ${fmt(s.block_reach_t90_p90_ms)} ms`, c(), { id: "block_reach_t90_p50_ms" }),
    kpi("Fee per PQ transaction, p50", s.user_fee_pq_p50_gwei, "gwei",
        isNum(s.user_fee_ecdsa_p50_gwei) ? `ECDSA p50 ${fmt(s.user_fee_ecdsa_p50_gwei)} gwei` : null, c(), { id: "user_fee_pq_p50_gwei" }),
    kpi("Home node bandwidth, up", s.bw_home_up_mean_mbps, "Mbit/s",
        `down ${fmt(s.bw_home_down_mean_mbps)} Mbit/s`, c(), { id: "bw_home_up_mean_mbps" })));
  NA_NOTE = null;
  CI_OF = null;
  const commits = [...new Set(group.points.map((p) => String(p.code_commit || "").split("+")[0].slice(0, 7)))];
  box.append(el("p", { class: "small dim", "data-commits": commits.join(",") },
    `Rows from simulator commit ${commits.join(", ")}`
    + (stepId === "I*" && group.points.every((p) => p.ecdsa_bg_tps === 0) ? " (no ECDSA background: unaffected by the ghost-ECDSA fix, carried over)" : "") + "."));
  const binds = el("div", { class: "stack", style: "gap:6px" },
    el("p", { class: "binds", "data-binding": s.binding },
      el("strong", {}, "What binds: "), resourceName(s.binding), " ", c(),
      el("span", { class: "small" }, ` (method: ${group.points[0]?.binding_method ?? "n/a"})`)),
    otherBindings(s),
    utilBars(s.util));
  box.append(binds);
  const bw = el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "node class"),
      el("th", { class: "n" }, "up, Mbit/s"), el("th", { class: "n" }, "down, Mbit/s"))),
    el("tbody", {}, BW_CLASSES.map(([k, name]) => el("tr", { "data-bw": k },
      el("th", { scope: "row" }, name),
      el("td", { class: "n" }, fmt(s[`bw_${k}_up_mean_mbps`])),
      el("td", { class: "n" }, fmt(s[`bw_${k}_down_mean_mbps`]))))));
  box.append(details("Bandwidth per node class", el("div", { class: "scroll" }, bw),
    el("p", { class: "note" }, "Mean over the class's nodes and the measured window, with background load. ", c())));
  return box;
}

function stepCard(stepId) {
  const st = stepOf(stepId);
  const card = el("article", { class: "step", "data-step": stepId });
  const deployment = stepId === "H*" ? "8141 full" : BRANCH_NAMES[state.branch];
  const months = st.months?.value;
  card.append(el("h3", {}, el("span", {}, `${stepId} · ${deployment}`),
    el("span", { class: "when" }, `from ${st.start?.value}, ${months} months `,
      statusChip(st.months?.status, st.months?.source))));
  if (stepId === "I*") {
    card.append(el("div", { class: "branch-switch" }, control("Branch at I*",
      segmented("Branch at I*", Object.entries(BRANCH_NAMES).map(([v, l]) => ({ value: v, label: l })),
        state.branch, (v) => { state.branch = v; render(); }))));
  }
  const ctls = el("div", { class: "ctls" });
  const gasLeaf = st.gas_limit;
  ctls.append(control("Gas limit", segmented(`Gas limit at ${stepId}`, T.axes.gas_limit.map((g) =>
    ({ value: g, label: fmtGas(g), title: g === firstValue(gasLeaf) ? `default: ${gasLeaf.reason || ""}` : null })),
    state.gas[stepId], (v) => { state.gas[stepId] = v; render(); }), " ", statusChip(gasLeaf?.status, `${gasLeaf?.source}. ${gasLeaf?.reason || ""}`)));
  const pqLeaf = st.pq_share;
  const sched = (v) => Object.entries(v || {}).map(([m, x]) => `${m}: ${fmtShare(x)}`).join(", ");
  ctls.append(control("PQ adoption", el("div", {}, segmented(`PQ share at ${stepId}`, T.axes.pq_share.map((p) => ({
    value: p, label: PQ_LABELS[p], title: `${T.pq_share_variants[p]}: ${sched(st.pq_variants[p])}`,
  })), state.pq[stepId], (v) => { state.pq[stepId] = v; render(); }), " ", statusChip(pqLeaf?.status, pqLeaf?.source),
    el("span", { class: "small", "data-pq-schedule": stepId, style: "display:block" }, sched(st.pq_variants[state.pq[stepId]])))));
  if (stepId === "I*") {
    const capLeaf = st.branch[state.branch].settings["8288.block.dep_cap"];
    ctls.append(control("Dependency cap", el("div", {}, segmented("Dependency cap", T.axes.dep_cap.map((c) =>
      ({ value: c, label: c === null ? "none" : c.toLocaleString("en-US") })), state.depCap,
      (v) => { state.depCap = v; render(); }), " ", statusChip(capLeaf?.status, capLeaf?.source))));
    if (state.branch === "leanpcs") {
      const rLeaf = st.branch.leanpcs.settings["leanpcs.sig_route"];
      ctls.append(control("Signature route", el("div", {}, segmented("Signature route", T.axes.sig_route.map((r) =>
        ({ value: r, label: ROUTE_NAMES[r], title: ROUTE_TITLES[r] })), state.route,
        (v) => { state.route = v; render(); }), " ", statusChip(rLeaf?.status, rLeaf?.source))));
    }
  }
  card.append(ctls);
  if (stepId === "I*") {
    for (const item of caveatsWhere((a) => a.step === "I*" && (a.branch || []).includes(state.branch)
        && (a.gas_limit === undefined || a.gas_limit === state.gas["I*"])
        && (a.route === undefined || (state.branch === "leanpcs" && a.route === state.route)))) {
      card.append(caveatBox(item));
    }
  }
  card.append(results(stepId));
  const fee = caveatsWhere((a) => a.fees)[0];
  if (fee) card.append(details("About the fee figure", el("p", { class: "small" }, fee.text), el("p", { class: "small dim" }, `Source: ${fee.section}.`)));
  return card;
}

function epsBox(stepId, input, title) {
  const group = groupFor(stepId);
  const items = (T.elasticity[currentKey(stepId)] || {})[input] || [];
  const box = el("div", { class: "box", "data-eps": `${stepId}|${input}` }, el("h4", {}, title));
  if (!items.length) {
    box.append(el("p", { class: "small dim" }, "not run for this combination (What was not run, below)"));
    return box;
  }
  const e = items[0];
  const size = runSize(e.base?.nodes, e.eps_fair?.seeds ?? "?");
  if (!isNum(e.eps_fair?.mean)) {
    box.append(el("p", { class: "small dim" }, `not defined at ${e.month}: ${e.note || "no estimate"}`));
    return box;
  }
  const c = e.fixture ? chip("fixture") : chip("simulated",
    `Simulated: paired central differences over seeds (common random numbers), Student-t 95 % interval; ${size}`, `sim · ${size}`);
  const derived = e.fixture ? chip("fixture") : chip("derived",
    `Derived: ε fair (simulated) divided by the PQ share of the binding resource, under kind-blind fee rationing (notes/phase4/build-s4.md S4-5); ${size}`,
    `derived · ${size}`);
  box.append(
    el("p", {}, el("span", { class: "mono", "data-v": "eps_supply" }, fmtSigned(e.eps_supply.mean)),
      " ε", el("sub", {}, "supply"), " ", derived,
      el("span", { class: "small" }, ` [${fmt(e.eps_supply.ci_lo, 2)}, ${fmt(e.eps_supply.ci_hi, 2)}], ${e.eps_supply.seeds} seeds`)),
    el("p", { class: "small" }, el("span", { class: "mono", "data-v": "eps_fair" }, fmtSigned(e.eps_fair.mean)),
      " ε", el("sub", {}, "fair"), " ", c,
      el("span", { class: "small" }, ` [${fmt(e.eps_fair.ci_lo, 2)}, ${fmt(e.eps_fair.ci_hi, 2)}]`),
      ` ÷ PQ share of the binding resource (${resourceName(e.resource)}) ${fmtShare(e.s_pq)}.`),
    ...(stepId === "I*" ? caveatsWhere((a) => (a.eps_branch || []).includes(state.branch)).map(caveatLine) : []),
    el("p", { class: "small" }, `Saturated runs at the ${e.month} settings (offered ${fmt(e.base?.offered_pq_tps)} PQ tx/s, not the timeline's demand), inputs moved ±${fmt(e.h * 100, 2)} %.`));
  return box;
}

function renderEps() {
  const box = clear($("#tl-eps"));
  box.append(el("p", { class: "small" },
    "Signature bytes (or permutations) +1 % changes included PQ signatures by about ε %. ε",
    el("sub", {}, "fair"), " is simulated with ECDSA traffic competing for the same blocks; ε",
    el("sub", {}, "supply"), " (headline) divides it by the PQ share of what binds: the change if PQ traffic alone bore it."));
  const grid = el("div", { class: "eps" });
  const I = `I* ${BRANCH_NAMES[state.branch]}`;
  grid.append(epsBox("H*", "sig_bytes", "H*: signature bytes"), epsBox("H*", "perms", "H*: permutations"),
    epsBox("I*", "sig_bytes", `${I}: signature bytes`), epsBox("I*", "perms", `${I}: permutations`));
  box.append(grid);
}

function row(cells, attrs = {}) {
  return el("tr", attrs, cells.map((c, i) => i === 0 ? el("th", { scope: "row" }, c) : el("td", { class: "n" }, c)));
}

export function labelCells(head, rows) {
  // every cell carries its column name, shown before it when the table stacks (narrow screens)
  for (const tr of rows) [...tr.children].forEach((cell, i) => { if (i) cell.dataset.label = head[i]; });
  return rows;
}

function table(head, rows) {
  return el("div", { class: "scroll" }, el("table", { class: "mid stackable" },
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i ? "n" : null }, h)))),
    el("tbody", {}, labelCells(head, rows))));
}

// sig/s over all seeds, sig/s of seed 0 alone (so 1-seed cells compare like with like), the runs, binds, latency, fee
function seed0(g) { return g?.summary?.ci?.sig_per_s?.per_seed?.["0"]; }
function runsChip(g) {
  if (!g) return "";
  if (g.fixture) return chip("fixture");
  return chip("simulated", `${runSize(g.summary.nodes, g.summary.seeds.length)} (seeds ${g.summary.seeds.join(", ")})`,
    `sim · ${runSize(g.summary.nodes, g.summary.seeds.length)}`);
}
function sumCells(g, cls = "pq1") {
  if (!g) return ["not run", "", "", "", "", ""];
  const s = g.summary;
  return [fmt(s.sig_per_s), fmt(seed0(g)), runsChip(g), resourceName(s.binding),
          fmt(s[`incl_latency_${cls}_p50_s`]), fmt(s.user_fee_pq_p50_gwei)];
}
const SUM_HEAD = ["sig/s", "seed 0 sig/s", "runs", "binds", "latency p50, s", "fee p50, gwei"];

function renderMore() {
  const box = clear($("#tl-more"));
  // a fresh chip per use: a DOM node can live in one place only
  const fx = () => (T.fixture ? chip("fixture") : chip("simulated"));
  // 1. per sampled month
  const monthRows = [];
  for (const stepId of ["H*", "I*"]) {
    const g = groupFor(stepId);
    if (!g) continue;
    const cls = latencyClass(stepId);
    for (const p of g.points) {
      monthRows.push(row([`${stepId} ${p.month}`, fmt(p.sig_per_s), fmt(p.all_tps),
        resourceName(p.binding), fmt(p[`incl_latency_${cls}_p50_s`]),
        `${p.seeds.length} seed${p.seeds.length === 1 ? "" : "s"}, ${p.nodes} nodes`], { "data-point": `${stepId}|${p.month}` }));
    }
  }
  box.append(details("Per sampled month",
    table(["step, month", "sig/s", "tx/s", "binds", "latency p50, s", "runs"], monthRows),
    el("p", { class: "note" }, "Each month is a snapshot of a few slots, not a simulated month. The step's figure above is the trapezoid mean of these months. ", fx())));
  // 2. I* side by side
  const sideRows = [];
  for (const [branch, route] of [["8288", null], ["leanpcs", "dependency"], ["leanpcs", "pcs_client"], ["leanpcs", "pcs_bundler"]]) {
    const g = T.groups[currentKey("I*", { branch, route: route || state.route })];
    const label = branch === "8288" ? "8288 full" : `leanPCS, ${route}`;
    sideRows.push(row([label, ...sumCells(g, route === "pcs_client" ? "pcs" : "pq1")], { "data-side": `${branch}|${route}` }));
  }
  box.append(details("I*: 8288 and the leanPCS routes side by side",
    table(["at I*", ...SUM_HEAD], sideRows),
    el("p", { class: "note" }, `At the gas limit, PQ share, dependency cap and set chosen above. `, fx())));
  // 3. dependency caps
  const capRows = T.axes.dep_cap.map((cap) => row([cap === null ? "none" : cap.toLocaleString("en-US"),
    ...sumCells(T.groups[currentKey("I*", { depCap: cap })], latencyClass("I*"))], { "data-cap": String(cap) }));
  box.append(details("I*: dependency cap none, 256, 4,096",
    table(["deps per block", ...SUM_HEAD], capRows),
    el("p", { class: "note" }, `${BRANCH_NAMES[state.branch]}${state.branch === "leanpcs" ? ", " + state.route : ""}. `, fx())));
  // 4. candidate sets
  const candRows = T.candidates.map((c) => {
    const h = T.groups[currentKey("H*", { candidate: c.name })];
    const i = T.groups[currentKey("I*", { candidate: c.name })];
    return row([`${c.name}${c.name === state.candidate ? " (shown)" : ""}`, c.sig_bytes.toLocaleString("en-US"),
      String(c.perms), h ? fmt(h.summary.sig_per_s) : "not run", h ? fmt(seed0(h)) : "", runsChip(h),
      i ? fmt(i.summary.sig_per_s) : "not run", i ? fmt(seed0(i)) : "", runsChip(i)],
      { "data-cand": c.name });
  });
  box.append(details("Candidate sets",
    table(["set", "bytes", "perms", "H* sig/s", "H* seed 0", "H* runs", "I* sig/s", "I* seed 0", "I* runs"], candRows),
    el("p", { class: "note" }, "Bytes and permutations per signature from the simulator's candidate table ",
      statusChip("R", T.candidates[0].source), "; throughput ", fx(), ". P99r is the working set, B0 today's leanSPHINCS.")));
  // 5. inputs
  const inputRows = [];
  for (const stepId of ["H*", "I*"]) {
    const st = stepOf(stepId);
    for (const name of ["demand_tps", "pq_share", "gas_limit", "slot_ms", "focil", "mix", "months"]) {
      const leaf = st[name];
      if (!leaf) continue;
      const v = leaf.value && typeof leaf.value === "object"
        ? Object.entries(leaf.value).map(([k, x]) => `${k}: ${typeof x === "number" && x >= 1e6 ? fmtGas(x) : x}`).join(", ")
        : String(leaf.value);
      inputRows.push(el("tr", {}, el("th", { scope: "row" }, `${stepId} ${name.replace("_", " ")}`),
        el("td", { style: "overflow-wrap:anywhere" }, v, " ", statusChip(leaf.status, `${leaf.source}${leaf.reason ? ". " + leaf.reason : ""}`))));
    }
  }
  box.append(details("Timeline inputs and how the figures are made",
    el("p", { class: "note" }, "From ", el("code", {}, T.timeline_file), `. Each step is sampled at ${T.points_per_step.value} months; a sample is one load-mode run of a few slots per seed. A step's figure is the mean over seeds, then the trapezoid mean over its sampled months. What binds is the most frequent label. Months are never simulated.`),
    el("div", { class: "scroll" }, el("table", {}, el("tbody", {}, inputRows))),
    el("p", { class: "note" }, "Each chip's tooltip gives the source and the reason.")));
}

function renderGlobal() {
  const box = clear($("#tl-global"));
  box.append(control("Parameter set", segmented("Parameter set", T.candidates.slice(0, 2).map((c) =>
    ({ value: c.name, label: c.name, title: `${c.role}: ${c.sig_bytes} B, ${c.perms} perms` })),
    state.candidate, (v) => { state.candidate = v; render(); }), " ",
    statusChip(stepOf("H*").candidate?.status, stepOf("H*").candidate?.source)));
  const select = el("select", { "aria-label": "Other parameter sets", id: "tl-cand",
    onchange: (e) => { state.candidate = e.target.value; render(); } },
    T.candidates.map((c) => el("option", { value: c.name, selected: c.name === state.candidate }, `${c.name} (${c.sig_bytes} B, ${c.perms} perms)`)));
  box.append(control("Any campaign set", select));
}

export function render() {
  renderGlobal();
  const path = clear($("#tl-path"));
  path.append(stepCard("H*"), el("div", { class: "arrow", "aria-hidden": "true" }, "→"), stepCard("I*"));
  renderEps();
  renderMore();
  document.dispatchEvent(new CustomEvent("timeline-rendered"));
}

export function getState() { return JSON.parse(JSON.stringify(state)); }

export function initTimeline(data) {
  T = data;
  initState();
  render();
}
