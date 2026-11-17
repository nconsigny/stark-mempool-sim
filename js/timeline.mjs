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
  pool: "builder's tx pool ran out (a client setting in the model)",
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

export function capKey(stepId, overrides = {}) {
  const st = { ...state, ...overrides };
  const n = (v) => (v === null || v === undefined ? "None" : String(v));
  if (stepId === "H*") return ["H*", "None", st.candidate, String(st.gas["H*"]), "None", "None"].join("|");
  return ["I*", st.branch, st.candidate, String(st.gas["I*"]), st.branch === "leanpcs" ? st.route : "None", n(st.depCap)].join("|");
}
function capFor(stepId, overrides) { return (T.capacity?.cells || {})[capKey(stepId, overrides)] || null; }

function capChip(c) {
  const title = `${c.saturated ? "Saturated runs (more PQ demand offered than fits)" : "Not a saturated run (timeline points)"}: ${runSize(c.nodes, c.seeds.length)} (seeds ${c.seeds.join(", ")}), commit ${c.commit.join(", ")}; ${c.file}; source ${c.source}`;
  return chip("simulated", title, `${c.saturated ? "sim" : "not saturated · timeline run"} · ${runSize(c.nodes, c.seeds.length)}`);
}

function capacityBlock(stepId) {
  const box = el("section", { class: "stack", "data-capacity": stepId });
  const gas = state.gas[stepId];
  box.append(el("h4", {}, `Capacity at ${fmtGas(gas)} gas`));
  const c = capFor(stepId);
  if (!c) {
    box.append(el("p", { class: "small", "data-cap-missing": capKey(stepId) },
      "No capacity record for this cell (set, gas limit, route, cap) in the capacity campaign; no figure is shown rather than a demand-bound one. Other saturated rows are under \"What binds past the gas limit\"."));
    return box;
  }
  const ch = () => capChip(c);
  if (!c.saturated) {
    box.append(el("p", { class: "small caveat", "data-cap-not-saturated": "1" }, c.note || `Not a saturated run: ${c.source}.`));
  }
  const bound = c.saturated ? c.analytic_gas_residual_tps : c.analytic_builder_proof_tps;
  const ci = (x) => intervalText(x);
  box.append(el("dl", { class: "kpis" },
    kpi(c.saturated ? "PQ signatures it can include" : "PQ signatures included at the cap", c.sig_per_s.mean, "sig/s", `${fmt(c.pq_tps.mean)} PQ tx/s; ${ci(c.sig_per_s)}`, ch(), { big: true, id: "cap_sig" }),
    kpi("ECDSA included alongside", c.ecdsa_tps.mean, "ECDSA tx/s", `all txs ${fmt(c.total_tps.mean)} tx/s; ECDSA offered: ${c.ecdsa_rule || "n/a"}`, ch(), { id: "cap_ecdsa" }),
    kpi(c.saturated ? "Analytic gas bound after today's ECDSA" : "Analytic bound: builder's final proof", bound, "PQ tx/s",
      `PQ only ${fmt(c.analytic_gas_tps)}; byte cap ${fmt(c.analytic_bytes_tps)}${isNum(c.analytic_dep_cap_tps) ? `; dependency cap ${fmt(c.analytic_dep_cap_tps)}` : ""}`
      + (c.saturated && isNum(c.check?.ratio) ? `; simulated = ${fmt(c.check.ratio, 3)} × the ${c.check.bound_key.replace("_", " ")} bound` : "")
      + "",
      chip("modelled", "The simulator's analytic bounds (gas limit over gas per PQ tx), not a simulation"), { id: "cap_bound" }),
    kpi(`${c.latency_class === "pcs" ? "PCS-claim" : "PQ"} tx latency ${c.saturated ? "at saturation" : "at the cap"}, p50`, c.latency_p50_s?.mean, "s",
      `not included by the end: ${fmtShare(c.censored_share)}`, ch(), { id: "cap_latency" }),
    kpi("Payload reaches 90 % of nodes", c.payload_reach_t90_ms, "ms", `block ${fmt(c.block_reach_t90_ms)} ms`, ch(), { id: "cap_reach" }),
    kpi(`Home node bandwidth ${c.saturated ? "at saturation" : "at the cap"}, up`, c.home_up_mbps, "Mbit/s", `down ${fmt(c.home_down_mbps)} Mbit/s`, ch(), { id: "cap_bw" })));
  const counts = Object.entries(c.slot_binding_counts || {}).map(([k, n]) => `${resourceName(k)} in ${n} of ${c.slots_window * c.seeds.length} slots`).join("; ");
  const slotTop = Object.entries(c.slot_binding_counts || {}).sort((a, b) => b[1] - a[1])[0];
  box.append(el("p", { class: "binds", "data-cap-binding": [...new Set(c.binding)].join(";") },
    el("strong", {}, "What binds: "), slotTop ? resourceName(slotTop[0])
      : (!c.saturated && isNum(c.analytic_builder_proof_tps) ? resourceName("proof_deadline") + " (the cap in the note)" : [...new Set(c.binding)].map(resourceName).join("; ")), " ", ch(),
    el("span", { class: "small" }, counts ? ` (per slot: ${counts}; utilisation label of the runs: ${[...new Set(c.binding)].map(resourceName).join("; ")})`
      : ` (utilisation label of the runs${c.saturated ? "" : "; the cap is the builder's proof, see the note"})`)),
    utilBars(Object.fromEntries(c.util_top.filter(([k]) => k !== "demand"))),
    ...("pool" in (c.slot_binding_counts || {}) ? caveatsWhere((a) => a.cap_pool).map((item) =>
      el("p", { class: "small caveat", "data-caveat": item.id }, `${item.title}: the builder ran out of pooled txs in ${c.slot_binding_counts.pool} of ${c.slots_window * c.seeds.length} slots, so this is below the gas bound. ${item.text} The pool-lifted rows under "What binds past the gas limit" show the gas bound reached.`)) : []),
    ...(c.variants || []).map((v) => el("p", { class: "small", "data-cap-variant": v.builder_p },
      `With ${v.builder_p} builder provers: ${fmt(v.sig_per_s.mean)} sig/s (${fmt(v.pq_tps.mean)} PQ tx/s), ${fmt(v.ecdsa_tps.mean)} ECDSA tx/s alongside; binds per slot: ${Object.entries(v.slot_binding_counts || {}).map(([k, n]) => `${resourceName(k)} ${n} of ${v.slots_window * v.seeds.length}`).join("; ") || [...new Set(v.binding)].map(resourceName).join("; ")}; ${v.saturated ? "saturated" : "not saturated"}. `, capChip(v))),
    ...(c.saturated
      ? [el("p", { class: "small dim" }, `Offered ${fmt(c.offered_tps)} PQ tx/s (${fmt(c.saturate_factor, 2)} × the smallest analytic bound) and ${fmt(c.offered_ecdsa_tps)} ECDSA tx/s (today's rate × the same factor); simulator commit ${c.commit.join(", ")}; ${c.source.startsWith("reused") ? "reused rows of the same code, " + c.source.slice(7) : "run for this view"}.`)]
      : []));
  return box;
}

