"""Global observations of direct leaves and raw witnesses in completed jobs."""

import math
from bisect import bisect_right

from starksim.arena import KIND_AGGREGATE, KIND_PQ, KIND_PRIVACY


MULTIPLICITY_MINIMUMS = (1, 2, 3, 5, 9, 17, 33, 65, 129, 257, 513)
LEAF_KINDS = (("pq", KIND_PQ), ("privacy", KIND_PRIVACY))


def _held_claim(obj):
    """Read an existing claim without touching the arena's coverage LRU.

    Production aggregates have frozen claims. Claim-less synthetic objects
    can supply their already-cached coverage, but observation never traverses
    their provenance to invent an assertion.
    """
    if obj.claimed is not None:
        return obj.claimed
    return obj._coverage or 0


def _service_index(kind):
    if kind == KIND_PQ:
        return 0
    if kind == KIND_PRIVACY:
        return 1
    return None


class FoldJob:
    """Submission evidence and one start-time snapshot for outstanding work.

    Direct inputs retain object references; each mode 0 input retains one
    claim mask, including overlaps with other inputs, because each occurrence
    is charged. Start scans only this node's held objects once, OR-ing existing
    recursive claims, at O(held objects * transaction-mask words), never once
    per leaf or across nodes. At 1,000 nodes and 10,000 transactions, the
    active jobs' coverage snapshots cost at most 1.25 MB of bit payload total,
    plus Python overhead. Queued jobs have no coverage snapshot; mode 0 input
    masks cost at most 1,250 bytes each at that transaction count. All state is
    released on completion, waiting cancellation or queue refusal.
    """

    __slots__ = ("service_per_leaf", "direct", "witness_masks", "covered",
                 "direct_unattributed_ms", "witness_unattributed_ms")

    def __init__(self, arena, view, input_ids, service_per_leaf):
        self.service_per_leaf = service_per_leaf
        self.direct = []
        self.witness_masks = []
        for object_id in input_ids:
            obj = arena.objects.get(object_id)
            if obj is None:
                continue
            if obj.is_leaf():
                self.direct.append(obj)
            elif object_id in view.witness_backed:
                self.witness_masks.append((object_id, _held_claim(obj)))
        self.covered = 0
        self.direct_unattributed_ms = [0.0, 0.0]
        self.witness_unattributed_ms = [0.0, 0.0]

    def start(self, arena, view, inputs):
        present = {obj.id for obj in inputs}
        surviving_direct = []
        for leaf in self.direct:
            if leaf.id not in present:
                index = _service_index(leaf.kind)
                if index is not None:
                    service = self.service_per_leaf[index]
                    self.direct_unattributed_ms[index] += service
                    self.witness_unattributed_ms[index] += service
            else:
                surviving_direct.append(leaf)
        self.direct = surviving_direct
        surviving_masks = []
        for object_id, mask in self.witness_masks:
            if object_id not in present:
                for leaf in arena.leaves_in(mask):
                    index = _service_index(leaf.kind)
                    if index is not None:
                        self.witness_unattributed_ms[index] += (
                            self.service_per_leaf[index])
            else:
                surviving_masks.append((object_id, mask))
        self.witness_masks = surviving_masks
        if not self.direct and not self.witness_masks:
            return
        for object_id in view.held:
            if object_id in view.witness_backed:
                continue
            obj = arena.objects.get(object_id)
            if obj is not None and obj.kind == KIND_AGGREGATE:
                self.covered |= _held_claim(obj)

    def witnesses(self, arena):
        yield from self.direct
        for _object_id, mask in self.witness_masks:
            yield from arena.leaves_in(mask)


