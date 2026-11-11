// The About view: the parts that come from the build record and from the
// limits in data/scenarios.json. The prose is in index.html.

import { $, el, clear, fmtGas, fmtInt } from "./util.mjs";

export function initAbout(app) {
  const { data } = app;
  const build = data.build;
  const scenarios = data.scenarios;

  // Browser limits, one row per scenario id (and per non-default
  // configuration whose limits differ from its id's).
  $("#ab-limits-basis").textContent = scenarios.limits_basis;
  const body = clear($("#ab-limits tbody"));
  for (const entry of scenarios.ids) {
    // The id's own row, then one row per set of configurations whose
    // limits differ from the id's.
    const key = (limits, exploreOnly) => JSON.stringify([limits.min_nodes, limits.max_nodes,
      limits.max_measured_slots, limits.max_gas_limit, limits.est_s, Boolean(exploreOnly)]);
    const groups = new Map([[key(entry.limits, entry.explore_only),
      { label: entry.id + " \u00b7 " + entry.name, names: [], limits: entry.limits, exploreOnly: entry.explore_only }]]);
    for (const name of entry.configurations) {
      const config = scenarios.configurations.find((item) => item.configuration === name);
      if (!config || name === entry.default_configuration) continue;
      const k = key(config.limits, config.explore_only);
      if (!groups.has(k)) groups.set(k, { label: null, names: [], limits: config.limits, exploreOnly: config.explore_only });
      groups.get(k).names.push(name);
    }
    for (const group of groups.values()) {
      const { limits } = group;
      const head = el("th", { scope: "row" }, group.label || group.names.join(", "),
        group.label && group.names.length ? el("span", { class: "sub" }, "also " + group.names.join(", ")) : null);
      if (group.exploreOnly) {
        body.append(el("tr", {}, head,
          el("td", { colspan: "4" }, "Tables only, not run in the browser: " + limits.reason + ".")));
        continue;
      }
      body.append(el("tr", {}, head,
        el("td", { class: "n" }, `${limits.min_nodes} to ${limits.max_nodes}`),
        el("td", { class: "n" }, `1 to ${limits.max_measured_slots}`),
        el("td", { class: "n" }, "up to " + fmtGas(limits.max_gas_limit)),
        el("td", { class: "n" }, `about ${limits.est_s} s`)));
    }
  }
  $("#ab-limits-note").textContent =
    `"Heaviest run" is the time of the heaviest allowed run in headless Chromium on the build machine. ` +
    `The engine stops any run after ${scenarios.time_limit_s} s. The campaign's tables are ${scenarios.table_nodes}-node runs, ` +
    `so a browser run of every scenario but F3 is a smaller network than the table cell beside it. ` +
    `No other machine, phone or browser was timed.`;

  // The build record.
  const record = clear($("#ab-build tbody"));
  const add = (label, ...value) => record.append(el("tr", {}, el("th", { scope: "row" }, label), el("td", {}, ...value)));
  const sim = build.simulator;
  add("Simulator commit", el("code", { id: "ab-commit" }, sim.commit), ` on branch ${sim.branch}`);
  add("Commit date", sim.commit_date);
  add("Commit subject", sim.subject);
  add("Pyodide", el("span", { id: "ab-pyodide" }, build.pyodide.version), ` (Python ${build.pyodide.python})`);
  add("Simulator archive", `${build.bundle.files} files, ${fmtInt(build.bundle.unpacked_bytes)} bytes unpacked`);
  add("Site size at build", `${(build.site_bytes / 1e6).toFixed(1)} MB, of which the WebAssembly runtime is the largest part`);
  const runtime = el("td", { id: "ab-runtime" }, "not started yet");
  record.append(el("tr", {}, el("th", { scope: "row" }, "Runtime in this browser"), runtime));
  app.onEngineState((state) => {
    if (state.state === "ready" && state.boot) {
      const info = state.boot.info;
      runtime.textContent = `Python ${info.python} on ${info.platform}, ready ${(state.boot.ready_ms / 1000).toFixed(1)} s after the worker started; ` +
        `simulator ${String(info.simulator_commit).slice(0, 12)}, network file ${info.default_network}`;
    } else if (state.state === "failed") {
      runtime.textContent = "failed to start: " + state.message;
    }
  });

  $("#foot-build").textContent = `This build: simulator commit ${sim.commit.slice(0, 12)} (${sim.commit_date.slice(0, 10)}), Pyodide ${build.pyodide.version}.`;
}
