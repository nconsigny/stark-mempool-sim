// Helpers shared by the views: DOM building, number formats, evidence chips.

export const $ = (selector, root = document) => root.querySelector(selector);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const MINUS = "−";

// Significant-digit format used for every figure of the site (the UI test
// recomputes it from the JSON with the same function).
export function fmt(value, digits = 3) {
  if (!isNum(value)) return "n/a";
  const abs = Math.abs(value);
  let text;
  if (abs >= 1000) text = Math.round(value).toLocaleString("en-US");
  else if (abs === 0) text = "0";
  else {
    const d = Math.max(0, digits - 1 - Math.floor(Math.log10(abs)));
    text = value.toFixed(Math.min(d, 4));
  }
  return text.replace("-", MINUS);
}

export function fmtSigned(value, digits = 2) {
  if (!isNum(value)) return "n/a";
  return (value > 0 ? "+" : "") + fmt(value, digits);
}

export function fmtShare(value) {
  if (!isNum(value)) return "n/a";
  return (value * 100).toFixed(1) + "%";
}

export function fmtGas(value) {
  if (!isNum(value)) return "n/a";
  if (value === 0) return "no limit";
  return (value / 1e6).toLocaleString("en-US") + " M";
}

// Evidence chips. A status string from the data files (E, R, M, C, Mo, A,
// PR, E-draft, Rc, with or without brackets, possibly compound) maps to the
// chip of its first tag; the full string is the chip's title.
const KINDS = {
  measured: ["m", "Measured on a real network, chain or device"],
  counted: ["c", "Counted: exact counts from real runs"],
  spec: ["s", "Read in a specification, EIP or pull request"],
  read: ["s", "Read in code or a cited source"],
  simulated: ["", "Simulated: a count from the v3 network simulator, on the modelled and measured inputs listed in About"],
  modelled: ["o", "Modelled: a formula or model, not a measurement"],
  assumed: ["o a", "Assumed: a default with no source; varied as a sensitivity"],
  recalled: ["o a", "Recalled, not checked"],
  derived: ["o", "Derived: computed from simulated values under a stated assumption"],
  unsourced: ["o a", "No status in the data file"],
  fixture: ["fx", "FIXTURE: a placeholder made from the row schema, not a simulation result"],
};

// Every tag in the string counts and the WEAKEST one picks the chip (a
// compound status such as "M (V) / Mo (module ...)" or "E-draft / A" is only
// as strong as its weakest part); the full string is in the chip's title.
const TAGS = [  // [regexp on a token, kind], token = a run of letters, '-' and '/'
  [/^(M|M-ext)$/, "measured"], [/^C$/, "counted"], [/^(E|E-draft|PR)$/, "spec"],
  [/^R$/, "read"], [/^Mo$/, "modelled"], [/^A$/, "assumed"], [/^Rc$/, "recalled"],
  [/^conj$/, "assumed"],
];
const STRENGTH = ["measured", "counted", "spec", "read", "modelled", "assumed", "recalled"];

export function statusKind(status) {
  const text = String(status ?? "").trim();
  if (/^FIXTURE/i.test(text)) return "fixture";
  const kinds = [];
  for (const token of text.replace(/[\[\](),;+:]/g, " ").split(/[\s/]+/)) {
    for (const [re, kind] of TAGS) if (re.test(token)) kinds.push(kind);
  }
  if (!kinds.length) return "unsourced";
  return kinds.reduce((a, b) => (STRENGTH.indexOf(b) > STRENGTH.indexOf(a) ? b : a));
}

// `label` (optional) replaces the visible text, e.g. "simulated · 200 nodes · 3 seeds";
// data-chip always holds the kind.
export function chip(kind, title, label) {
  const [cls, fallback] = KINDS[kind] || KINDS.modelled;
  return el("span", { class: ("ev " + cls).trim(), title: title || fallback, "data-chip": kind }, label || kind);
}

// "200 nodes · 3 seeds" from a list of node counts and a seed count
export function runSize(nodes, seeds) {
  const n = Array.isArray(nodes) ? nodes.join("/") : nodes;
  return `${n ?? "?"} nodes · ${seeds} seed${seeds === 1 ? "" : "s"}`;
}

// The interval text of a {mean, lo, hi, n, min, max} summary.
export function intervalText(ci, digits = 3) {
  if (!ci) return null;
  if (ci.n === 1) return "1 seed: no interval";
  if (ci.n === 2) return `2 seeds: ${fmt(ci.min, digits)} to ${fmt(ci.max, digits)} (a t-interval on 2 seeds is too wide to show)`;
  return `95 % CI ${fmt(ci.lo, digits)} to ${fmt(ci.hi, digits)}`;
}

export function statusChip(status, source) {
  const kind = statusKind(status);
  const title = `${KINDS[kind][1]}. Status: ${status ?? "none"}${source ? ". Source: " + source : ""}`;
  return chip(kind, title);
}

export async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

// A segmented control: options [{value, label, title}], onChange(value).
export function segmented(name, options, current, onChange) {
  const box = el("div", { class: "seg", role: "group", "aria-label": name });
  for (const opt of options) {
    const button = el("button", {
      type: "button", "aria-pressed": String(opt.value === current),
      "data-value": String(opt.value), title: opt.title || null,
      onclick: () => onChange(opt.value),
    }, opt.label);
    box.append(button);
  }
  return box;
}

export function control(label, node) {
  return el("div", { class: "ctl" }, el("span", { class: "lbl" }, label), node);
}

export function details(summary, ...children) {
  return el("details", {}, el("summary", {}, summary), ...children);
}
