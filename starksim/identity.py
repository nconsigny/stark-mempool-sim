"""Read-only run identity; no cost probes, random draws or scheduled events."""

from copy import copy
import hashlib
import json
import math
from pathlib import Path
import re

from starksim.costs import CircuitCost, CostTable, LinearCost, NativeVerifyCost
from starksim.network import UniformLatency
from starksim.trace import SCHEMA_VERSION


# Stable per run, so they may join the CSV row. The commit id is not: it
# changes with every commit, documentation included, and would break
# byte-for-byte reproduction. It lives in the JSON identity block only.
IDENTITY_COLUMNS = ("topology_fingerprint", "workload_generator_version")


def code_version(root=None):
    """Read HEAD without invoking git; unreadable or unsupported metadata is -1.

    A commit id does not attest that the working tree is clean. Linked worktrees
    resolve HEAD locally and shared refs through commondir; archives have no id.
    """
    if root is None:
        root = Path(__file__).resolve().parent.parent
    try:
        git_dir = Path(root) / ".git"
        if git_dir.is_file():
            pointer = git_dir.read_text().strip()
            if not pointer.startswith("gitdir: "):
                return -1
            git_dir = (Path(root) / pointer[8:]).resolve()
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            parts = Path(ref).parts
            if not ref.startswith("refs/") or ".." in parts:
                return -1
            common_dir = git_dir
            common_file = git_dir / "commondir"
            if common_file.is_file():
                common_dir = (git_dir / common_file.read_text().strip()).resolve()
            value = None
            for directory in (git_dir, common_dir):
                ref_file = directory / ref
                if ref_file.is_file():
                    value = ref_file.read_text().strip()
                    break
            if value is None:
                packed = common_dir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text().splitlines():
                        fields = line.split()
                        if len(fields) == 2 and fields[1] == ref:
                            value = fields[0]
                            break
            head = value
        if head is not None and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head):
            return head.lower()
    except (OSError, ValueError, UnicodeError):
        pass
    return -1


def topology_fingerprint(overlay):
    """SHA-256 of ordered directed adjacency rows and realised edge latencies.

    UniformLatency is pure except for memoisation; a private copy avoids warming
    the run's cache. Unknown latency implementations are not called: they may
    consume randomness or have stateful effects, so their fingerprint is -1.
    Hashing streams rows and does not retain another full graph representation.
    """
    if type(overlay.latency_model) is not UniformLatency:
        return -1
    latency = copy(overlay.latency_model)
    latency._by_edge = dict(latency._by_edge)
    digest = hashlib.sha256(b"starksim-topology-v1\n")
    digest.update(f"{overlay.node_count}\n".encode("ascii"))
    for node, peers in enumerate(overlay.adjacency):
        row = []
        for peer in sorted(peers):
            value = float(latency.latency(node, peer))
            if value == 0:
                value = 0.0
            row.append([peer, value.hex()])
        digest.update(json.dumps([node, row], separators=(",", ":")).encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def cost_value_profile(value):
    if type(value) is CircuitCost:
        parameters = {field: getattr(value, field)
                      for field in CircuitCost.__slots__}
        # JSON has no infinity; the emitter would turn one into null anyway.
        # Say null here so the profile equals its own JSON round trip.
        if not math.isfinite(parameters["trace_cap"]):
            parameters["trace_cap"] = None
        return {"type": "circuit", "parameters": parameters}
    if type(value) is LinearCost:
        return {"type": "linear", "per_unit_ms": value.per_unit}
    if isinstance(value, (int, float)):
        return {"type": "constant", "ms": value}
    # Probing arbitrary callables can mutate their state, and two probes cannot
    # identify a function. Unavailable is not a claim of an equivalent profile.
    return {"type": "unavailable", "parameters": -1}


def cost_table_profile(table):
    return {"costs": {kind: cost_value_profile(value)
                      for kind, value in table.costs.items()},
            "jitter_frac": table.jitter_frac, "seed": table.seed,
            "prove_cost_basis": table.prove_cost_basis}


def restore_cost_table(profile):
    """Restore construction parameters, not an advanced random-stream state."""
    costs = {}
    for kind, item in profile["costs"].items():
        if item["type"] == "constant":
            costs[kind] = item["ms"]
        elif item["type"] == "linear":
            costs[kind] = LinearCost(item["per_unit_ms"])
        elif item["type"] == "circuit":
            parameters = dict(item["parameters"])
            if parameters.get("trace_cap") is None:
                parameters["trace_cap"] = math.inf
            costs[kind] = CircuitCost(**parameters)
        else:
            raise ValueError("cannot restore an unavailable cost profile")
    table = CostTable(**costs, jitter_frac=profile["jitter_frac"],
                      seed=profile["seed"])
    table.prove_cost_basis = profile["prove_cost_basis"]
    return table


def run_identity(sim, workload, benchmark):
    """Describe constructed settings and workload metadata without changing them."""
    version = sim.workload_generator_version
    if version == -1:
        version = f"legacy:{benchmark}:v1"
    if "manifest_version" in workload:
        version = workload["manifest_version"]
        if sim.workload_generator_version not in (-1, version):
            raise ValueError("workload manifest version disagrees with the run")
    table = cost_table_profile(sim.costs)
    selection_table = cost_table_profile(sim._selection_costs)
    native = -1
    if type(sim.verify_cost) is NativeVerifyCost:
        native = {field: getattr(sim.verify_cost, field)
                  for field in NativeVerifyCost.__slots__}
    complete = all(item["type"] != "unavailable"
                   for item in table["costs"].values())
    if any(item["type"] == "unavailable"
           for item in selection_table["costs"].values()):
        complete = False
    if sim.verify_cost is not None and native == -1:
        complete = False
    proposer = sim.proposers
    dedicated = -1
    if proposer.mode == "dedicated":
        dedicated = proposer.fixed_id
    return {
        "topology_fingerprint": sim.topology_fingerprint,
        "workload_generator_version": version,
        "simulator_code_version": code_version(),
        "trace_schema_version": SCHEMA_VERSION,
        "cost_profile": {"table": table, "native_verify": native,
                         "selection_table": selection_table,
                         "execution_basis": sim.prove_cost_basis,
                         "complete": complete},
        "node_assignment": {
            "entry_nodes": list(sim.entry_nodes),
            "proposer": {"mode": proposer.mode, "fixed_id": proposer.fixed_id,
                         "node_count": proposer.node_count, "seed": proposer.seed,
                         "schedule_version": 1},
            "dedicated_builder": dedicated,
        },
    }
