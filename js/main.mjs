// Data loading, hash routing, theme, the one Engine (started on first visit
// to Simulate, so the Timeline view never waits for 15 MB of runtime).

import { $, el, fetchJson } from "./util.mjs";
import { initTimeline } from "./timeline.mjs";
import { initSimulate } from "./simulate.mjs";
import { initAbout } from "./about.mjs";
import { initBeyond } from "./beyond.mjs";
import { setCaveats } from "./caveats.mjs";
import { Engine } from "../engine/engine.mjs";

const VIEWS = ["timeline", "simulate", "about"];
let engine = null;
let simulateReady = false;
let data = null;

function setEngineStatus(state, text) {
  const node = $("#engine-status");
  node.dataset.state = state;
  node.textContent = text || {
    idle: "Simulator: starts on Simulate", loading: "Simulator: loading…",
    ready: "Simulator: ready", running: "Simulator: running…", failed: "Simulator: failed to start",
  }[state];
}

function startEngine() {
  if (engine) return;
  engine = new Engine();
  setEngineStatus("loading");
  engine.start().then(() => setEngineStatus("ready"), (error) => {
    setEngineStatus("failed");
    $("#sim-status").textContent = `The simulator did not start: ${error.message}`;
    $("#sim-status").classList.add("error");
    $("#sim-run").disabled = false;
    $("#sim-run").textContent = "Try again";
    engine = null;
  });
}

function show(view) {
  if (!VIEWS.includes(view)) view = "timeline";
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll(".tabs a")) {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (view === "simulate" && data) {
    startEngine();
    if (!simulateReady) {
      simulateReady = true;
      initSimulate(data.describe, {
        checkLimits: (c) => { startEngine(); return engine.checkLimits(c); },
        run: (c, o) => { startEngine(); return engine.run(c, o); },
        cancel: () => { if (engine) engine.cancel(); },
        start: () => { startEngine(); return engine.start(); },
      }, (state) => setEngineStatus(state), { offered: data.timeline.candidates.map((c) => c.name),
        axes: data.timeline.axes, simulator: data.build.simulator,
        campaignNodes: [...new Set([...Object.values(data.timeline.groups).flatMap((g) => g.summary.nodes || []),
          ...(data.beyond ? data.beyond.groups.map((g) => g.nodes) : [])])].sort((a, b) => a - b) });
    }
  }
}

function theme() {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem("lsp3-theme"); } catch { /* storage blocked */ }
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  $("#theme-toggle").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("lsp3-theme", root.dataset.theme); } catch { /* ignore */ }
    document.dispatchEvent(new CustomEvent("theme-changed"));
  });
}

async function main() {
  theme();
  try {
    const [timeline, sources, describe, build] = await Promise.all([
      fetchJson("data/timeline.json"), fetchJson("data/sources.json"),
      fetchJson("data/describe.json"), fetchJson("build.json")]);
    const beyond = build.files["data/beyond.json"] ? await fetchJson("data/beyond.json") : null;
    const dropped = await fetchJson("data/dropped.json");
    setCaveats(await fetchJson("data/caveats.json"));
    data = { timeline, sources, describe, build, beyond, dropped };
    if (timeline.fixture) {
      const banner = $("#fixture-banner");
      banner.hidden = false;
      banner.append(el("strong", {}, "Fixture data. "),
        "The timeline figures are placeholders generated from the simulator's row schema, not simulation results; every one is chipped ",
        el("span", { class: "ev fx" }, "fixture"), ". The precomputed v3 runs replace them before any release.");
    }
    const capacity = build.files["data/capacity.json"] ? await fetchJson("data/capacity.json") : { cells: {} };
    data.capacity = capacity;
    initTimeline(timeline, capacity);
    initBeyond(beyond, dropped, data.capacity);
    initAbout(data);
    const sim = build.simulator || {};
    $("#build-line").textContent = `Simulator ${sim.kind === "ref" ? sim.ref : sim.tree} @ ${(sim.commit || "").slice(0, 9)}${sim.dirty && sim.dirty.length ? " + uncommitted" : ""} · timeline runs: ${build.runs_source.kind} · static site, nothing is sent anywhere.`;
  } catch (error) {
    const fatal = $("#fatal");
    fatal.hidden = false;
    fatal.textContent = `The page could not load its data: ${error.message}`;
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1));
  document.documentElement.dataset.loaded = "1";
}

main();
