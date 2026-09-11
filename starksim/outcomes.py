"""Birth-relative outcomes and boundary-sampled utilisation and traffic.

Queries do not schedule events, draw randomness or read object coverage.
Outcome timestamps occupy space linear in settled transactions. Summaries
scan births only when requested, not at every observation or domain event.
"""

import math

from starksim.accounting import PAYLOAD_KINDS, WRAPPER_ENCODINGS
from starksim.payload import WireMessage


def _time(value, name):
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def transaction_outcomes(sim):
    """Detached records keyed by leaf index, for births through NetworkSim.

    A missing timestamp is -1, not a claim that the transaction never settles.
    Settlement injected directly into ChainState has no observed timestamp.
    """
    records = []
    for tx in sim.entry_of:
        records.append({
            "tx": tx,
            "born_ms": sim.arena.leaves[tx].born,
            "included_ms": sim.inclusion_times.get(tx, -1),
            "expired_ms": sim.expiry_times.get(tx, -1),
        })
    return records


def boundary_complete(sim):
    """Whether every event at the current instant has already run.

    `run(max_events=...)` can stop with more events due at `engine.now`; an
    expiry or inclusion among them has not happened yet, so a boundary equal
    to `now` is not yet observable. `run(until=...)` never stops that way.
    """
    calendar = sim.engine.calendar
    return not len(calendar) or calendar.peek_time() > sim.engine.now


