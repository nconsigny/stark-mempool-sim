"""Shared mechanics for benchmark harnesses.

Workload construction and interpretation stay in each benchmark. This module
contains only contracts repeated across harnesses: topology derivation, simple
summary operations, schedule alignment, table rules, and JSONL envelopes.
"""

import argparse
import json
import math
import random
import statistics

from starksim.presets import names as preset_names, preset
from starksim.network import Overlay
from starksim.netsim import CLOCKED_TRIGGERS
from starksim.identity import IDENTITY_COLUMNS, run_identity


def random_regular_overlay(nodes, degree, latency, seed, jitter_frac=0.1):
    """Build the benchmark topology without changing its random stream.

    The labelled seed and the separate latency seed are part of reproducibility:
    topology construction consumes only ``"{seed}:topology"`` while edge
    latency jitter is identity-derived from ``seed``.
    """
    topology_rng = random.Random(f"{seed}:topology")
    return Overlay.random_regular(
        nodes, degree, topology_rng, base_latency=latency,
        jitter_frac=jitter_frac, latency_seed=seed)


def mean_field(runs, key):
    """Arithmetic mean of one top-level field across run summaries."""
    return statistics.fmean(run[key] for run in runs)


def mean_path(runs, *path):
    """Arithmetic mean of one nested field path across run summaries."""
    values = []
    for run in runs:
        value = run
        for key in path:
            value = value[key]
        values.append(value)
    return statistics.fmean(values)


def configuration(spec):
    """`name` or `name+field=value...`: a preset with overrides applied.

    Override values are passed on as strings; a field `NetworkSim` coerces,
    such as `slot_length`, accepts a numeric string too, so the syntax is not
    limited to policy names. A bench that owns some fields itself must check
    the returned dict rather than rely on the syntax being too weak to
    conflict.
    """
    name, *overrides = spec.split("+")
    settings = preset(name)
    for override in overrides:
        field, value = override.split("=", 1)
        settings[field] = value
    return settings


def add_preset_argument(parser):
    """`--preset NAME`: start from a named configuration."""
    parser.add_argument("--preset", default=None, choices=preset_names(),
                        help="start from a named configuration "
                             "(starksim.presets); explicit policy flags "
                             "still win over it")


def _seed_start(value):
    start = int(value)
    if start < 0:
        raise argparse.ArgumentTypeError("seed start must be nonnegative")
    return start


def add_seed_start_argument(parser):
    """Select absolute seeds; the start is recorded in every JSON workload."""
    parser.add_argument("--seed-start", type=_seed_start, default=0,
                        help="first absolute seed; run --seeds consecutive "
                             "seeds starting here")


def seed_range(args):
    """The same per-seed experiments, over a half-open absolute seed range."""
    return range(args.seed_start, args.seed_start + args.seeds)


def preset_settings(args):
    """The keyword arguments a chosen preset contributes, or none."""
    return preset(args.preset) if getattr(args, "preset", None) else {}


def print_table_header(header):
    """Print a table heading followed by a width-matched ASCII rule."""
    print(header)
    print("-" * len(header))


def schedule_horizon(sim, horizon):
    """Schedule period counts over a duration; unclocked nodes need no ticks.

    Spread offsets can put the final tick past the duration. The caller's
    bounded run decides the observation endpoint, not the scheduling helper.
    """
    if not math.isfinite(horizon) or horizon < 0:
        raise ValueError("schedule horizon must be finite and nonnegative")
    if not math.isfinite(sim.slot_length) or sim.slot_length <= 0:
        raise ValueError("slot_length must be finite and positive")
    ticks = 0
    if sim.aggregation_trigger in CLOCKED_TRIGGERS:
        if (not math.isfinite(sim.aggregation_interval)
                or sim.aggregation_interval <= 0):
            raise ValueError("aggregation_interval must be finite and positive")
        ticks = int(horizon / sim.aggregation_interval)
        sim.schedule_ticks(ticks)
    slots = int(horizon / sim.slot_length)
    sim.schedule_slots(slots)
    return {"tick_count": ticks, "slot_count": slots}


def workload_parameters(args, **specific):
    """JSON-ready per-run workload settings from an argparse namespace."""
    workload = vars(args).copy()
    for output_field in ("json", "csv", "seeds"):
        workload.pop(output_field, None)
    workload.update(specific)
    return workload


def emit_json_record(benchmark, workload, seed, sim, metrics=None):
    """Write one strict-JSON record for one completed simulation run."""
    run_metrics = sim.metrics()
    if metrics is not None:
        run_metrics.update(metrics)
    identity = run_identity(sim, workload, benchmark)
    for field in IDENTITY_COLUMNS:
        run_metrics[field] = identity[field]
    record = {
        "benchmark": benchmark,
        "workload": workload,
        "seed": seed,
        "variant": sim.variant(),
        "metrics": run_metrics,
        "identity": identity,
    }
    print(json.dumps(_json_value(record), sort_keys=True,
                     separators=(",", ":"), allow_nan=False))


def _json_value(value):
    """Replace non-finite floats, which JSON cannot represent, with null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
