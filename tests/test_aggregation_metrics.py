"""Completion and block-density observations must not count overlap twice."""

import io
import json
import unittest
from unittest import mock

from bench import mainnet
from starksim.aggregation_metrics import (ROOT_CLAIM_BUCKET_MINIMUMS,
                                          BlockSelection, PlanRoots)
from starksim.arena import Arena, KIND_PQ
from starksim.costs import CostTable
from starksim.engine import Job
from starksim.netsim import NetworkSim, csv_row
from starksim.network import Overlay

from tests.support import two_node_overlay


def fixture(count, **settings):
    options = dict(seed=1, relay_policy="wrapper_only",
                   aggregation_trigger="aligned", block_budget=1,
                   costs=CostTable(prove_recursive=10.0))
    options.update(settings)
    sim = NetworkSim(two_node_overlay(1000.0), **options)
    leaves = [sim.arena.new_leaf(KIND_PQ, 0.0) for _ in range(count)]
    sim.arrived_txs = count
    sim.entry_of.update((index, 0) for index in range(count))
    return sim, leaves


def admit_claim(sim, leaves, claimed, node_id=0):
    obj = sim.arena.new_aggregate(leaves, sim.engine.now, node_id,
                                  claimed=claimed)
    sim.nodes[node_id].admit(obj, sim.engine.now)
    return obj


