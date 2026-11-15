// A replay of one browser run: nodes by region on a ring, builders in the
// middle, the CL mesh with each edge's width from the bytes of block and
// payload copies it carried (per-link load), the latest block or payload
// spreading (a node fills when it holds it), and a ring per node for the
// share of sampled transactions it has admitted. Everything is read from
// the run's trace (lsweb3.trace_of); nothing is invented. Optional and
// light: a canvas, no library, paused under prefers-reduced-motion.

import { el, fmt } from "./util.mjs";

const SPEEDS = [1, 4, 16];

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function layout(trace) {
  const regions = trace.regions;
  const sim = [];
  const builders = [];
  trace.nodes.forEach((node, i) => (node.builder ? builders : sim).push(i));
  sim.sort((a, b) => regions.indexOf(trace.nodes[a].region) - regions.indexOf(trace.nodes[b].region) || a - b);
  const pos = new Array(trace.n);
  sim.forEach((node, k) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * k) / sim.length;
    pos[node] = [0.5 + 0.34 * Math.cos(angle), 0.5 + 0.34 * Math.sin(angle), angle];
  });
  builders.forEach((node, k) => {
    const angle = (2 * Math.PI * k) / Math.max(1, builders.length);
    pos[node] = [0.5 + 0.08 * Math.cos(angle), 0.5 + 0.08 * Math.sin(angle), angle];
  });
  // region labels at the mean angle of their nodes
  const labels = [];
  for (const region of regions) {
    const members = sim.filter((n) => trace.nodes[n].region === region);
    if (!members.length) continue;
    const angle = members.reduce((s, n) => s + pos[n][2], 0) / members.length;
    labels.push([region, angle, members.length]);
  }
  return { pos, labels };
}