function demandBlock(stepId) {
  const st = stepOf(stepId);
  const box = el("section", { class: "stack secondary", "data-demand": stepId });
  const dLeaf = st.demand_tps, pqLeaf = st.pq_share;
  const sched = (v) => Object.entries(v || {}).map(([m, x]) => `${m}: ${typeof x === "number" && x <= 1 ? fmtShare(x) : fmt(x)}`).join(", ");
  box.append(el("h4", {}, "At an assumed demand"),
    el("p", { class: "small", "data-assumption": stepId }, el("strong", {}, "Assumption, not a result: "),
      `offered load ${sched(dLeaf?.value)} tx/s (${dLeaf?.reason || ""}) `, statusChip(dLeaf?.status, dLeaf?.source),
      `; PQ share ${sched(st.pq_variants[state.pq[stepId]])} `, statusChip(pqLeaf?.status, pqLeaf?.source), "."));
  box.append(control("PQ adoption", segmented(`PQ share at ${stepId}`, T.axes.pq_share.map((p) => ({
    value: p, label: PQ_LABELS[p], title: `${T.pq_share_variants[p]}: ${sched(st.pq_variants[p])}`,
  })), state.pq[stepId], (v) => { state.pq[stepId] = v; render(); })));
  if (stepId === "I*") {
    for (const item of caveatsWhere((a) => a.demand_only && a.step === "I*" && (a.branch || []).includes(state.branch)
        && (a.gas_limit === undefined || a.gas_limit === state.gas["I*"]))) box.append(caveatBox(item));
  }
  const group = groupFor(stepId);
  if (!group) {
    box.append(el("p", { class: "small", "data-missing": currentKey(stepId) }, "Not run at this assumed demand."));
    return box;
  }
  const s = group.summary;
  const c = () => evChip(group);
  const cls = latencyClass(stepId);
  NA_NOTE = naNote(group);
  CI_OF = s.ci || null;
  box.append(el("dl", { class: "kpis" },
    kpi("Blocks full", isNum(s.block_gas_fill_mean) ? s.block_gas_fill_mean * 100 : null, "% of the gas limit", null, c(), { id: "block_gas_fill_mean" }),
    kpi(cls === "pcs" ? "PCS-claim tx latency p50" : "PQ tx latency p50", s[`incl_latency_${cls}_p50_s`], "s",
        `p95 ${isNum(s[`incl_latency_${cls}_p95_s`]) ? fmt(s[`incl_latency_${cls}_p95_s`]) + " s" : "n/a"}; not included by the end: ${fmtShare(s[`incl_${cls}_censored_share`])}`,
        c(), { id: `incl_latency_${cls}_p50_s` }),
    kpi("Home node bandwidth, up", s.bw_home_up_mean_mbps, "Mbit/s", `down ${fmt(s.bw_home_down_mean_mbps)} Mbit/s`, c(), { id: "bw_home_up_mean_mbps" })));
  NA_NOTE = null;
  CI_OF = null;
  const demandBound = ["demand", "none"].includes(s.binding);
  box.append(el("p", { class: "small", "data-demand-binding": s.binding }, demandBound
    ? "Nothing binds at this load: every offered tx is included, so the throughput here is the assumed offer, not a result, and is not shown as one."
    : `What binds at this load: ${resourceName(s.binding)}.`, " ", c(), otherBindings(s)));
  const commits = [...new Set(group.points.map((p) => String(p.code_commit || "").split("+")[0].slice(0, 7)))];
  box.append(el("p", { class: "small dim", "data-commits": commits.join(",") },
    `Rows from simulator commit ${commits.join(", ")}`
    + (stepId === "I*" && group.points.every((p) => p.ecdsa_bg_tps === 0) ? " (no ECDSA background: unaffected by the ghost-ECDSA fix, carried over)" : "") + "."));
  const bw = el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "node class"),
      el("th", { class: "n" }, "up, Mbit/s"), el("th", { class: "n" }, "down, Mbit/s"))),
    el("tbody", {}, BW_CLASSES.map(([k, name]) => el("tr", { "data-bw": k },
      el("th", { scope: "row" }, name),
      el("td", { class: "n" }, fmt(s[`bw_${k}_up_mean_mbps`])),
      el("td", { class: "n" }, fmt(s[`bw_${k}_down_mean_mbps`]))))));
  const fee = caveatsWhere((a) => a.fees)[0];
  box.append(details("Bandwidth per node class at this load", el("div", { class: "scroll" }, bw), el("p", { class: "note" }, c())),
    details("Simulated fee path at this load (a transient)",
      el("p", { class: "small", "data-sim-fee": stepId }, `Fee per PQ tx p50 ${fmt(s.user_fee_pq_p50_gwei)} gwei; base fee at the end of the snapshot ${fmt(s.base_fee_end_gwei)} gwei. `, c()),
      el("p", { class: "small" }, `Each snapshot is 12 slots starting from today's median base fee, at blocks ${fmt(isNum(s.block_gas_fill_mean) ? s.block_gas_fill_mean * 100 : null)} % full: a transient, not an equilibrium. Use the gas per transaction below instead.`),
      fee ? el("p", { class: "small dim" }, `${fee.text} Source: ${fee.section}.`) : null));
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
    for (const item of caveatsWhere((a) => a.step === "I*" && !a.demand_only && (a.branch || []).includes(state.branch)
        && (a.gas_limit === undefined || a.gas_limit === state.gas["I*"])
        && (a.route === undefined || (state.branch === "leanpcs" && a.route === state.route)))) {
      card.append(caveatBox(item));
    }
  }
  card.append(capacityBlock(stepId), demandBlock(stepId));
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

