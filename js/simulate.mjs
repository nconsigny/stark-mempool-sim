// The Simulate view: a form built from the engine's scenario list, Run and
// Cancel, progress, the headline figures next to the campaign's table cell,
// the full row, the command line, and the last few runs.

import { EngineCancelled } from "../engine/engine.mjs";
import { pricingName, $, el, clear, fmtInt, fmtTps, fmtFixed, fmtShare, fmtGas,
         scenarioLabel, setLabel, CLASS_NAMES } from "./util.mjs";

const STORE_KEY = "leansphincs-web-form-v1";
const HISTORY_MAX = 6;
const DASH = "—";

// The headline figures, in the order of the result table. `field` is the
// column of the phase-2 row; the same column exists in the campaign's tables.
export const HEADLINES = [
  { field: "tps_tx", main: true, label: "Sustained throughput", sub: "transactions per second included over the measured slots", value: (v) => fmtTps(v), unit: "tx/s" },
  { field: "tps_sig", label: "Sustained throughput, signatures", sub: "transactions times signatures per transaction", value: (v) => fmtTps(v), unit: "sig/s" },
  { field: "tps_timely", label: "Timely throughput", sub: "transactions per second in blocks that reached the network in time", value: (v) => fmtTps(v), unit: "tx/s" },
  { field: "binding", main: true, label: "What binds", sub: "the limit that set the sustained rate", value: (v) => String(v), unit: "" },
  { field: "block_bytes", label: "Block size", sub: "mean bytes per block", value: (v) => fmtFixed(v / 1e6, 2), unit: "MB" },
  { field: "t90_block_s", label: "Block reaches 90% of nodes", sub: "seconds after the slot starts", value: (v) => fmtFixed(v, 2), unit: "s" },
  { field: "bw_node_mbit", label: "Bandwidth per node", sub: "mean upload", value: (v) => fmtFixed(v, 2), unit: "Mbit/s" },
  { field: "dup_share", label: "Duplicates", sub: "share of bytes received more than once", value: (v) => fmtShare(v), unit: "of bytes" },
  // Not simulated: the gas model's figures for one transaction of the run.
  { field: "tx_gas", gas: true, main: true, label: "Gas per transaction", sub: "total, from the gas model (not simulated)", value: (v) => fmtInt(v), unit: "gas" },
  { field: "tx_calldata_gas", gas: true, label: "Calldata gas per transaction", sub: "data bytes at the price that binds (gas model)", value: (v) => fmtInt(v), unit: "gas" },
];

const NBSP = "\u00a0";
const missing = (value) => value === null || value === undefined || value === "NA" || value === -1;

// A value cell: the figure on one line, its unit (and a remark) on the
// next. Every cell has both lines from the start, so a result does not
// change the height of a row.
function valueCell(td, text, under, dim = false) {
  clear(td).append(el("span", { class: dim ? "v dim" : "v" }, text), el("span", { class: "sub" }, under || NBSP));
}

// Variant suffixes a table row may carry beyond the configuration's own
// variant and still be the same cell.
const VARIANT_EXTRA = /^(deps=\d+|client_proofs=\w+|bundle=(law|repo)|phase2-cell)$/;

// The campaign's table cell for a run's settings, or null. A cell is
// identified as web/SCHEMA.md says; the tables are 40-node runs.
export function findTableCell(tables, row, knobs, tableNodes) {
  const matches = [];
  for (const table of tables) {
    for (const cell of table.rows) {
      if (cell.scenario !== row.scenario || cell.pricing !== row.pricing) continue;
      if (cell.gas_limit !== row.gas_limit || cell.profile !== row.profile) continue;
      if (cell.uplinks !== row.uplinks || cell.candidate !== row.candidate) continue;
      if (cell.k !== row.k || cell.nodes !== tableNodes) continue;
      if (cell.variant !== row.variant) {
        if (!cell.variant.startsWith(row.variant + ";")) continue;
        const rest = cell.variant.slice(row.variant.length + 1).split(";");
        if (!rest.every((token) => VARIANT_EXTRA.test(token))) continue;
      }
      if (cell.mode && knobs && knobs.mode && cell.mode !== knobs.mode) continue;
      if (cell.client_proofs && knobs && knobs.client_proofs && cell.client_proofs !== knobs.client_proofs) continue;
      matches.push(cell);
    }
  }
  matches.sort((a, b) => (b.headline || 0) - (a.headline || 0));
  return matches[0] || null;
}

