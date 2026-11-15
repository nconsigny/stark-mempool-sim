// Simulate: the v3 simulator in a Web Worker (Pyodide). The form builds the
// same argv as the command line (lsweb3.argv_of); the result is the row the
// command prints, shown as a few headline figures, the full row and the
// command. Defaults and limits come from data/describe.json (the engine's
// own `describe`), never from this file.

import { $, el, clear, fmt, fmtGas, isNum, chip, segmented, control, details } from "./util.mjs";
import { animation } from "./anim.mjs";

let D = null;          // describe.json
let engine = null;
let setStatus = () => {};
let anim = null;
let OFFERED = [];
let AXES = null;      // timeline.json axes (gas limits, dependency caps)
let SIM = null;       // build.json simulator
let CAMPAIGN_NODES = [];  // node counts of the precomputed runs
const form = {};

const DEPLOYMENTS = [["8141", "8141 full"], ["8288", "8288 full"], ["leanpcs", "leanPCS full"]];

// Headline figures: each entry lists the row keys it can come from (the S4
// row first, then the interim S3a row), with the label that matches that key.
const HEADLINE = [
  { id: "sig", alts: [["sig_per_s", "Included signatures", "sig/s"]] },
  { id: "pq", alts: [["pq_tps", "Included PQ transactions", "tx/s"], ["included_pq_tps", "Included PQ transactions", "tx/s"]] },
  { id: "all", alts: [["tps_included", "Included transactions, background apart", "tx/s"]] },
  { id: "bg", alts: [["ecdsa_bg_tps", "ECDSA background included", "tx/s"]] },
  { id: "ecdsa", alts: [["ecdsa_tps", "Explicit ECDSA transactions", "tx/s"], ["included_ecdsa_tps", "ECDSA transactions", "tx/s"]] },
  { id: "binds", alts: [["binding", "What binds", ""], ["binding_counts", "What bound each payload", ""]] },
  { id: "lat50", alts: [["incl_latency_pq1_p50_s", "PQ inclusion latency p50", "s"], ["inclusion_latency_ms.p50", "PQ inclusion latency p50", "ms"]] },
  { id: "lat95", alts: [["incl_latency_pq1_p95_s", "PQ inclusion latency p95", "s"], ["inclusion_latency_ms.p95", "PQ inclusion latency p95", "ms"]] },
  { id: "reach", alts: [["block_reach_t90_p50_ms", "Block reaches 90 % of nodes (median block)", "ms"],
                        ["payload_reach90_from_slot_start_ms_p50", "Payload at 90 % of nodes, from slot start (median)", "ms"]] },
  { id: "fee", alts: [["user_fee_pq_p50_gwei", "Fee per PQ transaction p50", "gwei"]] },
  { id: "bw", alts: [["bw_home_up_mean_mbps", "Home node uplink, mean", "Mbit/s"]] },
];

function lookup(row, key) {
  return key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), row);
}

export function headline(row) {
  const out = [];
  for (const h of HEADLINE) {
    for (const [key, label, unit] of h.alts) {
      let v = lookup(row, key);
      if (v === undefined) continue;
      if (v && typeof v === "object") {
        v = Object.entries(v).map(([k, n]) => `${k} (${n})`).join(", ");
      }
      out.push({ id: h.id, key, label, unit, value: v });
      break;
    }
  }
  return out;
}

function limitsFor(dep) { return D.limits.deployments[dep]; }

// A deployment's browser defaults (limits3.json), for the fields they name.
function applyDefaults(dep) {
  const d = limitsFor(dep).browser_defaults;
  form.nodes = d.nodes;
  form.measured = d["slots.measured"];
  form.warmup = d["slots.warmup"];
}

function field(label, input, hint) {
  return el("div", { class: "field" }, el("label", { for: input.id }, label), input,
    hint ? el("span", { class: "small" }, hint) : null);
}

function keyMeta(key) { return D.keys.find((k) => k.key === key); }

