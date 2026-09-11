"""Snapshot outstanding work; assess useful service against explicit windows.

Snapshots scan current queues and deliveries only when requested. No recovery
observer is installed on the event loop and no calendar event is added.
"""

import math

from starksim.payload import WireMessage


def _server_work(server, now, messages=False):
    active = server.current_job
    remaining = 0.0
    if active is not None:
        remaining = max(0.0, active.duration - (now - active.started_at))
    result = {
        "active_jobs": int(active is not None),
        "waiting_jobs": len(server.waiting),
        "active_remaining_ms": remaining,
        "waiting_ms": sum(job.duration for job in server.waiting),
    }
    if messages:
        active_bytes = 0.0
        if active is not None:
            if not isinstance(active.tag, WireMessage):
                active_bytes = -1.0
            else:
                active_bytes = active.tag.size_bytes()
                if active.duration > 0:
                    active_bytes *= remaining / active.duration
        waiting_bytes = 0
        for job in server.waiting:
            if not isinstance(job.tag, WireMessage):
                waiting_bytes = -1
                break
            waiting_bytes += job.tag.size_bytes()
        result["active_remaining_bytes"] = active_bytes
        result["waiting_bytes"] = waiting_bytes
    return result


def _work_totals(rows, messages=False):
    keys = ["active_jobs", "waiting_jobs", "active_remaining_ms", "waiting_ms"]
    if messages:
        keys.extend(("active_remaining_bytes", "waiting_bytes"))
    result = {}
    for key in keys:
        values = [row[key] for row in rows]
        if -1 in values:
            result[key] = -1
        else:
            result[key] = sum(values)
    return result


def recovery_state(sim):
    """Current work, not cumulative maxima or the remaining calendar length.

    Pending deliveries span optional link serialization and propagation,
    after node egress and before ingress. Link work is therefore a subset,
    not additional messages. Request-source history and spent timeout events
    do not identify requests still awaiting admission.
    """
    now = sim.engine.now
    result = {"recovery_observed_at_ms": now}
    for kind, servers in (("cpu", sim.cpu), ("egress", sim.egress)):
        rows = []
        for server in servers:
            rows.append(_server_work(server, now, messages=kind == "egress"))
        work = _work_totals(rows, messages=kind == "egress")
        work["by_node"] = rows
        result[kind + "_work"] = work
    link_rows = []
    for (sender, receiver), server in sorted(sim.link_servers.items()):
        row = _server_work(server, now, messages=True)
        row["sender"] = sender
        row["receiver"] = receiver
        link_rows.append(row)
    result["link_work"] = link_rows
    result["pending_deliveries"] = len(sim.pending_deliveries)
    result["pending_delivery_bytes"] = sum(
        message.size_bytes() for message in sim.pending_deliveries.values())
    requests = [len(node.view.requested) for node in sim.nodes]
    result["unresolved_requests_by_node"] = requests
    result["unresolved_requests"] = sum(requests)
    result["egress_waiting_empty"] = all(
        not server.waiting for server in sim.egress)
    idle = not sim.pending_deliveries
    for server in (*sim.cpu, *sim.egress, *sim.link_servers.values()):
        if server.current_job is not None or server.waiting:
            idle = False
    result["transport_idle"] = idle
    result["transport_quiescent"] = idle and not result["unresolved_requests"]
    result["included_txs"] = sim.chain.included_count()
    result["expired_txs"] = sim.chain.expired_count()
    result["pending_txs"] = (sim.arrived_txs - result["included_txs"]
                              - result["expired_txs"])
    # One instant has neither a reference service rate nor confirmation that
    # recovered service persisted. Quiescence cannot supply either.
    result["useful_service_recovered"] = -1
    return result


class RecoverySample:
    """Retained state at a reached boundary, suitable for window comparisons."""

    __slots__ = ("sim", "time_ms", "state")

    def __init__(self, sim, boundary_ms):
        if not math.isfinite(boundary_ms) or boundary_ms != sim.engine.now:
            raise ValueError("recovery must be sampled at engine.now")
        self.sim = sim
        self.time_ms = boundary_ms
        self.state = recovery_state(sim)


