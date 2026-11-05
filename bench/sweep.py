#!/usr/bin/env python3
"""
Parameter sweeps: one CSV row per configuration.

The simulator exists to compare design options, so the harness that varies them
is part of it rather than something a reader is left to write. Each row carries
its own variant identity, so a sweep's output is interpretable without the
command that produced it.

    python -m bench.sweep --axis fold_plan --values wide,binary
    python -m bench.sweep --axis relay_policy --values flood,announce_fetch --seeds 3
    python -m bench.sweep --axis proposer_mode --values fixed,rotating --csv out.csv

One axis varies; everything else is held. `--set key=value` holds a *different*
value than the default for the run, validated against the same names as the axes
so a typo cannot quietly become a row that describes a configuration nobody ran:

    python -m bench.sweep --axis wrapper_mode --values recursive,cheapest \
        --set relay_policy=flood

`docs/configuration.md` lists every field a run can set, with its default;
the axes below are the subset this harness varies.

`--prove-cost` is a flat number of milliseconds per recursive proving job,
independent of dependency or input count. This differs from economy's
pairwise-fold anchor and triage's cost per dependency.
`--prove-cost-circuit` replaces it with a cost priced by what the circuit
verifies, calibrated on one measured pairwise fold in milliseconds, and selects
`prove_cost_basis="circuit"` with it.

`--seed-start` defaults to zero; `--seeds` counts consecutive absolute seeds
from that start without changing their random streams.

For paired clocked/unclocked cells, pass --birth-horizon-ms and
--observation-horizon-ms together. Births span the first window; whole tick
and slot periods are scheduled over the second, with no ticks for an unclocked
trigger. These horizons replace --ticks and --slots. A when_ready cell also
requires --set proving_budget=MS (or a proving_budget axis). Clocked cells
without explicit horizons retain their legacy counts and drain endpoint.
JSON workload.schedule records the derivation and actual observation time.

Size axes use size_FIELD names and construct a SizeModel. Workload axes
transactions, kind, fee and lifetime affect births, not simulation variants;
the default remains PQ births with the index-modulo-five fee cycle and no
expiry. New optional numeric axes accept 'none' to remove a bound.
"""

import argparse
import math
import sys

from bench.common import (add_seed_start_argument, seed_range,
                          emit_json_record, random_regular_overlay,
                          schedule_horizon, workload_parameters)
from starksim.arena import KIND_PQ
from starksim.costs import CircuitCost, CostTable
from starksim.netsim import CLOCKED_TRIGGERS, NetworkSim, csv_header, csv_row
from starksim.network import SizeModel, bytes_per_ms


def optional_int(value):
    if value.lower() == "none":
        return None
    return int(value)


def optional_float(value):
    if value.lower() == "none":
        return None
    return float(value)


def boolean(value):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    raise ValueError("expected true or false")


# Axes a sweep may vary. Named rather than free-form: a typo in a keyword would
# otherwise be silently accepted by **kwargs and produce a row that looks like a
# result for a configuration that was never run.
AXES = {
    "relay_policy": str,
    "aggregator_share": float,
    "aggregator_placement": str,
    "relay_forwarding": str,
    "proposer_role": str,
    "chain_notify": str,
    "proposer_mode": str,
    "selection_strategy": str,
    "fold_plan": str,
    "supersede_policy": str,
    "eviction_policy": str,
    "wrapper_economy": str,
    "inclusion_policy": str,
    "cpu_discipline": str,
    "prove_cost_basis": str,
    "on_tick_overrun": str,
    "block_budget": int,
    "entry_subset": int,
    "aggregation_interval": float,
    "slot_length": float,
    "wrapper_mode": str,
    "fold_threshold": str,
    "aggregation_trigger": str,
    "proving_budget": float,
    "fold_amortize": float,
    "egress_queue_bytes": optional_int,
    "cpu_queue_jobs": optional_int,
    "seen_limit": optional_int,
    "candidate_limit": optional_int,
    "coverage_cache_limit": optional_int,
    "request_timeout": optional_float,
    "capacity": optional_float,
    "link_capacity": optional_float,
    "pin_own_aggregate": boolean,
    "proposer_id": int,
    "size_triple": int,
    "size_hash_ref": int,
    "size_sig_witness": int,
    "size_stark_witness": int,
    "size_recursive_proof": int,
    "size_tx_body": int,
    "size_announce_overhead": int,
    "transactions": int,
    "kind": str,
    "fee": optional_float,
    "lifetime": optional_float,
}