function runsChip(g) {
  if (!g) return "";
  if (g.fixture) return chip("fixture");
  return chip("simulated", `${runSize(g.summary.nodes, g.summary.seeds.length)} (seeds ${g.summary.seeds.join(", ")})`,
    `sim · ${runSize(g.summary.nodes, g.summary.seeds.length)}`);
}
// capacity sig/s with its runs, gas per PQ tx, latency at the assumed demand
function cmpCells(stepId, overrides, cls = "pq1") {
  const c = capFor(stepId, overrides);
  const g = T.groups[currentKey(stepId, overrides)];
  return [c ? fmt(c.sig_per_s.mean) : "not run", c ? capChip(c) : "",
          g ? fmt(g.summary.gas_per_pq_tx) : "not run",
          g ? fmt(g.summary[`incl_latency_${cls}_p50_s`]) : "", g ? runsChip(g) : ""];
}
const CMP_HEAD = ["capacity, sig/s", "capacity runs", "gas per PQ tx", "latency p50 at assumed demand, s", "demand runs"];

function renderMore() {
  const box = clear($("#tl-more"));
  const fx = () => (T.fixture ? chip("fixture") : chip("simulated"));
  const monthRows = [];
  for (const stepId of ["H*", "I*"]) {
    const g = groupFor(stepId);
    if (!g) continue;
    const cls = latencyClass(stepId);
    for (const p of g.points) {
      monthRows.push(row([`${stepId} ${p.month}`, fmt(isNum(p.block_gas_fill_mean) ? p.block_gas_fill_mean * 100 : null),
        resourceName(p.binding), fmt(p[`incl_latency_${cls}_p50_s`]),
        `${p.seeds.length} seed${p.seeds.length === 1 ? "" : "s"}, ${p.nodes} nodes`], { "data-point": `${stepId}|${p.month}` }));
    }
  }
  box.append(details("Assumed demand, per sampled month",
    table(["step, month", "blocks full, %", "binds", "latency p50, s", "runs"], monthRows),
    el("p", { class: "note" }, "Each month is a 12-slot snapshot at that month's assumed load, not a simulated month. ", fx())));
  const sideRows = [];
  for (const [branch, route] of [["8288", null], ["leanpcs", "dependency"], ["leanpcs", "pcs_client"], ["leanpcs", "pcs_bundler"]]) {
    const o = { branch, route: route || state.route };
    sideRows.push(row([branch === "8288" ? "8288 full" : `leanPCS, ${route}`, ...cmpCells("I*", o, route === "pcs_client" ? "pcs" : "pq1")], { "data-side": `${branch}|${route}` }));
  }
  box.append(details("I*: 8288 and the leanPCS routes side by side",
    table(["at I*", ...CMP_HEAD], sideRows),
    el("p", { class: "note" }, "At the gas limit, dependency cap and set chosen above; latency at the PQ adoption chosen above.")));
  const capRows = T.axes.dep_cap.map((cap) => row([cap === null ? "none" : cap.toLocaleString("en-US"),
    ...cmpCells("I*", { depCap: cap }, latencyClass("I*"))], { "data-cap": String(cap) }));
  box.append(details("I*: dependency cap none, 256, 4,096",
    table(["deps per block", ...CMP_HEAD], capRows),
    el("p", { class: "note" }, `${BRANCH_NAMES[state.branch]}${state.branch === "leanpcs" ? ", " + state.route : ""}.`)));
  const candRows = T.candidates.map((c) => {
    const o = { candidate: c.name };
    const hc = capFor("H*", o), ic = capFor("I*", o);
    const hg = T.groups[currentKey("H*", o)], ig = T.groups[currentKey("I*", o)];
    return row([`${c.name}${c.name === state.candidate ? " (shown)" : ""}`, c.sig_bytes.toLocaleString("en-US"), String(c.perms),
      hg ? fmt(hg.summary.gas_per_pq_tx) : "not run", ig ? fmt(ig.summary.gas_per_pq_tx) : "not run",
      hc ? fmt(hc.sig_per_s.mean) : "not run", hc ? capChip(hc) : "", ic ? fmt(ic.sig_per_s.mean) : "not run", ic ? capChip(ic) : ""],
      { "data-cand": c.name });
  });
  box.append(details("Candidate sets",
    table(["set", "bytes", "perms", "H* gas per PQ tx", "I* gas per PQ tx", "H* capacity, sig/s", "H* runs", "I* capacity, sig/s", "I* runs"], candRows),
    el("p", { class: "note" }, "Bytes and permutations from the simulator's candidate table ", statusChip("R", T.candidates[0].source),
      "; gas per PQ tx from the runs at the assumed demand (PQ gas over PQ txs, 5 % 2-of-3 multisig included). ", fx())));
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
    el("p", { class: "note" }, "From ", el("code", {}, T.timeline_file), `. Capacity: saturated runs per cell (more PQ demand than fits, today's ECDSA traffic alongside). Assumed demand: each step sampled at ${T.points_per_step.value} months, one 12-slot load-mode run per seed; a step's figure is the mean over seeds, then the month-weighted mean. Months are never simulated.`),
    el("div", { class: "scroll" }, el("table", {}, el("tbody", {}, inputRows))),
    el("p", { class: "note" }, "Each chip's tooltip gives the source and the reason.")));
}

