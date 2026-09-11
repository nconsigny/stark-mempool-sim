"""First-admission observations, independent of object identity and tracing."""

import json
import math


class FirstEvidence:
    """Two sparse timestamp tables, bounded by 2 * nodes * transactions.

    Each table maps a node and leaf index to its first observed time, and
    keeps beside it one mask per node of the indices already recorded. A
    repeated claim costs one and-not over the mask width; a new one costs
    that plus a byte walk over the novel bits, so an admission never pays per
    bit already known. No per-admission history, scheduling, or arena
    provenance reads are needed. Callers supply the admitted claim and
    available bodies. Direct mutations of node views outside the simulator
    are not observed.
    """

    def __init__(self, nodes):
        self.evidence = [{} for _ in range(nodes)]
        self.bodies = [{} for _ in range(nodes)]
        self._seen_evidence = [0] * nodes
        self._seen_bodies = [0] * nodes

    def admit(self, node_id, claimed, now):
        novel = claimed & ~self._seen_evidence[node_id]
        if novel:
            self._seen_evidence[node_id] |= novel
            self._record(self.evidence[node_id], novel, now)

    def body(self, node_id, available, now):
        novel = available & ~self._seen_bodies[node_id]
        if novel:
            self._seen_bodies[node_id] |= novel
            self._record(self.bodies[node_id], novel, now)

    @staticmethod
    def _record(table, novel, now):
        """Write `now` for every set bit; the caller guarantees none is known."""
        data = novel.to_bytes((novel.bit_length() + 7) // 8, "little")
        for index, byte in enumerate(data):
            base = index * 8
            while byte:
                low = byte & -byte
                table[base + low.bit_length() - 1] = now
                byte ^= low

    def summary(self):
        """Entry counts only; the tables themselves scale with nodes x txs."""
        return {"nodes": len(self.evidence),
                "evidence_entries": sum(len(row) for row in self.evidence),
                "body_entries": sum(len(row) for row in self.bodies)}

    def metrics(self):
        """The full tables, detached. Opt in: a record per node per tx."""
        return {"evidence_ms_by_node": [dict(row) for row in self.evidence],
                "body_ms_by_node": [dict(row) for row in self.bodies]}


def diffusion(sim, fraction=0.9, kind="evidence"):
    """Birth-relative first reach per transaction, not current residency.

    Unreached transactions have delay -1 and are excluded from the mean, with
    their count retained. Only direct leaf metadata is read, never provenance.
    """
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError("fraction must be finite and in (0, 1]")
    if kind not in ("evidence", "body"):
        raise ValueError("diffusion kind must be evidence or body")
    observer = sim.transaction_evidence
    delays = [-1.0] * len(sim.arena.leaves)
    reached = []
    if observer is not None:
        tables = observer.evidence
        if kind == "body":
            tables = observer.bodies
        needed = math.ceil(fraction * len(tables))
        for leaf in sim.arena.leaves:
            times = []
            for table in tables:
                if leaf.leaf_index in table:
                    times.append(table[leaf.leaf_index])
            if len(times) >= needed and needed > 0:
                times.sort()
                delay = times[needed - 1] - leaf.born
                delays[leaf.leaf_index] = delay
                reached.append(delay)
    mean = -1.0
    if reached:
        mean = math.fsum(reached) / len(reached)
    reached_count = len(reached)
    if observer is None:
        reached_count = -1
    return {"kind": kind, "fraction": fraction,
            "observed_until_ms": sim.engine.now,
            "time_to_fraction_ms": delays, "mean_ms": mean,
            "reached_txs": reached_count, "total_txs": len(delays)}


class DuplicateAccumulator:
    """Integer byte shares for written dup records, O(followed transactions).

    No trace or message history is retained. The tracer calls record only
    after focus and sampling filters, and follow keeps zero-burden ids too.
    """

    def __init__(self):
        self.attributed = {}
        self.traced_bytes = 0
        self.traced_messages = 0
        self.has_header = False
        self.error = None

    def follow(self, tx):
        self.attributed.setdefault(tx, 0)

    def record(self, txs, size):
        txs = sorted(set(txs))
        if not txs:
            self.error = "a dup record must name followed transactions"
            return
        if not isinstance(size, int) or size < 0:
            # SizeModel can be supplied non-integer sizes programmatically.
            # Reject the integer-share query, never interrupt its trajectory.
            self.error = "duplicate bytes must be nonnegative integers"
            return
        self.traced_bytes += size
        self.traced_messages += 1
        share, remainder = divmod(size, len(txs))
        for index, tx in enumerate(txs):
            extra = 0
            if index < remainder:
                extra = 1
            self.attributed[tx] = self.attributed.get(tx, 0) + share + extra


def duplicate_burden(sim, trace_text):
    """Attribute each existing dup record once; keep all-message totals apart.

    Integer shares are split among sorted, distinct followed transaction ids;
    the first ids receive any remainder bytes. Attribution depends on the
    followed set, not on unobserved transactions in the message. The trace must
    belong to this run and cover no later observation interval.
    """
    accumulator = DuplicateAccumulator()
    for tx in sorted(sim.knowledge.followed):
        accumulator.follow(tx)
    for line in trace_text.splitlines():
        event = json.loads(line)
        if event["e"] == "cfg":
            accumulator.has_header = True
        if event["e"] == "dup":
            accumulator.record(event["txs"], event["b"])
    return _duplicate_burden(sim, accumulator)


def streamed_duplicate_burden(sim):
    """The same burden query without retaining or parsing a trace string.

    Setting tracer.duplicate_accumulator to None before the run disables
    accumulation; this query then returns -1, not a measured zero burden.
    """
    accumulator = sim.tracer.duplicate_accumulator
    if accumulator is None:
        return -1
    return _duplicate_burden(sim, accumulator)


def _duplicate_burden(sim, accumulator):
    if accumulator.error is not None:
        raise ValueError(accumulator.error)
    kinds = ("object", "wrapper", "announce", "want", "block")
    ingress = dict.fromkeys(kinds, 0)
    duplicates = dict.fromkeys(kinds, 0)
    duplicate_counts = dict.fromkeys(kinds, 0)
    for node_kinds in sim.ingress_kind:
        for kind, size in node_kinds.items():
            ingress[kind] = ingress.get(kind, 0) + size
    duplicates.update(sim.duplicate_traffic.bytes_by_kind)
    duplicate_counts.update(sim.duplicate_traffic.messages_by_kind)
    traced_bytes = accumulator.traced_bytes
    traced_messages = accumulator.traced_messages
    if traced_bytes > sim.duplicate_bytes or traced_messages > sim.duplicates:
        raise ValueError("dup trace exceeds this run's message-level totals")
    available = accumulator.has_header and sim.knowledge.active()
    by_transaction = -1
    attributed_bytes = -1
    unattributed_bytes = -1
    if available:
        by_transaction = dict(accumulator.attributed)
        attributed_bytes = sum(by_transaction.values())
        unattributed_bytes = sim.duplicate_bytes - attributed_bytes
    focus = "all"
    if sim.tracer.focus is not None:
        focus = sorted(sim.tracer.focus)
    return {
        "attribution": "equal_integer_shares_among_named_followed_v1",
        "trace_focus": focus,
        "by_transaction_bytes": by_transaction,
        "followed_transactions": sorted(sim.knowledge.followed),
        "attributed_duplicate_bytes": attributed_bytes,
        "unattributed_duplicate_bytes": unattributed_bytes,
        "traced_duplicate_bytes": traced_bytes,
        "traced_duplicate_messages": traced_messages,
        "duplicate_bytes": sim.duplicate_bytes,
        "duplicate_messages": sim.duplicates,
        "duplicate_bytes_by_kind": duplicates,
        "duplicate_messages_by_kind": duplicate_counts,
        "ingress_bytes": sum(sim.ingress_bytes),
        "ingress_messages": sim.messages_delivered,
        "ingress_bytes_by_kind": ingress,
    }
