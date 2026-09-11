#!/usr/bin/env python3
"""
A mainnet-shaped workload: 12-second slots, a mixed transaction population,
and proving priced by measured leanVM-b cycle counts.

No other bench mixes transaction kinds on a priced wire: `bench.propagation`
measures each kind separately, and `bench.v1_calibration` mixes two streams
only on v1's free-bytes model. Mainnet would not be so tidy — bare
post-quantum signatures and privacy transactions share one mempool, and the
in-circuit price of a privacy witness is the one weight no measurement
covers. This bench mixes the two kinds at a stated share and sweeps the
unmeasured quantities over their bands, so a conclusion can be read as
"holds across the band" or "decided by a number nobody has yet".

Calibration, from leanEthereum/leanVM-b (retrieved 2026-08-30, Mac M4 Max),
kept as exact quotients of the published measurements: verifying one
leanSPHINCS signature in-circuit costs 2 677 883 / 245 cycles (~10 930),
verifying one nested recursive proof costs 746 499 / 2 cycles (~373 250),
and one 2-to-1 fold proves in 417 ms, which anchors cycles to milliseconds.
That single linear scale is itself uncertain: applied to the measured
SPHINCS aggregation it predicts 1 496 ms where 1 194 ms was measured, so
the two measured circuits disagree by 25% on milliseconds per cycle, and
absolute durations here carry at least that error. `--anchor-ms` is where
scale uncertainty lives — a larger anchor stands for a slower machine, and
417 ms is a fast desktop.

The leanSTARK witness weight is NOT measured anywhere; `--leanstark-factor`
expresses it as a multiple of the nested-proof weight. The default band of
1-2 reflects that a WHIR-family verifier's work grows slowly with the size
of the proven statement — an argument, not a measurement.

Receive verification is priced by what each delivery carries
(`NativeVerifyCost`): a signature body pays `--verify-sig-ms`, anything
STARK-class — a privacy body, an aggregate, a mode 1 wrapper, a block
notice — pays `--verify-stark-ms`, a mode 0 wrapper pays for every witness
it ships, and announces and wants are free. Both prices are sweep axes.
The STARK price extrapolates leanVM-b's one measured native verification
(14.1-17.1 ms for one proof size) to every STARK-class object; 15.6 is the
midpoint. The signature price has no measurement at all, and a mode 0
wrapper multiplies it by every signature it carries, so it is swept rather
than declared negligible. Under the default `chain_notify="instant"` no
block messages exist, so no block verification is charged.

    python -m bench.mainnet
    python -m bench.mainnet --tps 5,15,50 --privacy-share 0.0,0.2,0.5
    python -m bench.mainnet --leanstark-factor 1,2 --anchor-ms 417,1000
    python -m bench.mainnet --verify-stark-ms 14.1,17.1 --verify-sig-ms 0.1,0.9
    python -m bench.mainnet --presets high_throughput,high_throughput+fold_plan=capped8

Deliberate omissions, so a row is not misread: no trace ceiling is
modelled — the EIP's 2**24 is KoalaBear rows on a different stack and
leanVM-b states no cycle limit, so a cap here would be a threshold made of
mismatched units. Arrivals default to uniform spacing. Fees cycle 0-4 so
`eip_priority` has an ordering to act on; the cycle is recorded with each run.

`root_claim` retains its terminal-DAG meaning: aggregates never consumed as
fold inputs, including any unused internal outputs of incomplete plans.
`root_count` counts that same population in each JSON record; `--root-stats`
prints its mean over seeds beside the existing `claim` column. The default
text table stays compatible with published baselines. The empty population
retains the legacy mean of zero, identified by its zero count.

The `plan_root_*` JSON metrics instead count completed plan roots, including
ones later consumed by another fold. Both means accompany
`plan_roots_completed`: the object-weighted mean divides by that count,
while the claim-occurrence-weighted mean divides by the claim-width sum.
Neither weights a transaction uniquely across overlapping claims.
`block_observations` records each builder decision before settlement changes
its candidate set. Definitions and limits are in `docs/glossary.md`.

With `--json --outcome-ages-ms 12000,24000`, `fixed_age_outcomes` reports
inclusion, expiry and pending outcomes at each stated age since birth, not
since load end. Early and late cohorts are the two halves of the birth
window. Immature births are censored even if already settled; a comparison
requires both complete cohorts. No age request extends the simulation.
Without requested ages this metric is -1. `transaction_outcomes` retains
birth-indexed timestamps independently of the requested ages.

`load_utilisation` records per-node CPU and egress utilisation over the
birth window, from cumulative counters sampled at its start and end.
`load_traffic` records attempted, refused, accepted, completed and
partial-inclusive wire bytes/s by node and payload kind over that same
window, plus completed wrapper copies by encoding and a reference cadence
rate. Its zero sample precedes scheduling the time-zero births.
Drain length changes neither endpoint. Legacy terminal inclusion, delay
and the text table retain their existing meanings.

JSON `wire_copies` summarises completed raw-object body copies, wrapper
body copies and wrapper claim mentions over all births through observation
end, including the drain. Per-transaction rows stay behind the query API.
`--diffusion` requires `--json` and adds `evidence_diffusion` and
`body_diffusion` at fractions 0.9 and 1.0; it defaults off and does not enter
workload identity. First reach is not a count of transmitted copies.

`--seed-start` defaults to zero; `--seeds` counts consecutive absolute seeds
from that start without changing their random streams.

`--arrivals uniform|poisson|bursty` changes birth times only at matched count,
seed and entry pool. Poisson is conditioned on the fixed count, not an
unconditioned process. Bursty places births randomly in the active fifth of
each slot-length period, with independent seeded phase. JSON identifies the
manifest version, profile, phase and realised kind counts; `--emit-manifest`
also includes every birth record and requires `--json`. Prefixes select
records from a longer manifest without regenerating its mixture. See
`docs/glossary.md` for the workload contract and its comparison limits.
"""

