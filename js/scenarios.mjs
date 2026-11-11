// Plain explanations of the scenarios: the overview cards at the top of
// Explore, the reference point (a plain ECDSA transfer), the section
// "Reading the aggregation numbers", and the detail block of Simulate.
//
// The wording follows notes/phase3/scenario-details.md. Every figure that
// data/gas.json or the final tables hold is read from them here, at run
// time; only words and protocol constants are written out.

import { $, el, clear, chip, fmtInt, fmtTps, fmtGas } from "./util.mjs";

// ------------------------------------------------------------ the reference
// A plain transfer signed with ECDSA costs 21,000 gas (the transaction base);
// a slot is 12 seconds. Both are protocol constants.
export const ECDSA_TRANSFER_GAS = 21000;
export const SLOT_SECONDS = 12;
export const gasBound = (gasLimit, gasPerTx, signatures = 1) => gasLimit / gasPerTx / SLOT_SECONDS * signatures;
export const ecdsaBaseline = (gasLimit) => gasBound(gasLimit, ECDSA_TRANSFER_GAS);
export const WORKING_SET = "P99r";
export const TODAY_SET = "B0";
const CARD_GAS = 60000000;
const TABLE_ALIAS = { F2: "F1" };
const SIDE_RUN = /kill:|axis=|iso:|run@|mesh=/;

export function baselineSentence(gasLimit) {
  return `ECDSA reference at ${fmtGas(gasLimit)} gas: ${fmtTps(ecdsaBaseline(gasLimit))} tx/s (a gas bound for plain transfers).`;
}

// --------------------------------------------------- figures from the data
export function makeFigures(data) {
  const ids = data.scenarios.ids;
  const idById = new Map(ids.map((entry) => [entry.id, entry]));
  const configs = new Map(data.scenarios.configurations.map((entry) => [entry.configuration, entry]));
  const rows = [...data.noagg.rows, ...data.agg.rows];

  // One row of gas.json: one signature per transaction, or the scenario's
  // own count where it has no choice (the user-side-proof scenarios).
  function gasRow(configuration, candidate = WORKING_SET) {
    const cls = configs.get(configuration).class;
    return data.gas.rows.find((row) => row.configuration === configuration && row.candidate === candidate &&
      (row.k === 1 || cls === "pcs"));
  }

  // The campaign's 40-node cells for a scenario id at a pricing: baseline
  // profile, heterogeneous uplinks, the main runs only (no sensitivity run).
  function cells(id, { pricing, candidate = WORKING_SET, signatures = 1, gas = null, uplinks = "heterogeneous", mode = null } = {}) {
    const tableId = TABLE_ALIAS[id] || id;
    return rows.filter((row) => row.scenario === tableId && (pricing === undefined || row.pricing === pricing) &&
      row.candidate === candidate && row.nodes === data.scenarios.table_nodes && row.profile === "baseline" &&
      row.uplinks === uplinks && row.k === signatures && !SIDE_RUN.test(row.variant) &&
      (row.client_proofs === undefined || row.client_proofs === null || row.client_proofs === "draft") &&
      (gas === null || row.gas_limit === gas) && (mode === null || !row.mode || row.mode === mode))
      .sort((a, b) => a.gas_limit - b.gas_limit);
  }
  const cell = (id, options) => cells(id, options)[0] || null;
  const tps = (row) => (row ? (row.k > 1 ? row.tps_sig : row.tps_tx) : null);

  // The like-for-like aggregation figures (notes/phase3/aggregation-check.md).
  const agg = data.gas.aggregation;
  const gasRowK = (configuration, k, candidate = WORKING_SET) =>
    data.gas.rows.find((row) => row.configuration === configuration && row.candidate === candidate && row.k === k);
  // How many of the 40 nodes prove one raw signature in scenarios D and E:
  // the range over the tables' headline cells.
  function provedBy() {
    const dup = rows.filter((row) => (row.scenario === "D" || row.scenario === "E") && row.nodes === data.scenarios.table_nodes &&
      row.uplinks === "heterogeneous" && row.headline === 1 && !SIDE_RUN.test(row.variant) &&
      typeof row.dup_proofs_per_tx === "number").map((row) => row.dup_proofs_per_tx);
    return dup.length ? [Math.round(Math.min(...dup)), Math.round(Math.max(...dup))] : null;
  }
  // The prover-bound cells of the working set, in signatures per second.
  function proverBound() {
    const p = "8288:3000+30000/dep", deps = agg.deps;
    return [cell("D", { pricing: p, gas: 200000000 }), cell("E", { pricing: p, gas: 300000000 }),
            cell("D", { pricing: p, gas: 200000000, signatures: deps }),
            cell("E", { pricing: "leanPCS-draft:3000/dep", gas: CARD_GAS, signatures: deps })]
      .filter((row) => row && /prover/.test(row.binding)).map((row) => row.tps_sig).sort((a, b) => a - b);
  }
  // The rows of the same-unit table: gas per signature, the gas bound in
  // signatures per second at 60 M, and the simulated cell where there is one.
  function likeRows() {
    const p8288 = "8288:3000+30000/dep", pPcs = "leanPCS-draft:3000/dep", deps = agg.deps;
    const f3 = gasRow("F3");
    const dep = (key, name, configuration, k, simulated) => {
      const gas = gasRowK(configuration, k);
      return { key, name, gas, signatures: k, perSig: gas.gas_per_signature, bound: gasBound(CARD_GAS, gas.total, k), sim: simulated };
    };
    return [
      dep("8288-1", "EIP-8288, 1 dependency per transaction", "D", 1, cell("D", { pricing: p8288, gas: CARD_GAS })),
      dep(`8288-${deps}`, `EIP-8288, ${deps} dependencies`, "D", deps, cell("D", { pricing: p8288, gas: CARD_GAS, signatures: deps })),
      dep("3000-1", "3,000 gas, 1 dependency", "D-dep3000", 1, cell("E", { pricing: pPcs, gas: CARD_GAS })),
      dep(`3000-${deps}`, `3,000 gas, ${deps} dependencies`, "D-dep3000", deps, cell("E", { pricing: pPcs, gas: CARD_GAS, signatures: deps })),
      { key: "F3", name: `F3, user-side bundle of ${f3.signatures}`, gas: f3, signatures: f3.signatures, perSig: f3.gas_per_signature,
        bound: gasBound(CARD_GAS, f3.total, f3.signatures), sim: cell("F3", { gas: CARD_GAS, signatures: f3.signatures }) },
    ];
  }
  // The bundler machines the F3 cell at 200 M needs outside the simulation.
  function bundlers() {
    const top = rows.find((row) => row.scenario === "F3" && row.candidate === WORKING_SET && row.gas_limit === 200000000 &&
      row.headline === 1 && row.client_proofs === "draft" && row.nodes === data.scenarios.table_nodes && row.uplinks === "heterogeneous");
    return top ? { cell: top, machines: top.tps_sig / agg.bundler.sig_per_s_per_machine } : null;
  }

  // The key figures of a card row: the working set in the scenario's own
  // configuration, at 60 M gas.
  function cardFigures(id) {
    const entry = idById.get(id);
    const gas = gasRow(entry.default_configuration);
    const bound = gasBound(CARD_GAS, gas.total, gas.signatures);
    const options = { pricing: entry.pricing, signatures: gas.signatures };
    const all = cells(id, options);
    const at60 = all.filter((row) => row.gas_limit === CARD_GAS);
    const simulated = at60.find((row) => row.mode === entry.mode) || at60[0] || all[0] || null;
    return { id, entry, gas, bound, simulated, simulatedTps: tps(simulated),
             share: bound / ecdsaBaseline(CARD_GAS), unit: entry.class === "noagg" ? "tx/s" : "sig/s" };
  }

  return { ids, idById, configs, rows, gasRow, cells, cell, tps, cardFigures, agg, gasRowK, provedBy, proverBound, likeRows, bundlers };
}

