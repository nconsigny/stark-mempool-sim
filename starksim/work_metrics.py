"""Bounded work distributions and aggregation lifecycle observations."""

from starksim.costs import KINDS
from starksim.statistics import POWER_MINIMUMS, Moments


class PlanWork:
    """Counters per node; root identity state grows only with arena outputs."""

    def __init__(self, node_count):
        self.nodes = []
        self.ratios = []
        for _ in range(node_count):
            self.nodes.append(dict.fromkeys((
                "submitted", "started", "completed", "cancelled",
                "refused", "refused_over_capacity", "refused_queue",
                "still_pending", "completed_estimated_ms",
                "completed_charged_ms", "ratio_unavailable",
                "refused_estimated_ms", "proofs", "roots_completed",
                "roots_emitted", "roots_contributed",
                "roots_emitted_contributed"), 0))
            self.ratios.append(Moments())
        # One entry per completed root, not per peer send or block occurrence.
        self.roots = {}

    def submit(self, node_id):
        counts = self.nodes[node_id]
        counts["submitted"] += 1
        counts["still_pending"] += 1

    def start(self, run):
        if not run.work_started:
            run.work_started = True
            self.nodes[run.node_id]["started"] += 1

    def finish(self, run, outcome):
        if run.work_outcome is not None:
            return
        run.work_outcome = outcome
        counts = self.nodes[run.node_id]
        counts["still_pending"] -= 1
        if outcome.startswith("refused"):
            counts["refused"] += 1
            counts["refused_estimated_ms"] += run.plan.selection.estimated_cost
        counts[outcome] += 1
        if outcome == "completed":
            estimate = run.plan.selection.estimated_cost
            counts["completed_estimated_ms"] += estimate
            counts["completed_charged_ms"] += run.work_charged_ms
            if estimate > 0:
                self.ratios[run.node_id].record(run.work_charged_ms / estimate)
            else:
                counts["ratio_unavailable"] += 1

    def root(self, object_id, node_id):
        self.nodes[node_id]["roots_completed"] += 1
        self.roots[object_id] = [node_id, False, False]

    def emitted(self, object_id):
        state = self.roots.get(object_id)
        if state is None or state[1]:
            return
        state[1] = True
        counts = self.nodes[state[0]]
        counts["roots_emitted"] += 1
        if state[2]:
            counts["roots_emitted_contributed"] += 1

    def contributed(self, selected, included):
        for object_id, width, effective_width, marginal in selected:
            if not marginal & included:
                continue
            state = self.roots.get(object_id)
            if state is None or state[2]:
                continue
            state[2] = True
            counts = self.nodes[state[0]]
            counts["roots_contributed"] += 1
            if state[1]:
                counts["roots_emitted_contributed"] += 1

    def metrics(self):
        total = dict.fromkeys(self.nodes[0], 0)
        ratio = Moments()
        rows = []
        for counts, samples in zip(self.nodes, self.ratios):
            row = dict(counts, charged_estimate_ratio=samples.record_dict())
            row["internal_nonroot_proofs"] = counts["proofs"] - counts[
                "roots_completed"]
            rows.append(row)
            for key, value in counts.items():
                total[key] += value
            ratio.merge(samples)
        total["charged_estimate_ratio"] = ratio.record_dict()
        total["internal_nonroot_proofs"] = total["proofs"] - total[
            "roots_completed"]
        total["ratio_power_minimums"] = list(POWER_MINIMUMS)
        return {"total": total, "by_node": rows}


def cpu_accounting(sim):
    """Service is sampled now, including elapsed service of the active job."""
    rows = []
    totals = {}
    for kind in KINDS:
        totals[kind] = {"wait": Moments(), "service_ms": 0.0,
                        "refused_jobs": 0, "refused_estimated_ms": 0.0}
    for server in sim.cpu:
        row = {}
        for kind in KINDS:
            wait = server.wait_by_kind.get(kind, Moments())
            service = server.service_by_kind.get(kind, 0.0)
            active = server.current_job
            if active is not None and active.kind == kind:
                service += sim.engine.now - active.started_at
            refused = server.refused_by_kind.get(kind, 0)
            refused_ms = server.refused_ms_by_kind.get(kind, 0.0)
            row[kind] = {"wait": wait.record_dict(), "service_ms": service,
                         "refused_jobs": refused,
                         "refused_estimated_ms": refused_ms}
            totals[kind]["wait"].merge(wait)
            totals[kind]["service_ms"] += service
            totals[kind]["refused_jobs"] += refused
            totals[kind]["refused_estimated_ms"] += refused_ms
        rows.append(row)
    for record in totals.values():
        record["wait"] = record["wait"].record_dict()
    return {"cpu_accounting": {"observed_at_ms": sim.engine.now,
                                "power_minimums": list(POWER_MINIMUMS),
                                "total": totals, "by_node": rows},
            "plan_work": sim.plan_work.metrics(),
            "cpu_refused_estimated_ms": totals["verify_recv"][
                "refused_estimated_ms"]}