import argparse
import math
import random
import statistics
import sys

from bench.common import (add_seed_start_argument, seed_range,
                          configuration, emit_json_record, print_table_header,
                          random_regular_overlay, schedule_horizon,
                          workload_parameters)
from bench.workload import (ARRIVAL_PROFILES, Birth, WorkloadManifest,
                            arrival_times)
from starksim.arena import KIND_AGGREGATE, KIND_PQ, KIND_PRIVACY
from starksim.costs import CircuitCost, CostTable, NativeVerifyCost
from starksim.evidence import diffusion
from starksim.netsim import NetworkSim
from starksim.network import SizeModel, bytes_per_ms
from starksim.outcomes import (BusySample, TrafficSample, compare_cohorts,
                               outcomes_at_age, traffic_window,
                               utilisation_window)
from starksim.wire_copies import wire_copies

# leanVM-b's measured verification work, in VM cycles, as exact quotients of
# the published measurements (provenance above): a rounded weight would also
# shift the anchor's denominator.
LEANVM_B_SPHINCS_CYCLES = 2_677_883 / 245
LEANVM_B_RECURSIVE_CYCLES = 746_499 / 2
LEANVM_B_PAIRWISE_MS = 417.0

# Fields this bench derives from its own flags or its calibration. A preset
# (or a `+field=value` override) reaching one of these would relabel the run
# without changing its record -- the header printing an uplink the run does
# not use, or the topology built from a different seed than the variant
# names -- so such presets are refused up front. `prove_cost_basis` is
# deliberately absent: a preset naming another basis is overridden by
# design, because the calibration is the bench's subject and the comparison
# across presets must stay paired.
OWNED_FIELDS = ("seed", "capacity", "link_capacity", "size_model", "costs",
                "verify_cost", "egress_queue_bytes", "slot_length",
                "proving_budget")


def mixed_kinds(transactions, privacy_share, seed):
    """The kind of each born transaction, a function of the seed alone.

    The privacy count is `round(share * transactions)` rather than drawn per
    birth, so the realised mix cannot drift from the label on a short run --
    though the rounding itself can: the caller validates the share and the
    run's record carries the realised count. Only the interleaving is
    random, from a dedicated stream.
    """
    privacy = int(round(transactions * privacy_share))
    kinds = [KIND_PRIVACY] * privacy + [KIND_PQ] * (transactions - privacy)
    random.Random(f"{seed}:mix").shuffle(kinds)
    return kinds