// ----------------------------------------------------------- the wording
const WHEN = {
  "now": "Works today",
  "H*": "Needs the first fork after Glamsterdam",
  "H* to I*": "Between the first and the second fork after Glamsterdam",
  "I*": "Needs the second fork after Glamsterdam",
};
export const whenLabel = (when) => (WHEN[when] || when) + (when === "now" ? "" : ` (${when})`);

// The picker: a short title and one short line per scenario id.
const PICK = {
  E0a: ["Today's EVM: ERC-4337", "A contract verifies. No fork."],
  E0b: ["Today's EVM: Safe module", "A contract verifies. No fork."],
  E0c: ["Today's EVM: EIP-7702", "A contract verifies. No fork."],
  A: ["8141 full", "Account code verifies it."],
  B: ["8141 light", "Nodes verify it natively."],
  C1: ["8288 light, raw", "A dependency, no proof yet."],
  C2: ["8288, builder proves", "One proof per block."],
  D: ["Aggregation, gas binds", "Nodes prove in batches, recursively."],
  E: ["Aggregation, mempool binds", "Recursive batches; provers limit it."],
  F1: ["User-side proof", "Your computer proves it."],
  F2: ["User-side proof, first node", "The first node proves it."],
  F3: ["User-side proof, bundled", "A bundler proves a bundle."],
};
export const pickLine = (id) => (PICK[id] ? PICK[id][1] : "");
export const pickTitle = (id) => (PICK[id] ? PICK[id][0] : id);

const ROUTES = {
  E0a: "ERC-4337 smart account", E0b: "Safe module", E0c: "EIP-7702 account",
  F1: "Your computer proves it", F2: "The first node proves it", F3: "A bundler proves a bundle",
};
export const routeLabel = (id) => ROUTES[id] || null;

const K = (value) => (value / 1000).toFixed(1) + " K";
// The prover-bound ceiling of scenarios D and E (notes/phase3/aggregation-check.md, claim A4).
const UNRESOLVED = "Unresolved, not physical. Two causes are open: raw signatures are relayed to every node, so each is proved many times; and when only proofs travel, as the draft describes, the result depends on the leaf batch size for a reason not yet found.";
const admitGas = (text) => { const m = /\(([\d.]+)K\)/.exec(text || ""); return m ? m[1] + " K" : "n/a"; };

