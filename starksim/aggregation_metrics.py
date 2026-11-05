"""Root-completion counters and decision-time block observations.

No observer reads arena provenance, changes node beliefs or schedules work.
"""

import math


class FoldWidths:
    """Exact root-input widths with storage per distinct width, not per proof."""

    __slots__ = ("widths", "count", "children", "leaves")

    def __init__(self):
        self.widths = {}
        self.count = 0
        self.children = 0
        self.leaves = 0

    def record(self, inputs):
        width = len(inputs)
        self.count += 1
        self.widths[width] = self.widths.get(width, 0) + 1
        for obj in inputs:
            if obj.is_leaf():
                self.leaves += 1
            else:
                self.children += 1

    def metrics(self):
        mean = -1.0
        p95 = -1
        children = -1.0
        leaves = -1.0
        if self.count:
            mean = (self.children + self.leaves) / self.count
            children = self.children / self.count
            leaves = self.leaves / self.count
            needed = math.ceil(0.95 * self.count)
            cumulative = 0
            for width in sorted(self.widths):
                cumulative += self.widths[width]
                if cumulative >= needed:
                    p95 = width
                    break
        return {"roots_completed": self.count, "mean": mean, "p95": p95,
                "children_mean": children, "leaves_mean": leaves}


# Zero has its own bucket, followed by powers of two and an overflow bucket.
# The distribution has fixed storage even when a run produces wider claims.
ROOT_CLAIM_BUCKET_MINIMUMS = (0,) + tuple(1 << power for power in range(21))


class PlanRoots:
    """Completed plan roots, not internal proofs or terminal DAG objects.

    There is a fixed number of moment and bucket counters. One union costs a bit
    per transaction identity rather than a mask for every completed root.
    """

    __slots__ = ("count", "claim_sum", "claim_square_sum", "claim_union",
                 "histogram")

    def __init__(self):
        self.count = 0
        self.claim_sum = 0
        self.claim_square_sum = 0
        self.claim_union = 0
        self.histogram = [0] * len(ROOT_CLAIM_BUCKET_MINIMUMS)

    def record(self, claimed):
        width = claimed.bit_count()
        self.count += 1
        self.claim_sum += width
        self.claim_square_sum += width * width
        self.claim_union |= claimed
        bucket = min(width.bit_length(), len(self.histogram) - 1)
        self.histogram[bucket] += 1

    def metrics(self, discarded):
        mean = -1.0
        if self.count:
            mean = self.claim_sum / self.count
        weighted_mean = -1.0
        if self.claim_sum:
            weighted_mean = self.claim_square_sum / self.claim_sum
        histogram = []
        for index, minimum in enumerate(ROOT_CLAIM_BUCKET_MINIMUMS):
            maximum = None
            if index + 1 < len(ROOT_CLAIM_BUCKET_MINIMUMS):
                maximum = ROOT_CLAIM_BUCKET_MINIMUMS[index + 1] - 1
            histogram.append({"minimum": minimum, "maximum": maximum,
                              "count": self.histogram[index]})
        return {
            "plan_roots_completed": self.count,
            "plan_root_claim_sum": self.claim_sum,
            "plan_root_claim_square_sum": self.claim_square_sum,
            "plan_root_claim_mean": mean,
            "plan_root_claim_weighted_mean": weighted_mean,
            "plan_root_claim_union_txs": self.claim_union.bit_count(),
            "plan_root_live_claim_union_txs": (
                self.claim_union & ~discarded).bit_count(),
            "plan_root_claim_histogram": histogram,
        }


class BlockSelection:
    """Read claims already visited by the selector, without another arena scan.

    Candidate storage is one union mask. Selected masks survive only until the
    block is recorded; persistent observations contain counts and object ids.
    Canonical settlement is supplied after selection, never to its scoring.
    """

    __slots__ = ("known_discarded", "body_mask", "candidate_count",
                 "candidate_union", "selected_union", "chosen", "selected")

    def __init__(self, known_discarded, body_mask):
        self.known_discarded = known_discarded
        self.body_mask = body_mask
        self.candidate_count = 0
        self.candidate_union = 0
        self.selected_union = 0
        self.chosen = 0
        self.selected = []

    def candidate(self, claimed):
        self.candidate_count += 1
        self.candidate_union |= claimed

    def select(self, object_id, claimed, effective):
        effective &= ~self.known_discarded
        marginal = effective & ~self.chosen
        self.selected.append((object_id, claimed.bit_count(),
                              effective.bit_count(), marginal))
        self.selected_union |= claimed
        self.chosen |= effective

    def record(self, block, discarded_before, object_limit, diverges):
        candidate_effective = self.candidate_union & ~self.known_discarded
        candidate_missing_bodies = -1
        selected_missing_bodies = -1
        if self.body_mask is not None:
            candidate_missing_bodies = (
                candidate_effective & ~self.body_mask).bit_count()
            selected_missing_bodies = (
                self.selected_union & ~self.known_discarded
                & ~self.body_mask).bit_count()
            candidate_effective &= self.body_mask
        candidate_includable = candidate_effective & ~discarded_before
        remaining = (candidate_includable & ~block.included).bit_count()
        stale_attempted = -1
        if diverges:
            stale_attempted = block.wasted().bit_count()
        selected = []
        for object_id, width, effective_width, marginal in self.selected:
            selected.append({
                "object_id": object_id,
                "claim_txs": width,
                "effective_txs": effective_width,
                "marginal_attempted_txs": marginal.bit_count(),
                "marginal_included_txs": (marginal & block.included).bit_count(),
            })
        density = -1.0
        if selected:
            density = block.count() / len(selected)
        result = {
            "slot": block.slot,
            "time": block.time,
            "proposer": block.proposer,
            "selected_objects": len(selected),
            "selected": selected,
            "selected_claim_union_txs": self.selected_union.bit_count(),
            "selected_known_discarded_txs": (
                self.selected_union & self.known_discarded).bit_count(),
            "selected_missing_body_txs": selected_missing_bodies,
            "attempted_txs": block.attempted.bit_count(),
            "included_txs": block.count(),
            "stale_attempted_txs": stale_attempted,
            "included_per_selected_object": density,
            "candidate_objects": self.candidate_count,
            "candidate_claim_union_txs": self.candidate_union.bit_count(),
            "candidate_live_claim_union_txs": (
                self.candidate_union & ~discarded_before).bit_count(),
            "candidate_effective_txs": candidate_effective.bit_count(),
            "candidate_includable_txs": candidate_includable.bit_count(),
            "candidate_missing_body_txs": candidate_missing_bodies,
            "remaining_includable_txs": remaining,
            "budget_full_with_live_backlog": (
                len(selected) == object_limit and remaining > 0),
        }
        # A nonpositive limit bypasses the selector's first candidate scan.
        # Its empty selection is known; its available candidate set is not.
        if object_limit <= 0:
            for field in (
                    "candidate_objects", "candidate_claim_union_txs",
                    "candidate_live_claim_union_txs", "candidate_effective_txs",
                    "candidate_includable_txs", "candidate_missing_body_txs",
                    "remaining_includable_txs", "budget_full_with_live_backlog"):
                result[field] = -1
        return result