def calibrated_prover(anchor_ms, leanstark_factor):
    # Weights are cycles, not hashes; `anchored_on_pairwise_fold` divides the
    # anchor by a 2-to-1 fold's total, so the unit cancels. The infinite trace
    # cap records "no ceiling known" (docstring: the EIP's ceiling belongs to
    # a different stack), and rows-per-unit 1 records only that the unit is
    # a VM cycle -- with no cap it prices nothing.
    return CircuitCost.anchored_on_pairwise_fold(
        anchor_ms,
        sphincs_hashes=LEANVM_B_SPHINCS_CYCLES,
        leanstark_hashes=LEANVM_B_RECURSIVE_CYCLES * leanstark_factor,
        recursive_hashes=LEANVM_B_RECURSIVE_CYCLES,
        trace_rows_per_hash=1.0, trace_cap=math.inf)


def mean_root_claim(sim):
    """Mean transactions claimed per root aggregate.

    A root is an aggregate never taken as a fold input, so this is the
    width selection actually achieved: the tell for a binding proving
    budget (docs/pitfalls.md). Inputs-per-fold would not serve -- a binary
    plan sits at two by construction however wide its root's claim is.
    """
    claims = [sim.arena.claim(obj).bit_count()
              for obj in sim.arena.objects.values()
              if obj.kind == KIND_AGGREGATE and obj.consumed == 0]
    if not claims:
        return 0.0
    return statistics.fmean(claims)


def root_count(sim):
    """The terminal-DAG population used by `mean_root_claim`, at this instant."""
    count = 0
    for obj in sim.arena.objects.values():
        if obj.kind == KIND_AGGREGATE and obj.consumed == 0:
            count += 1
    return count


def workload_manifest(args, transactions, privacy_share, seed, entry_nodes):
    window = args.slots * args.slot_length
    times, phase = arrival_times(transactions, window, args.slot_length,
                                 args.arrivals, seed)
    kinds = mixed_kinds(transactions, privacy_share, seed)
    # Resolve the same legacy entry draws into literal records. The simulation
    # then consumes those records without making policy-dependent choices.
    entries = random.Random(f"{seed}:entry")
    births = []
    for index, kind in enumerate(kinds):
        node = entry_nodes[entries.randrange(len(entry_nodes))]
        births.append(Birth(times[index], kind, float(index % 5), None, node))
    return WorkloadManifest(tuple(births), args.arrivals, window,
                            args.slot_length, phase, tuple(entry_nodes),
                            window, transactions)


