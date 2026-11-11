// The Explore view: a scenario, its final table, the candidate sets, and
// the model calculator.

import { EngineCancelled } from "../engine/engine.mjs";
import { $, el, clear, chip, fmtInt, fmtTps, fmtPct, fmtGas, withExponent, taggedProse,
         unitOf, scenarioLabel, setLabel, CLASS_NAMES } from "./util.mjs";

// F2 has no table rows of its own (web/SCHEMA.md): it equals F1 in this model.
const TABLE_ALIAS = { F2: "F1" };
const MAIN_GAS = [60000000, 200000000];
const CALC_GAS = [60000000, 200000000, 300000000];
const PCT_COLUMNS = [
  ["dB10", "Bytes +10%"], ["dB100", "Bytes +100%"], ["dP10", "Perms +10%"], ["dP100", "Perms +100%"]];

// The rows of one scenario id, split into groups of cells that share
// everything but the candidate and the gas limit. In F3 the bundle size (and
// with it `k` and the variant's `bundle=N`) follows the candidate, so it is
// not part of the key there.
export function groupRows(rows, candidateOrder) {
  const groups = new Map();
  for (const row of rows) {
    const perSet = /(^|;)bundle=\d+/.test(row.variant) && row.k > 1;
    const variant = perSet ? row.variant.replace(/(^|;)bundle=\d+/, "$1bundle=per set") : row.variant;
    const k = perSet ? "per set" : row.k;
    const key = JSON.stringify([variant, row.pricing, row.profile, row.uplinks, row.nodes, k, row.mode || ""]);
    if (!groups.has(key)) {
      groups.set(key, { key, variant, pricing: row.pricing, profile: row.profile, uplinks: row.uplinks,
                        nodes: row.nodes, k, mode: row.mode || "", rows: [] });
    }
    groups.get(key).rows.push(row);
  }
  const list = [...groups.values()];
  for (const group of list) {
    const main = group.rows.filter((row) => candidateOrder.includes(row.candidate) && MAIN_GAS.includes(row.gas_limit));
    group.score = [
      new Set(main.map((row) => row.candidate + "@" + row.gas_limit)).size,
      group.profile === "baseline" ? 1 : 0,
      group.uplinks === "heterogeneous" ? 1 : 0,
      group.nodes === 40 ? 1 : 0,
      group.k === 1 || group.k === "per set" ? 1 : 0,
      group.rows.filter((row) => row.headline === 1).length,
    ];
  }
  // The group with the most candidate cells at 60 M and 200 M comes first;
  // ties keep the table's own order.
  return list.map((group, index) => ({ group, index })).sort((a, b) => {
    for (let i = 0; i < a.group.score.length; i++) {
      if (a.group.score[i] !== b.group.score[i]) return b.group.score[i] - a.group.score[i];
    }
    return a.index - b.index;
  }).map((entry) => entry.group);
}

// The unit of a table's dB*/dP* columns. The two final tables differ: the
// no-aggregation table holds percent (-8.5 is -8.5 %), the aggregation table
// holds fractions (-0.085 is -8.5 %). The table itself says which: a change
// for +100 % is 2^eps - 1 as a fraction. Returns the factor to percent.
export function pctFactor(table) {
  let fraction = 0, percent = 0;
  for (const row of table.rows) {
    for (const [eps, step] of [["eps_B", "dB100"], ["eps_P", "dP100"]]) {
      const slope = row[eps], value = row[step];
      if (typeof slope !== "number" || typeof value !== "number" || Math.abs(slope) < 1e-9 || value === 0) continue;
      const predicted = 2 ** slope - 1;
      if (Math.abs(value - predicted) < Math.abs(value - 100 * predicted)) fraction += 1;
      else percent += 1;
    }
  }
  return fraction > percent ? 100 : 1;
}

export function groupLabel(group) {
  const k = group.k === "per set" ? "bundle size per set" : `${group.k} signature${group.k === 1 ? "" : "s"} per transaction`;
  return [`pricing ${group.pricing}`, `${group.profile} profile`, `${group.uplinks} uplinks`,
    `${group.nodes} nodes`, k, group.mode ? `mode ${group.mode}` : null, group.variant,
    `${group.rows.length} rows`].filter(Boolean).join(" · ");
}