def one_run(nodes, degree, latency, seed, transactions, ticks, slots,
            uplink_mbps, prove_cost, circuit_anchor=None,
            birth_horizon_ms=None, observation_horizon_ms=None,
            kind=KIND_PQ, fee=None, lifetime=None, schedule_record=None,
            **overrides):
    for name, value in (("latency", latency), ("prove_cost", prove_cost),
                        ("uplink_mbps", uplink_mbps)):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if uplink_mbps == 0:
        raise ValueError("uplink_mbps must be positive; use capacity=none "
                         "for unmetered egress")
    if circuit_anchor is not None and (
            not math.isfinite(circuit_anchor) or circuit_anchor < 0):
        raise ValueError("circuit anchor must be finite and nonnegative")
    explicit = observation_horizon_ms is not None
    if explicit != (birth_horizon_ms is not None):
        raise ValueError("pass --birth-horizon-ms and --observation-horizon-ms "
                         "together")
    if explicit:
        for name, value in (("birth horizon", birth_horizon_ms),
                            ("observation horizon", observation_horizon_ms)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if observation_horizon_ms < birth_horizon_ms:
            raise ValueError("observation horizon must cover the birth horizon")
    trigger = overrides.get("aggregation_trigger", "spread")
    clocked = trigger in CLOCKED_TRIGGERS
    if not clocked:
        if not explicit:
            raise ValueError("unclocked triggers require --birth-horizon-ms "
                             "and --observation-horizon-ms")
        if overrides.get("proving_budget") is None:
            raise ValueError("unclocked triggers require an explicit "
                             "proving_budget: use --set proving_budget=MS")
        for setting in ("aggregation_interval", "on_tick_overrun"):
            if setting in overrides:
                raise ValueError(f"{setting} cannot affect an unclocked cell")
    if transactions < 0 or ticks < 0 or slots < 0:
        raise ValueError("transactions, ticks and slots must be nonnegative")
    if not 0 <= overrides.get("proposer_id", 0) < nodes:
        raise ValueError("proposer_id must name a node in the overlay")
    if kind not in ("q", "p"):
        raise ValueError("kind must be q or p")
    if lifetime is not None and (not math.isfinite(lifetime) or lifetime < 0):
        raise ValueError("lifetime must be finite and nonnegative, or none")
    if fee is not None and not math.isfinite(fee):
        raise ValueError("fee must be finite, or none for the default cycle")
    for name, value in overrides.items():
        if value is None or isinstance(value, str):
            continue
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        if name.startswith("size_") or name.endswith("_limit") or name in (
                "egress_queue_bytes", "cpu_queue_jobs", "proving_budget"):
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if name in ("capacity", "link_capacity", "request_timeout",
                    "slot_length", "aggregation_interval") and value <= 0:
            raise ValueError(f"{name} must be positive")
    if not explicit and overrides.get("request_timeout") is not None:
        raise ValueError("request retries require explicit birth and observation "
                         "horizons; an unanswered request may never drain")
    overlay = random_regular_overlay(nodes, degree, latency, seed)
    if circuit_anchor is None:
        prover = prove_cost
        basis = {}
    else:
        # Calibrated on one measured 2-to-1 fold, which is the only
        # millisecond measurement this project has to attach a scale to.
        prover = CircuitCost.anchored_on_pairwise_fold(circuit_anchor)
        basis = {"prove_cost_basis": "circuit"}
    settings = dict(seed=seed, capacity=bytes_per_ms(uplink_mbps),
                    costs=CostTable(verify_recv=2.0, prove_recursive=prover,
                                    seed=seed),
                    slot_length=2000.0,
                    proposer_id=0, block_budget=5)
    if clocked:
        settings["aggregation_interval"] = 500.0
    settings.update(basis)
    sizes = {}
    for name, value in overrides.items():
        if name.startswith("size_"):
            sizes[name.removeprefix("size_")] = value
        else:
            settings[name] = value
    if sizes:
        settings["size_model"] = SizeModel(**sizes)
    sim = NetworkSim(overlay, **settings)
    sim.workload_generator_version = "legacy:sweep:v1"
    if birth_horizon_ms is None:
        birth_horizon_ms = settings["slot_length"] * slots
    if not math.isfinite(birth_horizon_ms):
        raise ValueError("derived birth horizon must be finite")
    spacing = birth_horizon_ms / max(1, transactions)
    for index in range(transactions):
        birth_fee = fee
        if birth_fee is None:
            birth_fee = float(index % 5)
        sim.schedule_birth(index * spacing, kind, fee=birth_fee,
                           lifetime=lifetime)
    if explicit:
        counts = schedule_horizon(sim, observation_horizon_ms)
        sim.run(until=observation_horizon_ms)
    else:
        sim.schedule_ticks(ticks)
        sim.schedule_slots(slots)
        counts = {"tick_count": ticks, "slot_count": slots}
        sim.run()
    if schedule_record is not None:
        schedule_record.update(counts)
        schedule_record.update({
            "mode": "clocked" if clocked else "unclocked",
            "birth_horizon_ms": birth_horizon_ms,
            "birth_horizon_source": "explicit" if explicit else "slots",
            "observation_horizon_ms": observation_horizon_ms,
            "observation_horizon_source": (
                "explicit" if explicit else "calendar_drain"),
            "observed_until_ms": sim.engine.now,
            "tick_count_source": "none",
            "slot_count_source": "observation_horizon" if explicit else "slots",
            "proving_budget_ms": sim.proving_budget,
            "proving_budget_source": "explicit" if "proving_budget" in overrides
            else "aggregation_interval",
            "transactions": transactions, "kind": kind, "fee": fee,
            "lifetime_ms": lifetime,
        })
        if clocked:
            schedule_record["tick_count_source"] = (
                "observation_horizon" if explicit else "ticks")
    return sim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axis", required=True, choices=sorted(AXES))
    parser.add_argument("--values", required=True,
                        help="comma-separated values for the axis")
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--degree", type=int, default=4)
    parser.add_argument("--latency", type=float, default=100.0)
    parser.add_argument("--uplink-mbps", type=float, default=50.0)
    parser.add_argument("--prove-cost", type=float, default=30.0,
                        help="flat milliseconds per recursive proving job")
    parser.add_argument("--prove-cost-circuit", type=float, default=None,
                        metavar="PAIRWISE_MS",
                        help="price proving by what the circuit verifies, "
                             "calibrated on one measured 2-to-1 fold in ms; "
                             "implies prove_cost_basis=circuit")
    parser.add_argument("--set", dest="held", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="hold a non-swept setting at VALUE; repeatable")
    parser.add_argument("--transactions", type=int, default=120)
    parser.add_argument("--ticks", type=int, default=None,
                        help="legacy clocked rounds (default 24)")
    parser.add_argument("--slots", type=int, default=None,
                        help="legacy birth-window and block slots (default 6)")
    parser.add_argument("--birth-horizon-ms", type=float, default=None,
                        help="explicit half-open birth window in simulated ms")
    parser.add_argument("--observation-horizon-ms", type=float, default=None,
                        help="explicit observation endpoint in simulated ms; "
                             "derives tick/slot counts instead of --ticks/--slots")
    parser.add_argument("--seeds", type=int, default=2)
    add_seed_start_argument(parser)
    parser.add_argument("--csv", type=str, default=None,
                        help="write rows here instead of stdout")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON record per seed and axis value")
    args = parser.parse_args()

    if args.seeds < 1:
        parser.error("--seeds must be positive")

    if args.birth_horizon_ms is not None or args.observation_horizon_ms is not None:
        if args.ticks is not None or args.slots is not None:
            parser.error("explicit horizons replace --ticks and --slots")
    if args.ticks is None:
        args.ticks = 24
    if args.slots is None:
        args.slots = 6

    if args.csv and args.json:
        parser.error("--csv and --json are mutually exclusive")
    convert = AXES[args.axis]
    try:
        values = [convert(piece) for piece in args.values.split(",")]
    except ValueError as error:
        parser.error(f"--values for {args.axis}: {error}")

    held = {}
    for assignment in args.held:
        name, _, raw = assignment.partition("=")
        if not _:
            parser.error(f"--set wants KEY=VALUE, got {assignment!r}")
        if name not in AXES:
            parser.error(f"--set {name}: not a known setting; one of "
                         f"{', '.join(sorted(AXES))}")
        if name == args.axis:
            parser.error(f"--set {name}: that is the swept axis")
        try:
            held[name] = AXES[name](raw)
        except ValueError as error:
            parser.error(f"--set {name}: {error}")

    if args.json:
        stream = None
    elif args.csv:
        stream = open(args.csv, "w", buffering=1)
    else:
        stream = sys.stdout
    if stream is not None:
        stream.write(csv_header() + "\n")
    for value in values:
        for seed in seed_range(args):
            schedule = {}
            settings = dict(nodes=args.nodes, degree=args.degree,
                            latency=args.latency, seed=seed,
                            transactions=args.transactions, ticks=args.ticks,
                            slots=args.slots, uplink_mbps=args.uplink_mbps,
                            prove_cost=args.prove_cost,
                            circuit_anchor=args.prove_cost_circuit,
                            birth_horizon_ms=args.birth_horizon_ms,
                            observation_horizon_ms=args.observation_horizon_ms)
            settings.update(held)
            settings[args.axis] = value
            try:
                sim = one_run(**settings, schedule_record=schedule)
            except (ValueError, TypeError) as error:
                parser.error(f"{args.axis}={value}: {error}")
            if args.json:
                workload = workload_parameters(
                    args, axis=args.axis, axis_value=value, held=held,
                    transactions=schedule["transactions"], schedule=schedule)
                emit_json_record("sweep", workload, seed, sim)
            else:
                stream.write(csv_row(sim.metrics()) + "\n")
    if args.csv:
        stream.close()
        print(f"wrote {len(values) * args.seeds} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