export function animation(trace) {
  const canvas = el("canvas", { class: "anim", width: 640, height: 640, role: "img",
    "aria-label": `Network replay: ${trace.n_simulated} nodes and ${trace.n - trace.n_simulated} builders` });
  const time = el("input", { type: "range", min: 0, max: Math.round(trace.end_ms), step: 10, value: 0,
    "aria-label": "Simulated time" });
  const label = el("span", { class: "mono small" });
  const play = el("button", { type: "button" }, "Play");
  const speed = el("select", { "aria-label": "Replay speed" },
    SPEEDS.map((s) => el("option", { value: s }, `${s}x`)));
  const info = el("p", { class: "small" });
  const { pos, labels } = layout(trace);
  const maxEdge = Math.max(1, ...trace.cl_edge_bytes.map((e) => e[2]));
  const busy = trace.per_node["links.busy_ms_up"] || [];
  const end = Math.max(1, trace.end_ms);
  const messages = trace.messages;
  let playing = false;
  let last = 0;

  function draw() {
    const t = Number(time.value);
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const ink = css("--ink"), muted = css("--muted"), accent = css("--accent"),
      rule = css("--rule"), warn = css("--warn"), surface = css("--surface");
    ctx.fillStyle = surface; ctx.fillRect(0, 0, W, H);
    // edges: CL mesh, width by carried bytes
    for (const [u, v, bytes] of trace.cl_edge_bytes) {
      const a = pos[u], b = pos[v];
      ctx.strokeStyle = muted; ctx.globalAlpha = 0.15 + 0.55 * (bytes / maxEdge);
      ctx.lineWidth = 0.5 + 3.5 * (bytes / maxEdge);
      ctx.beginPath(); ctx.moveTo(a[0] * W, a[1] * H); ctx.lineTo(b[0] * W, b[1] * H); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // the latest block/payload published by t
    let msg = null;
    for (const m of messages) if (m.t_pub <= t) msg = m;
    const slot = Math.floor(t / trace.slot_ms);
    const txs = trace.txs.filter((x) => x.born !== null && x.born <= t);
    let holders = 0;
    trace.nodes.forEach((node, i) => {
      const [x, y] = pos[i];
      const r = 4 + 3 * Math.sqrt(Math.min(node.up_mbps, 10000) / 1000);
      const has = msg && msg.first[i] >= 0 && msg.first[i] <= t;
      if (has) holders += 1;
      ctx.beginPath(); ctx.arc(x * W, y * H, r, 0, 2 * Math.PI);
      ctx.fillStyle = has ? (msg.topic === "beacon_block" ? accent : warn) : rule;
      ctx.fill();
      ctx.lineWidth = 1 + 3 * Math.min(1, (busy[i] || 0) / end);
      ctx.strokeStyle = node.home ? muted : ink;
      ctx.stroke();
      if (txs.length) {
        const got = txs.filter((x) => x.first[i] >= 0 && x.first[i] <= t).length / txs.length;
        ctx.beginPath(); ctx.arc(x * W, y * H, r + 4, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * got);
        ctx.strokeStyle = accent; ctx.lineWidth = 2; ctx.stroke();
      }
      if (node.builder) {
        ctx.fillStyle = ink; ctx.font = "10px sans-serif"; ctx.fillText("B", x * W - 3, y * H + 3);
      }
    });
    ctx.fillStyle = muted; ctx.font = "12px sans-serif";
    for (const [region, angle, count] of labels) {
      const x = 0.5 + 0.41 * Math.cos(angle), y = 0.5 + 0.43 * Math.sin(angle);
      const text = `${region} (${count})`;
      const width = ctx.measureText(text).width;
      // outward of the ring, kept inside the canvas
      let left = x < 0.45 ? x * W - width : x > 0.55 ? x * W : x * W - width / 2;
      left = Math.min(Math.max(left, 4), W - 4 - width);
      ctx.textAlign = "left";
      ctx.fillText(text, left, y * H + 4);
    }
    ctx.textAlign = "left";
    const canonical = trace.slots.find((s) => s.slot === slot);
    label.textContent = `t = ${(t / 1000).toFixed(2)} s, slot ${slot}`;
    info.textContent = msg
      ? `${msg.topic === "beacon_block" ? "Block" : "Payload"} ${msg.id} (${fmt(msg.size)} B) published at ${(msg.t_pub / 1000).toFixed(2)} s by node ${msg.origin}: held by ${holders} of ${trace.n} nodes.`
        + (canonical ? ` Slot ${slot}: ${canonical.canonical ? "canonical" : (canonical.failure || "not canonical")}, ${canonical.txs ?? 0} txs.` : "")
        + ` Rings: share of ${txs.length} sampled txs admitted.`
      : "Before the first block.";
  }

  function frame(now) {
    if (!playing) return;
    const dt = last ? now - last : 0;
    last = now;
    const next = Number(time.value) + dt * Number(speed.value);
    time.value = String(next >= end ? 0 : next);
    draw();
    requestAnimationFrame(frame);
  }

  play.addEventListener("click", () => {
    playing = !playing;
    play.textContent = playing ? "Pause" : "Play";
    last = 0;
    if (playing) requestAnimationFrame(frame);
  });
  time.addEventListener("input", draw);
  const legend = el("p", { class: "small" },
    el("span", { class: "swatch", style: `background:${css("--accent")}` }), "holds the block ",
    el("span", { class: "swatch", style: `background:${css("--warn")}` }), "holds the payload ",
    el("span", { class: "swatch", style: `background:${css("--rule")}` }), "not yet. ",
    "Node size: uplink rate; outline width: share of the run its uplink was busy; outline colour: dark = datacentre, grey = home; B: builder. ",
    "Outer arc: share of the sampled transactions born so far that the node has admitted. ",
    "Lines: node pairs that exchanged block or payload copies (mesh, flood publish or IWANT), width by message bytes, counted once per sender of a copy. Paused until you press Play.");
  const wrap = el("div", { class: "anim-wrap", "data-anim": "1" },
    canvas, el("div", { class: "anim-ctl" }, play, speed, time, label), info, legend,
    el("p", { class: "note" }, trace.note));
  const first = messages.find((m) => m.topic === "beacon_block");
  time.value = String(first ? Math.round(first.t_pub + 300) : 0);
  requestAnimationFrame(draw);
  return { node: wrap, draw, stop: () => { playing = false; } };
}
