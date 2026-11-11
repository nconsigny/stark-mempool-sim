// The "Choose a set" view: one deployment per fork step (after Glamsterdam,
// after Hegotá, after the fork after it), the eight sets compared along that
// path, a two-set comparison and an all-scenarios matrix. Every figure comes
// from data/timeline.json, data/candidates.json and data/gas.json.

import { $, el, clear, chip, fmtInt, fmtTps, fmtFixed, fmtGas, pricingName, withExponent, setLabel } from "./util.mjs";
import { makeFigures, groupOf, detailBlock, pickLine, pickTitle, WORKING_SET, TODAY_SET } from "./scenarios.mjs";

export const SIBLING_SET = "P99a";
const MINUS = "−";

// Wording of the steps and of the options that are not one scenario id.
const STEP_NAME = {
  today: ["Before Glamsterdam", "today"],
  G: ["After Glamsterdam", "G*"],
  H: ["After Hegotá", "H*"],
  I: ["After the next fork", "I*"],
};
const OPTION_LABEL = {
  D: ["8288 aggregation, EIP price", "D/E"],
  "D-dep3000": ["8288 aggregation, leanPCS price", "D/E"],
};
const OPTION_LINE = {
  D: "Nodes prove in batches; 33,000 gas per signature.",
  "D-dep3000": "Nodes prove in batches; 3,000 gas per signature.",
};

const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const fmtLoss = (value) => (!isNumber(value) ? "n/a" : value < 0.005 ? "0%" : value.toFixed(value < 1 ? 2 : 1) + "%");
const signed = (value, text) => (value > 0 ? "+" : value < 0 ? MINUS : "") + text;

// ------------------------------------------------------------ the arithmetic
// One step of a path: {key, option, gas, months}. A set's loss in a step is
// its shortfall against the best set that has a public route there, in
// percent; a set without a route loses the whole step (the campaign's
// penalty). The weighted loss weights the steps by their months.
export function evaluatePath(t, names, path) {
  const months = path.reduce((sum, step) => sum + step.months, 0);
  const columns = path.map((step) => {
    const cell = t.cells[step.option][String(step.gas)];
    const admit = t.admission[step.option];
    const routed = names.filter((name) => admit[name].k1 !== false);
    const best = Math.max(...routed.map((name) => cell.sets[name].tps));
    const same = routed.every((name) => cell.sets[name].tps === best);
    return { step, cell, admit, best, same };
  });
  const rows = names.map((name, index) => {
    const steps = columns.map(({ cell, admit, best, same }) => {
      const route = admit[name].k1 !== false;
      const tps = cell.sets[name].tps;
      return { route, tps, loss: route ? (1 - tps / best) * 100 : t.penalty * 100,
               best: route && tps === best && !same, same: route && same, twoRefused: admit[name].k2 === false };
    });
    const noRoute = steps.filter((step) => !step.route).length;
    const weighted = months > 0
      ? steps.reduce((sum, step, i) => sum + step.loss * path[i].months, 0) / months : null;
    const c = t.constraints[name];
    return { name, index, steps, noRoute, weighted, eligible: noRoute === 0 && c.ledger_ok && c.lifetime_ok };
  });
  // A set without a public route in a step is never ranked above one that
  // has a route in every step.
  const order = [...rows].sort((a, b) => (a.noRoute > 0) - (b.noRoute > 0) ||
    (a.weighted ?? 0) - (b.weighted ?? 0) || a.index - b.index);
  order.forEach((row, i) => { row.rank = i + 1; });
  const eligible = order.filter((row) => row.eligible);
  return { rows, order, columns, months, winner: eligible[0] || null, runnerUp: eligible[1] || null };
}

// For each step, the other choices under which the winner changes (each
// alternative at its own default gas limit, the rest of the path unchanged).
export function winnerChanges(t, names, path) {
  const current = evaluatePath(t, names, path).winner;
  const changes = [];
  path.forEach((step, i) => {
    const spec = t.steps.find((item) => item.key === step.key);
    for (const option of spec.options) {
      if (option === step.option) continue;
      const other = path.map((item, j) => (j === i ? { ...item, option, gas: spec.default_gas[option] } : item));
      const winner = evaluatePath(t, names, other).winner;
      if ((winner && winner.name) !== (current && current.name)) {
        changes.push({ step: step.key, option, winner: winner ? winner.name : null });
      }
    }
  });
  return changes;
}