function renderForm() {
  const box = clear($("#sim-fields"));
  const dep = form.deployment;
  const lim = limitsFor(dep);
  box.append(control("Deployment", segmented("Deployment", DEPLOYMENTS.map(([v, l]) => ({ value: v, label: l })),
    dep, (v) => { form.deployment = v; applyDefaults(v); renderForm(); })));
  const grid = el("div", { class: "formgrid" });
  const cand = el("select", { id: "f-cand", onchange: (e) => { form.candidate = e.target.value; } },
    D.candidates.filter((c) => OFFERED.includes(c.name)).map((c) => el("option", { value: c.name, selected: c.name === form.candidate },
      `${c.name} (${c.sig_bytes} B, ${c.perms} perms)`)));
  grid.append(field("Parameter set", cand, `${D.candidates.length} sets on the CLI: candidate=<name> under More settings`));
  const gas = el("select", { id: "f-gas", onchange: (e) => { form.gas_limit = Number(e.target.value); } },
    AXES.gas_limit.filter((g) => g <= lim.max_gas_limit).map((g) =>
      el("option", { value: g, selected: g === form.gas_limit }, fmtGas(g))));
  grid.append(field("Gas limit", gas));
  const nodes = el("input", { id: "f-nodes", type: "number", min: lim.min_nodes, max: lim.max_nodes,
    value: form.nodes, oninput: (e) => { form.nodes = Number(e.target.value); } });
  grid.append(field("Nodes", nodes, `${lim.min_nodes} to ${lim.max_nodes} here`));
  const demand = el("select", { id: "f-demand", onchange: (e) => { form.demandMode = e.target.value; renderForm(); } },
    el("option", { value: "saturate", selected: form.demandMode === "saturate" }, "saturate (capacity)"),
    el("option", { value: "load", selected: form.demandMode === "load" }, "load (tx/s)"));
  grid.append(field("Demand", demand));
  if (form.demandMode === "load") {
    grid.append(field("Offered tx/s, all kinds", el("input", { id: "f-level", type: "number", min: 0, step: "any",
      value: form.level, oninput: (e) => { form.level = Number(e.target.value); } })));
    const meta = keyMeta("demand.pq_share");
    grid.append(field("PQ share", el("input", { id: "f-pq", type: "number", min: meta.min, max: meta.max, step: "0.01",
      value: form.pq_share, oninput: (e) => { form.pq_share = Number(e.target.value); } })));
  }
  if (dep !== "8141") {
    grid.append(field("Dependency cap", el("select", { id: "f-cap", onchange: (e) => { form.dep_cap = e.target.value === "none" ? null : Number(e.target.value); } },
      AXES.dep_cap.map((c) => c ?? "none").map((c) => el("option", { value: c, selected: String(form.dep_cap ?? "none") === String(c) }, String(c))))));
  }
  if (dep === "leanpcs") {
    const meta = keyMeta("agg.sig_route");
    grid.append(field("Signature route", el("select", { id: "f-route", onchange: (e) => { form.sig_route = e.target.value; } },
      meta.choices.map((c) => el("option", { value: c, selected: c === form.sig_route }, c)))));
  }
  grid.append(field("Seed", el("input", { id: "f-seed", type: "number", min: 0, value: form.seed,
    oninput: (e) => { form.seed = Number(e.target.value); } })));
  box.append(grid);
  const more = el("div", { class: "formgrid" },
    field("Measured slots", el("input", { id: "f-meas", type: "number", min: 1, max: lim.max_measured_slots,
      value: form.measured, oninput: (e) => { form.measured = Number(e.target.value); } })),
    field("Warm-up slots", el("input", { id: "f-warm", type: "number", min: 0, max: lim.max_measured_slots,
      value: form.warmup, oninput: (e) => { form.warmup = Number(e.target.value); } })),
    el("div", { class: "field check" }, el("input", { id: "f-trace", type: "checkbox", checked: form.trace,
      onchange: (e) => { form.trace = e.target.checked; } }), el("label", { for: "f-trace" }, "Record the network replay")));
  const sets = el("textarea", { id: "f-sets", rows: 3, placeholder: "net.idontwant=false",
    oninput: (e) => { form.extra = e.target.value; } }, form.extra);
  box.append(details("More settings", more, field("Other settings, one key=value per line (the CLI's --set; About lists the keys)", sets)));
  $("#sim-limits").textContent = `Here: ${lim.min_nodes} to ${lim.max_nodes} nodes, at most ${lim.max_measured_slots} measured slots, stopped after ${D.limits.time_limit_s} s. The command line's default is ${D.limits.cli_default_nodes} nodes; the campaign's runs used ${CAMPAIGN_NODES.length ? CAMPAIGN_NODES.join(", ") : "other"} nodes.`;
}

export function configOf() {
  const sets = {};
  if (form.measured) sets["slots.measured"] = form.measured;
  if (form.warmup !== null && form.warmup !== undefined) sets["slots.warmup"] = form.warmup;
  if (form.deployment !== "8141" && form.dep_cap !== null) sets["chain.dep_cap"] = form.dep_cap;
  if (form.deployment === "leanpcs") sets["agg.sig_route"] = form.sig_route;
  if (form.demandMode === "load") sets["demand.pq_share"] = form.pq_share;
  for (const line of (form.extra || "").split("\n")) {
    const text = line.trim();
    if (!text) continue;
    const at = text.indexOf("=");
    if (at < 1) throw new Error(`"${text}": expected key=value`);
    const raw = text.slice(at + 1).trim();
    let value;
    try { value = JSON.parse(raw); } catch { value = raw; }
    sets[text.slice(0, at).trim()] = value;
  }
  return {
    deployment: form.deployment, candidate: form.candidate, gas_limit: form.gas_limit,
    nodes: form.nodes, seed: form.seed,
    demand: form.demandMode === "load" ? String(form.level) : "saturate",
    sets, trace: form.trace,
  };
}