class TestPlanRoots(unittest.TestCase):
    def test_root_core_and_peer_send_counts_remain_separate(self):
        overlay = Overlay.from_adjacency([[1, 2, 3], [0], [0], [0]],
                                         base_latency=1000.0)
        sim = NetworkSim(overlay, relay_policy="wrapper_only", capacity=1.0,
                         egress_queue_bytes=0,
                         costs=CostTable(prove_recursive=10.0))
        sim._birth(KIND_PQ, 0)
        sim._birth(KIND_PQ, 0)
        sim._tick(0, 1)
        sim.run(until=10.0)
        self.assertEqual(sim.plan_roots.count, 1)
        self.assertEqual(sim.wrappers_emitted, 1)
        self.assertEqual(sim.attempted.messages_by_kind["wrapper"], 3)
        self.assertEqual(sim.offered_messages_by_kind["wrapper"], 1)
        self.assertEqual(sim.egress_refused.messages_by_kind["wrapper"], 2)

    def test_overlapping_roots_have_distinct_moments_and_union(self):
        sim, leaves = fixture(4)
        for leaf in leaves[:2]:
            sim.nodes[0].admit(leaf, 0.0)
        for leaf in leaves[1:]:
            sim.nodes[1].admit(leaf, 0.0)
        sim._tick(0, 1)
        sim._tick(1, 1)
        sim.run(until=10.0)
        metrics = sim.metrics()
        self.assertEqual(metrics["plan_roots_completed"], 2)
        self.assertEqual(metrics["plan_root_claim_sum"], 5)
        self.assertEqual(metrics["plan_root_claim_square_sum"], 13)
        self.assertEqual(metrics["plan_root_claim_mean"], 2.5)
        self.assertEqual(metrics["plan_root_claim_weighted_mean"], 13 / 5)
        self.assertEqual(metrics["plan_root_claim_union_txs"], 4)
        self.assertLess(metrics["plan_root_claim_union_txs"],
                        metrics["plan_root_claim_sum"])
        self.assertEqual(metrics["plan_root_live_claim_union_txs"], 4)
        histogram = metrics["plan_root_claim_histogram"]
        self.assertEqual(sum(bucket["count"] for bucket in histogram), 2)
        self.assertEqual(histogram[2],
                         {"minimum": 2, "maximum": 3, "count": 2})
        # Re-wrapping the same proofs adds cores and peer sends, not roots.
        sim._tick(0, 2)
        sim._tick(1, 2)
        self.assertEqual(sim.plan_roots.count, 2)
        self.assertEqual(sim.wrappers_emitted, 4)
        self.assertEqual(sim.attempted.messages_by_kind["wrapper"], 4)
        self.assertEqual(sim.offered_messages_by_kind["wrapper"], 4)

    def test_internal_proofs_are_not_plan_roots(self):
        for shape, count, proofs in (("wide", 4, 1), ("binary", 4, 3),
                                     ("capped8", 9, 2)):
            with self.subTest(shape=shape):
                sim, leaves = fixture(count, fold_plan=shape)
                for leaf in leaves:
                    sim.nodes[0].admit(leaf, 0.0)
                sim._tick(0, 1)
                if shape == "binary":
                    sim.run(until=20.0)
                    self.assertEqual(sim.proofs_completed, 2)
                    self.assertEqual(sim.plan_roots.count, 0)
                    self.assertEqual(sim.metrics()["plan_root_claim_mean"], -1)
                sim.run(until=40.0)
                self.assertEqual(sim.proofs_completed, proofs)
                self.assertEqual(sim.plan_roots.count, 1)
                self.assertEqual(sim.metrics()["plan_root_claim_mean"], count)

    def test_terminal_population_changes_at_consumption_not_completion(self):
        sim, leaves = fixture(3)
        for leaf in leaves[:2]:
            sim.nodes[0].admit(leaf, 0.0)
        sim._tick(0, 1)
        sim.run(until=10.0)
        self.assertEqual(mainnet.root_count(sim), 1)
        self.assertEqual(mainnet.mean_root_claim(sim), 2)
        sim.nodes[0].admit(leaves[2], sim.engine.now)
        sim._tick(0, 2)
        self.assertEqual(mainnet.root_count(sim), 0)
        self.assertEqual(mainnet.mean_root_claim(sim), 0.0)
        self.assertEqual(sim.plan_roots.count, 1)
        self.assertEqual(sim.plan_roots.claim_sum, 2)
        sim.run(until=20.0)
        self.assertEqual(mainnet.root_count(sim), 1)
        self.assertEqual(mainnet.mean_root_claim(sim), 3)
        self.assertEqual(sim.plan_roots.count, 2)
        self.assertEqual(sim.plan_roots.claim_sum, 5)

    def test_width_is_frozen_but_live_union_tracks_canonical_discards(self):
        sim, leaves = fixture(3)
        for leaf in leaves:
            sim.nodes[0].admit(leaf, 0.0)
        sim._tick(0, 1)
        sim.chain.include(1, 1)
        sim.run(until=10.0)
        self.assertEqual(sim.plan_roots.claim_sum, 3)
        self.assertEqual(sim.metrics()["plan_root_live_claim_union_txs"], 2)
        sim.chain.expire(2)
        metrics = sim.metrics()
        self.assertEqual(metrics["plan_root_live_claim_union_txs"], 1)
        self.assertEqual(metrics["plan_root_claim_union_txs"], 3)
        self.assertEqual(metrics["plan_root_claim_square_sum"], 9)

    def test_empty_roots_are_completed_but_have_no_weighted_mean(self):
        sim, leaves = fixture(2)
        for leaf in leaves:
            sim.nodes[0].admit(leaf, 0.0)
        sim.cpu[0].submit(Job(10.0))
        sim._tick(0, 1)
        sim.chain.include(3, 1)
        sim.run(until=20.0)
        metrics = sim.metrics()
        self.assertEqual(metrics["plan_roots_completed"], 1)
        self.assertEqual(metrics["plan_root_claim_mean"], 0)
        self.assertEqual(metrics["plan_root_claim_weighted_mean"], -1)
        self.assertEqual(sim.wrappers_emitted, 0)
        self.assertEqual(metrics["plan_root_claim_histogram"][0]["count"], 1)

    def test_no_completions_have_no_means(self):
        sim, _leaves = fixture(0)
        metrics = sim.metrics()
        self.assertEqual(metrics["plan_roots_completed"], 0)
        self.assertEqual(metrics["plan_root_claim_mean"], -1)
        self.assertEqual(metrics["plan_root_claim_weighted_mean"], -1)
        self.assertEqual(metrics["block_observations"], [])

    def test_histogram_is_fixed_size_and_has_an_overflow_bucket(self):
        roots = PlanRoots()
        for _ in range(1000):
            roots.record(0b11111)
        overflow_width = ROOT_CLAIM_BUCKET_MINIMUMS[-1]
        roots.record((1 << overflow_width) - 1)
        histogram = roots.metrics(0)["plan_root_claim_histogram"]
        self.assertEqual(len(histogram), len(ROOT_CLAIM_BUCKET_MINIMUMS))
        self.assertEqual(len(histogram), 22)
        self.assertEqual(overflow_width, 2 ** 20)
        self.assertEqual(histogram[3],
                         {"minimum": 4, "maximum": 7, "count": 1000})
        self.assertEqual(histogram[-1],
                         {"minimum": overflow_width, "maximum": None,
                          "count": 1})
        self.assertEqual(sum(bucket["count"] for bucket in histogram),
                         roots.count)