// Each group is one row of the campaign's scenario list. `detail` returns
// [label, sentence] pairs; `flow` four steps: you sign, where the bytes
// travel, who checks, what lands in the block.
export const GROUPS = [
  {
    key: "E0", ids: ["E0a", "E0b", "E0c"],
    simple: "A smart contract checks the signature. It works today, with no fork.",
    detail(f) {
      const a = f.gasRow("E0a_7623"), a76 = f.gasRow("E0a_7976"), b = f.gasRow("E0b_7623"), c = f.gasRow("E0c_7623");
      const b76 = f.gasRow("E0b_7976");
      return [
        ["Who verifies", "A verifier contract on the Ethereum Virtual Machine (EVM). An ERC-4337 smart account (E0a), a Safe module (E0b) or an EIP-7702 account (E0c) calls it."],
        ["Where the signature goes", "In the transaction's calldata. It is on chain, forever."],
        ["What you pay", `The larger of two prices. Standard price: ${a.signature_byte_price} gas per byte, plus what the contracts execute. The verifier runs about ${fmtInt(a.verification_measured)} gas for the working set (measured). ` +
          `Floor price: ${b.signature_byte_price} gas per byte today (EIP-7623), ${b76.signature_byte_price} after the Glamsterdam fork (EIP-7976). ` +
          `For the Safe module and the EIP-7702 account the floor wins, so bytes set the cost: ${fmtInt(b.total)} and ${fmtInt(c.total)} gas today. ` +
          `For ERC-4337 the standard price wins today (${fmtInt(a.total)} gas), because its EntryPoint contract adds about ${fmtInt(a.parts.overhead)} gas of execution. After Glamsterdam the floor wins there too (${fmtInt(a76.total)} gas).`],
        ["What limits throughput", "The block's gas limit."],
        ["Parameter set", "Any. Each wallet deploys the verifier it wants. A standard set still matters for hardware wallets and audits."],
        ["Caveat", "ERC-4337's public mempool accepts a user operation of at most 8,192 bytes. Today's set needs 8,544, so it has no public route there."],
      ];
    },
    flow(f) {
      return [
        "You sign the transaction with your leanSPHINCS key.",
        "The signature travels inside the transaction, as ordinary calldata, through the public mempool.",
        "Every node runs the verifier contract when it executes the transaction.",
        `The block holds the whole signature: ${fmtInt(f.gasRow("E0b_7623").sig_bytes)} bytes for the working set.`,
      ];
    },
  },
  {
    key: "A", ids: ["A"],
    simple: "The signature rides in a new kind of transaction (a frame transaction) and the account's own code verifies it.",
    detail(f) {
      const g = f.gasRow("A");
      const at300 = f.cells("A", { pricing: "8141+7976", gas: 300000000, mode: "capacity" }).find((row) => row.mode === "capacity");
      const admit = f.cell("A", { pricing: "8141+7976", gas: CARD_GAS });
      const admitToday = f.cell("A", { pricing: "8141+7976", gas: CARD_GAS, candidate: TODAY_SET });
      return [
        ["Who verifies", "The account's own EVM code, in the VERIFY frame of an EIP-8141 frame transaction."],
        ["Where the signature goes", "In the transaction. It is on chain."],
        ["What you pay", `${g.signature_byte_price} gas per signature byte. This floor price wins, so the verifier's execution (${fmtInt(g.verification_executed)} gas in the model) is not charged on top. One transaction: ${fmtInt(g.total)} gas.`],
        ["What limits throughput", "The block's gas limit up to 200 M (200 million) gas." + (at300
          ? ` At 300 M blocks get too large to spread in time: the simulation sustains ${fmtTps(at300.tps_tx)} of the ${fmtTps(gasBound(300000000, g.total))} tx/s that gas would allow.` : "")],
        ["Public mempool rule", "The validation gas must be declared at 100,000 or less. " + (admit
          ? `For the working set one signature needs ${admitGas(admit.admit_k1)}, two need ${admitGas(admit.admit_k2)} and three need ${admitGas(admit.admit_k3)}. So one fits, two fit only if signature bytes do not count (a draft proposes that), and three do not fit. ` : "") +
          (admitToday ? `Today's set fits one (${admitGas(admitToday.admit_k1)}).` : "")],
        ["Consequence", "Signatures verified this way can never be aggregated later."],
        ["Parameter set", "Any."],
      ];
    },
    flow(f) {
      return [
        "You sign the frame transaction with your leanSPHINCS key.",
        "The signature travels inside the transaction, as opaque bytes, through the public mempool.",
        "Every node runs your account's code, which checks the signature.",
        `The block holds the whole signature: ${fmtInt(f.gasRow("A").sig_bytes)} bytes for the working set.`,
      ];
    },
  },
  {
    key: "B", ids: ["B"],
    simple: "leanSPHINCS becomes a built-in signature type of frame transactions. Nodes verify it natively.",
    detail(f) {
      const g = f.gasRow("B_pr12616_64");
      return [
        ["Who verifies", "Every node, natively: in the client's own code, not in a contract, before any frame runs."],
        ["Where the signature goes", "In the transaction. It is on chain, but contracts cannot read it. So the same accounts can be aggregated later."],
        ["What you pay", `${g.signature_byte_price} gas per byte, plus the scheme's own charge: ${fmtInt(g.parts.verification)} gas for the working set's ${g.perms} permutations (about 25.5 gas per permutation in the draft pull request, rounded up to a hundred). One transaction: ${fmtInt(g.total)} gas.`],
        ["What limits throughput", "The block's gas limit."],
        ["Parameter set", "Fixed by the protocol. The draft pull request is written for today's 7,740-byte set."],
      ];
    },
    flow(f) {
      return [
        "You sign the frame transaction with your leanSPHINCS key.",
        "The signature travels inside the transaction through the public mempool.",
        "Every node checks it natively before running the transaction.",
        `The block holds the whole signature: ${fmtInt(f.gasRow("B_pr12616_64").sig_bytes)} bytes for the working set.`,
      ];
    },
  },
  {
    key: "C1", ids: ["C1"],
    simple: "Transactions declare their signature the EIP-8288 way, but there is no proof yet. Signatures sit in the block.",
    detail(f) {
      const g = f.gasRow("C1_flat_64");
      return [
        ["Who verifies", "Every node, natively."],
        ["Where the signature goes", "With the transaction, and in the block."],
        ["What you pay", `${g.signature_byte_price} gas per byte. This floor price wins, so the assumed charge of ${fmtInt(g.verification_executed)} gas per signature is not paid on top. One transaction: ${fmtInt(g.total)} gas. No public text defines this scenario: the price is the campaign's assumption.`],
        ["What limits throughput", "The block's gas limit."],
        ["Why it exists", "Accounts written for it move to full EIP-8288 aggregation without changes."],
        ["Parameter set", "Fixed by the protocol."],
      ];
    },
    flow() {
      return [
        "You sign the transaction with your leanSPHINCS key. The transaction names the signature as a dependency.",
        "The signature travels beside the transaction through the mempool.",
        "Every node checks it natively.",
        "The block holds the transaction and the whole signature.",
      ];
    },
  },
  {
    key: "C2", ids: ["C2"],
    simple: "Signatures travel raw in the mempool. Only the block builder proves them: one proof per block.",
    detail(f) {
      const g = f.gasRow("C2"), g3 = f.gasRow("C2-dep3000");
      const c60 = f.cell("C2", { pricing: "8288:3000+30000/dep", gas: CARD_GAS });
      const c200 = f.cell("C2", { pricing: "8288:3000+30000/dep", gas: 200000000 });
      const c300 = f.cell("C2", { pricing: "leanPCS-draft:3000/dep", gas: 300000000 });
      return [
        ["Who verifies", "Mempool nodes check the raw signature natively. The block builder then proves all of a block's signatures in one STARK proof."],
        ["Where the signature goes", "In the mempool only. Never on chain."],
        ["What you pay", `A charge per signature dependency: ${fmtInt(g.parts.verification)} gas in EIP-8288, ${fmtInt(g3.parts.verification)} in the leanPCS draft. One transaction: ${fmtInt(g.total)} or ${fmtInt(g3.total)} gas.`],
        ["What limits throughput", (c60 && c200 ? `Gas at 60 M and 200 M (${fmtTps(c60.tps_tx)} and ${fmtTps(c200.tps_tx)} tx/s simulated). ` : "") +
          (c300 ? `At 300 M and the lower price, blocks and raw signatures stop spreading in time: ${fmtTps(c300.tps_tx)} tx/s, where gas alone would allow ${fmtTps(gasBound(300000000, g3.total))}.` : "")],
        ["Parameter set", "Fixed by the protocol."],
        ["Caveat", "This scenario has tables but is too heavy to run in the browser."],
      ];
    },
    flow() {
      return [
        "You sign the transaction with your leanSPHINCS key.",
        "The raw signature travels beside the transaction through the mempool.",
        "Mempool nodes check it natively. The builder proves every signature of its block in one proof.",
        "The block holds the transactions and one proof. No signature.",
      ];
    },
  },
  {
    key: "D", ids: ["D"],
    simple: "Recursive proving in the mempool: every second, each node proves the signatures it holds in one batch and merges its neighbours' proofs. The block carries one proof. The gas charged per signature caps throughput.",
    detail(f) {
      const g = f.gasRow("D"), g3 = f.gasRow("D-dep3000");
      const sim = f.cell("D", { pricing: "8288:3000+30000/dep", gas: CARD_GAS });
      const sim3 = f.cell("E", { pricing: "leanPCS-draft:3000/dep", gas: CARD_GAS });
      const today3 = f.cell("E", { pricing: "leanPCS-draft:3000/dep", gas: CARD_GAS, candidate: TODAY_SET });
      const agg = f.agg, run = agg.run, deps = agg.deps, by = f.provedBy();
      const g16 = f.gasRowK("D", deps), sim16 = f.cell("D", { pricing: "8288:3000+30000/dep", gas: CARD_GAS, signatures: deps });
      const prover16 = f.cell("E", { pricing: "leanPCS-draft:3000/dep", gas: CARD_GAS, signatures: deps });
      return [
        ["Who verifies", "Mempool nodes, by recursive proving. Every second each node proves the new signatures it holds in one proof (a batch) and merges the proofs its neighbours send. The builder's last merge is the block's single STARK, which covers every signature. In the simulation every one of the 40 nodes aggregates" +
          (by ? `, and each signature ends up proved by ${by[0]} to ${by[1]} of them.` : ".")],
        ["One proof, many signatures", `One proof covers hundreds to thousands of signatures. In a ${run.nodes}-node run of this scenario a proof claimed ${fmtInt(run.claims_per_proof_mean)} signatures on average (the largest ${fmtInt(run.claims_per_proof_largest_bucket[0])} to ${fmtInt(run.claims_per_proof_largest_bucket[1])}), and one proof covered a block of ${fmtInt(run.max_txs_per_block)} transactions (simulated).`],
        ["Where the signature goes", "To the first aggregating nodes only, in a mempool wrapper. Never on chain. The transaction carries a 96-byte dependency instead: message hash, key and scheme."],
        ["What you pay, EIP-8288 as written", `${fmtInt(g3.parts.verification)} gas for the signature plus ${fmtInt(g.parts.verification - g3.parts.verification)} of recursive STARK gas, per dependency. Add the frame transaction itself (${fmtInt(g.parts.fixed)}) and the dependency's bytes (${fmtInt(g.parts.calldata)}): ${fmtInt(g.total)} gas. ` +
          `That allows ${fmtTps(gasBound(CARD_GAS, g.total))} sig/s (signatures per second, one per transaction) at 60 M` + (sim ? ` (${fmtTps(sim.tps_sig)} simulated)` : "") + ", about a third of the plain-transfer bound. This is a price in a draft, not a physical limit."],
        ["The charge does not amortise", `It is charged per signature, although one proof covers the whole block. A transaction with ${deps} dependencies (the most allowed) still pays ${fmtInt(g16.gas_per_signature)} gas per signature: ${fmtTps(gasBound(CARD_GAS, g16.total, deps))} sig/s at 60 M` +
          (sim16 ? ` (${fmtTps(sim16.tps_sig)} simulated)` : "") + `. No batch size passes ${fmtTps(agg.cases.limit_8288.sig_per_s)}.`],
        ["What you pay, leanPCS draft", `${fmtInt(g3.parts.verification)} gas per dependency: ${fmtInt(g3.total)} gas, ${fmtTps(gasBound(CARD_GAS, g3.total))} sig/s at 60 M` + (sim3 ? ` (${fmtTps(sim3.tps_sig)} simulated).` : ".")],
        ["If the price changed", `Gas bounds at 60 M with ${deps} dependencies per transaction (modelled): ${fmtTps(agg.cases.once_per_tx.sig_per_s)} sig/s if the ${fmtInt(agg.per_dependency.charge - agg.per_dependency.charge_light)} of recursive STARK gas were charged once per transaction, ${fmtTps(agg.cases.per_dep_light.sig_per_s)} at ${fmtInt(agg.per_dependency.charge_light)} per dependency.` +
          (prover16 && /prover/.test(prover16.binding) ? ` The provers then bind first, near ${fmtTps(prover16.tps_sig)} (simulated).` : "")],
        ["Signature size and permutations", "For the working set they do not change these figures: gas runs out first." +
          (today3 && /prover/.test(today3.binding) ? ` Today's set is on the edge at the leanPCS price: ${fmtTps(today3.tps_sig)} sig/s, with provers as the limit in some runs.` : "")],
        ["Parameter set", "Fixed by the protocol, under EIP-8288 and under leanPCS alike."],
      ];
    },
    flow() {
      return [
        "You sign the transaction with your leanSPHINCS key.",
        "The signature travels in a mempool wrapper to the first nodes. They replace it with a proof, and proofs are merged as they spread.",
        "Mempool nodes check signatures and proofs. The builder merges everything into one proof.",
        "The block holds the transactions, each with a 96-byte dependency, and one proof. No signature.",
      ];
    },
  },
  {
    key: "E", ids: ["E"],
    simple: "The same design once gas is no longer the limit. Then the provers or the nodes' uplinks are.",
    detail(f) {
      const p = "8288:3000+30000/dep";
      const today = f.cell("D", { pricing: p, gas: 200000000, candidate: TODAY_SET });
      const work = f.proverBound();
      const by = f.provedBy();
      return [
        ["When", "From about 200 M gas at EIP-8288's price of 33,000 gas per signature. Between 60 M and 200 M at the leanPCS draft's 3,000."],
        ["Provers", (today && work.length ? `With every node aggregating, the simulated network carries about ${fmtTps(today.tps_sig)} signatures per second with today's set and ${fmtTps(work[0])} to ${fmtTps(work[work.length - 1])} with the working set. ` : "") +
          "One 8-core node alone could prove more: 217 and about 350 per second in the campaign's prover model. " +
          (by ? `In the network each signature is proved by ${by[0]} to ${by[1]} of the 40 nodes, and nodes spend much of their time merging their neighbours' proofs. ` : "") +
          "Fewer, dedicated aggregators carry more on good uplinks."],
        ["Is the ceiling physical", UNRESOLVED],
        ["Bandwidth", "A node sends a proof of about 300 KB to each peer every second. On a 20 Mbit/s uplink that is the whole uplink. Whether it works depends on the queue policy, which EIP-8288 does not specify: in the simulation, keeping only the newest proof carries the gas bound, and queueing every proof carries almost nothing."],
        ["Signature size and permutations", "Here permutations cost throughput almost one for one. Bytes barely matter."],
        ["What you pay", "The same as in the scenario above: a charge per signature dependency, which does not amortise however many signatures one proof covers."],
        ["How sure", "These capacities come from a 40-node simulation with modelled prover prices. They are the least certain figures in this tool."],
        ["Parameter set", "Fixed by the protocol."],
      ];
    },
    flow() {
      return [
        "You sign the transaction with your leanSPHINCS key.",
        "The signature travels in a mempool wrapper to the first nodes. After that, nodes exchange proofs of about 300 KB every second.",
        "Every aggregating node proves new signatures and merges its neighbours' proofs. This is the work that runs out.",
        "The block holds the transactions, each with a 96-byte dependency, and one proof. No signature.",
      ];
    },
  },
  {
    key: "F", ids: ["F1", "F2", "F3"],
    simple: "No built-in signature scheme. The signature is proved inside a proof on the user's side, and a contract checks that proof.",
    detail(f) {
      const g1 = f.gasRow("F1"), g3 = f.gasRow("F3");
      const c1 = f.cell("F1", { pricing: f.idById.get("F1").pricing, gas: CARD_GAS });
      const c3 = f.cardFigures("F3").simulated;
      const repo = f.rows.find((row) => row.scenario === "F3" && row.candidate === WORKING_SET && row.gas_limit === CARD_GAS &&
        row.headline === 1 && row.client_proofs === "draft" && row.nodes === 40 && row.uplinks === "heterogeneous");
      const agg = f.agg, off = f.bundlers(), light = f.gasRow("D-dep3000");
      return [
        ["Not the leanPCS draft's way", "The leanPCS draft keeps plain signatures as EIP-8288 dependencies: see the two scenarios above."],
        ["Three routes", `Your computer proves its own signature (F1): about 300 KB to the first node and about 10 GB of memory, so no phone. Or you send the raw signature and the first node proves it (F2). Or a bundler proves many signatures in one user-side proof, a bundle (F3): ${g3.signatures} of the working set per bundle in the gas model` +
          (repo && repo.k !== g3.signatures ? `, ${repo.k} in the main simulated runs.` : ".")],
        ["Who verifies", "A contract checks the proof on chain. Nodes check and merge proofs in the mempool."],
        ["Where the signature goes", "Not on chain. The chain sees a proof of about 1,000 bytes and 64 bytes of claim (key and message) per signature."],
        ["What you pay", `${fmtInt(g1.total)} gas per transaction when it brings its own proof. A bundle costs ${fmtInt(g3.total)} gas, or ${fmtInt(g3.gas_per_signature)} per signature.`],
        ["What limits throughput", (c1 ? `With one proof per transaction the mempool has a proof to merge for every transaction: about ${fmtTps(c1.tps_tx)} tx/s in simulation, where gas would allow ${fmtTps(gasBound(CARD_GAS, g1.total))}. ` : "") +
          (c3 ? `Bundles carry about ${fmtInt(c3.tps_sig)} signatures per second at 60 M; there gas is the limit.` : "")],
        ["What the bundle figures leave out", "The bundler's proving happens outside the simulated network: no simulated node is charged for it. " +
          (off ? `The ${fmtInt(off.cell.tps_sig)} sig/s simulated at 200 M would need about ${off.machines.toFixed(1)} bundler machines of ${agg.bundler.cores} cores (EPYC 9355P, ${fmtInt(agg.bundler.sig_per_s_per_machine)} sig/s each; modelled). ` : "") +
          `Bundled signatures also pay no per-user transaction gas. If each paid a frame transaction's overhead (${fmtInt(agg.tx_overhead)} gas), the bound at 60 M would be about ${fmtInt(agg.cases.f3_with_tx_overhead.sig_per_s)} sig/s (modelled), level with aggregation at ${fmtInt(agg.per_dependency.charge_light)} gas per dependency (${fmtTps(gasBound(CARD_GAS, light.total))}).`],
        ["Parameter set", "Any. The wallet picks its circuit."],
        ["Caveat", "The first node's proving step (F2) was not simulated. F2 shows F1's figures."],
      ];
    },
    flow() {
      return [
        "You sign with your leanSPHINCS key.",
        "Your computer, the first node or a bundler turns the signature into a proof. Only the proof travels on.",
        "Mempool nodes check and merge proofs. A contract checks the final proof.",
        "The block holds a proof and 64 bytes per signature. No signature.",
      ];
    },
  },
];
export const groupOf = (id) => GROUPS.find((group) => group.ids.includes(id));