// Gas per PQ tx and its ratios (solid), and a fee example at a base fee the visitor types.
const ROUTES = [["H*", null, null, "8141 full (H*)"], ["I*", "8288", null, "8288 full"], ["I*", "leanpcs", "dependency", "leanPCS dependency"],
                ["I*", "leanpcs", "pcs_client", "leanPCS pcs_client"], ["I*", "leanpcs", "pcs_bundler", "leanPCS pcs_bundler (per user)"]];
let feeInput = null, ethInput = null;
function renderGas() {
  const box = clear($("#tl-gas"));
  const D = T.fee_default;
  if (feeInput === null) feeInput = D.base_fee_gwei;
  // keyed on the timeline's default adoption schedule: gas per tx does not depend on the load
  const gasOf = (stepId, branch, route, candidate) => T.groups[currentKey(stepId, { branch: branch || state.branch, route: route || state.route, candidate: candidate || state.candidate, pq: { "H*": "default", "I*": "default" } })]?.summary?.gas_per_pq_tx;
  const base = gasOf("H*", null, null);
  const rows = ROUTES.map(([stepId, branch, route, label]) => {
    const g = gasOf(stepId, branch, route);
    const fee = isNum(g) && isNum(feeInput) ? g * feeInput : null;
    const tr = row([label, fmt(g), isNum(g) && isNum(base) ? `${fmt(base / g, 2)}x` : "", "", ""], { "data-gas": `${stepId}|${branch}|${route}` });
    tr.dataset.g = isNum(g) ? String(g) : "";
    return tr;
  });
  const valid = (v) => v !== "" && Number.isFinite(Number(v)) && Number(v) >= 0;
  const feeBox = el("input", { type: "number", id: "fee-base", step: "any", min: 0, value: feeInput,
    oninput: (e) => { feeInput = valid(e.target.value) ? Number(e.target.value) : null; updateFees(); } });
  const ethBox = el("input", { type: "number", id: "fee-eth", step: "any", min: 0, value: ethInput ?? "", placeholder: "optional",
    oninput: (e) => { ethInput = valid(e.target.value) ? Number(e.target.value) : null; updateFees(); } });
  box.append(el("p", { class: "small" }, `Gas per PQ transaction for ${state.candidate}, from the runs (PQ gas over PQ txs) `, chip("simulated"),
    ". The ratios are the solid part; a fee needs a base fee, which no simulation predicts."),
    el("div", { class: "formgrid" },
      el("div", { class: "field" }, el("label", { for: "fee-base" }, "Base fee, gwei (example)"), feeBox,
        el("span", { class: "small" }, `Default: today's ${D.what}, ${fmt(D.base_fee_gwei)} gwei `, statusChip(D.status, `${D.source}; read ${D.read}; ${D.path}`))),
      el("div", { class: "field" }, el("label", { for: "fee-eth" }, "ETH price, USD (optional)"), ethBox)),
    table(["deployment", "gas per PQ tx", "8141 ÷ this", "fee at that base fee, gwei", "USD"], rows),
    el("p", { class: "note", id: "fee-msg" }, "Fee = gas × the base fee typed above (tip left out): an example, not a prediction. Gas per tx at the timeline's default adoption schedule (it does not depend on the load). 8288 and leanPCS charge no prover cost in gas."));
  updateFees();
}

function updateFees() {
  for (const tr of document.querySelectorAll("#tl-gas [data-gas]")) {
    const g = tr.dataset.g === "" ? null : Number(tr.dataset.g);
    const cells = tr.querySelectorAll("td");
    const fee = isNum(g) && isNum(feeInput) ? g * feeInput : null;
    cells[2].textContent = fee !== null ? fmt(fee) : (isNum(g) ? "enter a base fee" : "");
    cells[3].textContent = fee !== null && isNum(ethInput) ? fmt(fee * 1e-9 * ethInput, 3) : "";
  }
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
  renderGas();
  renderEps();
  renderMore();
  document.dispatchEvent(new CustomEvent("timeline-rendered"));
}

export function getState() { return JSON.parse(JSON.stringify(state)); }

export function initTimeline(data, capacity) {
  T = data;
  T.capacity = capacity;
  initState();
  render();
}
