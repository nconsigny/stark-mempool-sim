// "Past the gas limit" and "What was not run": data/beyond.json (the run
// campaign's raw rows grouped by tag; build.py beyond_json) and
// data/dropped.json (notes/phase4/runs.md section 8). Every figure from JSON.

import { $, el, clear, fmt, fmtSigned, fmtGas, chip, details, runSize, intervalText } from "./util.mjs";
import { resourceName, labelCells } from "./timeline.mjs";
import { caveatsWhere, caveatLine, allCaveats } from "./caveats.mjs";

const DEPLOYMENT_NAMES = { "8141": "8141 full", "8288": "8288 full", leanpcs: "leanPCS" };

function pointName(g) {
  const dep = DEPLOYMENT_NAMES[g.deployment] + (g.deployment === "leanpcs" && g.route ? ` ${g.route}` : "");
  const gas = g.gas_limit ? `${fmtGas(g.gas_limit)} gas` : "no gas limit";
  const bytes = g.byte_cap ? "" : ", no byte cap";
  const extra = [g.pool_lifted ? "pool lifted" : null, g.builder_p ? `${g.builder_p} builder provers` : null]
    .filter(Boolean).join(", ");
  return `${dep}, ${gas}${bytes}${extra ? ", " + extra : ""}`;
}

function simChip(nodes, seeds, extra = "") {
  return chip("simulated", `Simulated (saturated or fixed offer), ${runSize(nodes, seeds)}${extra}`,
    `sim · ${runSize(nodes, seeds)}`);
}

function card(g) {
  const counts = Object.entries(g.slot_binding_counts || {}).map(([k, n]) => `${resourceName(k)} ${n}`).join(", ");
  const labels = [...new Set(g.binding)].map(resourceName).join("; ");
  const box = el("div", { class: "box", "data-beyond": g.tag },
    el("h4", {}, pointName(g), " ", simChip(g.nodes, g.seeds.length,
      g.pool_lifted ? "; per-node pool capacity lifted by a driver hook, not a spec setting" : "")),
    el("p", {}, el("span", { class: "mono", "data-v": "pq_tps" }, fmt(g.pq_tps.mean)), " PQ tx/s included",
      el("span", { class: "small" }, ` of ${fmt(g.offered_tps)} offered; ${intervalText(g.pq_tps)}`)),
    el("p", { class: "small" }, el("strong", {}, "Binds: "), labels,
      counts ? ` (per slot: ${counts})` : ""),
    el("p", { class: "small" }, "Highest utilisations: ",
      g.util_top.map(([k, v]) => `${resourceName(k)} ${fmt(v, 2)}`).join(", ")),
    el("p", { class: "small" }, `Latency p50 ${fmt(g.latency_p50_s)} s (${fmt((g.censored_share ?? 0) * 100, 2)} % not included by the end); `
      + `home up/down ${fmt(g.home_up_mbps)} / ${fmt(g.home_down_mbps)} Mbit/s; payload reaches 90 % in ${fmt(g.payload_reach_t90_ms)} ms.`));
  return box;
}

function epsCard(e) {
  const size = runSize(e.nodes, e.eps_fair?.seeds ?? "?");
  const ch = chip("simulated", `Simulated: paired arms, Student-t 95 % interval; ${size}; ${e.note || ""}`, `sim · ${size}`);
  const where = e.pool_lifted ? `${DEPLOYMENT_NAMES[e.deployment]}, ${fmtGas(e.gas_limit)} gas, pool lifted`
    : `${DEPLOYMENT_NAMES[e.deployment]}, offered ${fmt(e.offered_pq_tps)} PQ tx/s`;
  const what = e.input === "sig_bytes" ? "signature bytes" : "permutations";
  return el("div", { class: "box", "data-beyond-eps": `${e.file}|${e.input}` },
    el("h4", {}, `${where}: ${what}`),
    el("p", { class: e.pre_fix ? "small caveat" : "small" }, e.pre_fix ? `Pre-fix run (${e.commit}), not rerun.` : `Simulator commit ${e.commit}.`),
    el("p", {}, el("span", { class: "mono", "data-v": "eps_fair" }, fmtSigned(e.eps_fair?.mean)), " ε", el("sub", {}, "fair"), " ", ch,
      el("span", { class: "small" }, ` [${fmt(e.eps_fair?.ci_lo, 2)}, ${fmt(e.eps_fair?.ci_hi, 2)}]`)),
    el("p", { class: "small" }, e.eps_supply && e.eps_supply.mean !== null && e.eps_supply.mean !== undefined
      ? `ε supply ${fmtSigned(e.eps_supply.mean)}; binding ${resourceName(e.resource)}`
      : `ε supply not defined (${e.resource ? "binding " + resourceName(e.resource) : "no single binding resource"}: ${e.resource_rule || "see the record"})`));
}