export function initChoose(app) {
  const { data } = app;
  const t = data.timeline;
  const sets = data.candidates.candidates;
  const names = sets.map((entry) => entry.name);
  const setByName = new Map(sets.map((entry) => [entry.name, entry]));
  const f = makeFigures(data);
  const measured = t.ledger.measured;

  const optionName = (key) => {
    const option = t.options[key];
    const [title, code] = OPTION_LABEL[key] || [pickTitle(option.id), option.id];
    return { title, code };
  };
  const optionLine = (key) => OPTION_LINE[key] || pickLine(t.options[key].id);
  const preset = t.timelines[t.default_timeline];

  const state = { today: false, steps: {}, pair: [WORKING_SET, SIBLING_SET], matrixGas: t.gas_limits[0] };
  for (const step of t.steps) {
    state.steps[step.key] = { option: step.default, gas: step.default_gas[step.default], months: preset.months[step.key] };
  }
  const activeSteps = () => t.steps.filter((step) => !step.optional || state.today);
  const path = () => activeSteps().map((step) => ({ key: step.key, ...state.steps[step.key] }));

  // --------------------------------------------------------------- the path
  const timelineSelect = $("#ch-timeline");
  t.timelines.forEach((item, index) => timelineSelect.append(el("option", { value: String(index), title: item.name },
    item.name.split(" (")[0] + ": " + t.steps.filter((step) => !step.optional).map((step) => item.months[step.key]).join(", "))));
  timelineSelect.append(el("option", { value: "custom", hidden: true }, "your own months"));
  timelineSelect.value = String(t.default_timeline);
  timelineSelect.addEventListener("change", () => {
    const chosen = t.timelines[Number(timelineSelect.value)];
    if (!chosen) return;
    for (const step of t.steps) state.steps[step.key].months = chosen.months[step.key];
    renderSteps();
    renderResult();
  });
  $("#ch-today").addEventListener("change", (event) => {
    state.today = event.target.checked;
    renderSteps();
    renderResult();
  });

  function renderSteps() {
    const box = clear($("#ch-steps"));
    activeSteps().forEach((step, index) => {
      const chosen = state.steps[step.key];
      const [title, code] = STEP_NAME[step.key];
      const number = state.today ? index : index + 1;
      const optionSelect = el("select", { id: `ch-opt-${step.key}` },
        step.options.map((key) => {
          const { title: name, code: id } = optionName(key);
          return el("option", { value: key }, `${name} (${id})${key === step.default ? ", default" : ""}`);
        }));
      optionSelect.value = chosen.option;
      optionSelect.addEventListener("change", () => {
        chosen.option = optionSelect.value;
        chosen.gas = step.default_gas[chosen.option];
        renderSteps();
        renderResult();
      });
      const cells = t.cells[chosen.option];
      const gasSelect = el("select", { id: `ch-gas-${step.key}` },
        t.gas_limits.map((gas) => el("option", { value: String(gas) },
          `${fmtGas(gas)}, ${cells[String(gas)].evidence === "simulated" ? "simulated" : "gas bound"}`)));
      gasSelect.value = String(chosen.gas);
      gasSelect.addEventListener("change", () => {
        chosen.gas = Number(gasSelect.value);
        renderSteps();
        renderResult();
      });
      const monthsInput = el("input", { id: `ch-months-${step.key}`, type: "number", inputmode: "numeric", min: "0", step: "1",
        value: String(chosen.months) });
      monthsInput.addEventListener("input", () => {
        const value = Number(monthsInput.value);
        if (monthsInput.value === "" || !Number.isFinite(value) || value < 0) return;
        chosen.months = value;
        timelineSelect.value = "custom";
        renderResult();
      });
      const cell = cells[String(chosen.gas)];
      const evidence = el("p", { class: "small ch-ev", "data-evidence": cell.evidence },
        chip(cell.evidence === "simulated" ? "simulated" : "modelled", cell.status), " ",
        cell.evidence === "simulated"
          ? (chosen.gas !== step.wanted_gas && chosen.gas === step.default_gas[chosen.option]
            ? `${fmtGas(step.wanted_gas)} is not simulated here; ${fmtGas(chosen.gas)} is the nearest that is.`
            : "40-node runs.")
          : `Gas bound: ${cell.simulated_sets} of ${names.length} sets simulated here.`);
      const group = groupOf(t.options[chosen.option].id);
      box.append(el("div", { class: "ch-step", "data-step": step.key, "data-option": chosen.option, "data-gas": String(chosen.gas) },
        el("h3", {}, `${number} · ${title} `, el("span", { class: "sc-id" }, code)),
        el("div", { class: "field" }, el("label", { for: optionSelect.id }, "Deployment"), optionSelect),
        el("p", { class: "small ch-line" }, optionLine(chosen.option)),
        el("div", { class: "ch-row" },
          el("div", { class: "field" }, el("label", { for: gasSelect.id }, "Block gas limit"), gasSelect),
          el("div", { class: "field" }, el("label", { for: monthsInput.id }, "Months"), monthsInput)),
        evidence,
        el("details", { id: `ch-how-${step.key}` },
          el("summary", {}, "How this deployment works"),
          el("div", { class: "stack" },
            el("p", { class: "note" }, group.simple + " Pricing: " + pricingName(t.options[chosen.option].pricing) + "."),
            detailBlock(group, f)))));
    });
  }

  // ---------------------------------------------------- the verdict, the table
  const setTitle = (name) => {
    const entry = setByName.get(name);
    return el("th", { scope: "row" }, name,
      name === WORKING_SET ? el("span", { class: "tag" }, "working set") : null,
      name === TODAY_SET ? el("span", { class: "tag", title: "Today's set: the reference" }, "reference") : null,
      el("span", { class: "sub" }, `${fmtInt(entry.sig_bytes)} B · ${fmtInt(entry.perms)} perms`));
  };
  const failing = (name) => {
    const c = t.constraints[name];
    return [c.ledger_ok ? null : "it misses the Ledger budget",
            c.lifetime_ok ? null : `its lifetime is below 2^${t.lifetime_floor_log2}`].filter(Boolean).join(" and ");
  };

  function stepCell(name, column, step) {
    const admit = column.admit[name];
    const attrs = { class: "n" + (step.best ? " best" : ""), "data-step": column.step.key, "data-tps": JSON.stringify(step.tps),
                    "data-loss": JSON.stringify(step.loss), "data-route": step.route ? "1" : "0" };
    if (!step.route) {
      return el("td", attrs, el("span", { class: "no", title: admit.basis || "not admitted to the public mempool" }, "no public route"),
        el("span", { class: "sub" }, "counts as 100%"));
    }
    return el("td", attrs, fmtTps(step.tps),
      el("span", { class: "sub" }, step.best ? "best" : step.same ? "same for all" : MINUS + fmtLoss(step.loss)),
      step.twoRefused ? el("span", { class: "sub no", "data-flag": "two-refused", title: isNumber(admit.rule6_gas_k2)
        ? `A transaction with two signatures is refused: ${fmtInt(admit.rule6_gas_k2)} gas of validation against ${fmtInt(t.max_verify_gas)}.`
        : "A transaction with two signatures is refused: " + (admit.basis || "") }, "2 sig. refused") : null);
  }

  function constraintCells(name) {
    const entry = setByName.get(name), figures = entry.figures, c = t.constraints[name];
    const isMeasured = measured.candidate === name;
    const certified = /^>=(\d+)/.exec(c.proof.certificate || "");
    return [
      el("td", { class: "n", "data-col": "ledger", "data-ok": c.ledger_ok ? "1" : "0" },
        `${fmtFixed(figures.sign_s_mean_5800, 1)} / ${fmtFixed(figures.sign_s_p99_5800, 1)}`,
        c.ledger_ok ? null : el("span", { class: "sub no" }, "over budget"),
        isMeasured ? el("span", { class: "sub", "data-measured": "sign", title: `Measured: mean and longest of ${measured.signatures} signatures` },
          `${fmtFixed(measured.sign_s.mean, 2)} / ${fmtFixed(measured.sign_s.max, 2)} max`, el("br"), chip("measured", measured.status)) : null),
      el("td", { class: "n", "data-col": "keygen" }, fmtFixed(figures.keygen_s_5800, 0),
        isMeasured ? el("span", { class: "sub", "data-measured": "keygen" }, fmtFixed(measured.keygen_s, 1), el("br"), chip("measured", measured.status)) : null),
      el("td", { class: "n", "data-col": "life", "data-ok": c.lifetime_ok ? "1" : "0" },
        withExponent("2^" + fmtFixed(figures.life_full_log2_cover, 1)),
        c.lifetime_ok ? null : el("span", { class: "sub" }, el("span", { class: "no" }, "below 2", el("sup", {}, String(t.lifetime_floor_log2))))),
      el("td", { "data-col": "digits" }, `${c.digit_bits}-bit`,
        c.digit_bits > 2 ? el("span", { class: "sub", title: `Its EVM verifier is ${fmtInt(entry.evm_verify_gas ? entry.evm_verify_gas.code_bytes : null)} bytes of code; its leanVM rows are ${c.proof.leanvm_rows}.` },
          "own guest, larger verifier") : null),
      el("td", { "data-col": "proof", title: c.proof.certificate },
        certified ? withExponent(`2^${certified[1]}`) : el("span", { class: "no" }, /not run/.test(c.proof.certificate || "") ? "not run" : "none"),
        el("span", { class: "sub" }, c.proof.caveat ? "FORS+C unverified"
          : c.proof.evm_verifier !== "built" ? "no EVM verifier" : "certified")),
    ];
  }

  function renderResult() {
    const steps = path();
    const result = evaluatePath(t, names, steps);
    const changes = winnerChanges(t, names, steps);
    const table = clear($("#ch-table"));
    table.dataset.path = JSON.stringify(steps);
    const head = el("tr", {}, el("th", { scope: "col" }, "Set"));
    for (const column of result.columns) {
      const { title, code } = optionName(column.step.option);
      head.append(el("th", { scope: "col", class: "n", title }, `${STEP_NAME[column.step.key][1]} · ${code}`,
        el("span", { class: "sub" }, `${t.options[column.step.option].unit}, ${fmtGas(column.step.gas)}`),
        chip(column.cell.evidence === "simulated" ? "simulated" : "modelled", column.cell.status)));
    }
    head.append(
      el("th", { scope: "col", class: "n" }, "Path loss", el("span", { class: "sub" }, "by months")),
      el("th", { scope: "col", class: "n" }, "Rank"),
      el("th", { scope: "col", class: "n" }, "Ledger signing", el("span", { class: "sub" }, "mean / p99, s"), chip("modelled", t.ledger.modelled_status)),
      el("th", { scope: "col", class: "n" }, "Keygen", el("span", { class: "sub" }, "seconds"), chip("modelled", t.ledger.modelled_status)),
      el("th", { scope: "col", class: "n" }, "Lifetime"),
      el("th", { scope: "col" }, "Digits"),
      el("th", { scope: "col" }, "Proof", el("span", { class: "sub" }, "lifetime")));
    const body = el("tbody");
    for (const row of result.rows) {
      const tr = el("tr", { class: row.name === WORKING_SET ? "pick" : null, "data-set": row.name,
        "data-weighted": JSON.stringify(row.weighted), "data-rank": String(row.rank),
        "data-eligible": row.eligible ? "1" : "0", "data-noroute": String(row.noRoute) }, setTitle(row.name));
      row.steps.forEach((step, i) => tr.append(stepCell(row.name, result.columns[i], step)));
      tr.append(
        el("td", { class: "n", "data-col": "weighted" }, row.weighted === null ? "n/a" : fmtLoss(row.weighted),
          row.noRoute ? el("span", { class: "sub" }, el("span", { class: "no" }, "no route")) : null),
        el("td", { class: "n" + (result.winner === row ? " best" : ""), "data-col": "rank" }, String(row.rank),
          result.winner === row ? el("span", { class: "sub" }, "wins") : null),
        ...constraintCells(row.name));
      body.append(tr);
    }
    table.append(el("thead", {}, head), body);

    // The verdict, in one sentence (and one more when the winner can change).
    const verdict = clear($("#ch-verdict"));
    const win = result.winner, second = result.runnerUp, first = result.order[0];
    verdict.dataset.winner = win ? win.name : "";
    verdict.dataset.runnerUp = second ? second.name : "";
    verdict.dataset.margin = JSON.stringify(win && second && result.months > 0 ? second.weighted - win.weighted : null);
    verdict.dataset.changes = JSON.stringify(changes);
    if (result.months <= 0) {
      verdict.append("Give at least one step a number of months to rank the sets.");
    } else if (!win) {
      verdict.append("No set meets the Ledger budget, the lifetime floor and has a public route on this path.");
    } else {
      const margin = second ? second.weighted - win.weighted : null;
      verdict.append(el("strong", {}, win.name), ` wins this path: ${fmtLoss(win.weighted)} lost against each step's best set`,
        second ? (margin < 0.005 ? `, level with ${second.name}` : `, ${margin.toFixed(margin < 1 ? 2 : 1)} points less than runner-up ${second.name}`) : "",
        " (sets meeting the Ledger budget and the lifetime floor).");
      if (first !== win) verdict.append(` ${first.name} loses less (${fmtLoss(first.weighted)}), but ${setLabel(failing(first.name))}.`);
      const stepWord = (key) => STEP_NAME[key][1] === "today" ? "today" : "after " + STEP_NAME[key][1];
      verdict.append(" ", changes.length
        ? "The winner changes with: " + changes.map((change) =>
          `${optionName(change.option).title} ${stepWord(change.step)} (then ${change.winner || "no set"})`).join("; ") + "."
        : "No other single choice of deployment changes the winner.");
    }
    $("#ch-note").textContent = `Ledger times: modelled at ${fmtInt(t.ledger.reference_rate_per_second)} permutations per second. ` +
      `The ${measured.candidate === WORKING_SET ? "working set" : measured.candidate} was also measured on a ${measured.device.replace(/^Ledger /, "")}: ` +
      `mean ${fmtFixed(measured.sign_s.mean, 2)} s, longest ${fmtFixed(measured.sign_s.max, 2)} s of ${measured.signatures} signatures, ` +
      `${fmtInt(measured.rate_in_signing_per_second)} permutations per second; key generation ${fmtFixed(measured.keygen_s, 1)} s.`;
    renderPair();
  }

  // ------------------------------------------------------ compare two sets
  const pairSelects = [$("#ch-pair-a"), $("#ch-pair-b")];
  pairSelects.forEach((select, index) => {
    for (const entry of sets) select.append(el("option", { value: entry.name }, entry.name + (entry.label ? ` (${entry.label})` : "")));
    select.value = state.pair[index];
    select.addEventListener("change", () => { state.pair[index] = select.value; renderPair(); });
  });

  function pairRows(a, b) {
    const figure = (key) => (entry) => entry.figures[key];
    const rows = [
      ["bytes", "Signature size, bytes", (e) => e.sig_bytes, 0, "counted"],
      ["perms", "Verify permutations", (e) => e.perms, 0, "counted"],
      ["life", "Lifetime, log2 of signatures", figure("life_full_log2_cover"), 2, "modelled"],
      ["sign_mean", "Ledger signing, mean, s", figure("sign_s_mean_5800"), 2, "modelled"],
      ["sign_p99", "Ledger signing, p99, s", figure("sign_s_p99_5800"), 2, "modelled"],
      ["sign_mean_measured", `Ledger signing measured, mean, s`, (e) => (e.name === measured.candidate ? measured.sign_s.mean : null), 2, "measured"],
      ["sign_max_measured", `Ledger signing measured, longest of ${measured.signatures}, s`, (e) => (e.name === measured.candidate ? measured.sign_s.max : null), 2, "measured"],
      ["keygen", "Key generation, s", figure("keygen_s_5800"), 1, "modelled"],
      ["keygen_measured", "Key generation measured, s", (e) => (e.name === measured.candidate ? measured.keygen_s : null), 1, "measured"],
      ["evm_gas", "EVM verify gas, mean", (e) => (e.evm_verify_gas ? e.evm_verify_gas.mean : null), 0, "measured"],
      ["evm_code", "EVM verifier code, bytes", (e) => (e.evm_verify_gas ? e.evm_verify_gas.code_bytes : null), 0, "measured"],
      ["cycles", "leanVM cycles per signature", (e) => (e.leanvm ? e.leanvm.cycles_per_sig : null), 0, "counted"],
      ["admit2", "Two-signature admission margin in 8141 full, gas",
        (e) => { const used = t.admission.A[e.name].rule6_gas_k2; return isNumber(used) ? t.max_verify_gas - used : null; }, 0, "modelled"],
      ["digits", "Digit width, bits", (e) => t.constraints[e.name].digit_bits, 0, "counted"],
    ];
    for (const step of path()) {
      const cell = t.cells[step.option][String(step.gas)], code = STEP_NAME[step.key][1], id = optionName(step.option).code;
      rows.push([`gas_${step.key}`, `${code} · ${id}: gas per transaction`, (e) => cell.sets[e.name].tx_gas, 0, "modelled"]);
      rows.push([`tps_${step.key}`, `${code} · ${id}: ${t.options[step.option].unit} at ${fmtGas(step.gas)}`,
        (e) => (t.admission[step.option][e.name].k1 === false ? null : cell.sets[e.name].tps), 2, cell.evidence === "simulated" ? "simulated" : "modelled"]);
    }
    return rows.map(([key, label, get, digits, evidence]) => ({ key, label, digits, evidence, a: get(a), b: get(b) }));
  }

  function renderPair() {
    const [a, b] = state.pair.map((name) => setByName.get(name));
    const table = clear($("#ch-pair-table"));
    table.dataset.a = a.name;
    table.dataset.b = b.name;
    table.append(el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Attribute"),
      el("th", { scope: "col", class: "n" }, a.name), el("th", { scope: "col", class: "n" }, b.name),
      el("th", { scope: "col", class: "n" }, `${b.name} minus ${a.name}`))));
    const body = el("tbody");
    const show = (value, digits) => (isNumber(value) ? fmtFixed(Math.abs(value), digits) : "none");
    for (const row of pairRows(a, b)) {
      const both = isNumber(row.a) && isNumber(row.b);
      const diff = both ? row.b - row.a : null;
      const cellOf = (value) => el("td", { class: "n", "data-value": JSON.stringify(value ?? null) },
        isNumber(value) ? signed(value < 0 ? -1 : 0, show(value, row.digits)) : el("span", { class: "dim" }, "none"));
      body.append(el("tr", { "data-key": row.key }, el("th", { scope: "row" }, row.label, chip(row.evidence)),
        cellOf(row.a), cellOf(row.b),
        el("td", { class: "n", "data-diff": JSON.stringify(diff) }, !both ? el("span", { class: "dim" }, "n/a")
          : Math.abs(diff) < 1e-12 ? "same" : [signed(diff, show(diff, row.digits)),
            row.a !== 0 ? el("span", { class: "sub" }, signed(diff / row.a, (Math.abs(diff / row.a) * 100).toFixed(1) + "%")) : null])));
    }
    const proof = (entry) => { const p = t.constraints[entry.name].proof;
      return [p.certificate, p.caveat, `EVM verifier ${p.evm_verifier}`, `leanVM rows ${p.leanvm_rows}`].filter(Boolean).join("; "); };
    body.append(el("tr", { "data-key": "proof" }, el("th", { scope: "row" }, "Proof status"),
      el("td", { class: "wrap" }, proof(a)), el("td", { class: "wrap" }, proof(b)),
      el("td", { class: "n" }, proof(a) === proof(b) ? "same" : "differs")));
    table.append(body);
  }

  // ------------------------------------------- all sets, all twelve scenarios
  const matrixGas = $("#ch-matrix-gas");
  for (const gas of t.gas_limits) matrixGas.append(el("option", { value: String(gas) }, fmtGas(gas)));
  matrixGas.value = String(state.matrixGas);
  matrixGas.addEventListener("change", () => { state.matrixGas = Number(matrixGas.value); renderMatrix(); });

  function renderMatrix() {
    const table = clear($("#ch-matrix-table"));
    table.dataset.gas = String(state.matrixGas);
    const columns = t.matrix.map((item) => {
      const cell = t.cells[item.option][String(state.matrixGas)], admit = t.admission[item.option];
      const routed = names.filter((name) => admit[name].k1 !== false);
      const best = Math.max(...routed.map((name) => cell.sets[name].tps));
      return { ...item, cell, admit, best, same: routed.every((name) => cell.sets[name].tps === best) };
    });
    const head = el("tr", {}, el("th", { scope: "col" }, "Set"));
    for (const column of columns) {
      head.append(el("th", { scope: "col", class: "n", title: optionName(column.option).title + ", " + pricingName(t.options[column.option].pricing) },
        column.id, el("span", { class: "sub" }, t.options[column.option].unit),
        chip(column.cell.evidence === "simulated" ? "simulated" : "modelled", column.cell.status)));
    }
    const body = el("tbody");
    for (const name of names) {
      const tr = el("tr", { class: name === WORKING_SET ? "pick" : null, "data-set": name }, setTitle(name));
      for (const column of columns) {
        const route = column.admit[name].k1 !== false, tps = column.cell.sets[name].tps;
        const loss = route ? (1 - tps / column.best) * 100 : null;
        const best = route && tps === column.best && !column.same;
        tr.append(el("td", { class: "n" + (best ? " best" : ""), "data-id": column.id, "data-tps": JSON.stringify(route ? tps : null),
          "data-loss": JSON.stringify(loss), "data-best": best ? "1" : "0" },
          route ? [fmtTps(tps), el("span", { class: "sub" }, best ? "best" : column.same ? "same" : MINUS + fmtLoss(loss))]
            : el("span", { class: "no", title: column.admit[name].basis || "" }, "no route")));
      }
      body.append(tr);
    }
    table.append(el("thead", {}, head), body);
  }

  // ------------------------------------------------------------- the rules
  $("#ch-rules-body").append(
    el("li", {}, `A step's figure is the campaign's 40-node cell where all ${names.length} sets were simulated at that gas limit; otherwise the gas bound of the modelled gas per transaction (gas limit / gas / ${t.slot_seconds} s), marked modelled. Where the signature is not on chain the gas bound is the same for every set.`),
    el("li", {}, `Loss is the shortfall against the best set of the step that has a public route. A set without one counts as ${fmtInt(t.penalty * 100)}% and is ranked after every set that has a route in all steps. Admission is the campaign's: the size limit of an ERC-4337 userOp, and the validation gas limit of a frame transaction (${fmtInt(t.max_verify_gas)} gas, signature bytes not counted).`),
    el("li", {}, `The verdict keeps the sets that meet the Ledger budget (modelled p99 at most ${fmtInt(t.ledger.budget.sign_p99_s)} s, at most 1 signature in ${fmtInt(1 / t.ledger.budget.sign_hard_share)} over ${fmtInt(t.ledger.budget.sign_hard_s)} s) and a lifetime of at least ${setLabel("2^" + t.lifetime_floor_log2)} signatures. The rank column ranks all sets on throughput alone.`),
    el("li", {}, `Ledger times of every set are modelled at the reference rate of ${fmtInt(t.ledger.reference_rate_per_second)} permutations per second. The device run of ${measured.candidate} (${measured.device}, ${measured.signatures} signatures at ${fmtInt(measured.state_count)} signatures of state) gave ${fmtInt(measured.rate_in_signing_per_second)} per second, ${fmtFixed((measured.rate_over_reference - 1) * 100, 1)}% above the reference; ${measured.signatures} samples cannot show the 1-in-${fmtInt(1 / t.ledger.budget.sign_hard_share)} tail, and the law rescaled to the measured rate (modelled from a measured rate) gives p99 ${fmtFixed(measured.extrapolated_sign_s.p99, 1)} s.`),
    el("li", {}, "Aggregation cells differ between sets by about one percent, which is inside the spread between seeds; read a small margin there as a tie."));

  renderSteps();
  renderResult();
  renderMatrix();
}