export function initSimulate(app) {
  const { data, engine } = app;
  const ids = data.scenarios.ids;
  const idById = new Map(ids.map((entry) => [entry.id, entry]));
  let configs = new Map(data.scenarios.configurations.map((entry) => [entry.configuration, entry]));
  const candidates = new Map(data.candidates.candidates.map((entry) => [entry.name, entry]));
  const tableNodes = data.scenarios.table_nodes;

  const form = $("#sim-form");
  const f = {
    scenario: $("#sim-scenario"), config: $("#sim-config"), candidate: $("#sim-candidate"),
    custom: $("#sim-custom"), bytes: $("#sim-bytes"), perms: $("#sim-perms"), gas: $("#sim-gas"),
    profile: $("#sim-profile"), uplinks: $("#sim-uplinks"), nodes: $("#sim-nodes"),
    slots: $("#sim-slots"), seed: $("#sim-seed"), k: $("#sim-k"),
  };
  const runButton = $("#sim-run");
  const cancelButton = $("#sim-cancel");
  const status = $("#sim-status");
  const progress = $("#sim-progress");
  const progressText = $("#sim-progress-text");
  const resultBody = $("#sim-result tbody");
  const moreBody = $("#sim-result-more tbody");
  const resultRow = (field) => document.querySelector(`#sim-result tr[data-field="${field}"], #sim-result-more tr[data-field="${field}"]`);
  const history = [];
  let running = null;      // {started, timer, label, last}

  const currentConfig = () => configs.get(f.config.value);

  function setStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("error", isError);
  }

  function fillSelect(select, values, label = (value) => value) {
    const before = select.value;
    clear(select);
    for (const value of values) select.append(el("option", { value }, label(value)));
    if (values.map(String).includes(before)) select.value = before;
  }

  // ------------------------------------------------------------- the form
  fillSelect(f.scenario, ids.map((entry) => entry.id), (id) => {
    const entry = idById.get(id);
    return scenarioLabel(entry) + (entry.explore_only ? " · tables only" : "");
  });

  function onScenario() {
    const entry = idById.get(f.scenario.value);
    const names = [entry.default_configuration,
      ...entry.configurations.filter((name) => name !== entry.default_configuration)];
    clear(f.config);
    for (const name of names) {
      const config = configs.get(name);
      const text = name + (name === entry.default_configuration ? " (default)" : "") +
        (config ? " · " + pricingName(config.pricing) : "");
      f.config.append(el("option", { value: name }, text));
    }
    f.config.disabled = names.length < 2;
    applyConfig();
  }

  function followCandidate() {
    const entry = candidates.get(f.candidate.value);
    f.bytes.disabled = f.perms.disabled = !f.custom.checked;
    f.bytes.required = f.perms.required = f.custom.checked;
    if (!f.custom.checked && entry) {
      f.bytes.value = entry.sig_bytes;
      f.perms.value = entry.perms;
    }
    $("label[for=sim-candidate]").textContent = f.custom.checked ? "Base set (its verifier model)" : "Parameter set";
  }

  function applyConfig() {
    const config = currentConfig();
    const entry = idById.get(config.id);
    const settings = config.settings;
    const mode = config.mode === "capacity"
      ? "a capacity search: up to 8 simulations for the highest sustained rate"
      : "one simulation at the offered load";
    clear($("#sim-scenario-note")).append(
      entry.description, " When: ", entry.when, ". Class: ", CLASS_NAMES[config.class] || config.class,
      ". Pricing: ", pricingName(config.pricing), ". Mode: ", mode, ".");

    if (app.scenarioHelp) { app.scenarioHelp.simHelp(); }
    const exploreOnly = $("#sim-explore-only");
    const fields = $("#sim-fields");
    if (config.explore_only) {
      fields.hidden = true;
      exploreOnly.hidden = false;
      clear(exploreOnly).append(
        el("h3", {}, config.configuration + " is not run in the browser"),
        el("p", { id: "sim-explore-only-reason" }, "Reason: " + config.limits.reason + "."),
        el("p", {}, "Its tables are in ", el("a", { href: "#explore" }, "Explore"),
          ". To run it yourself, from the simulator checkout:"),
        el("pre", { class: "cmd", tabindex: "0" }, "python3 -m bench.leansphincs run --scenario " +
          (config.default ? config.id : config.configuration) +
          " --network-params ../../data/phase2b/network_params_v2.json"));
      updateButtons();
      return;
    }
    fields.hidden = false;
    exploreOnly.hidden = true;

    fillSelect(f.candidate, settings.candidate.values, (name) => {
      const set = candidates.get(name);
      return set ? `${setLabel(set.label)} · ${name} (${fmtInt(set.sig_bytes)} B, ${set.perms} perms)` : name;
    });
    if (!f.candidate.dataset.touched && settings.candidate.values.includes("P99r")) f.candidate.value = "P99r";
    followCandidate();

    fillSelect(f.gas, settings.gas_limit.values, (value) => fmtGas(Number(value)) + " gas");
    if (!f.gas.dataset.touched || !settings.gas_limit.values.includes(Number(f.gas.value))) {
      f.gas.value = settings.gas_limit.values.includes(config.gas_limit) ? config.gas_limit : settings.gas_limit.values[0];
    }
    f.gas.disabled = settings.gas_limit.values.length < 2;
    fillSelect(f.profile, settings.profile.values);
    if (!f.profile.dataset.touched) f.profile.value = settings.profile.default;
    fillSelect(f.uplinks, settings.uplinks.values);
    if (!f.uplinks.dataset.touched) f.uplinks.value = settings.uplinks.default;

    const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
    f.nodes.min = settings.nodes.min;
    f.nodes.max = settings.nodes.max;
    const startNodes = (config.browser_defaults && config.browser_defaults.nodes) ?? settings.nodes.default;
    f.nodes.value = f.nodes.dataset.touched && f.nodes.value !== ""
      ? clamp(Number(f.nodes.value), settings.nodes.min, settings.nodes.max)
      : clamp(startNodes, settings.nodes.min, settings.nodes.max);
    f.slots.min = settings.measured_slots.min;
    f.slots.max = settings.measured_slots.max;
    f.slots.value = f.slots.dataset.touched && f.slots.value !== ""
      ? clamp(Number(f.slots.value), settings.measured_slots.min, settings.measured_slots.max)
      : clamp(settings.measured_slots.default, settings.measured_slots.min, settings.measured_slots.max);
    f.seed.min = settings.seed.min;
    if (f.seed.value === "") f.seed.value = settings.seed.default;

    const kField = $("#sim-k-field");
    kField.hidden = !settings.k;
    if (settings.k) {
      f.k.min = settings.k.min;
      const label = config.class === "agg8288" ? "Signatures (dependencies) per transaction"
        : config.class === "pcs" ? "Signatures per transaction (bundle)" : "Signatures per transaction";
      $("#sim-k-label").textContent = label;
      if (settings.k.follows) {
        f.k.placeholder = "auto: " + settings.k.default + " for today's set";
        f.k.required = false;
        if (!f.k.dataset.touched) f.k.value = "";
        f.k.title = "Left empty, it follows the permutations: " + settings.k.follows.replace(/^perms:\s*/, "");
      } else {
        f.k.placeholder = "";
        f.k.title = "";
        f.k.required = true;
        if (!f.k.dataset.touched || f.k.value === "") f.k.value = settings.k.default;
      }
    }

    const limits = config.limits;
    const gasText = settings.gas_limit.values.length < 2
      ? `a gas limit of ${fmtGas(limits.max_gas_limit)} only`
      : `gas limits up to ${fmtGas(limits.max_gas_limit)}`;
    const limitsLine = clear($("#sim-limits"));
    limitsLine.append(`Browser limits: ${limits.min_nodes} to ${limits.max_nodes} nodes, ` +
      `1 to ${limits.max_measured_slots} measured slots, ${gasText}; the engine stops any run after ${data.scenarios.time_limit_s} s.`);
    limitsLine.title = (limits.reason ? `Why: ${limits.reason}. ` : "") +
      `The heaviest run takes about ${limits.est_s} s on the build machine, under about ${data.scenarios.budget_s} s on a slower laptop.`;
    updateButtons();
    save();
  }

  // The configuration sent to the engine: the harness's knob names.
  function readConfig() {
    const config = currentConfig();
    const out = {
      scenario: config.default ? config.id : config.configuration,
      candidate: f.candidate.value,
    };
    if (f.custom.checked) {
      out.sig_bytes = Number(f.bytes.value);
      out.perms = Number(f.perms.value);
    }
    out.gas_limit = Number(f.gas.value);
    out.profile = f.profile.value;
    out.uplinks = f.uplinks.value;
    out.nodes = Number(f.nodes.value);
    out.measured_slots = Number(f.slots.value);
    out.seed = Number(f.seed.value);
    if (config.settings.k && f.k.value !== "") out.k = Number(f.k.value);
    return out;
  }

  function firstInvalid() {
    for (const input of [f.bytes, f.perms, f.nodes, f.slots, f.seed, f.k]) {
      if (input.disabled || input.closest("[hidden]")) continue;
      if (!input.checkValidity() || (input.value !== "" && !Number.isInteger(Number(input.value)))) {
        const label = $(`label[for=${input.id}]`).textContent;
        const range = input.min && input.max ? `a whole number from ${input.min} to ${input.max}`
          : input.min ? `a whole number of at least ${input.min}` : "a whole number";
        return { input, message: `${label}: enter ${range}.` };
      }
    }
    return null;
  }

  // ------------------------------------------------ remembered form values
  let restored = false;   // nothing is written before the stored form was read
  function save() {
    if (!restored) return;
    try {
      const values = { scenario: f.scenario.value, configuration: f.config.value };
      for (const [name, input] of Object.entries(f)) {
        if (name === "scenario" || name === "config") continue;
        values[name] = input.type === "checkbox" ? input.checked : input.value;
      }
      localStorage.setItem(STORE_KEY, JSON.stringify(values));
    } catch { /* storage is optional */ }
  }

  function restore() {
    let values = null;
    try { values = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch { values = null; }
    if (!values || !idById.has(values.scenario)) return;
    f.scenario.value = values.scenario;
    onScenario();
    if (configs.has(values.configuration) && configs.get(values.configuration).id === values.scenario) {
      f.config.value = values.configuration;
    }
    for (const [name, input] of Object.entries(f)) {
      if (name === "scenario" || name === "config" || values[name] === undefined) continue;
      if (input.type === "checkbox") input.checked = Boolean(values[name]);
      else if (input.tagName === "SELECT") {
        if ([...input.options].some((option) => option.value === String(values[name]))) input.value = values[name];
      } else input.value = values[name];
      input.dataset.touched = "1";
    }
    applyConfig();
  }

  // ------------------------------------------------------------ run state
  function updateButtons() {
    const config = currentConfig();
    const ready = app.engineState.state === "ready";
    runButton.disabled = !ready || Boolean(running) || !config || config.explore_only;
    cancelButton.disabled = !running;
  }

  function showProgress() {
    if (!running) return;
    const elapsed = (performance.now() - running.started) / 1000;
    const last = running.last;
    let text = `${elapsed.toFixed(1)} s elapsed`;
    if (last) {
      const inRun = last.horizon_ms ? Math.min(1, (last.sim_ms || 0) / last.horizon_ms) : 0;
      progress.value = Math.min(1, (last.probe - 1 + inRun) / last.max_probes);
      const which = last.max_probes > 1 ? `Simulation ${last.probe} of at most ${last.max_probes}` : "Simulation";
      const simulated = last.horizon_ms
        ? `, simulated ${((last.sim_ms || 0) / 1000).toFixed(1)} of ${(last.horizon_ms / 1000).toFixed(0)} s` : "";
      text = `${which}${simulated} · ${text}`;
    } else {
      text = "Starting · " + text;
    }
    progressText.textContent = text;
  }

  function onProgress(event) {
    if (!running) return;
    running.last = { ...(running.last || {}), ...event };
    if (event.event === "probe") running.last.sim_ms = 0;
    running.events += 1;
    showProgress();
  }

  async function run() {
    if (running) return;
    const invalid = firstInvalid();
    if (invalid) {
      setStatus(invalid.message, true);
      invalid.input.focus();
      return;
    }
    const cfg = currentConfig();
    const config = readConfig();
    save();
    running = { started: performance.now(), last: null, events: 0, timer: 0 };
    updateButtons();
    setStatus("Checking the limits…");
    progress.value = 0;
    try {
      const { reasons } = await engine.checkLimits(config);
      if (reasons.length) {
        setStatus("Not run: " + reasons.join("; ") + ".", true);
        return;
      }
      running.started = performance.now();
      running.timer = setInterval(showProgress, 200);
      const label = `${config.scenario}, ${config.candidate}${config.sig_bytes ? " custom" : ""}, ${config.nodes} nodes`;
      app.setEngineState("running", "running " + label);
      setStatus("Running " + label + "…");
      form.dataset.state = "running";
      const result = await engine.run(config, { onProgress });
      // The gas model's split of one transaction of this run (web/ENGINE.md,
      // gas_breakdown). A failure leaves the two gas rows empty.
      let gas = null;
      try {
        const point = { scenario: config.scenario, candidate: config.candidate, gas_limit: config.gas_limit };
        for (const key of ["sig_bytes", "perms", "k"]) if (config[key] !== undefined && config[key] !== null) point[key] = config[key];
        gas = await engine.call("gas_breakdown", { config: point });
      } catch (error) {
        console.warn("gas breakdown failed", error);
      }
      progress.value = 1;
      const took = result.timing.wall_ms / 1000;
      const runs = result.timing.probes;
      progressText.textContent = `Finished: ${runs} simulation${runs === 1 ? "" : "s"} in ${took.toFixed(1)} s.`;
      setStatus(`Done in ${took.toFixed(1)} s.`);
      showResult(result, config, cfg, gas);
      form.dataset.state = "done";
    } catch (error) {
      if (error instanceof EngineCancelled) {
        const after = ((performance.now() - running.started) / 1000).toFixed(1);
        setStatus(`Cancelled after ${after} s. The simulator restarts in about a second.`);
        progressText.textContent = "Cancelled.";
        form.dataset.state = "cancelled";
      } else {
        setStatus("The run failed. " + (error && error.message ? error.message : String(error)), true);
        progressText.textContent = "Failed.";
        form.dataset.state = "failed";
        console.warn("run failed", error && error.traceback ? error.traceback : error);
      }
    } finally {
      clearInterval(running.timer);
      running = null;
      if (app.engineState.state === "running") {
        const boot = app.engineState.boot;
        app.setEngineState("ready", boot
          ? `ready (Pyodide ${boot.pyodide_version}, simulator ${String(boot.info.simulator_commit).slice(0, 7)})`
          : "ready");
      }
      updateButtons();
    }
  }

  function cancel() {
    if (!running) return;
    app.restartEngine();
  }

  // ------------------------------------------------------------ the result
  function buildResultRows() {
    clear(resultBody);
    clear(moreBody);
    for (const spec of HEADLINES) {
      const runCell = el("td", { class: "n run" });
      const tableCell = el("td", { class: "n cell" });
      valueCell(runCell, DASH, spec.unit);
      valueCell(tableCell, DASH, spec.unit);
      (spec.main ? resultBody : moreBody).append(el("tr", { "data-field": spec.field },
        spec.main ? el("th", { scope: "row", title: spec.sub }, spec.label)
          : el("th", { scope: "row" }, spec.label, el("span", { class: "sub" }, spec.sub)), runCell, tableCell));
    }
    $("#sim-col-table-sub").textContent = `${tableNodes} nodes`;
  }

  function showResult(result, config, cfg, gas) {
    const row = result.phase2_row;
    const knobs = result.row.knobs || {};
    const cell = findTableCell([data.noagg, data.agg], row, knobs, tableNodes);
    // The gas rows: this run's transaction from the engine, and the same
    // model at the named set from data/gas.json (none for a custom size).
    const gasRun = gas ? { tx_gas: result.row.tx_gas ?? gas.total, tx_calldata_gas: gas.parts.calldata } : {};
    const gasRow = config.sig_bytes ? null : data.gas.rows.find((item) =>
      item.configuration === cfg.configuration && item.candidate === config.candidate && item.k === row.k);
    const gasTable = gasRow ? { tx_gas: gasRow.total, tx_calldata_gas: gasRow.parts.calldata } : null;
    for (const spec of HEADLINES) {
      const tr = resultRow(spec.field);
      const runCell = tr.querySelector("td.run");
      const tableCell = tr.querySelector("td.cell");
      if (spec.gas) {
        const value = gasRun[spec.field];
        valueCell(runCell, missing(value) ? "n/a" : spec.value(value), spec.unit);
        runCell.dataset.value = JSON.stringify(value ?? null);
        delete tableCell.dataset.value;
        if (!gasTable) {
          valueCell(tableCell, "no cell", "", true);
        } else {
          valueCell(tableCell, spec.value(gasTable[spec.field]), spec.unit);
          tableCell.dataset.value = JSON.stringify(gasTable[spec.field]);
        }
        continue;
      }
      const value = row[spec.field];
      valueCell(runCell, missing(value) ? "n/a" : spec.value(value), spec.unit);
      runCell.dataset.value = JSON.stringify(value);
      delete tableCell.dataset.value;
      if (!cell) {
        valueCell(tableCell, "no cell", "", true);
        continue;
      }
      const cellValue = cell[spec.field];
      let under = spec.unit;
      if (spec.field === "tps_tx" || spec.field === "tps_sig") {
        const lo = cell.tps_min ?? cell.tps_tx_min;
        const hi = cell.tps_max ?? cell.tps_tx_max;
        const factor = spec.field === "tps_sig" ? cell.k : 1;
        const spread = cell.seeds > 1 && lo !== null && lo !== undefined && hi !== lo;
        under += spread ? `, ${fmtTps(lo * factor)}\u2013${fmtTps(hi * factor)} over ${cell.seeds} seeds`
          : `, ${cell.seeds || 1} seed${cell.seeds > 1 ? "s" : ""}`;
      }
      valueCell(tableCell, missing(cellValue) ? "n/a" : spec.value(cellValue), under);
      tableCell.dataset.value = JSON.stringify(cellValue);
    }
    $("#sim-col-run-sub").textContent = `${row.nodes} nodes`;
    const what = `${row.scenario} (${cfg.configuration}), ${row.candidate}, ${fmtGas(row.gas_limit)} gas, ` +
      `${row.profile} profile, ${row.uplinks} uplinks, ${row.nodes} nodes, ${config.measured_slots} measured slot${config.measured_slots === 1 ? "" : "s"}, ` +
      `seed ${config.seed}, ${row.k} signature${row.k === 1 ? "" : "s"} per transaction`;
    const lead = clear($("#sim-result-lead"));
    lead.append(el("strong", {}, "This run: "), what + ". ");
    if (cell) {
      const tableSlots = cell.measured_slots ?? cfg.settings.measured_slots.default;
      const differs = [row.nodes === tableNodes ? null : `${row.nodes} nodes`, "one seed",
        config.measured_slots === tableSlots ? null : `${config.measured_slots} measured slot${config.measured_slots === 1 ? "" : "s"} against ${tableSlots}`]
        .filter(Boolean).join(", ");
      lead.append(`The table column is the campaign's cell for the same settings at ${tableNodes} nodes` +
        (cell.seeds > 1 ? `, a mean over ${cell.seeds} seeds` : cell.seeds === 1 ? ", one seed" : "") +
        `. This run has ${differs}, so the two can differ. `);
    } else {
      const why = config.sig_bytes ? "a custom size has no cell"
        : "the campaign did not run this combination at " + tableNodes + " nodes";
      lead.append(`The campaign's tables have no cell for these settings (${why}). ` +
        (row.nodes === tableNodes ? "" : `A browser run is a smaller network than the tables' ${tableNodes} nodes. `));
    }
    if (gas) {
      lead.append(`One transaction costs ${fmtInt(gasRun.tx_gas)} gas in the gas model, ${fmtInt(gas.parts.calldata)} of it (${(gas.calldata_share * 100).toFixed(1)}%) calldata; the ` +
        `${gas.binds === "floor" ? "calldata floor" : "standard path"} binds` +
        (gas.signature_on_chain ? ". " : ", and the signature bytes are not on chain. "));
    }
    lead.append(`Took ${(result.timing.wall_ms / 1000).toFixed(1)} s here (simulation ${result.timing.runtime_s.toFixed(1)} s, ` +
      `${result.timing.probes} simulation${result.timing.probes === 1 ? "" : "s"}).`);
    $("#sim-command").textContent = result.command;
    $("#sim-row-phase2").textContent = JSON.stringify(row, null, 1);
    $("#sim-row-full").textContent = JSON.stringify(result.row, null, 1);
    $("#sim-result").dataset.run = String(history.length + 1);

    history.unshift({ row, config, cfg, timing: result.timing });
    if (history.length > HISTORY_MAX) history.pop();
    renderHistory();
    app.lastRun = { config, result, cell, gas };
    if (app.scenarioHelp) app.scenarioHelp.simBaseline(row);
  }

  function renderHistory() {
    const body = clear($("#sim-history tbody"));
    for (const item of history) {
      const row = item.row;
      body.append(el("tr", {},
        el("th", { scope: "row" }, row.scenario, el("span", { class: "sub" }, item.cfg.configuration)),
        el("td", {}, row.candidate, el("span", { class: "sub" }, `${fmtInt(row.B)} B, ${row.P} perms, k ${row.k}`)),
        el("td", { class: "n" }, fmtGas(row.gas_limit)),
        el("td", {}, row.profile, el("span", { class: "sub" }, row.uplinks)),
        el("td", { class: "n" }, row.nodes),
        el("td", { class: "n" }, item.config.measured_slots),
        el("td", { class: "n" }, item.config.seed),
        el("td", { class: "n" }, fmtTps(row.tps_tx), el("span", { class: "sub" }, row.k === 1 ? "tx/s" : fmtTps(row.tps_sig) + " sig/s")),
        el("td", { class: "n" }, fmtTps(row.tps_timely)),
        el("td", {}, row.binding),
        el("td", { class: "n" }, row.t90_block_s >= 0 ? fmtFixed(row.t90_block_s, 2) + " s" : "n/a"),
        el("td", { class: "n" }, (item.timing.wall_ms / 1000).toFixed(1) + " s")));
    }
    if (!history.length) body.append(el("tr", { class: "empty" }, el("td", { colspan: "12" }, "No run yet.")));
    $("#sim-history-h").textContent = `Recent runs of this visit (${history.length})`;
  }

  // --------------------------------------------------------------- wiring
  f.scenario.addEventListener("change", () => {
    for (const input of [f.gas, f.profile, f.uplinks, f.nodes, f.slots, f.k]) delete input.dataset.touched;
    onScenario();
  });
  f.config.addEventListener("change", () => {
    for (const input of [f.gas, f.profile, f.uplinks, f.nodes, f.slots, f.k]) delete input.dataset.touched;
    applyConfig();
  });
  f.candidate.addEventListener("change", () => { f.candidate.dataset.touched = "1"; followCandidate(); save(); });
  f.custom.addEventListener("change", () => { followCandidate(); save(); });
  for (const input of [f.bytes, f.perms, f.gas, f.profile, f.uplinks, f.nodes, f.slots, f.seed, f.k]) {
    input.addEventListener("change", () => { input.dataset.touched = "1"; save(); });
  }
  form.addEventListener("submit", (event) => { event.preventDefault(); run(); });
  cancelButton.addEventListener("click", cancel);

  buildResultRows();
  onScenario();
  restore();
  restored = true;

  app.onEngineState((state) => {
    updateButtons();
    if (running) return;
    if (state.state === "loading") setStatus("The simulator is loading. Run becomes available when it is ready.");
    else if (state.state === "ready") {
      if (!form.dataset.state || form.dataset.state === "cancelled") setStatus("Ready.");
    } else if (state.state === "failed") {
      clear(status).append("The simulator could not start: " + state.message.replace(/^failed to start\.\s*/, "") + " ",
        el("button", { type: "button", class: "link", onclick: () => app.restartEngine() }, "Try again"));
      status.classList.add("error");
    }
  });

  // The form is drawn from data/scenarios.json so that it is there at once;
  // that file is the engine's own list. Once the engine answers, its list
  // replaces the file's.
  let listed = false;
  app.onEngineState((state) => {
    if (state.state !== "ready" || listed) return;
    listed = true;
    engine.scenarios().then((listing) => {
      const fresh = new Map(listing.scenarios.map((entry) => [entry.configuration, entry]));
      const same = JSON.stringify([...fresh.values()]) === JSON.stringify([...configs.values()]);
      configs = fresh;
      form.dataset.listing = same ? "engine (equal to data/scenarios.json)" : "engine";
      if (!same && !running) applyConfig();
    }).catch(() => { listed = false; });
  });
}