def _pending_work(state):
    cpu = state["cpu_work"]
    egress = state["egress_work"]
    egress_bytes = -1
    if egress["active_remaining_bytes"] >= 0 and egress["waiting_bytes"] >= 0:
        egress_bytes = (egress["active_remaining_bytes"]
                        + egress["waiting_bytes"])
    return {
        "pending_txs": state["pending_txs"],
        "cpu_jobs": cpu["active_jobs"] + cpu["waiting_jobs"],
        "cpu_ms": cpu["active_remaining_ms"] + cpu["waiting_ms"],
        "egress_jobs": egress["active_jobs"] + egress["waiting_jobs"],
        "egress_bytes": egress_bytes,
        "pending_deliveries": state["pending_deliveries"],
        "pending_delivery_bytes": state["pending_delivery_bytes"],
        "unresolved_requests": state["unresolved_requests"],
    }


def _window(samples):
    first = samples[0]
    last = samples[-1]
    elapsed = last.time_ms - first.time_ms
    if elapsed <= 0:
        raise ValueError("recovery windows must have positive duration")
    offsets = []
    peak = {}
    previous = None
    for sample in samples:
        if sample.sim is not first.sim:
            raise ValueError("recovery samples must belong to one run")
        if previous is not None and sample.time_ms <= previous:
            raise ValueError("recovery samples must have increasing times")
        previous = sample.time_ms
        offsets.append(sample.time_ms - first.time_ms)
        for key, value in _pending_work(sample.state).items():
            if value < 0:
                peak[key] = -1
            elif peak.get(key) != -1:
                peak[key] = max(peak.get(key, 0), value)
    return {
        "start_ms": first.time_ms,
        "end_ms": last.time_ms,
        "sample_offsets_ms": offsets,
        "included_per_ms": (last.state["included_txs"]
                             - first.state["included_txs"]) / elapsed,
        "expired_txs": last.state["expired_txs"] - first.state["expired_txs"],
        "peak_sampled_work": peak,
    }


def assess_useful_recovery(reference, recovery, confirmation, *,
                           rate_tolerance=0.0, work_tolerance=0.0):
    """Compare equal-window inclusion and sampled work, then confirm once.

    Tolerances are relative to the pre-burst reference and must be chosen
    before observing recovery. Samples in all three windows use one offset
    grid. Work peaks are sampled, not continuous-time maxima. The result is
    an instrument reading over these windows, not evidence of future stability.
    """
    for tolerance in (rate_tolerance, work_tolerance):
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("recovery tolerances must be finite and nonnegative")
    if rate_tolerance >= 1:
        raise ValueError("rate_tolerance must leave a positive service floor")
    result = {"useful_service_recovered": -1,
              "rate_tolerance": rate_tolerance,
              "work_tolerance": work_tolerance}
    if any(len(samples) < 2 for samples in (reference, recovery, confirmation)):
        return result
    if not (reference[0].sim is recovery[0].sim is confirmation[0].sim):
        raise ValueError("recovery windows must belong to one run")
    before = _window(reference)
    after = _window(recovery)
    sustained = _window(confirmation)
    if (before["sample_offsets_ms"] != after["sample_offsets_ms"]
            or before["sample_offsets_ms"] != sustained["sample_offsets_ms"]):
        raise ValueError("recovery windows must use the same sampling grid")
    if (before["end_ms"] > after["start_ms"]
            or after["end_ms"] != sustained["start_ms"]):
        raise ValueError("reference must precede recovery and confirmation follow it")
    result.update(reference=before, recovery=after, confirmation=sustained)
    if before["included_per_ms"] <= 0:
        return result
    for window in (before, after, sustained):
        if -1 in window["peak_sampled_work"].values():
            return result
    minimum = before["included_per_ms"] * (1 - rate_tolerance)
    maximum = before["included_per_ms"] * (1 + rate_tolerance)
    limits = {}
    for key, value in before["peak_sampled_work"].items():
        limits[key] = value * (1 + work_tolerance)
    result["included_per_ms_bounds"] = [minimum, maximum]
    result["sampled_work_limits"] = limits
    recovered = True
    for window in (after, sustained):
        if not minimum <= window["included_per_ms"] <= maximum:
            recovered = False
        for key, value in window["peak_sampled_work"].items():
            if value > limits[key]:
                recovered = False
    result["useful_service_recovered"] = recovered
    return result
