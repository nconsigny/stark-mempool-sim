// Small helpers shared by the three views: DOM building, number formats,
// evidence chips.

export const $ = (selector, root = document) => root.querySelector(selector);

// el("td", {class: "n", "data-x": 1}, "text", node, ...)
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

const MINUS = "−";
const isNumber = (value) => typeof value === "number" && Number.isFinite(value);

// 4,420
export function fmtInt(value) {
  if (!isNumber(value)) return "n/a";
  return Math.round(value).toLocaleString("en-US");
}

// Throughput: two decimals below 100, one above (as the report writes it).
export function fmtTps(value) {
  if (!isNumber(value) || value < 0) return "n/a";
  const digits = value >= 100 ? 1 : 2;
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtFixed(value, digits) {
  if (!isNumber(value) || value < 0) return "n/a";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// A change in percent: "−8.5%", "0", "+8.1%".
export function fmtPct(value) {
  if (!isNumber(value)) return "n/a";
  if (Math.abs(value) < 0.005) return "0";
  const digits = Math.abs(value) < 1 ? 2 : 1;
  const body = Math.abs(value).toFixed(digits) + "%";
  return (value < 0 ? MINUS : "+") + body;
}

// A share between 0 and 1 as a percentage: "77.0%".
export function fmtShare(value) {
  if (!isNumber(value) || value < 0) return "n/a";
  return (value * 100).toFixed(1) + "%";
}

// 60000000 -> "60 M"
export function fmtGas(value) {
  if (!isNumber(value)) return "n/a";
  if (value === 0) return "no limit";
  if (value % 1e6 === 0) return (value / 1e6).toLocaleString("en-US") + " M";
  return value.toLocaleString("en-US");
}

export function fmtBytesMB(value) {
  if (!isNumber(value) || value < 0) return "n/a";
  return (value / 1e6).toFixed(2) + " MB";
}

// Evidence chips, in the form the one-page summary uses: solid = measured,
// outlined accent = counted, outlined neutral = simulated, dashed = modelled.
const CHIPS = {
  measured: ["m", "Measured: gas from Forge tests on a real verifier"],
  counted: ["c", "Counted: exact leanVM row counts from real runs"],
  simulated: ["", "Simulated: a count from the network simulator, on modelled gas, prover prices and uplinks"],
  modelled: ["o", "Modelled: derived from EIP text or from a timing or prover model"],
};

export function chip(kind, title) {
  const [cls, fallback] = CHIPS[kind];
  return el("span", { class: ("ev " + cls).trim(), title: title || fallback }, kind);
}

// "2^24" -> 2<sup>24</sup>
export function withExponent(text) {
  const match = /^(.*?)2\^([\d.]+)(.*)$/.exec(String(text));
  if (!match) return document.createTextNode(String(text));
  const span = el("span", {}, match[1] + "2", el("sup", {}, match[2]), match[3]);
  return span;
}

// "Lifetime 2^22" -> "Lifetime 2²²", for labels in plain-text places.
const SUPERSCRIPT = { 0: "\u2070", 1: "\u00b9", 2: "\u00b2", 3: "\u00b3", 4: "\u2074", 5: "\u2075", 6: "\u2076", 7: "\u2077", 8: "\u2078", 9: "\u2079" };
export function setLabel(text) {
  return String(text).replace(/2\^(\d+)/g, (_all, digits) => "2" + [...digits].map((d) => SUPERSCRIPT[d]).join(""));
}

// Campaign prose with its own bracket tags ([C], [Mo], [M ...]): the tags
// are kept as written and set in the data face.
export function taggedProse(text) {
  const out = document.createDocumentFragment();
  const parts = String(text).split(/(\[(?:C|Mo|M|E|R|inferred|not simulated)[^\]]*\])/);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("[") && part.endsWith("]")) out.append(el("span", { class: "rawtag" }, part));
    else out.append(part);
  }
  return out;
}

export const CLASS_NAMES = {
  noagg: "no aggregation",
  agg8288: "EIP-8288 aggregation",
  pcs: "signature inside a user-side proof (leanPCS claims)",
};

// The throughput a class is reported in: transactions per second with one
// signature each where nothing is aggregated, signatures per second otherwise.
export function unitOf(cls) {
  return cls === "noagg" ? { key: "tps_tx", unit: "tx/s" } : { key: "tps_sig", unit: "sig/s" };
}

// Plain names for the campaign's pricing codes (the codes stay in the data).
const PRICING_NAMES = {
  "7623": "calldata as today (EIP-7623)",
  "7976": "calldata after Glamsterdam (EIP-7976)",
  "8141+7976": "frame transaction, Glamsterdam calldata",
  "8288:3000+30000/dep": "EIP-8288: 33,000 gas per signature",
  "leanPCS-draft:3000/dep": "leanPCS draft: 3,000 gas per signature",
  "leanPCS-4905424d/7976": "user-side proof checked through leanPCS claims",
};
export function pricingName(code) {
  return PRICING_NAMES[code] || code;
}

export function scenarioLabel(entry) {
  return entry.name + (entry.route ? ": " + entry.route : "") + " (" + entry.id + ")";
}

export async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}
