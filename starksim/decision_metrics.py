"""Bounded observations of aggregation decisions and their completion."""

import copy

from starksim.statistics import Moments, POWER_MINIMUMS


DEADLINE_COUNTS = (
    "submitted", "completed_within_interval", "completed_late",
    "terminal_without_completion", "cancelled", "refused_queue",
    "refused_over_capacity", "completed_without_emission",
)
LATENCIES = ("decision_to_start_ms", "decision_to_root_ms",
             "decision_to_emission_ms")
TIERS = ("previous_root", "peer_aggregates", "fee_leaves")
STOP_REASONS = ("none", "peer_aggregates", "fee_leaves", "both")


class PlanDeadlines:
    """O(nodes x buckets + outstanding plans), with no completed-plan log.

    Only outstanding plans retain a decision timestamp. A terminal failure
    cannot later become a completion when an internal job finishes. Latency
    samples condition on actually reaching their respective milestones.
    """

    def __init__(self, nodes, interval):
        self.interval = interval
        self.counts = [dict.fromkeys(DEADLINE_COUNTS, 0) for _ in range(nodes)]
        self.latencies = [{name: Moments() for name in LATENCIES}
                          for _ in range(nodes)]
        self.outstanding = {}

    def submit(self, run):
        if self.interval is None:
            return
        self.counts[run.node_id]["submitted"] += 1
        self.outstanding[run] = False

    def start(self, run, now):
        if run not in self.outstanding or self.outstanding[run]:
            return
        self.outstanding[run] = True
        self.latencies[run.node_id]["decision_to_start_ms"].record(
            now - run.decision_ms)

    def finish(self, run, outcome, now, emitted=False):
        if run not in self.outstanding:
            return
        del self.outstanding[run]
        counts = self.counts[run.node_id]
        if outcome != "completed":
            counts["terminal_without_completion"] += 1
            counts[outcome] += 1
            return
        elapsed = now - run.decision_ms
        if now <= run.decision_ms + self.interval:
            counts["completed_within_interval"] += 1
        else:
            counts["completed_late"] += 1
        self.latencies[run.node_id]["decision_to_root_ms"].record(elapsed)
        if emitted:
            self.latencies[run.node_id]["decision_to_emission_ms"].record(elapsed)
        else:
            counts["completed_without_emission"] += 1

    def metrics(self, now):
        if self.interval is None:
            return -1
        rows = []
        for counts in self.counts:
            rows.append(dict(counts, still_outstanding=0,
                             outstanding_censored=0, outstanding_overdue=0))
        for run in self.outstanding:
            row = rows[run.node_id]
            row["still_outstanding"] += 1
            if now <= run.decision_ms + self.interval:
                row["outstanding_censored"] += 1
            else:
                row["outstanding_overdue"] += 1
        total = dict.fromkeys(DEADLINE_COUNTS + (
            "still_outstanding", "outstanding_censored", "outstanding_overdue"), 0)
        latencies = {name: Moments() for name in LATENCIES}
        for row, samples in zip(rows, self.latencies):
            for name, count in row.items():
                total[name] += count
            for name, moments in samples.items():
                latencies[name].merge(moments)
                row[name] = moments.record_dict()
        for name, moments in latencies.items():
            total[name] = moments.record_dict()
        return {"observed_at_ms": now, "interval_ms": self.interval,
                "power_minimums": list(POWER_MINIMUMS),
                "total": total, "by_node": rows}


class SelectionObservation:
    """Ephemeral masks from one selector call; no masks enter retained samples."""

    def __init__(self):
        self.eligible = 0
        self.leaves = bytearray()
        self.tiers = {tier: {"objects": 0, "marginal_live_txs": 0}
                      for tier in TIERS}
        self.peer_budget_stop = False
        self.leaf_budget_stop = False

    def leaf(self, index):
        byte = index // 8
        if byte >= len(self.leaves):
            self.leaves.extend(b"\0" * (byte + 1 - len(self.leaves)))
        self.leaves[byte] |= 1 << (index % 8)

    def take(self, tier, live_claim, selected):
        self.tiers[tier]["objects"] += 1
        self.tiers[tier]["marginal_live_txs"] += (
            live_claim & ~selected).bit_count()


class SelectionDecisions:
    """Fixed per-node summaries plus the first sample_limit diagnostic rows.

    Diagnostics are a bounded prefix, not a random or representative sample.
    Storage is O(nodes x buckets + sample_limit); each row has fixed fields.
    The selector supplies already-read claims and leaf identities, never
    extra scores, cost evaluations or arena provenance reads.
    """

    def __init__(self, nodes, sample_limit=0):
        if not isinstance(sample_limit, int) or sample_limit < 0:
            raise ValueError("sample_limit must be a nonnegative integer")
        self.sample_limit = sample_limit
        self.samples = []
        self.nodes = []
        for _ in range(nodes):
            self.nodes.append({
                "decisions": 0,
                "tiers": {tier: {"objects": 0, "marginal_live_txs": 0}
                          for tier in TIERS},
                "budget_stops": dict.fromkeys(STOP_REASONS, 0),
                "selected_live_union": Moments(),
                "remaining_eligible_union": Moments(),
            })

    def record(self, node_id, tick, observation, claimed):
        reason = "none"
        if observation.peer_budget_stop and observation.leaf_budget_stop:
            reason = "both"
        elif observation.peer_budget_stop:
            reason = "peer_aggregates"
        elif observation.leaf_budget_stop:
            reason = "fee_leaves"
        remaining = 0
        if reason != "none":
            remaining = (observation.eligible & ~claimed).bit_count()
        row = self.nodes[node_id]
        row["decisions"] += 1
        row["budget_stops"][reason] += 1
        for tier, values in observation.tiers.items():
            for key, count in values.items():
                row["tiers"][tier][key] += count
        row["selected_live_union"].record(claimed.bit_count())
        row["remaining_eligible_union"].record(remaining)
        if len(self.samples) < self.sample_limit:
            self.samples.append({
                "node": node_id, "tick": tick,
                "tiers": copy.deepcopy(observation.tiers),
                "budget_stop": reason,
                "selected_live_union": claimed.bit_count(),
                "remaining_eligible_union": remaining,
            })

    def metrics(self):
        total = {"decisions": 0,
                 "tiers": {tier: {"objects": 0, "marginal_live_txs": 0}
                           for tier in TIERS},
                 "budget_stops": dict.fromkeys(STOP_REASONS, 0)}
        distributions = {name: Moments() for name in
                         ("selected_live_union", "remaining_eligible_union")}
        rows = []
        for counts in self.nodes:
            row = {"decisions": counts["decisions"],
                   "tiers": copy.deepcopy(counts["tiers"]),
                   "budget_stops": dict(counts["budget_stops"])}
            total["decisions"] += row["decisions"]
            for tier, values in row["tiers"].items():
                for key, count in values.items():
                    total["tiers"][tier][key] += count
            for reason, count in row["budget_stops"].items():
                total["budget_stops"][reason] += count
            for name, moments in distributions.items():
                moments.merge(counts[name])
                row[name] = counts[name].record_dict()
            rows.append(row)
        for name, moments in distributions.items():
            total[name] = moments.record_dict()
        return {"total": total, "by_node": rows,
                "power_minimums": list(POWER_MINIMUMS),
                "sample_limit": self.sample_limit,
                "samples": copy.deepcopy(self.samples)}