def one_run(args, name, transactions, privacy_share, leanstark_factor,
            anchor_ms, sig_ms, stark_ms, seed, manifest=None):
    overlay = random_regular_overlay(args.nodes, args.degree, args.latency,
                                     seed)
    settings = dict(seed=seed, capacity=bytes_per_ms(args.uplink_mbps),
                    size_model=SizeModel(),
                    costs=CostTable(
                        prove_recursive=calibrated_prover(anchor_ms,
                                                          leanstark_factor),
                        seed=seed),
                    verify_cost=NativeVerifyCost(sig_ms, stark_ms),
                    egress_queue_bytes=args.egress_bytes or None,
                    slot_length=args.slot_length)
    settings.update(configuration(name))
    settings["prove_cost_basis"] = "circuit"
    if args.proving_budget is not None:
        settings["proving_budget"] = args.proving_budget
    sim = NetworkSim(overlay, **settings)

    if manifest is None:
        manifest = workload_manifest(args, transactions, privacy_share,
                                     seed, sim.entry_nodes)
    if manifest.entry_nodes != sim.entry_nodes:
        raise ValueError("manifest and simulation require the same entry pool")
    window = manifest.window_ms
    # Sample zero before births at zero enter the calendar: a queued birth
    # would make that boundary incomplete, and its sends belong to the load.
    traffic_start = TrafficSample(sim, 0.0)
    manifest.schedule(sim)
    horizon = window + args.drain_slots * args.slot_length
    schedule_horizon(sim, horizon)
    load_start = BusySample(sim, 0.0)
    # Utilisation is read at the end of the birth window: read at the
    # horizon it would count the drain's idle time in its denominator and
    # fall as the drain grows (docs/pitfalls.md: a statistic can measure its
    # own procedure). Inclusion and delay are read after the drain.
    sim.run(until=window)
    cpu_load_window = sim.mean_cpu_utilisation()
    load_end = BusySample(sim, window)
    traffic_end = TrafficSample(sim, window)
    sim.run(until=horizon)

    metrics = sim.metrics()
    fixed_age_outcomes = -1
    if args.outcome_ages_ms:
        fixed_age_outcomes = []
        for age in args.outcome_ages_ms:
            comparison = compare_cohorts(sim, age, (0.0, window / 2),
                                         (window / 2, window))
            comparison["all"] = outcomes_at_age(sim, age, 0.0, window)
            fixed_age_outcomes.append(comparison)
    outcome = {
        "offered": metrics["offered_bytes"],
        "wire": metrics["wire_bytes"],
        "included": metrics["included_txs"],
        "arrived": metrics["arrived_txs"],
        "delay": metrics["delay_mean_slots"],
        "cpu_load_window": cpu_load_window,
        "proofs": metrics["proofs_completed"],
        "root_claim": mean_root_claim(sim),
        "root_count": root_count(sim),
        "fixed_age_outcomes": fixed_age_outcomes,
        "load_utilisation": utilisation_window(load_start, load_end),
        "load_traffic": traffic_window(traffic_start, traffic_end),
        "wire_copies": wire_copies(sim),
    }
    if args.diffusion:
        outcome["evidence_diffusion"] = [diffusion(sim, fraction)
                                         for fraction in (0.9, 1.0)]
        outcome["body_diffusion"] = [diffusion(sim, fraction, kind="body")
                                     for fraction in (0.9, 1.0)]
    return outcome, sim, manifest


def mean_of(runs, field):
    return statistics.fmean(run[field] for run in runs)