class LeafFolds:
    """One node bitset per folded leaf, plus fixed per-kind/node counters.

    Storage is O(folded leaves * nodes / word size + nodes), with no job or
    event history. At 1,000 nodes each leaf needs at most 125 bytes of bit
    payload, plus Python integer/dictionary overhead. Separate direct-leaf and
    witness observers need two such bitsets per leaf. Updates visit supplied
    raw inputs, never the provenance behind a recursive proof. Callers record
    in completion order and retain submission evidence and start coverage in
    FoldJob only while work is outstanding.
    """

    def __init__(self, nodes, circuit_basis):
        self.circuit_basis = circuit_basis
        self.kinds = {}
        for _name, kind in LEAF_KINDS:
            self.kinds[kind] = {"nodes": {}, "fold_events": 0,
                                "service_ms": 0.0, "repeat_service_ms": 0.0,
                                "covered_at_start_events": 0,
                                "covered_at_start_service_ms": 0.0,
                                "unattributed_service_ms": 0.0}
        self.fold_events_by_node = [0] * nodes
        self.repeat_events_by_node = [0] * nodes
        self.repeat_service_ms_by_node = [0.0] * nodes
        self.covered_at_start_events_by_node = [0] * nodes
        self.covered_at_start_service_ms_by_node = [0.0] * nodes
        self.completed_service_ms = 0.0
        self.unsupported_leaf_events = 0

    @staticmethod
    def service_per_leaf(circuit, composition, duration):
        """Price each raw witness using the composition charged at submission.

        The full hash denominator includes overhead, nested proofs and any
        witness-backed aggregate inputs. Each leaf's own hash weight splits
        the charged duration, including jitter.
        A zero-hash job has zero duration and no attributable leaf service.
        """
        hashes = circuit.hashes(*composition)
        if hashes <= 0:
            return (0.0, 0.0)
        return (duration * circuit.sphincs_hashes / hashes,
                duration * circuit.leanstark_hashes / hashes)

    def record(self, node_id, inputs, duration, service_per_leaf, covered=0,
               unattributed_ms=(0.0, 0.0)):
        if self.circuit_basis:
            # Includes internal jobs of failed plans and jobs with no leaves.
            self.completed_service_ms += duration
            for index, (_name, kind) in enumerate(LEAF_KINDS):
                self.kinds[kind]["unattributed_service_ms"] += (
                    unattributed_ms[index])
        node_bit = 1 << node_id
        for obj in inputs:
            if not obj.is_leaf():
                continue
            row = self.kinds.get(obj.kind)
            if row is None:
                self.unsupported_leaf_events += 1
                continue
            previous = row["nodes"].get(obj.leaf_index, 0)
            row["nodes"][obj.leaf_index] = previous | node_bit
            row["fold_events"] += 1
            self.fold_events_by_node[node_id] += 1
            service = 0.0
            if self.circuit_basis:
                index = 0
                if obj.kind == KIND_PRIVACY:
                    index = 1
                service = service_per_leaf[index]
                row["service_ms"] += service
            # Any earlier completion makes this a repeat, even on this node.
            if previous:
                self.repeat_events_by_node[node_id] += 1
                row["repeat_service_ms"] += service
                self.repeat_service_ms_by_node[node_id] += service
            if covered & (1 << obj.leaf_index):
                row["covered_at_start_events"] += 1
                row["covered_at_start_service_ms"] += service
                self.covered_at_start_events_by_node[node_id] += 1
                self.covered_at_start_service_ms_by_node[node_id] += service

    def _summary(self, kinds):
        multiplicities = []
        fold_events = 0
        service = 0.0
        repeat_service = 0.0
        covered_events = 0
        covered_service = 0.0
        unattributed_service = 0.0
        for kind in kinds:
            row = self.kinds[kind]
            for nodes in row["nodes"].values():
                multiplicities.append(nodes.bit_count())
            fold_events += row["fold_events"]
            service += row["service_ms"]
            repeat_service += row["repeat_service_ms"]
            covered_events += row["covered_at_start_events"]
            covered_service += row["covered_at_start_service_ms"]
            unattributed_service += row["unattributed_service_ms"]
        repeat_share = -1.0
        if self.circuit_basis:
            if self.completed_service_ms > 0:
                repeat_share = repeat_service / self.completed_service_ms
        else:
            service = -1.0
            repeat_service = -1.0
            covered_service = -1.0
            unattributed_service = -1.0
        return {
            "leaves_folded": len(multiplicities),
            "fold_events": fold_events,
            "repeat_events": fold_events - len(multiplicities),
            "multiplicity": _multiplicity(multiplicities),
            "service_ms": service,
            "repeat_service_ms": repeat_service,
            "repeat_service_share": repeat_share,
            "covered_at_start_events": covered_events,
            "covered_at_start_service_ms": covered_service,
            "unattributed_service_ms": unattributed_service,
        }

    def metrics(self):
        repeat_service = [-1.0] * len(self.repeat_service_ms_by_node)
        covered_service = [-1.0] * len(self.covered_at_start_service_ms_by_node)
        completed_service = -1.0
        if self.circuit_basis:
            repeat_service = list(self.repeat_service_ms_by_node)
            covered_service = list(self.covered_at_start_service_ms_by_node)
            completed_service = self.completed_service_ms
        return {
            "total": self._summary(self.kinds),
            "by_kind": {name: self._summary((kind,))
                        for name, kind in LEAF_KINDS},
            "fold_events_by_node": list(self.fold_events_by_node),
            "repeat_events_by_node": list(self.repeat_events_by_node),
            "repeat_service_ms_by_node": repeat_service,
            "covered_at_start_events_by_node": list(
                self.covered_at_start_events_by_node),
            "covered_at_start_service_ms_by_node": covered_service,
            "completed_service_ms": completed_service,
            "unsupported_leaf_events": self.unsupported_leaf_events,
        }


def _multiplicity(values):
    counts = [0] * len(MULTIPLICITY_MINIMUMS)
    for value in values:
        counts[bisect_right(MULTIPLICITY_MINIMUMS, value) - 1] += 1
    histogram = []
    for index, minimum in enumerate(MULTIPLICITY_MINIMUMS):
        maximum = None
        if index + 1 < len(MULTIPLICITY_MINIMUMS):
            maximum = MULTIPLICITY_MINIMUMS[index + 1] - 1
        histogram.append({"minimum": minimum, "maximum": maximum,
                          "count": counts[index]})
    mean = p50 = p95 = maximum = -1
    if values:
        ordered = sorted(values)
        mean = sum(ordered) / len(ordered)
        p50 = ordered[math.ceil(0.50 * len(ordered)) - 1]
        p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
        maximum = ordered[-1]
    return {"mean": mean, "p50": p50, "p95": p95, "max": maximum,
            "histogram": histogram}
