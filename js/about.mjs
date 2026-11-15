// About: what is simulated, measured and assumed; every input leaf of the two
// parameter files with its status chip (data/sources.json); the limits; the
// command line; this build (build.json).

import { $, el, clear, fmt, chip, statusChip, statusKind, details } from "./util.mjs";

function legend() {
  return el("ul", { class: "legend" },
    ["measured", "counted", "spec", "read", "simulated", "derived", "modelled", "assumed", "fixture"].map((k) =>
      el("li", {}, chip(k), el("span", {}, {
        measured: "on a real network, chain or device",
        counted: "exact counts from real runs",
        spec: "EIP, spec or pull request text",
        read: "read in code or a cited source",
        simulated: "the v3 simulator's output",
        derived: "computed from simulated values under a stated assumption",
        modelled: "a formula or model",
        assumed: "a default with no source",
        fixture: "placeholder, not a result",
      }[k]))));
}

function statusCounts(leaves) {
  const counts = {};
  for (const leaf of leaves) {
    const k = statusKind(leaf.status);
    counts[k] = (counts[k] || 0) + 1;
  }
  return counts;
}

function sourcesTable(sources) {
  const filter = el("input", { type: "search", id: "ab-filter", placeholder: "filter, e.g. uplink or Mo",
    "aria-label": "Filter the inputs" });
  const tbody = el("tbody");
  const rows = [];
  for (const [file, entry] of Object.entries(sources.files)) {
    for (const leaf of entry.leaves) {
      const tr = el("tr", { "data-leaf": `${file}:${leaf.path}` },
        el("th", { scope: "row", style: "overflow-wrap:anywhere" }, el("span", { class: "small" }, file.replace(".json", "")), " ", leaf.path),
        el("td", { style: "overflow-wrap:anywhere" }, String(leaf.value), leaf.unit ? ` ${leaf.unit}` : "", " ", statusChip(leaf.status, leaf.source)),
        el("td", { class: "small", style: "overflow-wrap:anywhere" }, leaf.source || ""));
      tr.dataset.text = `${file} ${leaf.path} ${leaf.status} ${leaf.source}`.toLowerCase();
      rows.push(tr);
    }
  }
  const shown = el("p", { class: "small" });
  function apply() {
    const q = filter.value.trim().toLowerCase();
    clear(tbody);
    const hits = rows.filter((r) => !q || r.dataset.text.includes(q));
    hits.slice(0, 200).forEach((r) => tbody.append(r));
    shown.textContent = `${hits.length} of ${rows.length} inputs${hits.length > 200 ? ", first 200 shown" : ""}.`;
  }
  filter.addEventListener("input", apply);
  apply();
  return el("div", { class: "stack" }, filter, shown,
    el("div", { class: "scroll" }, el("table", { class: "mid", style: "table-layout:fixed" },
      el("thead", {}, el("tr", {}, el("th", { style: "width:34%" }, "input"), el("th", { style: "width:30%" }, "value"), el("th", {}, "source"))), tbody)));
}