// The full explanation of a group: the detail list and the flow in words.
export function detailBlock(group, f) {
  const list = el("dl", { class: "kv sc-detail" });
  for (const [label, text] of group.detail(f)) list.append(el("dt", {}, label), el("dd", {}, text));
  return el("div", { class: "stack sc-detail-body" }, list,
    el("h4", {}, "How a transaction flows"),
    el("ol", { class: "sc-flow" }, group.flow(f).map((step) => el("li", {}, step))));
}

// ---------------------------------------------------------- the overview
export function initScenarioHelp(app) {
  const data = app.data;
  const f = makeFigures(data);
  const select = $("#ex-scenario");

  const base = ecdsaBaseline(CARD_GAS);
  const about = $("#ab-baseline");
  if (about) about.textContent = fmtTps(base);

  // The picker: one button per scenario id, a short title and a short line.
  const picker = clear($("#sc-picker"));
  for (const group of GROUPS) {
    for (const id of group.ids) {
      const [title, line] = PICK[id] || [f.idById.get(id).name, ""];
      const button = el("button", { type: "button", class: "sc-pick", "data-id": id, "data-group": group.key, "aria-pressed": "false" },
        el("span", { class: "sc-pick-title" }, title, " ", el("span", { class: "sc-id" }, id)),
        el("span", { class: "sc-line" }, line));
      button.addEventListener("click", () => choose(id));
      picker.append(button);
    }
  }

  // All scenarios side by side: the working set at 60 M gas (collapsed).
  const compare = clear($("#sc-compare"));
  compare.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col" }, "Scenario"),
    el("th", { scope: "col" }, "When"),
    el("th", { scope: "col" }, "Signature on chain"),
    el("th", { scope: "col", class: "n" }, "Throughput", el("span", { class: "sub" }, "at 60 M gas")),
    el("th", { scope: "col", class: "n" }, "Gas", el("span", { class: "sub" }, "per signature")))));
  const compareBody = el("tbody");
  for (const group of GROUPS) {
    for (const id of group.ids) {
      const fig = f.cardFigures(id);
      const entry = fig.entry;
      const sim = fig.simulated && fig.simulated.gas_limit === CARD_GAS ? fig.simulated : null;
      const onChain = fig.gas.signature_on_chain;
      const throughput = el("td", { class: "n", "data-fig": "throughput", "data-bound": String(fig.bound),
        "data-simulated": sim ? String(fig.simulatedTps) : "", "data-unit": fig.unit });
      if (sim) {
        throughput.append(el("span", { class: "sc-num" }, fmtTps(fig.simulatedTps)), " " + fig.unit,
          chip("simulated", `${sim.status}. Source: ${sim.source}`),
          el("span", { class: "sub" }, `gas bound ${fmtTps(fig.bound)}` +
            (fig.gas.signatures > 1 ? `; ${fmtInt(fig.gas.signatures)} per bundle transaction` : ` · ${Math.round(fig.share * 100)}% of ECDSA`)));
      } else {
        throughput.append(el("span", { class: "sc-num" }, fmtTps(fig.bound)), " " + fig.unit,
          chip("modelled", "The gas bound: 60 M / gas per transaction / 12 s"),
          el("span", { class: "sub" }, `gas bound · ${Math.round(fig.share * 100)}% of ECDSA` +
            (fig.simulated ? `; simulated only at ${fmtGas(fig.simulated.gas_limit)}: ${fmtTps(fig.simulatedTps)}` : "; no simulated cell")));
      }
      const perSig = fig.gas.signatures > 1;
      const gas = el("td", { class: "n", "data-fig": "gas", "data-total": String(fig.gas.total), "data-signatures": String(fig.gas.signatures) },
        el("span", { class: "sc-num" }, fmtInt(perSig ? fig.gas.gas_per_signature : fig.gas.total)),
        chip("modelled", data.gas.status_default),
        perSig ? el("span", { class: "sub" }, `per signature; ${fmtInt(fig.gas.total)} per bundle of ${fig.gas.signatures}`) : null);
      const pick = el("button", { type: "button", class: "link", "data-id": id, title: entry.name + (routeLabel(id) ? ": " + routeLabel(id) : "") },
        PICK[id] ? PICK[id][0] : entry.name);
      pick.addEventListener("click", () => { choose(id); $("#ex-pick").scrollIntoView({ block: "start" }); });
      compareBody.append(el("tr", { "data-id": id, "data-group": group.key },
        el("th", { scope: "row" }, pick, " ", el("span", { class: "sc-id" }, id)),
        el("td", { title: whenLabel(entry.when) }, entry.when === "now" ? "today" : entry.when),
        el("td", { "data-onchain": String(onChain) }, onChain ? "yes" : "no"),
        throughput, gas));
    }
  }
  compare.append(compareBody);

  // The selected scenario: its plain line and its full explanation.
  function showSelected() {
    const id = select.value;
    const group = groupOf(id);
    const entry = f.idById.get(id);
    for (const button of picker.querySelectorAll(".sc-pick")) {
      button.setAttribute("aria-pressed", String(button.dataset.id === id));
    }
    for (const tr of compareBody.querySelectorAll("tr")) tr.classList.toggle("pick", tr.dataset.id === id);
    if (!group || !entry) return;
    clear($("#ex-simple")).append(group.simple, entry.when === "now" ? "" : " " + whenLabel(entry.when) + ".");
    const how = $("#ex-how");
    if (how.dataset.group !== group.key) {
      how.dataset.group = group.key;
      clear($("#ex-how-body")).append(detailBlock(group, f));
    }
  }
  function choose(id) {
    if (select.value === id) return;
    select.value = id;
    select.dispatchEvent(new Event("change"));
  }
  select.addEventListener("change", showSelected);
  showSelected();

  // ------------------------------------- reading the aggregation numbers
  const dep = f.gasRow("D"), dep3 = f.gasRow("D-dep3000"), plain = f.gasRow("A"), plainToday = f.gasRow("A", TODAY_SET);
  const p8288 = "8288:3000+30000/dep", pPcs = "leanPCS-draft:3000/dep";
  const simD = f.cell("D", { pricing: p8288, gas: CARD_GAS });
  const simD3 = f.cell("E", { pricing: pPcs, gas: CARD_GAS });
  const simA = f.cell("A", { pricing: "8141+7976", gas: CARD_GAS });
  const simAToday = f.cell("A", { pricing: "8141+7976", gas: CARD_GAS, candidate: TODAY_SET });
  const charge = dep.parts.verification, charge3 = dep3.parts.verification;
  const aggRows = [
    { key: "ecdsa", name: "Plain ECDSA transfer", sub: "the reference", fixed: ECDSA_TRANSFER_GAS, bytes: 0, charge: 0, total: ECDSA_TRANSFER_GAS, sim: null },
    { key: "noagg", name: "No aggregation", sub: "signature in a frame transaction (8141 full), working set", fixed: plain.parts.fixed, bytes: plain.parts.calldata, charge: plain.parts.verification, total: plain.total, sim: simA },
    { key: "8288", name: "Aggregation, EIP-8288's price", sub: `${fmtInt(charge)} gas per signature dependency`, fixed: dep.parts.fixed, bytes: dep.parts.calldata, charge, total: dep.total, sim: simD },
    { key: "leanpcs", name: "Aggregation, leanPCS draft's price", sub: `${fmtInt(charge3)} gas per signature dependency`, fixed: dep3.parts.fixed, bytes: dep3.parts.calldata, charge: charge3, total: dep3.total, sim: simD3 },
  ];
  const body = clear($("#agg-table tbody"));
  for (const row of aggRows) {
    const bound = gasBound(CARD_GAS, row.total);
    body.append(el("tr", { "data-row": row.key, "data-total": String(row.total), "data-bound": String(bound),
                           "data-simulated": row.sim ? String(row.sim.tps_tx) : "", "data-unit": "sig/s" },
      el("th", { scope: "row" }, row.name, el("span", { class: "sub" }, row.sub)),
      el("td", { class: "n" }, fmtInt(row.fixed)),
      el("td", { class: "n" }, fmtInt(row.bytes)),
      el("td", { class: "n" }, fmtInt(row.charge)),
      el("td", { class: "n", "data-field": "total" }, fmtInt(row.total)),
      el("td", { class: "n", "data-field": "bound" }, fmtTps(bound)),
      el("td", { class: "n", "data-field": "simulated" }, row.sim ? fmtTps(row.sim.tps_tx) : el("span", { class: "dim" }, "n/a"))));
  }
  // The same unit: gas per signature and signatures per second at 60 M.
  const agg = f.agg, like = f.likeRows(), off = f.bundlers(), by = f.provedBy();
  const likeBody = clear($("#agg-like tbody"));
  for (const row of like) {
    const sim = row.sim ? row.sim.tps_sig : null;
    const bindsElse = row.sim && !/^gas$/.test(row.sim.binding);
    likeBody.append(el("tr", { "data-row": row.key, "data-total": String(row.gas.total), "data-signatures": String(row.signatures),
                               "data-per-sig": String(row.perSig), "data-bound": String(row.bound), "data-simulated": sim === null ? "" : String(sim),
                               "data-binding": row.sim ? row.sim.binding : "" },
      el("th", { scope: "row" }, row.name),
      el("td", { class: "n", "data-field": "per-sig" }, fmtInt(row.perSig)),
      el("td", { class: "n", "data-field": "bound" }, fmtTps(row.bound)),
      el("td", { class: "n", "data-field": "simulated" }, sim === null ? el("span", { class: "dim" }, "n/a")
        : [fmtTps(sim), chip("simulated", `${row.sim.status}. Source: ${row.sim.source}`),
           bindsElse ? el("span", { class: "sub" }, `${row.sim.binding} binds`) : null])));
  }
  const likeF3 = like[like.length - 1], run = agg.run;
  const noCharge = gasBound(CARD_GAS, dep.parts.fixed + dep.parts.calldata);
  const ratio = (a, b) => (a && b ? a.tps_tx / b.tps_tx : null);
  const ratios = [ratio(simD, simA), ratio(simD3, simA)].filter(Boolean);
  const todayD = f.cell("D", { pricing: p8288, gas: CARD_GAS, candidate: TODAY_SET });
  const todayD3 = f.cell("E", { pricing: pPcs, gas: CARD_GAS, candidate: TODAY_SET });
  const ratiosToday = [ratio(todayD, simAToday), ratio(todayD3, simAToday)].filter(Boolean);
  const proverToday = f.cell("D", { pricing: p8288, gas: 200000000, candidate: TODAY_SET });
  const proverWork = f.proverBound();
  const one = (value) => value.toFixed(1);
  const points = clear($("#agg-points"));
  points.append(
    el("li", { id: "agg-gas-point" }, el("strong", {}, `What sets ${Math.round(gasBound(CARD_GAS, dep.total))} and ${Math.round(simD3 ? simD3.tps_tx : gasBound(CARD_GAS, dep3.total))} sig/s: a price. `),
      `(${fmtTps(gasBound(CARD_GAS, dep.total))} is the gas bound under EIP-8288; ${simD3 ? fmtTps(simD3.tps_tx) + " is the simulated figure" : fmtTps(gasBound(CARD_GAS, dep3.total)) + " is the gas bound"} under the leanPCS draft.) `,
      `With aggregation no signature byte is on chain, but each signature is a dependency of the transaction, and each draft charges gas for it: ${fmtInt(charge)} in EIP-8288 (${fmtInt(charge3)} for the signature and ${fmtInt(charge - charge3)} of recursive STARK gas), ${fmtInt(charge3)} in the leanPCS draft. ` +
      "A STARK is the kind of proof that replaces the signatures. These are prices in drafts, not physical limits."),
    el("li", { id: "agg-recursive-point" }, el("strong", {}, "Full EIP-8288 aggregates recursively too. "),
      `Every node proves a batch each second and merges its neighbours' proofs, so one proof covers hundreds to thousands of signatures: ${fmtInt(run.claims_per_proof_mean)} per proof on average and ${fmtInt(run.max_txs_per_block)} transactions per block under one proof in a ${run.nodes}-node run. `,
      "A batch is many signatures in one node's proof; a bundle is many signatures in one user-side proof (F3).", " ", chip("simulated", `${run.status}. Source: ${run.source}`)),
    el("li", { id: "agg-amortise-point" }, el("strong", {}, "Why the bundle looks far ahead: the charge does not amortise. "),
      `EIP-8288 charges ${fmtInt(agg.per_dependency.charge)} gas plus ${fmtInt(agg.per_dependency.calldata)} of calldata for every signature, whatever the batch: at most ${fmtTps(agg.cases.limit_8288.sig_per_s)} sig/s at 60 M. ` +
      `F3 pays once per bundle: ${fmtInt(likeF3.perSig)} gas per signature, ${(like[0].perSig / likeF3.perSig).toFixed(1)} times less. That ratio is the draft's price, not the simulation.`),
    el("li", { id: "agg-fair-point" }, el("strong", {}, "The bundle is not like for like. "),
      "Its bundler proves outside the simulated network" + (off ? ` (about ${off.machines.toFixed(1)} machines for the ${fmtInt(off.cell.tps_sig)} sig/s at 200 M, modelled)` : "") +
      `, and its signatures pay no per-user transaction gas. With a frame transaction's overhead each, F3 would carry about ${fmtInt(agg.cases.f3_with_tx_overhead.sig_per_s)} sig/s at 60 M. `,
      `With ${agg.deps} dependencies per transaction, charging the ${fmtInt(agg.per_dependency.charge - agg.per_dependency.charge_light)} once per transaction would allow ${fmtTps(agg.cases.once_per_tx.sig_per_s)} sig/s, and ${fmtInt(agg.per_dependency.charge_light)} per dependency ${fmtTps(agg.cases.per_dep_light.sig_per_s)} (gas bounds).`, " ", chip("modelled", agg.status)),
    el("li", { id: "agg-ratio-point" }, el("strong", {}, "Against no aggregation. "),
      (ratios.length === 2 ? `At 60 M gas aggregation carries ${one(ratios[0])} to ${one(ratios[1])} times more signatures than the same working set without aggregation` : "Aggregation carries several times more") +
      (ratiosToday.length === 2 ? ` (${one(ratiosToday[0])} to ${one(ratiosToday[1])} times for today's larger set, ${fmtTps(simAToday.tps_tx)} tx/s without aggregation). ` : ". ") +
      `Size no longer matters: ${fmtInt(plain.total)} gas per transaction becomes ${fmtInt(dep.total)} or ${fmtInt(dep3.total)}.`),
    el("li", { id: "agg-below-point" }, el("strong", {}, `Why it stays below ${Math.round(base)}. `),
      `A frame transaction with one dependency costs ${fmtInt(dep.parts.fixed)} gas before any charge, and the dependency's 96 bytes cost ${fmtInt(dep.parts.calldata)}. That is already more than a whole ECDSA transfer (${fmtInt(ECDSA_TRANSFER_GAS)}). ` +
      `Even with a charge of zero the bound would be ${fmtTps(noCharge)} sig/s with one signature per transaction.`),
    el("li", { id: "agg-prover-point" }, el("strong", {}, "What sets the prover-bound figures: every node proving and merging. "),
      "When the gas limit is raised, or the charge lowered, gas stops being the limit and the provers take over. " +
      (proverToday && proverWork.length ? `The simulated network then carries about ${fmtTps(proverToday.tps_tx)} signatures per second with today's set and ${fmtTps(proverWork[0])} to ${fmtTps(proverWork[proverWork.length - 1])} with the working set. ` : "") +
      "In that design every node proves the signatures it receives and merges its neighbours' proofs every second, so most signatures are proved many times over" + (by ? ` (by ${by[0]} to ${by[1]} of the 40 nodes). ` : ". ") +
      "The ceiling is a property of this simulated mempool design, not of the prover's raw speed: one 8-core node alone proves more in the campaign's model. " + UNRESOLVED, " ", chip("simulated")),
    el("li", { id: "agg-uncertainty" }, el("strong", {}, "How sure. "),
      "The gas-bound figures follow from the draft prices by arithmetic. The prover-bound figures come from a 40-node simulation with modelled prover prices and a search that brackets the capacity to about 5%. " +
      "They are the least certain figures in this tool, and a different mempool design (fewer, dedicated aggregators) would move them."));

  // ------------------------------------------- baselines beside the figures
  // The calculator: the reference at its gas limit.
  const calcGas = $("#calc-gas");
  function calcBaseline() {
    const gas = Number(calcGas.value);
    const node = $("#calc-baseline");
    if (!gas) { node.textContent = ""; return; }
    node.dataset.baseline = String(ecdsaBaseline(gas));
    node.dataset.gas = String(gas);
    node.textContent = baselineSentence(gas);
  }
  calcGas.addEventListener("change", calcBaseline);
  calcBaseline();

  // Simulate: the plain summary, the details, and the reference at the
  // form's gas limit (with the last run beside it, once there is one).
  const simScenario = $("#sim-scenario"), simGas = $("#sim-gas");
  function simHelp() {
    const group = groupOf(simScenario.value);
    const entry = f.idById.get(simScenario.value);
    if (!group || !entry) return;
    clear($("#sim-scenario-simple")).append(pickLine(entry.id), " ", el("span", { class: "dim" }, whenLabel(entry.when) + "."));
    const details = $("#sim-scenario-details");
    if (details.dataset.group !== group.key) {
      details.dataset.group = group.key;
      clear($("#sim-scenario-details-body")).append(detailBlock(group, f));
    }
  }
  function simBaseline(run = null) {
    const gas = Number(run ? run.gas_limit : simGas.value);
    const node = $("#sim-baseline");
    if (!gas) return;
    const baseline = ecdsaBaseline(gas);
    node.dataset.baseline = String(baseline);
    node.dataset.gas = String(gas);
    clear(node).append(baselineSentence(gas), " ", chip("modelled", "A gas bound: gas limit / 21,000 / 12 s"),
      (run && typeof run.tps_tx === "number" && run.tps_tx >= 0
        ? ` This run: ${fmtTps(run.tps_tx)} tx/s, ${(run.tps_tx / baseline * 100).toFixed(0)}% of it.` : ""));
  }
  simScenario.addEventListener("change", () => { simHelp(); simBaseline(); });
  simGas.addEventListener("change", () => simBaseline());
  $("#sim-config").addEventListener("change", () => simBaseline());
  app.scenarioHelp = { figures: f, simHelp, simBaseline, choose };
  simHelp();
  simBaseline();
}