function resultView(res) {
  const box = clear($("#sim-result"));
  if (anim) anim.stop();
  const sim = () => chip("simulated", `Simulated in this browser: ${res.row.nodes ?? form.nodes} nodes, seed ${res.row.seed}; a small network, not the campaign's ${CAMPAIGN_NODES.join("/")}-node runs`);
  const items = headline(res.row);
  box.append(el("dl", { class: "kpis", id: "sim-kpis" }, items.map((h) =>
    el("div", { class: "kpi", "data-key": h.key },
      el("dt", {}, h.label, " ", sim()),
      el("dd", { title: `row key ${h.key}` },
        el("span", { class: "v" }, isNum(h.value) ? fmt(h.value) : String(h.value ?? "n/a")),
        h.unit ? el("span", { class: "unit" }, h.unit) : null)))));
  box.append(el("p", { class: "small", id: "sim-run-line" },
    `${res.row.deployment ?? form.deployment}, ${res.row.candidate ?? ""}, ${fmtGas(res.row.gas_limit)} gas, ${res.row.nodes} nodes, seed ${res.row.seed}; ${(res.timing.wall_ms / 1000).toFixed(1)} s in this browser. Row: ${Object.keys(res.row).length} columns${res.row.stage ? ` (${res.row.stage})` : ""}.`));
  if (res.trace) {
    anim = animation(res.trace);
    box.append(details("Network replay of this run", anim.node));
  }
  box.append(details("Command line and full row",
    el("p", { class: "small" }, "Run in the simulator this site was built from (", el("code", {},
      SIM.kind === "ref" ? `sims/stark-mempool-sim-v2 at ${SIM.ref} ${SIM.commit.slice(0, 9)}` : `${SIM.tree} at ${SIM.commit.slice(0, 9)}${SIM.dirty.length ? " plus uncommitted changes" : ""}`),
      "), this command prints the same row, wall-time fields aside (test/determinism.mjs checks it):"),
    el("pre", { class: "cmd", id: "sim-command" }, res.command),
    el("pre", { id: "sim-row", style: "max-height:420px" }, res.row_text)));
}

export function initSimulate(describe, eng, onStatus, { offered, axes, simulator, campaignNodes }) {
  CAMPAIGN_NODES = campaignNodes || [];
  D = describe;
  OFFERED = offered;
  AXES = axes;
  SIM = simulator;
  engine = eng;
  setStatus = onStatus;
  const defaults = D.deployment_defaults["8141"];
  form.deployment = "8141";
  form.candidate = defaults.candidate?.value || "P99r";
  form.gas_limit = defaults["chain.gas_limit"]?.value;

  form.seed = 0;
  form.demandMode = "saturate";
  form.level = 30;
  form.pq_share = keyMeta("demand.pq_share").default;
  form.dep_cap = null;
  form.sig_route = keyMeta("agg.sig_route").default;
  applyDefaults("8141");
  form.trace = true;
  form.extra = "";
  $("#sim-intro").textContent = "The v3 simulator runs in your browser (Pyodide); its row is the one the command line prints for the same settings. Small networks only.";
  renderForm();
  const status = $("#sim-status");
  const run = $("#sim-run");
  const cancel = $("#sim-cancel");
  const progress = $("#sim-progress");
  const ptext = $("#sim-progress-text");
  $("#sim-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    let config;
    try { config = configOf(); } catch (error) { status.textContent = error.message; status.classList.add("error"); return; }
    status.classList.remove("error");
    status.textContent = "Starting the simulator…";
    run.disabled = true; cancel.disabled = false;
    progress.hidden = false; progress.value = 0; ptext.textContent = "";
    try {
      const { reasons } = await engine.checkLimits(config);
      if (reasons.length) throw new Error(reasons.join("; "));
      setStatus("running");
      status.textContent = "Running…";
      const res = await engine.run(config, { onProgress: (ev) => {
        if (ev.event === "tick") {
          progress.value = ev.sim_ms / ev.horizon_ms;
          ptext.textContent = `simulated ${(ev.sim_ms / 1000).toFixed(1)} of ${(ev.horizon_ms / 1000).toFixed(0)} s · ${ev.elapsed_s.toFixed(1)} s elapsed`;
        }
      } });
      progress.value = 1;
      ptext.textContent = `finished in ${(res.timing.wall_ms / 1000).toFixed(1)} s`;
      status.textContent = "Done.";
      resultView(res);
      document.dispatchEvent(new CustomEvent("sim-result", { detail: res }));
    } catch (error) {
      status.classList.add("error");
      status.textContent = error.name === "EngineCancelled" ? "Cancelled." : `The simulator stopped: ${error.message}`;
    } finally {
      run.disabled = false; cancel.disabled = true;
      setStatus("ready");
    }
  });
  cancel.addEventListener("click", () => { engine.cancel(); setStatus("loading"); engine.start().then(() => setStatus("ready"), () => setStatus("failed")); });
}