export function initAbout({ timeline, sources, describe, build }) {
  const body = clear($("#ab-body"));
  body.append(el("p", { class: "prose" }, timeline.fixture
    ? "In this build the Timeline's figures are a fixture (placeholders, chipped fixture); Simulate's come from the v3 network simulator running here; inputs come from the two parameter files. Each figure carries a chip saying how it is known."
    : "Every figure on this site comes from the campaign's v3 network simulator or from its two parameter files; each carries a chip saying how it is known."),
    legend());
  const fileCounts = Object.entries(sources.files).map(([file, entry]) => {
    const c = statusCounts(entry.leaves);
    return el("li", {}, el("code", {}, file), `: ${entry.leaves.length} inputs; `,
      Object.entries(c).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${k}`).join(", "), ".");
  });
  body.append(details("What is simulated, measured and assumed",
    el("ul", { class: "plain" },
      el("li", {}, "Simulated: one row per run, from a discrete-event simulation of every node: EL transaction gossip (eth/68 push and announce/fetch, eth/73 wrappers), CL gossipsub v1.2 with IDONTWANT and data columns, uplink and downlink queues, the slot clock with ePBS deadlines and FOCIL, EIP-1559 fees, an ECDSA background, and for 8288 and leanPCS the recursive leanVM provers and the builder's final proof."),
      el("li", {}, "Measured or read: region latencies, node bandwidth classes, client constants, today's traffic and fee data, spec constants, gas measured on chain. Each input is listed below with its status."),
      el("li", {}, "Assumed or modelled: the timeline (dates, months, PQ adoption, demand growth), prover hardware at I*, and the inputs marked so. The PQ share and the gas limit are switches on the timeline because they are assumptions.")),
    el("ul", { class: "plain" }, fileCounts)));
  body.append(details("Every input with its source", sourcesTable(sources)));
  const lim = describe.limits;
  body.append(details("Limits of these figures",
    el("ul", { class: "plain" },
      el("li", {}, `A timeline step is sampled at ${timeline.points_per_step.value} months; each sample is a snapshot of a few slots, not a simulated month. A step's figure is a mean over seeds and a trapezoid mean over the sampled months.`),
      el("li", {}, "What binds comes from the row's attribution: capacity shadow prices when the runs include perturbations, else a utilisation reading, named beside it as its method."),
      el("li", {}, "Elasticities: ε fair is a paired central difference of simulated runs (common random numbers) with a Student-t interval over seeds; ε supply (the headline) = ε fair ÷ the PQ share of the binding resource, which assumes kind-blind fee rationing between PQ and ECDSA traffic. The chip on a figure names the weakest tag of its inputs' statuses."),
      el("li", {}, `Browser runs are small (${Object.entries(lim.deployments).map(([d, l]) => `${d}: ${l.min_nodes} to ${l.max_nodes} nodes`).join("; ")}) and can differ from the ${lim.cli_default_nodes}-node command-line runs.`),
      el("li", {}, "Nothing here was measured on a Ledger, a real 8288 mempool or a real leanPCS deployment; neither exists yet."),
      timeline.fixture ? el("li", {}, chip("fixture"), " This build's timeline figures are a fixture: placeholders with the row schema's shape, not simulation results.") : null)));
  const cmds = [
    `cd sims/stark-mempool-sim-v2   # ${build.simulator.kind === "ref" ? build.simulator.ref + " " + build.simulator.commit.slice(0, 9) : build.simulator.tree}`,
    "python3 -m bench.leansphincs run --deployment 8141 --candidate P99r --gas-limit 200e6 --nodes 200",
    "python3 -m bench.leansphincs run --deployment leanpcs --set agg.sig_route=pcs_client --set chain.dep_cap=256",
    "python3 -m bench.leansphincs timeline ../../data/phase4/timeline_default.json --branch 8288 --nodes 200 --seeds 0,1,2 --out tl.jsonl",
    "python3 -m bench.leansphincs elasticity --timeline ../../data/phase4/timeline_default.json --point 'I*:8288:2029-10' --input both --seeds 0,1,2",
    "python3 -m bench.leansphincs run --list-keys      # every --set key",
  ];
  body.append(details("Command line", el("pre", { class: "cmd" }, cmds.join("\n")),
    el("p", { class: "small" }, "The site's timeline records were written by these verbs (notes/phase4/runs.md gives every command and log).")));
  const sim = build.simulator || {};
  body.append(details("This build",
    el("dl", { class: "kv", id: "ab-build" },
      el("dt", {}, "simulator"), el("dd", {}, `${sim.kind} ${sim.ref || sim.tree || ""} @ ${sim.commit || ""}${sim.dirty && sim.dirty.length ? ` with ${sim.dirty.length} uncommitted files` : ""}`),
      el("dt", {}, "timeline runs"), el("dd", {}, `${build.runs_source.kind}: ${Object.keys(build.runs_source.files).length} files; simulator commits of their rows: ${(timeline.runs_source.code_commits || []).join(", ")}`),
      el("dt", {}, "parameter files"), el("dd", {}, Object.entries(build.data).map(([f, h]) => `${f} ${h.slice(0, 12)}`).join("; ")),
      el("dt", {}, "Pyodide"), el("dd", {}, build.pyodide),
      el("dt", {}, "size"), el("dd", {}, `${fmt(build.total_bytes / 1e6)} MB in ${Object.keys(build.files).length} files`),
      el("dt", {}, "privacy"), el("dd", {}, "No cookies, no requests to other sites; the theme choice is kept in this browser."))));
}
