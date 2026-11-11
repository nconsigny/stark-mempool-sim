// The page's side of the engine: a small promise client for worker.mjs.
// API: web/ENGINE.md.
//
//   import { Engine } from "./engine/engine.mjs";
//   const engine = new Engine();
//   const { scenarios } = await engine.scenarios();
//   const { phase2_row } = await engine.run({ scenario: "A", nodes: 12 },
//                                           { onProgress: console.log });
//   engine.cancel();   // terminates the worker; the next call starts a new one

export class EngineCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "EngineCancelled";
  }
}

export class EngineError extends Error {
  constructor(error, method) {
    super(error.message);
    this.name = "EngineError";
    this.method = method;
    this.traceback = error.traceback;
  }
}

export class Engine {
  // `onBoot({stage, ms})` is called while a worker starts.
  constructor({ workerUrl, onBoot } = {}) {
    this.workerUrl = workerUrl || new URL("./worker.mjs", import.meta.url);
    this.onBoot = onBoot || null;
    this.worker = null;
    this.pending = new Map();
    this.nextId = 1;
    this.boot = null;
    this.readyPromise = null;
  }

  // Starts the worker if there is none. Resolves with the boot report:
  // {pyodide_ms, unpacked_ms, ready_ms, bundle_bytes, pyodide_version, info}.
  start() {
    if (this.readyPromise) return this.readyPromise;
    const worker = new Worker(this.workerUrl, { type: "module" });
    this.worker = worker;
    this.readyPromise = new Promise((resolve, reject) => {
      this.rejectReady = reject;
      worker.onmessage = (message) => {
        const data = message.data;
        if (data.type === "boot") {
          if (this.onBoot) this.onBoot(data);
        } else if (data.type === "ready") {
          this.boot = data.boot;
          resolve(data.boot);
        } else if (data.type === "error" && data.id === null) {
          reject(new EngineError(data.error, data.method));
        } else {
          this.dispatch(data);
        }
      };
      worker.onerror = (event) => {
        reject(new Error(event.message || "the worker failed to load"));
      };
    });
    return this.readyPromise;
  }

  dispatch(data) {
    const entry = this.pending.get(data.id);
    if (!entry) return;
    if (data.type === "progress") {
      if (entry.onProgress) entry.onProgress(data.event);
      return;
    }
    this.pending.delete(data.id);
    if (data.type === "result") {
      entry.resolve(data.result);
    } else {
      entry.reject(new EngineError(data.error, data.method));
    }
  }

  // One API call. `args` is the message's `args`; the promise resolves with
  // the message's `result`.
  async call(method, args = {}, { onProgress } = {}) {
    await this.start();
    const id = this.nextId++;
    const worker = this.worker;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, onProgress });
      worker.postMessage({ id, method, args });
    });
  }

  info() { return this.call("info"); }
  scenarios() { return this.call("scenarios"); }
  candidates(options = {}) { return this.call("candidates", options); }
  checkLimits(config) { return this.call("check_limits", { config }); }
  evaluateModel(config) { return this.call("evaluate_model", { config }); }

  // Resolves with {row, phase2_row, row_text, phase2_text, argv, command,
  // timing}; `timing.wall_ms` is the time the page waited.
  async run(config, options = {}) {
    const started = performance.now();
    const result = await this.call("run", { config }, options);
    result.timing.wall_ms = performance.now() - started;
    return result;
  }

  // Terminates the worker. Every pending call rejects with EngineCancelled.
  // The next call starts a fresh worker (and pays the start-up again).
  cancel() {
    if (this.worker) this.worker.terminate();
    this.worker = null;
    this.readyPromise = null;
    this.boot = null;
    if (this.rejectReady) this.rejectReady(new EngineCancelled());
    const pending = [...this.pending.values()];
    this.pending.clear();
    for (const entry of pending) entry.reject(new EngineCancelled());
  }
}