def floats(text):
    return [float(piece) for piece in text.split(",")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--degree", type=int, default=6)
    parser.add_argument("--uplink-mbps", type=float, default=25.0)
    parser.add_argument("--latency", type=float, default=100.0)
    parser.add_argument("--slot-length", type=float, default=12000.0,
                        help="ms per slot; mainnet-shaped is 12000")
    parser.add_argument("--slots", type=int, default=5,
                        help="slots over which transactions are born")
    parser.add_argument("--drain-slots", type=int, default=2,
                        help="quiet slots after the load, for inclusion")
    parser.add_argument("--tps", default="15",
                        help="transactions per second, one sweep value each")
    parser.add_argument("--privacy-share", default="0.0,0.2",
                        help="fraction of born transactions that are privacy "
                             "transactions, within [0, 1]; one sweep value "
                             "each")
    parser.add_argument("--leanstark-factor", default="1",
                        help="in-circuit weight of one leanSTARK witness as a "
                             "multiple of the nested-proof weight; an "
                             "unmeasured assumption, default band 1-2")
    parser.add_argument("--anchor-ms", default=str(LEANVM_B_PAIRWISE_MS),
                        help="measured ms for one 2-to-1 fold; the hardware "
                             "axis -- 417 is leanVM-b's Mac M4 Max, larger "
                             "stands for a slower machine")
    parser.add_argument("--verify-sig-ms", default="0.1",
                        help="native ms to verify one bare signature; "
                             "unmeasured, and a mode 0 wrapper multiplies it "
                             "by every signature it carries; one sweep value "
                             "each")
    parser.add_argument("--verify-stark-ms", default="15.6",
                        help="native ms to verify one STARK-class object; "
                             "extrapolated from leanVM-b's one measured "
                             "proof, 14.1-17.1 ms; one sweep value each")
    parser.add_argument("--proving-budget", type=float, default=None,
                        help="ms of proving per plan; default leaves the "
                             "model's one-aggregation-interval budget, which "
                             "binds on privacy-heavy plans -- root claim "
                             "width in the output is the tell")
    parser.add_argument("--egress-bytes", type=int, default=4_000_000,
                        help="send-buffer bound per node; 0 for unbounded")
    parser.add_argument("--presets", default="eip_faithful,high_throughput",
                        help="named configurations to compare, in order; "
                             "`name+field=value` holds a string override")
    parser.add_argument("--seeds", type=int, default=2)
    add_seed_start_argument(parser)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--diffusion", action="store_true",
                        help="include first evidence/body reach at fractions "
                             "0.9 and 1.0; requires --json")
    parser.add_argument("--arrivals", choices=ARRIVAL_PROFILES, default="uniform",
                        help="fixed-count arrival profile; default uniform")
    parser.add_argument("--emit-manifest", action="store_true",
                        help="include birth records in JSON workload metadata")
    parser.add_argument("--root-stats", action="store_true",
                        help="print the terminal-root count beside claim; "
                             "JSON always includes the count")
    parser.add_argument("--outcome-ages-ms", default="",
                        help="comma-separated nonnegative finite ages in ms "
                             "for fixed-age outcomes; requires --json; "
                             "does not extend the drain")
    args = parser.parse_args()
    if args.diffusion and not args.json:
        parser.error("--diffusion requires --json")
    if args.emit_manifest and not args.json:
        parser.error("--emit-manifest requires --json")
    try:
        if args.outcome_ages_ms:
            args.outcome_ages_ms = floats(args.outcome_ages_ms)
        else:
            args.outcome_ages_ms = []
    except ValueError:
        parser.error("--outcome-ages-ms requires comma-separated numbers")
    for age in args.outcome_ages_ms:
        if not math.isfinite(age) or age < 0:
            parser.error("--outcome-ages-ms requires finite nonnegative ages")
    if args.outcome_ages_ms and not args.json:
        parser.error("--outcome-ages-ms requires --json")

    rates = []
    for tps in floats(args.tps):
        transactions = int(round(tps * args.slots * args.slot_length / 1000.0))
        if transactions < 1:
            parser.error(f"--tps {tps:g}: rounds to zero transactions over "
                         f"{args.slots} slots of {args.slot_length:g} ms")
        rates.append((tps, transactions))
    shares = floats(args.privacy_share)
    for share in shares:
        if not 0.0 <= share <= 1.0:
            parser.error(f"--privacy-share {share:g}: a share lies in [0, 1]")
    factors = floats(args.leanstark_factor)
    anchors = floats(args.anchor_ms)
    sig_prices = floats(args.verify_sig_ms)
    stark_prices = floats(args.verify_stark_ms)
    presets = args.presets.split(",")
    for name in presets:
        fields = configuration(name)  # refuse a typo before spending minutes
        for field in OWNED_FIELDS:
            if field in fields:
                parser.error(
                    f"--presets {name}: sets {field}, which this bench "
                    "derives from its own flags; the run's record could not "
                    "attribute it")

    results = {}
    for tps, transactions in rates:
        for share in shares:
            manifests = {}
            privacy_born = int(round(transactions * share))
            for factor in factors:
                for anchor in anchors:
                    for sig_ms in sig_prices:
                        for stark_ms in stark_prices:
                            for name in presets:
                                runs = []
                                for seed in seed_range(args):
                                    entry_subset = configuration(name).get(
                                        "entry_subset")
                                    key = (seed, entry_subset)
                                    outcome, sim, manifest = one_run(
                                        args, name, transactions, share,
                                        factor, anchor, sig_ms, stark_ms,
                                        seed, manifests.get(key))
                                    manifests[key] = manifest
                                    runs.append(outcome)
                                    if args.json:
                                        workload = workload_parameters(
                                            args, preset=name, tps=tps,
                                            transactions=transactions,
                                            privacy_share=share,
                                            privacy_born=privacy_born,
                                            leanstark_factor=factor,
                                            anchor_ms=anchor,
                                            verify_sig_ms=sig_ms,
                                            verify_stark_ms=stark_ms,
                                            fee_model="cycle5")
                                        # Presentation cannot distinguish two
                                        # otherwise identical workload records.
                                        workload.pop("root_stats", None)
                                        workload.pop("emit_manifest", None)
                                        workload.pop("diffusion", None)
                                        workload.update(manifest.metadata())
                                        if args.emit_manifest:
                                            workload["manifest"] = (
                                                manifest.to_record())
                                        emit_json_record(
                                            "mainnet",
                                            workload,
                                            seed, sim, outcome)
                                results[(tps, share, factor, anchor, sig_ms,
                                         stark_ms, name)] = runs
    if args.json:
        return 0

    if args.arrivals != "uniform":
        print(f"arrivals={args.arrivals} (fixed-count profile)")

    budget_note = (f"{args.proving_budget:g} ms" if args.proving_budget
                   is not None else "one aggregation interval (default)")
    egress_note = (f"{args.egress_bytes / 1e6:g} MB" if args.egress_bytes
                   else "unbounded")
    print(f"nodes={args.nodes} degree={args.degree} "
          f"uplink={args.uplink_mbps:.0f} Mbit/s latency={args.latency:.0f} ms "
          f"slot={args.slot_length / 1000:g} s, born over {args.slots} slots, "
          f"{args.drain_slots} drain, seeds={args.seeds}")
    print(f"circuit weights (leanVM-b cycles): signature~10930 nested~373250; "
          f"leanSTARK witness = factor x nested, unmeasured")
    print(f"egress bound {egress_note} per node, proving budget {budget_note}, "
          f"fees cycle 0-4")
    print(f"native verify swept: sig {args.verify_sig_ms} ms, "
          f"STARK-class {args.verify_stark_ms} ms (leanVM-b measures "
          f"14.1-17.1 for one proof size); no block messages under "
          f"instant notify")
    print()
    width = max(len(name) for name in presets)
    root_header = ""
    if args.root_stats:
        root_header = f" {'roots':>7}"
    print_table_header(
        f"{'tx/s':>5} {'privacy':>7} {'lstark':>6} {'anchor':>6} "
        f"{'vsig':>5} {'vstk':>5} "
        f"{'preset':>{width}} {'offered MB':>11} {'included':>12} "
        f"{'delay':>6} {'cpu%':>5} {'claim':>6}{root_header} {'proofs':>7}")
    for tps, transactions in rates:
        for share in shares:
            for factor in factors:
                for anchor in anchors:
                    for sig_ms in sig_prices:
                        for stark_ms in stark_prices:
                            for index, name in enumerate(presets):
                                runs = results[(tps, share, factor, anchor,
                                                sig_ms, stark_ms, name)]
                                root_cell = ""
                                if args.root_stats:
                                    root_cell = (
                                        f" {mean_of(runs, 'root_count'):>7.1f}")
                                lead = (f"{tps:>5g} {share:>7.2f} "
                                        f"{factor:>6g} {anchor:>6g} "
                                        f"{sig_ms:>5g} {stark_ms:>5g}"
                                        ) if index == 0 else " " * 39
                                print(f"{lead} {name:>{width}} "
                              f"{mean_of(runs, 'offered') / 1e6:>11.1f} "
                              f"{mean_of(runs, 'included'):>6.1f}/"
                              f"{mean_of(runs, 'arrived'):<5.0f} "
                              f"{mean_of(runs, 'delay'):>6.2f} "
                              f"{mean_of(runs, 'cpu_load_window') * 100:>5.1f} "
                              f"{mean_of(runs, 'root_claim'):>6.1f}{root_cell} "
                              f"{mean_of(runs, 'proofs'):>7.0f}")
    print()
    print("included is counted after the drain; delay averages included")
    print("transactions only. cpu% is utilisation over the birth window,")
    print("read before the drain. claim is mean transactions claimed per")
    print("root aggregate (one never folded further): near two when the")
    print("proving budget binds selection, whatever the plan shape. Cells")
    print("are means over seeds; publication evidence is the --json records.")
    print("No trace ceiling is modelled, and the leanSTARK weight is an")
    print("assumption swept over its band, not a measurement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