export function initExplore(app) {
  const { data, engine } = app;
  const ids = data.scenarios.ids;
  const idById = new Map(ids.map((entry) => [entry.id, entry]));
  const configs = new Map(data.scenarios.configurations.map((entry) => [entry.configuration, entry]));
  const sets = data.candidates.candidates;
  const setByName = new Map(sets.map((entry) => [entry.name, entry]));
  const order = sets.map((entry) => entry.name);
  const allRows = [...data.noagg.rows, ...data.agg.rows];
  const toPercent = new Map();
  for (const table of [data.noagg, data.agg]) {
    const factor = pctFactor(table);
    for (const row of table.rows) toPercent.set(row, factor);
  }

  const scenarioSelect = $("#ex-scenario");
  const groupSelect = $("#ex-group");
  let groups = [];

  for (const entry of ids) scenarioSelect.append(el("option", { value: entry.id }, scenarioLabel(entry)));
  scenarioSelect.value = "A";

  // ---------------------------------------------------------- the scenario
  function renderScenario() {
    const entry = idById.get(scenarioSelect.value);
    const config = configs.get(entry.default_configuration);
    const card = clear($("#ex-scenario-card"));
    card.append(
      el("h3", {}, entry.name + (entry.route ? ": " + entry.route : "")),
      el("p", {}, entry.description),
      el("ul", { class: "facts" },
        el("li", {}, el("b", {}, "When"), entry.when),
        el("li", {}, el("b", {}, "Class"), CLASS_NAMES[entry.class] || entry.class),
        el("li", {}, el("b", {}, "Pricing"), el("code", {}, entry.pricing)),
        el("li", {}, el("b", {}, "Simulated as"), entry.mode === "capacity" ? "a search for the highest sustained rate" : "one run at the offered load"),
        el("li", {}, el("b", {}, "Configurations"), String(entry.configurations.length))),
      el("p", { class: "small" }, "H* and I* stand for the hard forks after Glamsterdam: H* the next one, I* the one after it.",
        entry.explore_only ? " This scenario has tables but is not run in the browser: " + entry.limits.reason + "." : ""));
    card.dataset.scenario = entry.id;

    const tableId = TABLE_ALIAS[entry.id] || entry.id;
    groups = groupRows(allRows.filter((row) => row.scenario === tableId), order);
    clear(groupSelect);
    groups.forEach((group, index) => groupSelect.append(el("option", { value: String(index) }, groupLabel(group))));
    groupSelect.disabled = groups.length < 2;
    renderTable();
    renderSummary(entry);
    updateCalcContext(entry, config);
    scheduleCalc(0);
  }

  // ------------------------------------------------------- the final table
  function throughputCell(row, unit, max) {
    const value = row[unit.key];
    const factor = unit.key === "tps_sig" ? row.k : 1;
    const lo = row.tps_min ?? row.tps_tx_min;
    const hi = row.tps_max ?? row.tps_tx_max;
    const td = el("td", { class: "n", "data-field": unit.key, "data-value": JSON.stringify(value) },
      fmtTps(value), chip("simulated", `${row.status}. Source: ${row.source}`));
    const seeds = row.seeds ? `${row.seeds} seed${row.seeds === 1 ? "" : "s"}` : "";
    const range = row.seeds > 1 && lo !== null && lo !== undefined && hi !== null && hi !== undefined && hi !== lo
      ? `${fmtTps(lo * factor)}\u2013${fmtTps(hi * factor)}, ` : "";
    if (seeds) td.append(el("span", { class: "sub" }, range + seeds));
    if (max > 0 && typeof value === "number" && value >= 0) {
      td.append(el("span", { class: "barcell", "aria-hidden": "true",
                             title: `${fmtTps(value)} ${unit.unit}; the largest at this gas limit is ${fmtTps(max)}` },
        el("span", { style: `width:${Math.max(1, (value / max) * 100).toFixed(1)}%` })));
    }
    return td;
  }

  function pctCell(row, field, isAgg) {
    const value = row[field];
    const td = el("td", { class: "n", "data-field": field, "data-value": JSON.stringify(value) });
    if (value === null || value === undefined) {
      td.append(el("span", { class: "dim" }, "n/a"));
      return td;
    }
    td.dataset.percent = String(value * toPercent.get(row));
    td.append(fmtPct(value * toPercent.get(row)), isAgg
      ? chip("simulated", "A difference between simulated cells" + (row.eps_note ? ": " + row.eps_note : ""))
      : chip("modelled", row.eps_source || "The analytic gas/byte bound"));
    return td;
  }

  function renderTable() {
    const entry = idById.get(scenarioSelect.value);
    const tableId = TABLE_ALIAS[entry.id] || entry.id;
    const unit = unitOf(entry.class);
    const isAgg = entry.class !== "noagg";
    const table = clear($("#ex-table"));
    const lead = clear($("#ex-table-lead"));
    const note = clear($("#ex-table-note"));
    const group = groups[Number(groupSelect.value) || 0];
    table.dataset.scenario = entry.id;
    table.dataset.tableScenario = tableId;
    table.dataset.unit = unit.key;

    if (TABLE_ALIAS[entry.id]) {
      lead.append(`${entry.id} was not simulated separately: it equals ${tableId} in this model, and its first hop is not simulated. The cells below are ${tableId}'s. `);
    }
    if (!group) {
      $("#ex-group-full").textContent = "";
      lead.append("The final tables hold no row for this scenario.");
      return;
    }
    table.dataset.group = group.key;
    $("#ex-group-full").textContent = "Cells shown: " + groupLabel(group) + ".";
    lead.append(`Throughput is in ${unit.unit === "tx/s" ? "transactions per second (tx/s)" : "signatures per second (sig/s)"}` +
      ", the mean over the seeds; the bar under a figure compares the sets at one gas limit. " +
      "The percentage columns give the change in throughput when the signature grows by 10% or 100% in bytes, or in permutations. " +
      "Every row is one row of the campaign's final table.");

    table.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col" }, "Set"),
      el("th", { scope: "col", class: "n" }, "Throughput, " + unit.unit),
      el("th", { scope: "col" }, "What binds"),
      ...PCT_COLUMNS.map(([, label]) => el("th", { scope: "col", class: "n" }, label)))));

    const gasLimits = [...new Set([...MAIN_GAS, ...group.rows.map((row) => row.gas_limit)])].sort((a, b) => a - b);
    for (const gas of gasLimits) {
      const rows = group.rows.filter((row) => row.gas_limit === gas).sort((a, b) => {
        const ia = order.indexOf(a.candidate), ib = order.indexOf(b.candidate);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.P - b.P;
      });
      const body = el("tbody", { "data-gas": String(gas) });
      body.append(el("tr", { class: "group" }, el("th", { colspan: "7", scope: "colgroup" }, fmtGas(gas) + " gas limit")));
      if (!rows.length) {
        body.append(el("tr", { class: "empty" }, el("td", { colspan: "7" },
          `Not simulated at ${fmtGas(gas)} in these cells. Where the report gives a model bound for it, it is in the summary below.`)));
      }
      const max = Math.max(0, ...rows.map((row) => row[unit.key]).filter((value) => typeof value === "number"));
      for (const row of rows) {
        const set = setByName.get(row.candidate);
        const head = el("th", { scope: "row" }, set ? setLabel(set.label) : row.candidate,
          set && set.pick ? [" ", el("span", { class: "tag" }, "pick")] : null,
          el("span", { class: "sub" }, (set ? row.candidate + " · " : "") +
            `${fmtInt(row.B)} B · ${row.P} perms` + (row.k > 1 ? ` · ${row.k} per tx` : "")));
        body.append(el("tr", { class: set && set.pick ? "pick" : null, "data-candidate": row.candidate, "data-gas": String(gas) },
          head, throughputCell(row, unit, max),
          el("td", { "data-field": "binding", "data-value": JSON.stringify(row.binding) }, row.binding),
          ...PCT_COLUMNS.map(([field]) => pctCell(row, field, isAgg))));
      }
      table.append(body);
    }

    const unique = (values) => [...new Set(values.filter(Boolean))];
    const statuses = unique(group.rows.map((row) => row.status));
    const sources = unique(group.rows.map((row) => row.source));
    note.append(chip("simulated"), " Throughput and what binds: ", taggedProse(statuses.join("; ")), ". ");
    if (isAgg) {
      const notes = unique(group.rows.map((row) => row.eps_note));
      note.append(chip("simulated"), " Percentage columns: differences between simulated cells",
        notes.length ? " (" + notes.join("; ") + ")" : "", ". ");
    } else {
      note.append(chip("modelled"), " Percentage columns: ", taggedProse(unique(group.rows.map((row) => row.eps_source)).join("; ")), ". ");
    }
    note.append("A label such as ", el("code", {}, "gas(2)/prover(1)"), " counts the seeds per limit. Source: ",
      el("span", { class: "mono" }, sources.join("; ")), ".");
  }

  // ------------------------------------------------- the report's summary
  const SUMMARY_FIELDS = [
    ["when", "When"], ["variant", "Variant"], ["unit", "Unit"],
    ["binds_60M", "Binds at 60 M"], ["binds_200M", "Binds at 200 M"],
    ["tps_B0", "Today's set (B0)"], ["tps_P99r", "Working set (P99r)"], ["tps_P99a", "Smaller sibling (P99a)"],
    ["tps_S24minB", "3-bit digits (S24-minB)"], ["tps_N2", "Layered, FORS+C (N2)"], ["spread", "Seed spread"],
    ["dB10", "Bytes +10%"], ["dB100", "Bytes +100%"], ["dP10", "Perms +10%"], ["dP100", "Perms +100%"],
    ["perms_show_up_in", "Permutations show up in"], ["best_set", "Best set"],
    ["regret_P99r", "Cost of the working set"], ["regret_P99a", "Cost of the smaller sibling"],
    ["status", "Evidence"], ["note", "Note"]];

  function renderSummary(entry) {
    const rows = data.summary.rows.filter((row) => row.scenario === entry.id || row.scenario.startsWith(entry.id + "-"));
    const body = clear($("#ex-summary-body"));
    $("#ex-summary").hidden = !rows.length;
    $("#ex-summary > summary").textContent =
      `What the report's summary says about this scenario (${rows.length} row${rows.length === 1 ? "" : "s"})`;
    body.append(el("p", { class: "note" }, "From the report's summary table, as written there. The tags in brackets are the campaign's: ",
      el("span", { class: "rawtag" }, "[C]"), " counted or simulated, ", el("span", { class: "rawtag" }, "[Mo]"), " modelled, ",
      el("span", { class: "rawtag" }, "[M]"), " measured, ", el("span", { class: "rawtag" }, "[E]"), " from an EIP text."));
    for (const row of rows) {
      const list = el("dl", { class: "kv" });
      for (const [field, label] of SUMMARY_FIELDS) {
        if (row[field] === null || row[field] === undefined || row[field] === "") continue;
        list.append(el("dt", {}, label), el("dd", {}, taggedProse(row[field])));
      }
      body.append(el("div", { class: "stack" }, el("h4", {}, row.name), list));
    }
  }

  // ------------------------------------------------------- candidate sets
  function renderCandidates() {
    const table = clear($("#ex-candidates"));
    table.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col" }, "Set"),
      el("th", { scope: "col", class: "n" }, "Bytes"),
      el("th", { scope: "col", class: "n" }, "Perms"),
      el("th", { scope: "col", class: "n" }, "Lifetime", el("span", { class: "sub" }, "signatures ", chip("modelled"))),
      el("th", { scope: "col", class: "n" }, "Ledger sign", el("span", { class: "sub" }, "mean / p99 ", chip("modelled"))),
      el("th", { scope: "col" }, "Passes at 5.0K/s", el("span", { class: "sub" }, chip("modelled"))),
      el("th", { scope: "col", class: "n" }, "EVM verify", el("span", { class: "sub" }, "gas ", chip("measured"))),
      el("th", { scope: "col", class: "n" }, "leanVM", el("span", { class: "sub" }, "cycles per sig ", chip("counted"))))));
    const body = el("tbody");
    for (const set of sets) {
      const page = set.page || {};
      const passes = page["Passes at 5.0K/s"] || "";
      const passCell = el("td", {});
      if (/^no/.test(passes)) passCell.append(el("span", { class: "no" }, "no"), passes.replace(/^no\s*/, ""));
      else passCell.append(passes);
      const gas = set.evm_verify_gas ? fmtInt(set.evm_verify_gas.mean) : null;
      body.append(el("tr", { class: set.pick ? "pick" : null, "data-candidate": set.name },
        el("th", { scope: "row" }, setLabel(set.label), set.pick ? [" ", el("span", { class: "tag" }, "pick")] : null,
          el("span", { class: "sub" }, set.name), el("span", { class: "sub" }, el("code", {}, set.params))),
        el("td", { class: "n", "data-field": "sig_bytes" }, fmtInt(set.sig_bytes)),
        el("td", { class: "n", "data-field": "perms" }, fmtInt(set.perms)),
        el("td", { class: "n" }, withExponent(page.Lifetime || "n/a")),
        el("td", { class: "n" }, `${set.figures.sign_s_mean_5800.toFixed(1)} / ${set.figures.sign_s_p99_5800.toFixed(1)} s`),
        passCell,
        el("td", { class: "n" }, gas || el("span", { class: "dim" }, "not built")),
        el("td", { class: "n" }, page["leanVM (cycles per sig)"] || fmtInt(set.leanvm.cycles_per_sig))));
    }
    table.append(body);
  }

  // ---------------------------------------------------------- calculator
  const calc = { base: $("#calc-base"), bytes: $("#calc-bytes"), perms: $("#calc-perms"), gas: $("#calc-gas") };
  const calcStatus = $("#calc-status");
  const calcBody = $("#calc-table tbody");
  const CALC_ROWS = [
    ["candidate", "Point", (r) => String(r.candidate)],
    ["k", "Signatures per transaction", (r) => fmtInt(r.k)],
    ["tx_gas", "Gas per transaction", (r) => fmtInt(r.tx_gas) + " gas"],
    ["tx_onchain_bytes", "Bytes on chain per transaction", (r) => fmtInt(r.tx_onchain_bytes) + " B"],
    ["ideal_bound_tps", "Throughput bound", (r) => fmtTps(r.ideal_bound_tps) + " tx/s"],
    ["tps_sig_bound", "Throughput bound, signatures", (r) => fmtTps(r.tps_sig_bound) + " sig/s"],
    ["ideal_binding", "What binds", (r) => String(r.ideal_binding)],
    ["gas_bound_tps", "Gas bound alone", (r) => fmtTps(r.gas_bound_tps) + " tx/s"],
    ["byte_bound_tps", "Block byte bound alone", (r) => fmtTps(r.byte_bound_tps) + " tx/s"],
    ["prover_bound_tps", "Prover bound alone", (r) => r.prover_bound_tps < 0 ? "none" : fmtTps(r.prover_bound_tps) + " tx/s"],
    ["dB10", "Bytes +10%", (r) => fmtPct(r.dB10)],
    ["dB100", "Bytes +100%", (r) => fmtPct(r.dB100)],
    ["dP10", "Perms +10%", (r) => fmtPct(r.dP10)],
    ["dP100", "Perms +100%", (r) => fmtPct(r.dP100)],
  ];
  for (const [field, label] of CALC_ROWS) {
    calcBody.append(el("tr", { "data-field": field },
      el("th", { scope: "row" }, label), el("td", { class: "n value" }, "—"), el("td", {}, chip("modelled"))));
  }
  for (const set of sets) {
    calc.base.append(el("option", { value: set.name }, `${setLabel(set.label)} · ${set.name} (${fmtInt(set.sig_bytes)} B, ${set.perms} perms)`));
  }
  calc.base.value = setByName.has("P99r") ? "P99r" : sets[0].name;
  for (const gas of CALC_GAS) calc.gas.append(el("option", { value: String(gas) }, fmtGas(gas) + " gas"));
  const baseValues = () => {
    const set = setByName.get(calc.base.value);
    calc.bytes.value = set.sig_bytes;
    calc.perms.value = set.perms;
  };
  baseValues();

  function updateCalcContext(entry, config) {
    const follows = config.settings.k && config.settings.k.follows;
    clear($("#calc-context")).append("Scenario: ", el("strong", {}, scenarioLabel(entry)), ", configuration ",
      el("code", {}, entry.default_configuration), " (chosen above). The base set fixes the EVM verifier model: single layer for the working set and its siblings, layered for today's set, N2 and N3r. ",
      follows ? "Here a transaction is a bundle whose size follows the permutations, so the percentage rows are changes of the bundle rate, not of signatures per second." : "");
  }

  // The engine's config for the calculator. At the base set's own bytes and
  // permutations the named set is evaluated (with its counted prover rows
  // where they exist); otherwise the custom point on the base set's verifier.
  function calcConfig() {
    const entry = idById.get(scenarioSelect.value);
    const set = setByName.get(calc.base.value);
    const bytes = Number(calc.bytes.value), perms = Number(calc.perms.value);
    const config = { scenario: entry.id, candidate: set.name, gas_limit: Number(calc.gas.value) };
    if (bytes !== set.sig_bytes || perms !== set.perms) {
      config.sig_bytes = bytes;
      config.perms = perms;
    }
    return config;
  }

  let calcTimer = 0;
  let calcSeq = 0;
  function scheduleCalc(delay = 250) {
    clearTimeout(calcTimer);
    calcTimer = setTimeout(runCalc, delay);
  }

  function setCalcStatus(text, isError = false) {
    calcStatus.textContent = text;
    calcStatus.classList.toggle("error", isError);
  }

  async function runCalc() {
    const state = app.engineState.state;
    if (state === "loading") return setCalcStatus("Waiting for the simulator to load.");
    if (state === "failed") return setCalcStatus("The model needs the simulator, which did not start: " + app.engineState.message, true);
    for (const input of [calc.bytes, calc.perms]) {
      const value = Number(input.value);
      if (input.value === "" || !Number.isInteger(value) || value < 1) {
        return setCalcStatus($(`label[for=${input.id}]`).textContent + ": enter a whole number of at least 1.", true);
      }
    }
    const config = calcConfig();
    const seq = ++calcSeq;
    setCalcStatus(state === "running" ? "Waiting for the running simulation to finish…" : "Evaluating the model…");
    try {
      const result = await engine.evaluateModel(config);
      if (seq !== calcSeq) return;
      for (const [field, , format] of CALC_ROWS) {
        const td = calcBody.querySelector(`tr[data-field="${field}"] td.value`);
        const value = result[field];
        td.textContent = value === null || value === undefined ? "n/a" : format(result);
        td.dataset.value = JSON.stringify(value === undefined ? null : value);
      }
      $("#calc-table").dataset.config = JSON.stringify(config);
      $("#calc-table").dataset.seq = String(seq);
      setCalcStatus(`Model evaluated in ${Math.round(result.timing.worker_ms)} ms: ${result.candidate} in ${result.scenario} at ${fmtGas(result.gas_limit)} gas.`);
      const settings = configs.get(idById.get(scenarioSelect.value).default_configuration).settings;
      const note = clear($("#calc-note"));
      const swB = settings.sig_bytes.swept, swP = settings.perms.swept;
      if (swB && swP && (result.sig_bytes < swB[0] || result.sig_bytes > swB[1] || result.perms < swP[0] || result.perms > swP[1])) {
        note.append(el("span", { class: "no" }, "Outside the swept range. "),
          `The campaign's sweeps moved the signature from ${fmtInt(swB[0])} to ${fmtInt(swB[1])} bytes and from ${fmtInt(swP[0])} to ${fmtInt(swP[1])} permutations; outside that the model extrapolates. `);
      }
      note.append("The bound is the lowest of the three limits, with networking assumed away. A block holds a whole number of transactions, so the bound moves in steps. ",
        result.prover_cost ? `Prover price: ${result.prover_cost}. ` : "No prover in this scenario. ",
        "Percentage rows: ", result.elasticity_basis || "the model", ".");
    } catch (error) {
      if (seq !== calcSeq) return;
      if (error instanceof EngineCancelled) return setCalcStatus("The simulator was restarted; the model runs again when it is ready.");
      setCalcStatus("The model refused this point. " + (error && error.message ? error.message : String(error)), true);
    }
  }

  // --------------------------------------------------------------- wiring
  scenarioSelect.addEventListener("change", renderScenario);
  groupSelect.addEventListener("change", renderTable);
  calc.base.addEventListener("change", () => { baseValues(); scheduleCalc(0); });
  calc.gas.addEventListener("change", () => scheduleCalc(0));
  calc.bytes.addEventListener("input", () => scheduleCalc());
  calc.perms.addEventListener("input", () => scheduleCalc());
  $("#calc-form").addEventListener("submit", (event) => { event.preventDefault(); scheduleCalc(0); });

  renderCandidates();
  renderScenario();
  let lastState = null;
  app.onEngineState((state) => {
    if (state.state === lastState) return;
    lastState = state.state;
    if (state.state === "ready" || state.state === "failed" || state.state === "loading") scheduleCalc(0);
  });
}