def outcomes_at_age(sim, age_ms, birth_start_ms, birth_end_ms):
    """Outcomes by age for births in [birth_start_ms, birth_end_ms).

    Immature births are censored even if they have already settled. A mature
    birth is pending at the requested age if its settlement happened later.
    Cohorts whose birth interval is still open cannot support a full-cohort
    comparison, even when every birth observed so far has matured.
    """
    _time(age_ms, "age_ms")
    _time(birth_start_ms, "birth_start_ms")
    _time(birth_end_ms, "birth_end_ms")
    if birth_end_ms <= birth_start_ms:
        raise ValueError("birth_end_ms must exceed birth_start_ms")
    now = sim.engine.now
    complete = boundary_complete(sim)
    result = {
        "age_ms": age_ms,
        "birth_start_ms": birth_start_ms,
        "birth_end_ms": birth_end_ms,
        "observed_until_ms": now,
        "boundary_complete": complete,
        "cohort_closed": now >= birth_end_ms,
        "born_txs": 0,
        "mature_txs": 0,
        "censored_txs": 0,
        "included_txs": 0,
        "expired_txs": 0,
        "pending_txs": 0,
        "unknown_txs": 0,
    }
    # Bytes permit constant-time membership without allocating a wide integer
    # for every leaf. Direct chain mutations must not look like pending births
    # when their settlement time was never observed by the wiring.
    discarded = sim.chain.discarded_mask()
    discarded_bytes = discarded.to_bytes((discarded.bit_length() + 7) // 8,
                                         "little")
    for tx in sim.entry_of:
        born = sim.arena.leaves[tx].born
        if born < birth_start_ms or born >= birth_end_ms:
            continue
        result["born_txs"] += 1
        boundary = born + age_ms
        # A boundary at `now` with same-time events still pending is censored:
        # its settlement may be the very next event.
        if boundary > now or (boundary == now and not complete):
            result["censored_txs"] += 1
            continue
        result["mature_txs"] += 1
        included = sim.inclusion_times.get(tx)
        expired = sim.expiry_times.get(tx)
        if included is not None and included <= boundary:
            result["included_txs"] += 1
        elif expired is not None and expired <= boundary:
            result["expired_txs"] += 1
        elif (included is None and expired is None
              and tx // 8 < len(discarded_bytes)
              and discarded_bytes[tx // 8] & (1 << (tx % 8))):
            result["unknown_txs"] += 1
        else:
            result["pending_txs"] += 1
    for outcome in ("included", "expired", "pending"):
        fraction = -1.0
        if result["mature_txs"] and not result["unknown_txs"]:
            fraction = result[outcome + "_txs"] / result["mature_txs"]
        result[outcome + "_fraction"] = fraction
    return result


def compare_cohorts(sim, age_ms, early, late):
    """Late minus early inclusion fraction at one age, or -1 if incomplete.

    Each cohort is a (start_ms, end_ms) pair. The validity flag distinguishes
    an unavailable contrast from a measured difference of minus one.
    """
    first = outcomes_at_age(sim, age_ms, *early)
    last = outcomes_at_age(sim, age_ms, *late)
    comparable = True
    for cohort in (first, last):
        if (not cohort["cohort_closed"] or not cohort["mature_txs"]
                or cohort["censored_txs"] or cohort["unknown_txs"]):
            comparable = False
    difference = -1.0
    if comparable:
        difference = last["included_fraction"] - first["included_fraction"]
    return {
        "age_ms": age_ms,
        "early": first,
        "late": last,
        "comparable": comparable,
        "included_fraction_late_minus_early": difference,
    }


class BusySample:
    """Cumulative counters read at one reached boundary, including active work."""

    __slots__ = ("sim", "time_ms", "cpu_ms", "egress_ms")

    def __init__(self, sim, boundary_ms):
        _time(boundary_ms, "boundary_ms")
        if boundary_ms != sim.engine.now:
            raise ValueError("busy counters must be sampled at engine.now")
        self.sim = sim
        self.time_ms = boundary_ms
        self.cpu_ms = tuple(server.busy_time_at(boundary_ms)
                            for server in sim.cpu)
        self.egress_ms = tuple(server.busy_time_at(boundary_ms)
                               for server in sim.egress)


def utilisation_window(start, end):
    """Per-node busy-time differences, divided by an explicit elapsed window."""
    if start.sim is not end.sim:
        raise ValueError("utilisation samples must come from the same run")
    elapsed = end.time_ms - start.time_ms
    if elapsed <= 0:
        raise ValueError("utilisation window must have positive duration")
    result = {"start_ms": start.time_ms, "end_ms": end.time_ms}
    for kind in ("cpu", "egress"):
        before = getattr(start, kind + "_ms")
        after = getattr(end, kind + "_ms")
        busy = []
        for left, right in zip(before, after):
            busy.append(right - left)
        result[kind + "_busy_ms_by_node"] = busy
        result[kind + "_utilisation_by_node"] = [ms / elapsed for ms in busy]
        mean = -1.0
        if busy:
            mean = sum(busy) / (elapsed * len(busy))
        result[kind + "_utilisation"] = mean
    return result


class TrafficSample:
    """Detached cumulative egress counters at a complete reached boundary.

    Storage and sampling time are O(nodes x fixed payload/encoding classes).
    No message history, queue scan, event or arena lookup is needed: completed
    bytes plus the active message's elapsed service classify wire traffic.
    Directly injected, unclassified egress work makes its wire split -1.
    """

    __slots__ = ("sim", "time_ms", "bytes_by_node_kind", "bytes_by_node",
                 "wrapper_copies", "reference_cadence")

    def __init__(self, sim, boundary_ms):
        # Local import avoids the wiring/reporting/outcomes import cycle.
        from starksim.netsim import CLOCKED_TRIGGERS

        _time(boundary_ms, "boundary_ms")
        if boundary_ms != sim.engine.now:
            raise ValueError("traffic counters must be sampled at engine.now")
        if not boundary_complete(sim):
            raise ValueError("traffic counters require a complete boundary")
        self.sim = sim
        self.time_ms = boundary_ms
        self.bytes_by_node_kind = {}
        self.bytes_by_node = {}
        sources = {
            "attempted": sim.attempted.bytes_by_node_kind,
            "refused": sim.egress_refused.bytes_by_node_kind,
            "accepted": sim.offered_kind,
            "completed": sim.sent_kind,
        }
        for stage, tables in sources.items():
            rows = []
            totals = []
            for table in tables:
                totals.append(sum(table.values()))
                if table.keys() - set(PAYLOAD_KINDS):
                    rows.append(dict.fromkeys(PAYLOAD_KINDS, -1))
                else:
                    rows.append({kind: table.get(kind, 0)
                                 for kind in PAYLOAD_KINDS})
            self.bytes_by_node_kind[stage] = tuple(rows)
            self.bytes_by_node[stage] = tuple(totals)

        wire_rows = []
        wire_totals = []
        for node_id, server in enumerate(sim.egress):
            row = dict(self.bytes_by_node_kind["completed"][node_id])
            job = server.current_job
            if sim.capacity is not None and job is not None:
                if (isinstance(job.tag, WireMessage)
                        and job.tag.payload.kind in row):
                    elapsed = boundary_ms - job.started_at
                    row[job.tag.payload.kind] += elapsed * sim.capacity
                else:
                    row = dict.fromkeys(PAYLOAD_KINDS, -1)
            total = sim.wire_bytes(node_id)
            # Non-message jobs submitted directly to egress have busy time
            # but no payload classification, including after they complete.
            if (any(value < 0 for value in row.values())
                    or not math.isclose(sum(row.values()), total,
                                        rel_tol=1e-9, abs_tol=1e-6)):
                row = dict.fromkeys(PAYLOAD_KINDS, -1)
            wire_rows.append(row)
            wire_totals.append(total)
        self.bytes_by_node_kind["wire"] = tuple(wire_rows)
        self.bytes_by_node["wire"] = tuple(wire_totals)
        self.wrapper_copies = None
        if sim.wrapper_copies is not None:
            self.wrapper_copies = tuple(dict(row) for row in sim.wrapper_copies)
        cadence = []
        for node in sim.nodes:
            rate = -1.0
            if sim.aggregation_trigger in CLOCKED_TRIGGERS:
                rate = (1000.0 * len(node.peers) * sim.size_model.recursive_proof
                        / sim.aggregation_interval)
            cadence.append(rate)
        self.reference_cadence = tuple(cadence)


def _traffic_mean(values):
    if not values or any(value < 0 for value in values):
        return -1.0
    return sum(values) / len(values)


def traffic_window(start, end):
    """Rates over (start, end], and completed wrapper copies in that window.

    Means include idle nodes. Unavailable classifications propagate -1 rather
    than subtracting sentinels into a false zero. Cadence is a reference for
    one full proof per peer per clock tick, not an observed emission rate.
    """
    if start.sim is not end.sim:
        raise ValueError("traffic samples must come from the same run")
    elapsed = end.time_ms - start.time_ms
    if elapsed <= 0:
        raise ValueError("traffic window must have positive duration")
    if start.reference_cadence != end.reference_cadence:
        raise ValueError("traffic window requires unchanged reference cadence")
    seconds = elapsed / 1000.0
    result = {"start_ms": start.time_ms, "end_ms": end.time_ms}
    for stage in start.bytes_by_node_kind:
        rows = []
        for before, after in zip(start.bytes_by_node_kind[stage],
                                  end.bytes_by_node_kind[stage]):
            row = {}
            for kind in PAYLOAD_KINDS:
                rate = -1.0
                if before[kind] >= 0 and after[kind] >= 0:
                    rate = (after[kind] - before[kind]) / seconds
                row[kind] = rate
            rows.append(row)
        means = {}
        for kind in PAYLOAD_KINDS:
            means[kind] = _traffic_mean([row[kind] for row in rows])
        totals = []
        for before, after in zip(start.bytes_by_node[stage],
                                  end.bytes_by_node[stage]):
            totals.append((after - before) / seconds)
        prefix = stage + "_bytes_per_second"
        result[prefix + "_by_node_kind"] = rows
        result[prefix + "_by_kind"] = means
        result[prefix + "_by_node"] = totals
        result[prefix] = _traffic_mean(totals)

    copies = []
    for node_id in range(len(start.reference_cadence)):
        row = dict.fromkeys(WRAPPER_ENCODINGS, -1)
        if start.wrapper_copies is not None and end.wrapper_copies is not None:
            for encoding in WRAPPER_ENCODINGS:
                row[encoding] = (end.wrapper_copies[node_id][encoding]
                                 - start.wrapper_copies[node_id][encoding])
        copies.append(row)
    totals = {}
    for encoding in WRAPPER_ENCODINGS:
        values = [row[encoding] for row in copies]
        total = -1
        if values and all(value >= 0 for value in values):
            total = sum(values)
        totals[encoding] = total
    result["wrapper_copies_by_node_encoding"] = copies
    result["wrapper_copies_by_encoding"] = totals
    result["reference_cadence_bytes_per_second_by_node"] = list(
        start.reference_cadence)
    result["reference_cadence_bytes_per_second"] = _traffic_mean(
        start.reference_cadence)
    return result