export function initBeyond(B, dropped) {
  const box = clear($("#tl-beyond"));
  if (!B) {
    box.append(el("p", { class: "small dim" }, "No past-the-gas-limit runs in this build."));
  } else {
    box.append(el("p", { class: "small" },
      "Saturated runs (more PQ demand than fits) at the headline point, then with the gas limit raised or removed, to see what binds next. At the gas-bound headline points throughput, binding and latency did not change with the node count (8141: 50 to 1,000 nodes, notes/phase4/runs.md section 1; 8288 and leanPCS: 50 to 200 nodes, notes/phase4/runs-fix.md section 1); that is not shown where bandwidth binds, and the rows past the gas limit ran at 50 to 200 nodes, so read each at the node count shown."));
    const head = B.groups.filter((g) => g.section === "headline");
    const past = B.groups.filter((g) => g.section === "beyond");
    const rowOf = (g) => el("tr", { "data-beyond-row": g.tag },
      el("th", { scope: "row" }, pointName(g)),
      el("td", { class: "n" }, el("span", { "data-v": "pq_tps" }, fmt(g.pq_tps.mean)), " PQ tx/s",
        el("span", { class: "sub" }, `of ${fmt(g.offered_tps)} offered, ${fmt(g.offered_arrived_tps)} arrived`)),
      el("td", {}, el("span", { "data-slot-binding": "1" }, Object.entries(g.slot_binding_counts || {})
        .map(([k, n]) => `${resourceName(k)} in ${n} of ${g.slots_window * g.seeds.length} slots`).join("; ")),
        el("span", { class: "sub" }, `row label: ${[...new Set(g.binding)].map(resourceName).join("; ")}`),
        g.blocks_orphaned ? el("span", { class: "sub" }, `${g.blocks_orphaned} orphaned block${g.blocks_orphaned === 1 ? "" : "s"}`) : null,
        ...caveatsWhere((a) => (a.beyond_tags || []).includes(g.tag)).map(caveatLine)),
      el("td", {}, simChip(g.nodes, g.seeds.length, g.pool_lifted ? "; pool lifted by a driver hook" : "")));
    box.append(el("div", { class: "scroll" }, el("table", { id: "beyond-table", class: "stackable" },
      el("thead", {}, el("tr", {}, el("th", {}, "saturated point"), el("th", { class: "n" }, "PQ tx/s"), el("th", {}, "what the builder hit, per slot"), el("th", {}, "runs"))),
      el("tbody", {}, labelCells(["", "PQ tx/s", "builder hit", "runs"], [...head, ...past].map(rowOf))))),
      el("p", { class: "note" }, "Per slot: the reason the builder's block stopped growing, counted over the measured slots. Row label: the simulator's utilisation reading for the whole run. Where 8288 and leanPCS stop at about the same rate with \"no more txs to add\", each node's pool (geth's default size) was full; the pool-lifted rows raise that size with a driver hook and show what binds next (notes/phase4/runs.md section 4)."));
    box.append(details("Each run: intervals, utilisations, latency, bandwidth",
      el("h3", {}, `Capacity at ${fmtGas(head[0]?.gas_limit)} gas`), el("div", { class: "eps" }, head.map(card)),
      el("h3", {}, "Past the gas limit"), el("div", { class: "eps" }, past.map(card))));
    const epsRow = (e) => el("tr", { "data-beyond-eps": `${e.file}|${e.input}` },
      el("th", { scope: "row" }, `${pointName({ deployment: e.deployment, gas_limit: e.gas_limit, byte_cap: e.byte_cap ?? 1, pool_lifted: e.pool_lifted, builder_p: e.builder_p })}, offered ${fmt(e.offered_pq_tps)} PQ tx/s: ${e.input === "sig_bytes" ? "signature bytes" : "permutations"}`,
        e.pre_fix ? el("span", { class: "sub caveat" }, "pre-fix run (f827ca7), not rerun") : null),
      el("td", { class: "n" }, el("span", { "data-v": "eps_fair" }, fmtSigned(e.eps_fair?.mean)),
        el("span", { class: "sub" }, `[${fmt(e.eps_fair?.ci_lo, 2)}, ${fmt(e.eps_fair?.ci_hi, 2)}]`)),
      el("td", {}, simChip(e.nodes, e.eps_fair?.seeds ?? "?", `; commit ${e.commit}; ${e.note || ""}`),
        el("span", { class: "sub" }, `commit ${e.commit}`),
        ...caveatsWhere((a) => a.beyond_eps_regime && a.beyond_eps_regime === e.regime).map(caveatLine)));
    const pastEps = B.elasticity.filter((e) => e.regime !== "network");
    const netEps = B.elasticity.filter((e) => e.regime === "network");
    const epsRows = pastEps.map(epsRow);
    box.append(el("h3", {}, "Elasticities past the gas limit"),
      el("div", { class: "scroll" }, el("table", { id: "beyond-eps", class: "stackable" },
        el("thead", {}, el("tr", {}, el("th", {}, "point: input"), el("th", { class: "n" }, "ε fair [95 % CI]"), el("th", {}, "runs"))),
        el("tbody", {}, labelCells(["", "ε fair [95 % CI]", "runs"], epsRows)))),
      details("ε supply and the binding resource of each", el("div", { class: "eps" }, pastEps.map(epsCard))));
    if (netEps.length) {
      box.append(el("h3", { id: "network-regime" }, "Network-bound regime (separate study)"),
        el("p", { class: "small" }, "Points where gas does not bind and the network is the candidate limit: 8288 with the pool lifted and more builder provers, 8141 with no gas limit and no byte cap. Run at f827ca7: the 8141 rows are row-identical with the fixed simulator (runs-fix.md section A.3); the 8288 rows are pre-fix. Kept apart from the figures above."),
        ...caveatsWhere((a) => a.beyond_eps_regime === "network").map((c) => caveatLine(c)),
        details("Network-regime elasticities",
          el("div", { class: "scroll" }, el("table", { id: "network-eps", class: "stackable" },
            el("thead", {}, el("tr", {}, el("th", {}, "point: input"), el("th", { class: "n" }, "ε fair [95 % CI]"), el("th", {}, "runs"))),
            el("tbody", {}, labelCells(["", "ε fair [95 % CI]", "runs"], netEps.map(epsRow))))),
          el("div", { class: "eps" }, netEps.map(epsCard))));
    }
    const attr = B.attribution.map((a) => el("tr", { "data-attr": a.file },
      el("th", { scope: "row" }, `${DEPLOYMENT_NAMES[a.deployment]}, offered ${fmt(a.offered_pq_tps)}`,
        a.pre_fix ? el("span", { class: "sub caveat" }, "pre-fix run (f827ca7), not rerun") : null),
      el("td", {}, a.sum_down < 0.1
        ? `none binds: the total response to every capacity is ${fmt(a.sum_down, 2)}, so the shares are noise`
        : Object.entries(a.shares).filter(([, v]) => v > 0.0005).map(([k, v]) => `${resourceName(k)} ${fmt(v, 3)}`).join(", ") || "none",
        el("span", { class: "sub" }, `total response ${fmt(a.sum_down, 2)}`)),
      el("td", {}, `${a.label} (${a.method})`),
      el("td", {}, simChip(a.nodes, a.seeds.length))));
    box.append(details("Shadow prices: share of each capacity",
      el("div", { class: "scroll" }, el("table", { class: "stackable" }, el("thead", {}, el("tr", {}, el("th", {}, "point"), el("th", {}, "shares"), el("th", {}, "simulator's label"), el("th", {}, "runs"))),
        el("tbody", {}, labelCells(["", "shares", "simulator's label", "runs"], attr)))),
      el("p", { class: "note" }, "Each capacity knob raised and lowered 10 % on paired runs; a share is that knob's throughput response over the sum. \"unresolved\": two seeds give intervals too wide for the label's strict test.")));
    const sens = B.groups.filter((g) => g.section === "sensitivity");
    box.append(details(`Sensitivities at ${fmtGas(sens[0]?.gas_limit)} gas`, el("div", { class: "eps" }, sens.map((g) => {
      const c = card(g);
      c.querySelector("h4").prepend(`${g.tag.split("|")[1]}: `);
      return c;
    })), el("p", { class: "note" }, "uplink_x0.5 halves every uplink class, not only home links (no per-class setting in the frozen simulator).")));
  }
  const corr = clear($("#tl-corrections"));
  const C = allCaveats();
  corr.append(el("p", { class: "small" }, `An independent verification of the run campaign (${C.source}) found a model defect and some readings to correct. Each figure it touches carries a correction line; the list:`),
    el("div", { class: "stack" }, C.items.map((item) => {
      const d = details(item.title, el("p", { class: "small" }, item.text), el("p", { class: "small dim" }, `Source: ${item.section}.`));
      d.dataset.caveat = item.id;
      return d;
    })));
  const drop = clear($("#tl-dropped"));
  drop.append(el("ul", { class: "plain" }, dropped.items.map((d) =>
    el("li", { "data-dropped": d.what }, `${d.what}: `, el("strong", {}, d.short)))),
    details("Why, and what stands in", el("ul", { class: "plain" }, dropped.items.map((d) =>
      el("li", {}, el("strong", {}, d.what), `: ${d.fate}.`, d.instead !== "none" ? ` Instead: ${d.instead}.` : "")))),
    el("p", { class: "note" }, `Source: ${dropped.source}. Single-seed figures say "1 seed: no interval" beside them.`));
}
