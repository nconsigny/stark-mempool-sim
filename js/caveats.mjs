// Corrections from notes/phase4/verify-final.md (data/caveats.json): which
// apply where, and how they are drawn. The texts quote the verification.

import { el, details } from "./util.mjs";

let C = { items: [], source: "" };
export function setCaveats(data) { C = data || C; }
export function allCaveats() { return C; }

export function caveatsWhere(test) { return C.items.filter((c) => test(c.applies || {})); }

export function caveatBox(item, open = false) {
  const body = el("div", { class: "stack", style: "gap:4px" },
    el("p", { class: "small" }, item.text),
    el("p", { class: "small dim" }, `Source: ${item.section}.`));
  const box = el("div", { class: "card warn", "data-caveat": item.id },
    el("p", {}, el("strong", {}, "Correction: "), item.title));
  if (open) box.append(body); else box.append(details("What and how much", body));
  return box;
}

export function caveatLine(item) {
  return el("span", { class: "sub caveat", "data-caveat": item.id, title: `${item.text} (${item.section})` },
    `Correction: ${item.title}`);
}