class TestBlockObservations(unittest.TestCase):
    def assert_partition(self, record):
        selected = record["selected"]
        self.assertEqual(record["selected_objects"], len(selected))
        self.assertEqual(sum(obj["marginal_attempted_txs"] for obj in selected),
                         record["attempted_txs"])
        self.assertEqual(sum(obj["marginal_included_txs"] for obj in selected),
                         record["included_txs"])
        if record["selected_missing_body_txs"] >= 0:
            self.assertEqual(record["selected_claim_union_txs"],
                             record["selected_known_discarded_txs"]
                             + record["selected_missing_body_txs"]
                             + record["attempted_txs"])
        if record["stale_attempted_txs"] >= 0:
            self.assertEqual(record["attempted_txs"], record["included_txs"]
                             + record["stale_attempted_txs"])
        self.assertEqual(record["candidate_includable_txs"],
                         record["included_txs"]
                         + record["remaining_includable_txs"])

    def test_overlapping_selected_claims_add_only_their_marginal_union(self):
        sim, leaves = fixture(4)
        first = admit_claim(sim, leaves, 0b0111)
        second = admit_claim(sim, leaves, 0b1110)
        sim.nodes[0].view.bodies = 0b1111
        block = sim._build_block(1)
        record = sim.metrics()["block_observations"][0]
        self.assertEqual(block.object_ids, (first.id, second.id))
        self.assertEqual(record["selected"], [
            {"object_id": first.id, "claim_txs": 3, "effective_txs": 3,
             "marginal_attempted_txs": 3, "marginal_included_txs": 3},
            {"object_id": second.id, "claim_txs": 3, "effective_txs": 3,
             "marginal_attempted_txs": 1, "marginal_included_txs": 1},
        ])
        self.assertEqual(record["selected_claim_union_txs"], 4)
        self.assertLess(record["selected_claim_union_txs"],
                        sum(obj["claim_txs"] for obj in record["selected"]))
        self.assertEqual(record["candidate_live_claim_union_txs"], 4)
        self.assertEqual(record["candidate_includable_txs"], 4)
        self.assertEqual(record["included_per_selected_object"], 2)
        self.assertFalse(record["budget_full_with_live_backlog"])
        self.assert_partition(record)
        # These are pre-settlement counts even after instant notification.
        self.assertEqual(sim.nodes[0].known_discarded(), 0b1111)
        sim.nodes[0].view.held.clear()
        sim._build_block(2)
        self.assertEqual(sim.block_observations[0], record)
        self.assertEqual(sim.block_observations[1]["candidate_includable_txs"], 0)

    def test_duplicate_representation_does_not_raise_density(self):
        records = []
        for duplicate in (False, True):
            sim, leaves = fixture(3)
            admit_claim(sim, leaves, 0b111)
            if duplicate:
                admit_claim(sim, leaves, 0b111)
            sim.nodes[0].view.bodies = 0b111
            sim._build_block(1)
            records.append(sim.block_observations[0])
        self.assertEqual(records[0]["candidate_objects"], 1)
        self.assertEqual(records[1]["candidate_objects"], 2)
        for record in records:
            self.assertEqual(record["selected_objects"], 1)
            self.assertEqual(record["candidate_includable_txs"], 3)
            self.assertEqual(record["included_per_selected_object"], 3)
            self.assert_partition(record)

    def test_known_discards_missing_bodies_and_stale_attempts_are_separate(self):
        sim, leaves = fixture(6, chain_notify="overlay")
        admit_claim(sim, leaves, 0b011111)
        admit_claim(sim, leaves, 0b110110)
        sim.nodes[0].view.bodies = 0b011111
        sim.chain.include(0b11, 0)
        sim.notifier.known[0] = 0b1
        sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(record["selected_claim_union_txs"], 5)
        self.assertEqual(record["selected_known_discarded_txs"], 1)
        self.assertEqual(record["attempted_txs"], 4)
        self.assertEqual(record["stale_attempted_txs"], 1)
        self.assertEqual(record["included_txs"], 3)
        self.assertEqual(record["candidate_claim_union_txs"], 6)
        self.assertEqual(record["candidate_live_claim_union_txs"], 4)
        self.assertEqual(record["candidate_effective_txs"], 4)
        self.assertEqual(record["candidate_missing_body_txs"], 1)
        self.assert_partition(record)

    def test_selected_missing_bodies_and_unclaimed_coverage_are_not_attempted(self):
        sim, leaves = fixture(4)
        admit_claim(sim, leaves, 0b0111)
        sim.nodes[0].view.bodies = 0b1001
        sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(record["selected_missing_body_txs"], 2)
        self.assertEqual(record["selected"][0]["claim_txs"], 3)
        self.assertEqual(record["selected"][0]["effective_txs"], 1)
        self.assertEqual(record["included_txs"], 1)
        self.assertEqual(record["candidate_claim_union_txs"], 3)
        self.assertEqual(record["stale_attempted_txs"], -1)
        self.assert_partition(record)

    def test_full_budget_requires_additional_locally_includable_claims(self):
        for wide in (False, True):
            sim, leaves = fixture(4, block_budget=0)
            if wide:
                admit_claim(sim, leaves, 0b1111)
            else:
                admit_claim(sim, leaves, 0b0011)
                admit_claim(sim, leaves, 0b1100)
            sim.nodes[0].view.bodies = 0b1111
            sim._build_block(1)
            record = sim.block_observations[0]
            self.assertEqual(record["selected_objects"], 1)
            self.assertEqual(record["candidate_includable_txs"], 4)
            self.assertEqual(record["budget_full_with_live_backlog"], not wide)
            self.assertEqual(record["remaining_includable_txs"],
                             0 if wide else 2)
            self.assert_partition(record)

    def test_claim_only_cannot_measure_body_exclusions(self):
        sim, leaves = fixture(2, inclusion_policy="claim_only")
        admit_claim(sim, leaves, 0b11)
        sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(record["included_txs"], 2)
        self.assertEqual(record["selected_missing_body_txs"], -1)
        self.assertEqual(record["candidate_missing_body_txs"], -1)
        self.assert_partition(record)

    def test_greedy_backlog_does_not_prove_a_larger_budget_is_necessary(self):
        sim, leaves = fixture(6)
        first = admit_claim(sim, leaves, 0b001111)
        second = admit_claim(sim, leaves, 0b010011)
        third = admit_claim(sim, leaves, 0b101100)
        sim.nodes[0].view.bodies = 0b111111
        block = sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(block.object_ids, (first.id, second.id))
        self.assertEqual(record["included_txs"], 5)
        self.assertEqual(record["remaining_includable_txs"], 1)
        self.assertTrue(record["budget_full_with_live_backlog"])
        # A different pair covers the full local union with the same budget.
        alternative = sim.arena.claim(second) | sim.arena.claim(third)
        self.assertEqual(alternative.bit_count(), 6)
        self.assertEqual(record["candidate_includable_txs"], 6)
        self.assert_partition(record)

    def test_empty_selection_has_no_density(self):
        sim, _leaves = fixture(0)
        sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(record["selected_objects"], 0)
        self.assertEqual(record["included_txs"], 0)
        self.assertEqual(record["included_per_selected_object"], -1)
        self.assert_partition(record)

    def test_a_bypassed_scan_cannot_report_an_empty_candidate_set(self):
        sim, leaves = fixture(1, block_budget=-1)
        admit_claim(sim, leaves, 1)
        sim._build_block(1)
        record = sim.block_observations[0]
        self.assertEqual(record["selected_objects"], 0)
        self.assertEqual(record["candidate_objects"], -1)
        self.assertEqual(record["candidate_includable_txs"], -1)
        self.assertEqual(record["budget_full_with_live_backlog"], -1)


