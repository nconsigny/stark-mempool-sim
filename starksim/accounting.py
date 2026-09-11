"""Exact message counts, independent of transport and observation schedules."""


PAYLOAD_KINDS = ("object", "wrapper", "announce", "want", "block")
WRAPPER_ENCODINGS = ("full_proof", "proof_reference", "mode_zero_witnesses")


class MessageCounts:
    """Cumulative messages and bytes by payload kind and accounting node.

    Storage grows with nodes and payload kinds, never with message count.
    The caller supplies the sender for an attempt or egress refusal, and the
    receiver for a CPU refusal. No message identities or event hooks are kept.
    """

    __slots__ = ("messages", "bytes", "messages_by_kind", "bytes_by_kind",
                 "messages_by_node", "bytes_by_node",
                 "messages_by_node_kind", "bytes_by_node_kind")

    def __init__(self, nodes):
        self.messages = 0
        self.bytes = 0
        self.messages_by_kind = {}
        self.bytes_by_kind = {}
        self.messages_by_node = [0] * nodes
        self.bytes_by_node = [0] * nodes
        self.messages_by_node_kind = [dict() for _ in range(nodes)]
        self.bytes_by_node_kind = [dict() for _ in range(nodes)]

    def record(self, node_id, kind, size):
        self.messages += 1
        self.bytes += size
        self.messages_by_kind[kind] = self.messages_by_kind.get(kind, 0) + 1
        self.bytes_by_kind[kind] = self.bytes_by_kind.get(kind, 0) + size
        self.messages_by_node[node_id] += 1
        self.bytes_by_node[node_id] += size
        messages = self.messages_by_node_kind[node_id]
        messages[kind] = messages.get(kind, 0) + 1
        sizes = self.bytes_by_node_kind[node_id]
        sizes[kind] = sizes.get(kind, 0) + size

    def metrics(self, prefix):
        """A detached snapshot; subsequent traffic cannot change old readings.

        Counts are defined even without a queue bound: zero refusals then
        accounts for all attempts, rather than estimating an unmodelled limit.
        """
        return {
            f"{prefix}_messages": self.messages,
            f"{prefix}_bytes": self.bytes,
            f"{prefix}_messages_by_kind": dict(self.messages_by_kind),
            f"{prefix}_bytes_by_kind": dict(self.bytes_by_kind),
            f"{prefix}_messages_by_node": list(self.messages_by_node),
            f"{prefix}_bytes_by_node": list(self.bytes_by_node),
            f"{prefix}_messages_by_node_kind": [
                dict(counts) for counts in self.messages_by_node_kind],
            f"{prefix}_bytes_by_node_kind": [
                dict(counts) for counts in self.bytes_by_node_kind],
        }
