// The Explore view: a scenario, its final table, the candidate sets, and
// the model calculator.

import { EngineCancelled } from "../engine/engine.mjs";
import { $, el, clear, chip, fmtInt, fmtTps, fmtPct, fmtGas, withExponent, taggedProse, pricingName,
         unitOf, scenarioLabel, setLabel, CLASS_NAMES } from "./util.mjs";
import { ecdsaBaseline, WORKING_SET, TODAY_SET } from "./scenarios.mjs";

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
  return [`${pricingName(group.pricing)}`, `${group.profile} profile`, `${group.uplinks} uplinks`,
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
      el("p", {}, entry.description),
      el("ul", { class: "facts" },
        el("li", {}, el("b", {}, "When"), entry.when),
        el("li", {}, el("b", {}, "Class"), CLASS_NAMES[entry.class] || entry.class),
        el("li", {}, el("b", {}, "Pricing"), pricingName(entry.pricing)),
        el("li", {}, el("b", {}, "Simulated as"), entry.mode === "capacity" ? "a search for the highest sustained rate" : "one run at the offered load"),
        el("li", {}, el("b", {}, "Configurations"), String(entry.configurations.length))),
      entry.explore_only ? el("p", { class: "small" }, "Tables only, not run in the browser: " + entry.limits.reason + ".") : null);
    card.dataset.scenario = entry.id;
    const head = clear($("#ex-head-h"));
    head.append(entry.name + (entry.route ? ": " + entry.route : ""), " ", el("span", { class: "sc-id" }, entry.id));
    const alias = $("#ex-alias");
    alias.hidden = !TABLE_ALIAS[entry.id];
    alias.textContent = TABLE_ALIAS[entry.id]
      ? `${entry.id} was not simulated separately: these are ${TABLE_ALIAS[entry.id]}'s cells, equal in this model.` : "";

    const tableId = TABLE_ALIAS[entry.id] || entry.id;
    groups = groupRows(allRows.filter((row) => row.scenario === tableId), order);
    clear(groupSelect);
    groups.forEach((group, index) => groupSelect.append(el("option", { value: String(index) }, groupLabel(group))));
    groupSelect.disabled = groups.length < 2;
    renderTable();
    fillGasControls(entry);
    renderGas();
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

    renderHeadline(entry, group);
    if (TABLE_ALIAS[entry.id]) {
      lead.append(`${entry.id} was not simulated separately; its first hop is not simulated. `);
    }
    if (!group) {
      $("#ex-group-full").textContent = "";
      lead.append("The final tables hold no row for this scenario.");
      return;
    }
    table.dataset.group = group.key;
    $("#ex-group-full").textContent = "Cells shown: " + groupLabel(group) + ".";
    lead.append(`${unit.unit}, mean over the seeds; the bar compares the sets at one gas limit. ` +
      "Percent columns: the change in throughput when bytes or permutations grow by 10% or 100%.");

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
      body.append(el("tr", { class: "group" }, el("th", { colspan: "7", scope: "colgroup", "data-baseline": String(ecdsaBaseline(gas)) },
        fmtGas(gas) + " gas limit",
        el("span", { class: "sub baseline" }, `ECDSA reference: ${fmtTps(ecdsaBaseline(gas))} tx/s (gas bound)`))));
      if (!rows.length) {
        body.append(el("tr", { class: "empty" }, el("td", { colspan: "7" },
          `Not simulated at ${fmtGas(gas)} in these cells; the report's summary below may give a model bound.`)));
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

  // ---------------------------------------------------------- the headline
  // The working set and today's set at one gas limit, from the group of
  // cells the full table shows (its first group by default), what binds, and
  // the gas of one transaction from data/gas.json.
  let hlGas = MAIN_GAS[0];
  const hlOpts = $("#hl-gas-opts");
  let hlState = null;

  function gasRowFor(entry, group, candidate) {
    const names = entry.configurations.filter((name) => configs.get(name) && group && configs.get(name).pricing === group.pricing);
    const name = names.includes(entry.default_configuration) ? entry.default_configuration : (names[0] || entry.default_configuration);
    const cls = configs.get(name).class;
    const k = group && typeof group.k === "number" ? group.k : 1;
    return data.gas.rows.find((row) => row.configuration === name && row.candidate === candidate && (cls === "pcs" || row.k === k)) || null;
  }

  function stat(key, label, value, sub, attrs = {}) {
    return el("div", { class: "stat", "data-stat": key, ...attrs },
      el("p", { class: "stat-label" }, label),
      el("p", { class: "stat-value" }, ...value),
      el("p", { class: "stat-sub" }, sub || "\u00a0"));
  }

  function renderHeadline(entry, group) {
    hlState = { entry, group };
    const unit = unitOf(entry.class);
    const limits = [...new Set([...MAIN_GAS, ...(group ? group.rows.map((row) => row.gas_limit) : [])])].sort((a, b) => a - b);
    if (!limits.includes(hlGas)) hlGas = limits.includes(MAIN_GAS[0]) ? MAIN_GAS[0] : limits[0];
    if (hlOpts.dataset.limits !== limits.join()) {
      hlOpts.dataset.limits = limits.join();
      clear(hlOpts);
      for (const gas of limits) {
        const input = el("input", { type: "radio", name: "hl-gas", value: String(gas), id: "hl-gas-" + gas });
        input.addEventListener("change", () => {
          if (!input.checked) return;
          hlGas = gas;
          renderHeadline(hlState.entry, hlState.group);
        });
        hlOpts.append(el("label", { class: "seg-opt", for: "hl-gas-" + gas }, input, el("span", {}, fmtGas(gas))));
      }
    }
    for (const input of hlOpts.querySelectorAll("input")) input.checked = Number(input.value) === hlGas;

    const rows = group ? group.rows.filter((row) => row.gas_limit === hlGas) : [];
    const rowOf = (name) => rows.find((row) => row.candidate === name) || null;
    const work = rowOf(WORKING_SET), today = rowOf(TODAY_SET);
    const box = clear($("#ex-headline"));
    box.dataset.scenario = entry.id;
    box.dataset.gas = String(hlGas);
    box.dataset.group = group ? group.key : "";
    const tpsStat = (key, label, name, row) => {
      const set = setByName.get(name);
      const sub = set ? `${name} · ${fmtInt(set.sig_bytes)} B · ${set.perms} perms` : name;
      if (!row || typeof row[unit.key] !== "number") {
        return stat(key, label, [el("span", { class: "dim" }, "not simulated")], `${sub} · none at ${fmtGas(hlGas)}`, { "data-value": "null" });
      }
      return stat(key, label, [el("span", { class: "num" }, fmtTps(row[unit.key])), " " + unit.unit,
        chip("simulated", `${row.status}. Source: ${row.source}`)], sub, { "data-value": JSON.stringify(row[unit.key]) });
    };
    const gasWork = gasRowFor(entry, group, WORKING_SET), gasToday = gasRowFor(entry, group, TODAY_SET);
    let gasSub = "";
    if (gasWork && gasWork.signatures > 1) gasSub = `bundle of ${fmtInt(gasWork.signatures)}, ${fmtInt(gasWork.gas_per_signature)} per signature`;
    else if (gasWork && gasToday && gasToday.total !== gasWork.total) gasSub = `working set; today's set ${fmtInt(gasToday.total)}`;
    else if (gasWork) gasSub = "the same for every set";
    const binding = work ? work.binding : null;
    const todayBinding = today ? today.binding : null;
    box.append(
      tpsStat("work", "Working set", WORKING_SET, work),
      tpsStat("today", "Today's set", TODAY_SET, today),
      stat("binding", "What limits it", [binding ? el("span", { class: "word" }, binding) : el("span", { class: "dim" }, "n/a")],
        todayBinding && todayBinding !== binding ? `today's set: ${todayBinding}` : (binding ? "both sets" : ""),
        { "data-value": JSON.stringify(binding) }),
      stat("gas", "Gas per transaction", gasWork ? [el("span", { class: "num" }, fmtInt(gasWork.total)), " gas", chip("modelled", data.gas.status_default)]
        : [el("span", { class: "dim" }, "n/a")], gasSub,
        { "data-value": JSON.stringify(gasWork ? gasWork.total : null), "data-configuration": gasWork ? gasWork.configuration : "" }));

    const ref = clear($("#sc-reference"));
    ref.dataset.baseline = String(ecdsaBaseline(hlGas));
    ref.dataset.gas = String(hlGas);
    ref.append("Reference: plain ECDSA transfers, ", el("strong", {}, fmtTps(ecdsaBaseline(hlGas)) + " tx/s"),
      ` at ${fmtGas(hlGas)} gas `, chip("modelled", "A gas bound: gas limit / 21,000 / 12 s"), " (a gas bound, not a measurement).");
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

  // ------------------------------------------- gas and calldata per transaction
  const gasData = data.gas;
  const gasConfig = $("#gas-config");
  const gasK = $("#gas-k");
  const gasFee = $("#gas-basefee");
  const gasUsd = $("#gas-ethusd");
  const BINDS = { floor: "calldata floor", standard: "standard path" };
  const GAS_LEAD = {
    noagg: "One transaction, with the signature in it. A transaction pays the larger of two prices: the standard path (its data bytes at the standard price, plus execution) or the calldata floor (its data bytes at the floor price, and nothing for execution). Where the floor binds, the verifier's execution is not charged on top; the verification column then shows what runs, marked \u201cinside the floor\u201d. \u201cBinds\u201d says which of the two prices the transaction pays.",
    agg8288: "In this scenario the signature bytes are not on chain. The transaction names each signature as a dependency, a 96-byte triple of message hash, key and scheme, and the signatures are proved off chain. What is paid instead is a charge per dependency (33,000 gas in EIP-8288: 3,000 for verification and 30,000 of recursive STARK gas; 3,000 in the leanPCS draft) and the triple's calldata. The gas is the same for every parameter set.",
    pcs: "In this scenario the signature bytes are not on chain. The transaction carries a proof (1,005 bytes of calldata and a 193-byte frame) and 64 bytes of claim, key and message, per signature it proves. The parameter set changes the gas only through the number of signatures that fit one proof.",
  };

  function gasRows() {
    const k = gasK.disabled ? null : Number(gasK.value);
    return gasData.rows.filter((row) => row.configuration === gasConfig.value && (k === null || row.k === k));
  }

  function fillGasControls(entry) {
    clear(gasConfig);
    for (const name of entry.configurations) {
      const config = configs.get(name);
      gasConfig.append(el("option", { value: name },
        `${name} · ${pricingName(config.pricing)}` + (name === entry.default_configuration ? " (the scenario's own)" : "")));
    }
    gasConfig.value = entry.default_configuration;
    gasConfig.disabled = entry.configurations.length < 2;
    const ks = gasData.ks[entry.class] || [];
    clear(gasK);
    if (ks.length) for (const k of ks) gasK.append(el("option", { value: String(k) }, String(k)));
    else gasK.append(el("option", { value: "" }, entry.id === "F3" ? "as many as one proof holds" : "1"));
    gasK.disabled = !ks.length;
    $("#gas-k-label").textContent = entry.class === "agg8288" ? "Dependencies per transaction"
      : entry.class === "pcs" ? "Signatures per proof" : "Signatures per transaction";
  }

  function fmtEth(value) {
    if (!Number.isFinite(value)) return "n/a";
    return value.toLocaleString("en-US", { minimumFractionDigits: 6, maximumFractionDigits: 6 });
  }
  function fmtUsd(value) {
    if (!Number.isFinite(value)) return "n/a";
    const digits = value >= 100 ? 0 : value >= 0.1 ? 2 : 4;
    return "$" + value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  const positive = (input) => (input.value !== "" && Number(input.value) >= 0 ? Number(input.value) : NaN);

  // The figure a verification cell shows: what is charged, or, where the
  // floor binds and nothing is charged on top, what the verifier executes.
  const verificationShown = (row) => (row.binds === "floor" && row.parts.verification === 0
    ? row.verification_executed : row.parts.verification);

  function gasCell(field, value, text, kind, title, ...subs) {
    // The evidence chip of these columns is in the column head; the cell
    // carries the status sentence as its title.
    const td = el("td", { class: "n", "data-field": field, "data-value": JSON.stringify(value), "data-evidence": kind, title: title || null }, text);
    for (const sub of subs) if (sub) td.append(el("span", { class: "sub" }, sub));
    return td;
  }

  function renderGas() {
    const entry = idById.get(scenarioSelect.value);
    const table = clear($("#ex-gas"));
    const rows = gasRows();
    const status = gasData.status[rows.length ? rows[0].status_key : entry.class] || {};
    const onChain = rows.length ? rows[0].signature_on_chain : entry.class === "noagg";
    table.dataset.scenario = entry.id;
    table.dataset.configuration = gasConfig.value;
    table.dataset.k = gasK.disabled ? "" : gasK.value;
    clear($("#ex-gas-lead")).append(GAS_LEAD[entry.class] || "");
    const fee = positive(gasFee), usd = positive(gasUsd);
    table.dataset.baseFee = String(fee);
    table.dataset.ethUsd = String(usd);
    const verifyHead = entry.class === "agg8288" ? "Dependency charge" : entry.class === "pcs" ? "Proof check" : "Verification";
    table.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col" }, "Set"),
      el("th", { scope: "col", class: "n" }, "Total gas", el("span", { class: "sub" }, chip("modelled", status.total))),
      el("th", { scope: "col", class: "n" }, "Calldata gas", el("span", { class: "sub" }, "share of total ", chip("modelled", status.calldata))),
      el("th", { scope: "col", class: "n" }, "Signature bytes", el("span", { class: "sub" }, "bytes × price ", chip("modelled", status.calldata))),
      el("th", { scope: "col", class: "n" }, verifyHead, el("span", { class: "sub" }, "gas ", chip("modelled", status.verification))),
      el("th", { scope: "col", class: "n" }, "Fixed, overhead", el("span", { class: "sub" }, "gas ", chip("modelled", status.fixed))),
      el("th", { scope: "col", class: "n" }, "On chain", el("span", { class: "sub" }, "bytes ", chip("modelled"))),
      el("th", { scope: "col" }, "Binds"),
      el("th", { scope: "col", class: "n" }, "Cost, ETH", el("span", { class: "sub" }, "and USD")))));
    const body = el("tbody");
    for (const row of rows) {
      const set = setByName.get(row.candidate);
      const many = row.signatures > 1;
      const shown = verificationShown(row);
      const inside = row.binds === "floor" && row.parts.verification === 0 && row.verification_executed > 0;
      const verify = gasCell("verification", row.parts.verification, fmtInt(shown), "modelled", status.verification,
        inside ? "inside the floor" : null);
      verify.dataset.shown = JSON.stringify(shown);
      if (row.verification_measured !== null && row.verification_measured !== undefined) {
        verify.append(el("span", { class: "sub", "data-field": "verification_measured", "data-value": JSON.stringify(row.verification_measured) },
          `Forge ${fmtInt(row.verification_measured)}`, chip("measured", "The campaign's Solidity verifier, mean over 52 test vectors (data/phase2b/evm_gas.json)")));
      }
      const eth = row.total * fee * 1e-9;
      body.append(el("tr", { class: set && set.pick ? "pick" : null, "data-candidate": row.candidate, "data-k": String(row.k) },
        el("th", { scope: "row" }, set ? setLabel(set.label) : row.candidate,
          set && set.pick ? [" ", el("span", { class: "tag" }, "pick")] : null,
          el("span", { class: "sub" }, `${row.candidate} · ${fmtInt(row.sig_bytes)} B · ${row.perms} perms` +
            (many ? ` · ${fmtInt(row.signatures)} signatures` : ""))),
        gasCell("total", row.total, fmtInt(row.total), "modelled", status.total,
          many ? `${fmtInt(row.gas_per_signature)} per signature` : null),
        gasCell("calldata", row.parts.calldata, fmtInt(row.parts.calldata), "modelled", status.calldata,
          `${(row.calldata_share * 100).toFixed(1)}% of total`),
        onChain
          ? gasCell("signature_calldata", row.signature_calldata, fmtInt(row.signature_calldata), "modelled", status.calldata,
            `${fmtInt(row.k * row.sig_bytes)} B × ${row.signature_byte_price}`)
          : el("td", { class: "n", "data-field": "signature_calldata", "data-value": "0" }, el("span", { class: "dim" }, "not on chain")),
        verify,
        gasCell("other", row.parts.fixed + row.parts.overhead, fmtInt(row.parts.fixed + row.parts.overhead), "modelled",
          `Fixed: ${status.fixed}. Overhead: ${status.overhead}`,
          `${fmtInt(row.parts.fixed)} fixed + ${fmtInt(row.parts.overhead)}`),
        gasCell("onchain_bytes", row.onchain_bytes, fmtInt(row.onchain_bytes), "modelled", "The model's estimate of the transaction's size in the block"),
        el("td", { "data-field": "binds", "data-value": JSON.stringify(row.binds) }, row.binds === "floor" ? "floor" : "standard"),
        gasCell("cost_eth", Number.isFinite(eth) ? eth : null, Number.isFinite(eth) ? fmtEth(eth) : "n/a", null, null,
          Number.isFinite(eth * usd) ? fmtUsd(eth * usd) : "enter both prices")));
    }
    table.append(body);

    const note = clear($("#ex-gas-note"));
    note.append(chip("modelled"), " Every gas figure in this table is the campaign's gas model (",
      el("span", { class: "mono" }, rows.length ? rows[0].model : ""), "), not a receipt. Total: ", taggedProse(status.total || ""),
      ". Fixed: ", taggedProse(status.fixed || ""), ". Calldata: ", taggedProse(status.calldata || ""),
      ". ", verifyHead, ": ", taggedProse(status.verification || ""), ". Overhead: ", taggedProse(status.overhead || ""), ". ");
    if (rows.some((row) => row.verification_measured !== null && row.verification_measured !== undefined)) {
      note.append(chip("measured"), " \u201cForge\u201d is the mean gas of the campaign's own verifier for that set over 52 test vectors. The model's fitted price per permutation is higher, by 3% for the 2-bit sets and by 11 to 12% for the 3-bit sets; where the floor binds this does not change the total. ");
    }
    if (rows.some((row) => row.k > 1) && entry.class === "noagg") {
      note.append("Rows with more than one signature per transaction carry the multisig caveat in About. ");
    }
    if (entry.class === "pcs") note.append("On-chain bytes are the harness's figure for the whole transaction; the floor prices the proof calldata, the frame and the claims. ");
  }

  const KIND_GROUPS = [
    ["frame_tx", "EIP-8141 frame transaction (the route of scenario A)"],
    ["verify_tx", "Standalone verify transaction (calldata priced as in E0b and E0c, no account around it)"],
    ["handleops", "ERC-4337 handleOps (the route of scenario E0a)"],
    ["verify_call", "Verifier call alone, in Foundry (no transaction)"]];

  function measuredRow(key, what, sub, bytes, gas, exact, where, when, model, diff, notes, source) {
    return el("tr", { "data-measured": key },
      el("th", { scope: "row" }, what, sub ? el("span", { class: "sub" }, sub) : null),
      el("td", { class: "n", "data-field": "sig_bytes", "data-value": JSON.stringify(bytes) }, fmtInt(bytes)),
      el("td", { class: "n", "data-field": "gas", "data-value": JSON.stringify(gas) }, (exact ? "" : "about ") + fmtInt(gas), chip("measured", source)),
      el("td", {}, where, when ? el("span", { class: "sub" }, when) : null),
      model === null || model === undefined
        ? el("td", { class: "n", "data-field": "model_gas", "data-value": "null" }, el("span", { class: "dim" }, "none"))
        : el("td", { class: "n", "data-field": "model_gas", "data-value": JSON.stringify(model) }, fmtInt(model), chip("modelled"),
          el("span", { class: "sub" }, diff === 0 ? "equal to the gas" : fmtPct(diff) + " against the receipt")),
      el("td", { class: "notes" }, notes || "", el("span", { class: "sub mono" }, source)));
  }

  function renderMeasured() {
    const measured = gasData.measured;
    const table = clear($("#ex-measured"));
    table.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col" }, "What"),
      el("th", { scope: "col", class: "n" }, "Signature", el("span", { class: "sub" }, "bytes")),
      el("th", { scope: "col", class: "n" }, "Gas"),
      el("th", { scope: "col" }, "Where and when"),
      el("th", { scope: "col", class: "n" }, "Model", el("span", { class: "sub" }, "same shape")),
      el("th", { scope: "col" }, "Caveats and source"))));
    const campaign = el("tbody", { "data-group": "campaign" });
    campaign.append(el("tr", { class: "group" }, el("th", { colspan: "6", scope: "colgroup" },
      "The campaign's working set with its real verifier, on a local chain (the routes of E0b and E0c)")));
    measured.campaign.forEach((row, index) => {
      campaign.append(measuredRow(`campaign:${index}`, row.what,
        `verifier ${fmtInt(row.verifier_gas)} gas, overhead ${fmtInt(row.overhead_gas)}, ${fmtInt(row.input_bytes)} B of calldata`,
        row.sig_bytes, row.gas, true, row.network, row.date, row.model_gas, row.model_diff_pct,
        [`The ${row.path === "floor" ? "calldata floor" : "standard path"} binds. Re-priced under EIP-7976 and EIP-2780 the receipt gives ${fmtInt(row.glamsterdam_total_recorded)}; the model gives ${fmtInt(row.glamsterdam_total_model)}.`,
          row.caveat ? " " + row.caveat.replace(/^./, (c) => c.toUpperCase()) + "." : ""].join(""),
        row.source));
    });
    table.append(campaign);
    const deployments = measured.deployments;
    for (const [kind, label] of KIND_GROUPS) {
      const body = el("tbody", { "data-group": kind });
      body.append(el("tr", { class: "group" }, el("th", { colspan: "6", scope: "colgroup" },
        "Other parameter sets of the family, deployed: " + label)));
      deployments.rows.forEach((row, index) => {
        if (row.kind !== kind) return;
        body.append(measuredRow(`deployments:${index}`, row.scheme,
          (row.perms ? `${row.perms} permutations` : "") + (row.verify_frame_gas ? ` · VERIFY frame ${fmtInt(row.verify_frame_gas)} gas` : ""),
          row.sig_bytes, row.gas, row.exact, row.network, row.date, row.model_gas, row.model_diff_pct,
          row.caveat ? row.caveat.replace(/^./, (c) => c.toUpperCase()) + "." : "",
          `SPHINCs- ${row.source.file}:${row.source.line}`));
      });
      table.append(body);
    }
    clear($("#ex-measured-lead")).append(
      "Gas of real transactions, for comparison with the model above. None of these rows is one of the candidate sets on a public network: the first four are the working set on a local anvil chain, the rest are other parameter sets of the same family (C7, C12, C13, SLH-DSA-Keccak, leanSphincs and its -s, -f and -layered variants) that were deployed on Sepolia and on the ethrex EIP-8141 testnet. The model column is the campaign's gas model for the same shape, where it has one.");
    const receipts = gasData.cross_checks.filter((item) => item.kind === "receipt");
    const worst = Math.max(...receipts.map((item) => Math.abs(item.diff_pct)));
    clear($("#ex-measured-note")).append(chip("measured"),
      " Receipts on Sepolia and the ethrex testnet are quoted from the SPHINCs- repository (",
      el("span", { class: "mono" }, `commit ${String(deployments.commit).slice(0, 7)}, ${String(deployments.commit_date).slice(0, 10)}`),
      "); this site did not re-read them from the chain. Figures with “about” are rounded in the source. Rows dated June 2026 predate the 64-gas-per-byte calldata floor and the October verifier optimisations, and are not comparable with the later rows. ",
      chip("modelled"), ` The build compares the model with ${receipts.length} recorded receipts and stops above ${gasData.tolerance_pct}%; the largest difference is ${worst.toFixed(2)}%. The frame and verify transactions are reproduced to the gas because the calldata floor binds and the model's layout was fitted on them.`);
    const list = gasData.disagreements;
    $("#ex-disagree > summary").textContent = `Where two sources give different numbers (${list.length})`;
    const body = clear($("#ex-disagree-body"));
    body.append(el("p", { class: "note" }, "Both figures are shown with their labels; neither is dropped."));
    const dl = el("dl", { class: "kv" });
    for (const item of list) {
      dl.append(el("dt", {}, item.what),
        el("dd", { "data-disagreement": item.what },
          `${fmtInt(item.a.value)} `, taggedProse(item.a.label), ` against ${fmtInt(item.b.value)} `, taggedProse(item.b.label),
          ` (${fmtPct(item.diff_pct)}). Effect: ${item.effect}.`));
    }
    body.append(dl);
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
  const calcGasBody = $("#calc-gas-table tbody");
  const CALC_GAS_ROWS = [
    ["total", "Total gas", (g) => g.total, (g) => fmtInt(g.total) + " gas"],
    ["binds", "Price that binds", (g) => g.binds, (g) => BINDS[g.binds]],
    ["fixed", "Fixed", (g) => g.parts.fixed, (g) => fmtInt(g.parts.fixed) + " gas"],
    ["calldata", "Calldata", (g) => g.parts.calldata, (g) => `${fmtInt(g.parts.calldata)} gas, ${(g.calldata_share * 100).toFixed(1)}%`],
    ["signature_calldata", "of which signature bytes", (g) => g.signature_calldata,
      (g) => g.signature_on_chain ? `${fmtInt(g.signature_calldata)} gas (${fmtInt(g.k * g.sig_bytes)} B × ${g.signature_byte_price})` : "not on chain"],
    ["verification", "Verification, charged", (g) => g.parts.verification, (g) => fmtInt(g.parts.verification) + " gas"],
    ["verification_executed", "Verification, as it runs", (g) => g.verification_executed, (g) => fmtInt(g.verification_executed) + " gas"],
    ["overhead", "Overhead", (g) => g.parts.overhead, (g) => fmtInt(g.parts.overhead) + " gas"],
    ["standard_total", "Standard path", (g) => g.standard.total, (g) => fmtInt(g.standard.total) + " gas"],
    ["floor_total", "Calldata floor", (g) => g.floor.total, (g) => fmtInt(g.floor.total) + " gas"],
    ["gas_per_signature", "Gas per signature", (g) => g.gas_per_signature, (g) => fmtInt(g.gas_per_signature) + " gas"],
  ];
  for (const [field, label] of CALC_GAS_ROWS) {
    calcGasBody.append(el("tr", { "data-field": field },
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
    clear($("#calc-context")).append("Scenario ", el("strong", {}, scenarioLabel(entry)), ", configuration ",
      el("code", {}, entry.default_configuration), ". The base set fixes the verifier model (single layer, or layered for B0, N2 and N3r). ",
      follows ? "A transaction here is a bundle whose size follows the permutations: percentage rows are changes of the bundle rate." : "");
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
      const split = result.gas_breakdown;
      for (const [field, , pick, format] of CALC_GAS_ROWS) {
        const td = calcGasBody.querySelector(`tr[data-field="${field}"] td.value`);
        td.textContent = split ? format(split) : "n/a";
        td.dataset.value = JSON.stringify(split ? pick(split) : null);
      }
      if (split) {
        clear($("#calc-gas-note")).append(
          `Fixed, calldata, verification charged and overhead sum to the total: the ${BINDS[split.binds]} binds (the transaction pays the larger of the two paths). `,
          split.signature_on_chain
            ? (split.binds === "floor" && split.parts.verification === 0
              ? "Under the floor the verifier's execution is not charged on top; \u201cas it runs\u201d is what it would cost on the standard path. "
              : "")
            : "The signature bytes are not on chain here; the calldata is what stands in for them (dependency triples, or the proof and its claims). ",
          "Model: ", el("span", { class: "mono" }, split.model), ".");
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
  gasConfig.addEventListener("change", renderGas);
  gasK.addEventListener("change", renderGas);
  gasFee.addEventListener("input", renderGas);
  gasUsd.addEventListener("input", renderGas);
  $("#gas-form").addEventListener("submit", (event) => event.preventDefault());
  calc.base.addEventListener("change", () => { baseValues(); scheduleCalc(0); });
  calc.gas.addEventListener("change", () => scheduleCalc(0));
  calc.bytes.addEventListener("input", () => scheduleCalc());
  calc.perms.addEventListener("input", () => scheduleCalc());
  $("#calc-form").addEventListener("submit", (event) => { event.preventDefault(); scheduleCalc(0); });

  renderCandidates();
  renderMeasured();
  renderScenario();
  let lastState = null;
  app.onEngineState((state) => {
    if (state.state === lastState) return;
    lastState = state.state;
    if (state.state === "ready" || state.state === "failed" || state.state === "loading") scheduleCalc(0);
  });
}
