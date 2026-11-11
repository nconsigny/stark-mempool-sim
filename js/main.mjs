// Entry point: loads the JSON data, starts the engine, switches the three
// views on the hash (#explore, #simulate, #about).

import { Engine, EngineCancelled } from "../engine/engine.mjs";
import { $, fetchJson } from "./util.mjs";
import { initExplore } from "./explore.mjs";
import { initSimulate } from "./simulate.mjs";
import { initAbout } from "./about.mjs";

const VIEWS = ["explore", "simulate", "about"];

function currentView() {
  const name = location.hash.replace(/^#/, "");
  return VIEWS.includes(name) ? name : "explore";
}

function showView() {
  const name = currentView();
  for (const view of document.querySelectorAll(".view")) {
    view.hidden = view.dataset.view !== name;
  }
  for (const link of document.querySelectorAll(".tabs a")) {
    if (link.dataset.view === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.documentElement.dataset.view = name;
}

// ---------------------------------------------------------------- the engine
// One engine for the page. `status` is what the header line and the views
// show: loading, ready, running, failed.
const listeners = new Set();
const engineState = { state: "loading", message: "", boot: null, error: null };

function setEngineState(state, message, extra = {}) {
  Object.assign(engineState, { state, message }, extra);
  const line = $("#engine-status");
  line.dataset.state = state;
  line.textContent = "Simulator: " + message;
  for (const listener of listeners) listener(engineState);
}

const STAGES = {
  pyodide: "Python runtime loaded, unpacking the simulator…",
  unpacked: "simulator unpacked, importing…",
  ready: "starting…",
};

const engine = new Engine({
  onBoot: (event) => {
    if (engineState.state === "loading" && STAGES[event.stage]) {
      setEngineState("loading", STAGES[event.stage]);
    }
  },
});

// Starts (or restarts) the worker. Never rejects: a failure becomes the
// "failed" state with a readable message.
function bootEngine() {
  setEngineState("loading", "loading the Python runtime (14 MB on a first visit)…", { error: null });
  return engine.start().then((boot) => {
    const commit = String(boot.info.simulator_commit).slice(0, 7);
    setEngineState("ready", `ready (Pyodide ${boot.pyodide_version}, simulator ${commit})`, { boot });
    return boot;
  }, (error) => {
    if (error instanceof EngineCancelled) return null;
    const text = error && error.message ? error.message : String(error);
    setEngineState("failed", "failed to start. " + text.replace(/\.?$/, ".") +
      " The engine/ folder must be served over HTTP with all its files, the .wasm file included.", { error });
    return null;
  });
}

const app = {
  engine,
  engineState,
  onEngineState(listener) { listeners.add(listener); listener(engineState); },
  setEngineState,
  bootEngine,
  // Stops the worker and starts a new one (cancel, or retry after a failure).
  restartEngine() {
    engine.cancel();
    return bootEngine();
  },
};
window.lsApp = app;   // for the console and the tests

async function main() {
  showView();
  window.addEventListener("hashchange", showView);
  bootEngine();
  let data;
  try {
    const [scenarios, candidates, noagg, agg, summary, build] = await Promise.all([
      fetchJson("data/scenarios.json"), fetchJson("data/candidates.json"),
      fetchJson("data/table_noagg.json"), fetchJson("data/table_agg.json"),
      fetchJson("data/summary.json"), fetchJson("build.json")]);
    data = { scenarios, candidates, noagg, agg, summary, build };
  } catch (error) {
    const fatal = $("#fatal");
    fatal.hidden = false;
    fatal.textContent = "The site's data files could not be loaded (" + error.message +
      "). The page must be served over HTTP from the folder that holds data/ and engine/.";
    return;
  }
  app.data = data;
  initExplore(app);
  initSimulate(app);
  initAbout(app);
  document.documentElement.dataset.ready = "1";
}

main();
