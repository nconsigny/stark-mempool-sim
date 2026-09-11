"""Completed uplink copies, separate from receiver admission and reach."""

from starksim.statistics import Moments, POWER_MINIMUMS


COPY_CLASSES = ("raw_object_bodies", "wrapper_bodies", "wrapper_claim_mentions")


class WireCopies:
    """Three sparse leaf-indexed counters, O(transactions x fixed classes).

    No message history or arena provenance is retained. A raw object costs
    one increment; a wrapper costs a byte walk over each payload mask and
    one increment per named bit. The masks, not the aggregate DAG, define
    which transactions crossed egress. Queries include zero-copy births.
    """

    def __init__(self):
        self.counts = {kind: {} for kind in COPY_CLASSES}

    def raw_body(self, leaf_index):
        table = self.counts["raw_object_bodies"]
        table[leaf_index] = table.get(leaf_index, 0) + 1

    def wrapper(self, body_mask, tx_mask):
        self._increment(self.counts["wrapper_bodies"], body_mask)
        self._increment(self.counts["wrapper_claim_mentions"], tx_mask)

    @staticmethod
    def _increment(table, mask):
        data = mask.to_bytes((mask.bit_length() + 7) // 8, "little")
        for index, byte in enumerate(data):
            base = index * 8
            while byte:
                low = byte & -byte
                tx = base + low.bit_length() - 1
                table[tx] = table.get(tx, 0) + 1
                byte ^= low


def wire_copies(sim, per_transaction=False):
    """Summary over every born leaf, with optional leaf-indexed rows.

    A disabled observer returns -1. An enabled empty population has count
    and sum zero but undefined mean/max (-1). Each class histogram has the
    fixed power-of-two buckets of starksim.statistics, including zero.
    Query time is O(transactions x classes); default output is fixed-size.
    """
    if sim.wire_copies is None:
        return -1
    transactions = len(sim.arena.leaves)
    classes = {}
    for kind, table in sim.wire_copies.counts.items():
        moments = Moments()
        for tx in range(transactions):
            moments.record(table.get(tx, 0))
        record = moments.record_dict()
        record["mean"] = -1.0
        if transactions:
            record["mean"] = moments.sum / transactions
        classes[kind] = record
    result = {
        "observed_until_ms": sim.engine.now,
        "total_txs": transactions,
        "histogram_power_minimums": list(POWER_MINIMUMS),
        "classes": classes,
    }
    if per_transaction:
        rows = []
        for tx in range(transactions):
            row = {"tx": tx}
            for kind, table in sim.wire_copies.counts.items():
                row[kind] = table.get(tx, 0)
            rows.append(row)
        result["by_transaction"] = rows
    return result
