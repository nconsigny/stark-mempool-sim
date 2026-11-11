// The engine: a module Web Worker that runs bench.leansphincs in Pyodide.
// API: web/ENGINE.md. Nothing is fetched from outside this folder.
//
//   page -> worker   {id, method, args}
//   worker -> page   {type: "boot", stage, ms}            while starting
//                    {type: "ready", boot: {...}}         once, when usable
//                    {type: "progress", id, event}        during a run
//                    {type: "result", id, method, result, ms}
//                    {type: "error", id, method, error: {message, traceback}}
//
// Python runs synchronously inside the worker, so a running simulation
// cannot be interrupted by a message: to cancel, the page terminates the
// worker and starts a new one (engine.mjs does both).

import { loadPyodide } from "./pyodide/pyodide.mjs";

const BUNDLE_ROOT = "/leansphincs";
const BUNDLE = "leansphincs-bundle.zip";
const base = new URL("./", import.meta.url);

let lsweb = null;
let current = null;
const boot = { started: performance.now() };

function stage(name) {
  const ms = performance.now() - boot.started;
  boot[name + "_ms"] = Math.round(ms);
  postMessage({ type: "boot", stage: name, ms: Math.round(ms) });
}

async function start() {
  const archive = fetch(new URL(BUNDLE, base)).then((response) => {
    if (!response.ok) throw new Error(`${BUNDLE}: HTTP ${response.status}`);
    return response.arrayBuffer();
  });
  const pyodide = await loadPyodide({ indexURL: new URL("pyodide/", base).href });
  stage("pyodide");
  const buffer = await archive;
  boot.bundle_bytes = buffer.byteLength;
  pyodide.FS.mkdirTree(BUNDLE_ROOT);
  pyodide.unpackArchive(buffer, "zip", { extractDir: BUNDLE_ROOT });
  stage("unpacked");
  pyodide.runPython(
    `import sys\nsys.path.insert(0, "${BUNDLE_ROOT}/web")\nimport lsweb\nlsweb.setup()\n` +
    // Import the harness now, so the first call does not pay for it.
    "import bench.leansphincs.__main__\n");
  lsweb = pyodide.pyimport("lsweb");
  lsweb.set_progress((text) => {
    if (current !== null) {
      postMessage({ type: "progress", id: current, event: JSON.parse(text) });
    }
  });
  stage("ready");
  boot.info = JSON.parse(lsweb.call("info", "{}"));
  boot.pyodide_version = pyodide.version;
  const { started, ...report } = boot;
  postMessage({ type: "ready", boot: report });
}

const ready = start();
ready.catch((error) => {
  postMessage({ type: "error", id: null, method: "boot",
                error: { message: String(error && error.message || error),
                         traceback: String(error && error.stack || "") } });
});

self.onmessage = async (message) => {
  const { id, method, args } = message.data || {};
  try {
    await ready;
    const started = performance.now();
    if (method === "ping") {
      postMessage({ type: "result", id, method, result: { pong: true }, ms: 0 });
      return;
    }
    current = id;
    let text;
    try {
      text = lsweb.call(method, JSON.stringify(args || {}));
    } finally {
      current = null;
    }
    const result = JSON.parse(text);
    const ms = performance.now() - started;
    if (result && typeof result === "object" && result.timing) {
      result.timing.worker_ms = ms;
    }
    postMessage({ type: "result", id, method, result, ms });
  } catch (error) {
    const text = String(error && error.message || error);
    // A Python exception arrives as its traceback; the last line is the
    // message a page can show.
    const lines = text.trim().split("\n");
    postMessage({ type: "error", id, method,
                  error: { message: lines[lines.length - 1], traceback: text } });
  }
};