class TestObservationNeutrality(unittest.TestCase):
    def test_selector_observation_does_not_add_claim_reads_or_change_cache(self):
        results = []
        original_claim = Arena.claim
        for observe in (False, True):
            sim, leaves = fixture(4, coverage_cache_limit=1)
            # Claim-less synthetic aggregates exercise the coverage-cache
            # fallback; production claims do not need that fallback.
            for children in (leaves[:3], leaves[1:]):
                obj = sim.arena.new_aggregate(children, 0.0, 0)
                sim.nodes[0].admit(obj, 0.0)
            observation = None
            if observe:
                observation = BlockSelection(0, 0b1111)
            with mock.patch.object(Arena, "claim", autospec=True,
                                   side_effect=original_claim) as reads:
                picked, claim = sim.nodes[0].select_by_coverage(
                    sim.arena, 2, 0, body_mask=0b1111,
                    observation=observation)
            results.append(([obj.id for obj in picked], claim, reads.call_count,
                            sim.arena.coverage_memos, sim.arena.cache_evictions,
                            list(sim.arena._cache)))
            if observe:
                self.assertEqual(observation.candidate_count, 2)
        self.assertEqual(results[0], results[1])

    def _trajectory(self):
        trace = io.StringIO()
        sim = NetworkSim(two_node_overlay(1.0), seed=7, trace=trace,
                         chain_notify="overlay", capacity=1000.0,
                         aggregation_trigger="aligned", fold_plan="binary",
                         aggregation_interval=20.0, slot_length=100.0,
                         costs=CostTable(prove_recursive=2.0, verify_recv=1.0))
        for index in range(8):
            sim.schedule_birth(index * 5.0, KIND_PQ, node_id=index % 2)
        sim.schedule_ticks(10)
        sim.schedule_slots(2)
        sim.run(until=250.0)
        return sim, trace.getvalue()

    def test_instruments_do_not_change_trace_events_or_existing_csv(self):
        counted, trace = self._trajectory()
        with mock.patch.object(PlanRoots, "record", return_value=None):
            with mock.patch.object(BlockSelection, "candidate"):
                with mock.patch.object(BlockSelection, "select"):
                    with mock.patch.object(BlockSelection, "record",
                                           return_value={}):
                        disabled, disabled_trace = self._trajectory()
        self.assertGreater(counted.plan_roots.count, 0)
        self.assertEqual(trace, disabled_trace)
        self.assertEqual(counted.engine.events_processed,
                         disabled.engine.events_processed)
        self.assertEqual(csv_row(counted.metrics()), csv_row(disabled.metrics()))
        self.assertEqual(counted.variant(), disabled.variant())

    def test_metrics_are_detached_and_repeated_reads_do_not_change_state(self):
        sim, trace = self._trajectory()
        first = sim.metrics()
        expected = json.dumps(first, sort_keys=True)
        first["plan_root_claim_histogram"][0]["count"] = -100
        first["block_observations"][0]["selected"].clear()
        first["block_observations"][0]["included_txs"] = -100
        self.assertEqual(json.dumps(sim.metrics(), sort_keys=True), expected)
        header = json.loads(trace.splitlines()[0])
        for name in sim.plan_roots.metrics(0):
            self.assertNotIn(name, header)
        self.assertNotIn("block_observations", header)


if __name__ == "__main__":
    unittest.main()
