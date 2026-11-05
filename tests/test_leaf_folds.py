"""Leaf work counts direct inputs, in global job-completion order."""

import io
import json
import unittest
from unittest import mock

from bench.mainnet import calibrated_prover, mixed_kinds
from starksim.arena import Arena, KIND_PQ, KIND_PRIVACY
from starksim.costs import (VERIFY_RECV, CircuitCost, CostTable,
                            NativeVerifyCost)
from starksim.engine import Job
from starksim.leaf_folds import LeafFolds
from starksim.netsim import csv_row

from tests.support import make_sim
from tests.test_aggregation_metrics import fixture
from tests.test_evidence import wrapper


def circuit(**settings):
    options = dict(ms_per_hash=1, sphincs_hashes=10,
                   leanstark_hashes=20, recursive_hashes=30)
    options.update(settings)
    return CircuitCost(**options)


def admit(sim, objects, node_id=0):
    for obj in objects:
        sim.nodes[node_id].admit(obj, sim.engine.now)


def circuit_fixture(count=1, **settings):
    options = dict(costs=CostTable(prove_recursive=circuit()),
                   prove_cost_basis="circuit")
    options.update(settings)
    return fixture(count, **options)


def mixed_fixture():
    cost = circuit(ms_per_hash=0.5, overhead_hashes=30, recursive_hashes=50)
    sim, signatures = circuit_fixture(
        2, costs=CostTable(prove_recursive=cost, jitter_frac=0.25, seed=7))
    privacy = sim.arena.new_leaf(KIND_PRIVACY, 0)
    hidden = [sim.arena.new_leaf(KIND_PQ, 0),
              sim.arena.new_leaf(KIND_PRIVACY, 0)]
    mask = sum(1 << obj.leaf_index for obj in hidden)
    nested = sim.arena.new_aggregate(hidden, 0, 1, claimed=mask)
    return sim, signatures, privacy, nested, cost


class TestLeafFolds(unittest.TestCase):
    def test_first_and_repeat_follow_completion_instead_of_submission(self):
        sim, leaves = circuit_fixture()
        admit(sim, leaves, 0)
        admit(sim, leaves, 1)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        sim._tick(0, 1)
        sim._tick(1, 1)
        sim.run(until=9)
        self.assertEqual(sim.metrics()["leaf_folds"]["total"]["fold_events"], 0)
        sim.run(until=10)
        first = sim.metrics()["leaf_folds"]
        self.assertEqual(first["fold_events_by_node"], [0, 1])
        self.assertEqual(first["repeat_events_by_node"], [0, 0])
        sim.run(until=15)
        record = sim.metrics()["leaf_folds"]
        total = record["total"]
        self.assertEqual((total["leaves_folded"], total["fold_events"],
                          total["repeat_events"]), (1, 2, 1))
        for field in ("mean", "p50", "p95", "max"):
            self.assertEqual(total["multiplicity"][field], 2)
        self.assertEqual(record["fold_events_by_node"], [1, 1])
        self.assertEqual(record["repeat_events_by_node"], [1, 0])
        self.assertEqual(record["repeat_service_ms_by_node"], [10, 0])
        self.assertEqual(total["service_ms"], 20)
        self.assertEqual(total["repeat_service_ms"], 10)
        self.assertEqual(total["repeat_service_share"], 0.5)
        self.assertEqual(total["covered_at_start_events"], 0)
        self.assertEqual(total["covered_at_start_service_ms"], 0)
        self.assertEqual(record["completed_service_ms"], 20)
        self.assertEqual(total, record["by_kind"]["pq"])
        self.assertEqual(record["by_kind"]["privacy"]["fold_events"], 0)

    def test_same_time_completions_follow_calendar_insertion_order(self):
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                sim, leaves = circuit_fixture()
                for node_id in order:
                    admit(sim, leaves, node_id)
                    sim._tick(node_id, 1)
                sim.run(until=10)
                repeats = sim.metrics()["leaf_folds"]["repeat_events_by_node"]
                self.assertEqual(repeats[order[0]], 0)
                self.assertEqual(repeats[order[1]], 1)

    def test_replan_on_one_node_repeats_without_increasing_multiplicity(self):
        sim, leaves = circuit_fixture()
        admit(sim, leaves)
        sim._tick(0, 1)
        sim._tick(0, 2)
        sim.run(until=20)
        record = sim.metrics()["leaf_folds"]
        total = record["total"]
        self.assertEqual((total["leaves_folded"], total["fold_events"],
                          total["repeat_events"]), (1, 2, 1))
        self.assertEqual(total["multiplicity"]["max"], 1)
        self.assertEqual(total["multiplicity"]["histogram"][0]["count"], 1)
        self.assertEqual(record["fold_events_by_node"], [2, 0])
        self.assertEqual(record["repeat_events_by_node"], [1, 0])
        self.assertEqual(total["covered_at_start_events"], 1)
        self.assertEqual(total["covered_at_start_service_ms"], 10)

    def test_internal_jobs_count_leaves_and_roots_only_add_to_service_denominator(self):
        sim, leaves = circuit_fixture(4, fold_plan="binary")
        admit(sim, leaves)
        first = sim._tick(0, 1)
        sim._tick(0, 2)
        sim.run(until=40)
        total = sim.metrics()["leaf_folds"]["total"]
        self.assertEqual(sim.proofs_completed, 2)
        self.assertEqual(sim.plan_roots.count, 0)
        self.assertEqual(total["fold_events"], 4)
        self.assertEqual(total["service_ms"], 40)
        sim.run(until=100)
        self.assertEqual(sim.metrics()["leaf_folds"]["total"], total)
        self.assertEqual(sim.plan_roots.count, 1)
        self.assertEqual(first.leaf_service_ms, {})
        sim.run(until=200)
        record = sim.metrics()["leaf_folds"]
        self.assertEqual(sim.proofs_completed, 6)
        self.assertEqual(record["total"]["leaves_folded"], 4)
        self.assertEqual(record["total"]["fold_events"], 8)
        self.assertEqual(record["total"]["repeat_events"], 4)
        self.assertEqual(record["total"]["service_ms"], 80)
        self.assertEqual(record["total"]["repeat_service_ms"], 40)
        self.assertEqual(record["total"]["repeat_service_share"], 40 / 200)

    def test_mixed_job_splits_charged_service_by_leaf_hashes(self):
        sim, signatures, privacy, nested, cost = mixed_fixture()
        # One signature and the privacy witness have already been folded;
        # the second signature and both leaves behind the proof have not.
        admit(sim, [signatures[0], privacy], 1)
        sim._tick(1, 1)
        sim.run(until=50)
        before = sim.metrics()["leaf_folds"]
        admit(sim, signatures + [privacy, nested])
        run = sim._tick(0, 1)
        self.assertEqual(sim._job_composition(sim.nodes[0], run.plan.jobs[0]),
                         (2, 1, 1))
        duration = sim.cpu[0].current_job.duration
        self.assertGreater(duration, cost.duration(2, 1, 1))
        sim.run(until=150)
        record = sim.metrics()["leaf_folds"]
        hashes = cost.hashes(2, 1, 1)
        expected = duration * (2 * 10 + 20) / hashes
        self.assertAlmostEqual(record["total"]["service_ms"]
                               - before["total"]["service_ms"], expected)
        self.assertAlmostEqual(record["total"]["repeat_service_ms"],
                               duration * (10 + 20) / hashes)
        for kind, weight, repeat_weight in (("pq", 20, 10), ("privacy", 20, 20)):
            row = record["by_kind"][kind]
            self.assertAlmostEqual(row["service_ms"]
                                   - before["by_kind"][kind]["service_ms"],
                                   duration * weight / hashes)
            self.assertAlmostEqual(row["repeat_service_ms"],
                                   duration * repeat_weight / hashes)
            self.assertAlmostEqual(row["repeat_service_share"],
                                   row["repeat_service_ms"]
                                   / sim.leaf_folds.completed_service_ms)
        self.assertEqual(record["total"]["leaves_folded"], 3)
        self.assertEqual(record["total"]["fold_events"], 5)
        self.assertEqual(record["total"]["repeat_events"], 2)
        self.assertEqual(record["fold_events_by_node"], [3, 2])
        self.assertEqual(record["repeat_events_by_node"], [2, 0])
        self.assertAlmostEqual(record["repeat_service_ms_by_node"][0],
                               record["total"]["repeat_service_ms"])

    def test_submission_price_survives_changed_evidence_while_queued(self):
        sim, signatures, privacy, nested, cost = mixed_fixture()
        admit(sim, signatures + [privacy, nested])
        sim.nodes[0].view.witness_backed.add(nested.id)
        sim.cpu[0].submit(Job(10, kind=VERIFY_RECV))
        run = sim._tick(0, 1)
        duration = sim.cpu[0].waiting[0].duration
        submitted = sim._job_composition(sim.nodes[0], run.plan.jobs[0])
        self.assertEqual(submitted, (3, 2, 0))
        sim.nodes[0].view.witness_backed.remove(nested.id)
        self.assertEqual(sim._job_composition(sim.nodes[0], run.plan.jobs[0]),
                         (2, 1, 1))
        sim.run(until=150)
        record = sim.metrics()["leaf_folds"]
        self.assertEqual(record["total"]["fold_events"], 3)
        self.assertAlmostEqual(record["total"]["service_ms"],
                               duration * (2 * 10 + 20) / cost.hashes(*submitted))
        self.assertEqual(record["by_kind"]["pq"]["leaves_folded"], 2)
        self.assertEqual(record["by_kind"]["privacy"]["leaves_folded"], 1)

    def test_completed_internal_work_of_a_refused_plan_counts(self):
        sim, leaves = circuit_fixture(4, fold_plan="binary", cpu_queue_jobs=0)
        admit(sim, leaves)
        run = sim._tick(0, 1)
        self.assertIsNone(run)
        self.assertEqual(sim.plan_work.nodes[0]["refused_queue"], 1)
        active_run = sim.cpu[0].current_job.tag[0]
        self.assertEqual(set(active_run.leaf_service_ms), {0})
        sim.run(until=20)
        record = sim.metrics()["leaf_folds"]
        self.assertEqual(sim.plan_roots.count, 0)
        self.assertEqual(record["total"]["fold_events"], 2)
        self.assertEqual(record["total"]["service_ms"], 20)
        self.assertEqual(record["completed_service_ms"], 20)
        self.assertEqual(sim.metrics()["witness_folds"]["completed_service_ms"], 20)
        self.assertEqual(active_run.leaf_service_ms, {})

    def test_mode_zero_witnesses_count_raw_work_and_repeats(self):
        sim, leaves = circuit_fixture(3)
        admit(sim, leaves[:2], 0)
        sim._tick(0, 1)
        sim.run(until=20)
        proof = sim.own_aggregate[0]
        admit(sim, leaves[2:], 1)
        sim._on_deliver(sim.engine, wrapper(sim, proof, mode=0, bodies=3))
        self.assertIn(proof.id, sim.nodes[1].view.witness_backed)
        run = sim._tick(1, 1)
        self.assertEqual(sim._job_composition(sim.nodes[1], run.plan.jobs[0]),
                         (3, 0, 0))
        sim.run(until=50)
        direct = sim.metrics()["leaf_folds"]
        witness = sim.metrics()["witness_folds"]
        self.assertEqual(direct["total"]["fold_events"], 3)
        self.assertEqual(direct["total"]["service_ms"], 30)
        self.assertEqual(direct["total"]["repeat_events"], 0)
        total = witness["total"]
        self.assertEqual(total["leaves_folded"], 3)
        self.assertEqual(total["fold_events"], 5)
        self.assertEqual(total["repeat_events"], 2)
        self.assertEqual(total["multiplicity"]["mean"], 5 / 3)
        self.assertEqual(total["multiplicity"]["max"], 2)
        self.assertEqual(total["service_ms"], 50)
        self.assertEqual(total["repeat_service_ms"], 20)
        self.assertEqual(total["repeat_service_share"], 20 / 50)
        self.assertEqual(total["covered_at_start_events"], 0)
        self.assertEqual(total["covered_at_start_service_ms"], 0)
        self.assertEqual(witness["completed_service_ms"], 50)
        self.assertEqual(witness["fold_events_by_node"], [2, 3])
        self.assertEqual(witness["repeat_events_by_node"], [0, 2])

    def test_mode_zero_mixed_witness_service_includes_each_kind_and_jitter(self):
        sim, signatures, privacy, nested, cost = mixed_fixture()
        admit(sim, signatures + [privacy, nested])
        sim.nodes[0].view.witness_backed.add(nested.id)
        run = sim._tick(0, 1)
        duration = sim.cpu[0].current_job.duration
        self.assertEqual(sim._job_composition(sim.nodes[0], run.plan.jobs[0]),
                         (3, 2, 0))
        sim.run(until=100)
        record = sim.metrics()["witness_folds"]
        self.assertEqual(record["total"]["fold_events"], 5)
        self.assertEqual(record["by_kind"]["pq"]["fold_events"], 3)
        self.assertEqual(record["by_kind"]["privacy"]["fold_events"], 2)
        self.assertAlmostEqual(record["total"]["service_ms"],
                               duration * 70 / cost.hashes(3, 2, 0))
        self.assertAlmostEqual(record["by_kind"]["pq"]["service_ms"],
                               duration * 30 / cost.hashes(3, 2, 0))
        self.assertAlmostEqual(record["by_kind"]["privacy"]["service_ms"],
                               duration * 40 / cost.hashes(3, 2, 0))

    def test_queued_mode_zero_upgrade_keeps_submission_witness_price(self):
        sim, signatures, privacy, nested, cost = mixed_fixture()
        admit(sim, signatures + [privacy, nested])
        sim.nodes[0].view.witness_backed.add(nested.id)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        sim._tick(0, 1)
        duration = sim.cpu[0].waiting[0].duration
        sim.nodes[0].view.witness_backed.remove(nested.id)
        sim.run(until=100)
        record = sim.metrics()["witness_folds"]
        self.assertEqual(record["total"]["fold_events"], 5)
        self.assertAlmostEqual(record["total"]["service_ms"],
                               duration * 70 / cost.hashes(3, 2, 0))
        self.assertEqual(record["total"]["covered_at_start_events"], 2)
        self.assertAlmostEqual(record["total"]["covered_at_start_service_ms"],
                               duration * 30 / cost.hashes(3, 2, 0))
        self.assertEqual(sim.metrics()["leaf_folds"]["total"][
            "covered_at_start_events"], 0)

    def test_covered_at_start_uses_local_recursive_claim_and_freezes_at_start(self):
        sim, leaves = circuit_fixture(3)
        admit(sim, leaves)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        sim._tick(0, 1)
        # The plan was queued before these arrived. A structural child outside
        # the proof's claim is not covered, nor is a mode 0 witness-backed claim.
        recursive = sim.arena.new_aggregate(leaves[:2], 0, 1, claimed=1)
        mode_zero = sim.arena.new_aggregate(leaves[2:], 0, 1, claimed=4)
        admit(sim, [recursive, mode_zero])
        sim.nodes[0].view.witness_backed.add(mode_zero.id)
        sim.run(until=10)
        # A later proof cannot change the already sampled start population;
        # losing the earlier proof while running cannot erase it either.
        later = sim.arena.new_aggregate(leaves[1:], 10, 1, claimed=6)
        admit(sim, [later])
        del sim.nodes[0].view.held[recursive.id]
        for name in ("leaf_folds", "witness_folds"):
            in_flight = sim.metrics()[name]
            self.assertEqual(in_flight["completed_service_ms"], 0)
            self.assertEqual(in_flight["total"]["fold_events"], 0)
            self.assertEqual(in_flight["total"]["covered_at_start_events"], 0)
        sim.run(until=35)
        for name in ("leaf_folds", "witness_folds"):
            record = sim.metrics()[name]
            self.assertEqual(record["total"]["fold_events"], 3)
            self.assertEqual(record["total"]["covered_at_start_events"], 1)
            self.assertEqual(record["total"]["covered_at_start_service_ms"], 10)
            self.assertEqual(record["by_kind"]["pq"]["covered_at_start_events"], 1)
            self.assertEqual(record["covered_at_start_events_by_node"], [1, 0])
            self.assertEqual(record["covered_at_start_service_ms_by_node"], [10, 0])

    def test_queued_removal_attributes_missing_priced_leaf_work_only_at_completion(self):
        sim, leaves = circuit_fixture(2)
        admit(sim, leaves)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        sim._tick(0, 1)
        del sim.arena.objects[leaves[0].id]
        sim.run(until=15)
        for name in ("leaf_folds", "witness_folds"):
            in_flight = sim.metrics()[name]
            self.assertEqual(in_flight["completed_service_ms"], 0)
            self.assertEqual(in_flight["total"]["service_ms"], 0)
            self.assertEqual(in_flight["total"]["unattributed_service_ms"], 0)
        sim.run(until=25)
        for name in ("leaf_folds", "witness_folds"):
            record = sim.metrics()[name]
            self.assertEqual(record["completed_service_ms"], 20)
            self.assertEqual(record["total"]["fold_events"], 1)
            self.assertEqual(record["total"]["service_ms"], 10)
            self.assertEqual(record["total"]["unattributed_service_ms"], 10)
            self.assertEqual(record["by_kind"]["pq"]["unattributed_service_ms"], 10)

    def test_empty_input_completion_still_enters_service_denominator(self):
        sim, leaves = circuit_fixture(2)
        admit(sim, leaves)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        run = sim._tick(0, 1)
        for leaf in leaves:
            del sim.arena.objects[leaf.id]
        sim.run(until=25)
        self.assertEqual(sim.proofs_completed, 0)
        self.assertEqual(run.work_outcome, "cancelled")
        self.assertEqual(run.leaf_service_ms, {})
        for name in ("leaf_folds", "witness_folds"):
            record = sim.metrics()[name]
            self.assertEqual(record["completed_service_ms"], 20)
            self.assertEqual(record["total"]["fold_events"], 0)
            self.assertEqual(record["total"]["service_ms"], 0)
            self.assertEqual(record["total"]["unattributed_service_ms"], 20)
            self.assertEqual(record["total"]["repeat_service_share"], 0)

    def test_removed_mode_zero_input_has_unattributed_witness_service(self):
        sim, signatures, privacy, nested, cost = mixed_fixture()
        admit(sim, signatures + [privacy, nested])
        sim.nodes[0].view.witness_backed.add(nested.id)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        sim._tick(0, 1)
        duration = sim.cpu[0].waiting[0].duration
        del sim.arena.objects[nested.id]
        sim.run(until=100)
        direct = sim.metrics()["leaf_folds"]
        witness = sim.metrics()["witness_folds"]
        self.assertEqual(direct["total"]["unattributed_service_ms"], 0)
        self.assertEqual(witness["total"]["fold_events"], 3)
        self.assertAlmostEqual(witness["total"]["unattributed_service_ms"],
                               duration * 30 / cost.hashes(3, 2, 0))
        self.assertAlmostEqual(witness["by_kind"]["pq"]["unattributed_service_ms"],
                               duration * 10 / cost.hashes(3, 2, 0))
        self.assertAlmostEqual(witness["by_kind"]["privacy"]["unattributed_service_ms"],
                               duration * 20 / cost.hashes(3, 2, 0))

    def test_cancellation_releases_waiting_entries_but_counts_active_completion(self):
        sim, leaves = circuit_fixture(4, fold_plan="binary", on_tick_overrun="abandon")
        admit(sim, leaves)
        first = sim._tick(0, 1)
        second = sim._tick(0, 2)
        self.assertEqual(set(first.leaf_service_ms), {0})
        self.assertEqual(set(second.leaf_service_ms), {0, 1, 2})
        sim._tick(0, 3)
        self.assertEqual(second.leaf_service_ms, {})
        self.assertEqual(first.work_outcome, "cancelled")
        sim.run(until=20)
        self.assertEqual(first.leaf_service_ms, {})
        for name in ("leaf_folds", "witness_folds"):
            record = sim.metrics()[name]
            self.assertEqual(record["completed_service_ms"], 20)
            self.assertEqual(record["total"]["service_ms"], 20)
            self.assertEqual(record["total"]["fold_events"], 2)

    def test_queue_refusal_releases_refused_and_withdrawn_entries(self):
        sim, leaves = circuit_fixture(4, fold_plan="binary", cpu_queue_jobs=1)
        admit(sim, leaves)
        self.assertIsNone(sim._tick(0, 1))
        run = sim.cpu[0].current_job.tag[0]
        self.assertEqual(run.work_outcome, "refused_queue")
        self.assertEqual(set(run.leaf_service_ms), {0})
        self.assertEqual(len(sim.cpu[0].waiting), 0)
        sim.run(until=20)
        self.assertEqual(run.leaf_service_ms, {})
        for name in ("leaf_folds", "witness_folds"):
            self.assertEqual(sim.metrics()[name]["completed_service_ms"], 20)

    def test_unsupported_leaf_kind_is_counted_without_service_or_exception(self):
        sim, leaves = circuit_fixture()
        leaves[0].kind = "x"
        admit(sim, leaves)
        sim._tick(0, 1)
        sim.run(until=20)
        self.assertEqual(sim.proofs_completed, 1)
        for name in ("leaf_folds", "witness_folds"):
            record = sim.metrics()[name]
            self.assertEqual(record["unsupported_leaf_events"], 1)
            self.assertEqual(record["total"]["fold_events"], 0)
            self.assertEqual(record["total"]["service_ms"], 0)
            self.assertEqual(record["total"]["repeat_service_ms"], 0)
            self.assertEqual(record["completed_service_ms"], 20)

    def test_resolved_inputs_survive_arena_removal_before_completion(self):
        sim, leaves = circuit_fixture()
        admit(sim, leaves)
        sim._tick(0, 1)
        del sim.arena.objects[leaves[0].id]
        sim.run(until=10)
        self.assertEqual(sim.metrics()["leaf_folds"]["total"]["fold_events"], 1)

    def test_non_circuit_bases_keep_counts_and_disable_every_service_field(self):
        for basis in ("deps", "inputs"):
            with self.subTest(basis=basis):
                sim, leaves = fixture(1, prove_cost_basis=basis)
                admit(sim, leaves)
                sim._tick(0, 1)
                sim._tick(0, 2)
                sim.run(until=20)
                record = sim.metrics()["leaf_folds"]
                self.assertEqual(record["total"]["fold_events"], 2)
                self.assertEqual(record["total"]["repeat_events"], 1)
                self.assertEqual(record["total"]["multiplicity"]["mean"], 1)
                for name in ("leaf_folds", "witness_folds"):
                    record = sim.metrics()[name]
                    for row in [record["total"], *record["by_kind"].values()]:
                        for field in ("service_ms", "repeat_service_ms",
                                      "repeat_service_share", "covered_at_start_service_ms",
                                      "unattributed_service_ms"):
                            self.assertEqual(row[field], -1)
                    self.assertEqual(record["repeat_service_ms_by_node"], [-1, -1])
                    self.assertEqual(record["covered_at_start_service_ms_by_node"], [-1, -1])
                    self.assertEqual(record["completed_service_ms"], -1)

    def test_empty_and_zero_service_populations_have_no_ratio(self):
        for cost in (circuit(ms_per_hash=0),
                     circuit(sphincs_hashes=0, leanstark_hashes=0,
                             recursive_hashes=0)):
            with self.subTest(cost=cost):
                sim, leaves = circuit_fixture(costs=CostTable(prove_recursive=cost))
                before = sim.metrics()["leaf_folds"]
                self.assertEqual(before["total"]["fold_events"], 0)
                for field in ("mean", "p50", "p95", "max"):
                    self.assertEqual(before["total"]["multiplicity"][field], -1)
                admit(sim, leaves)
                sim._tick(0, 1)
                sim._tick(0, 2)
                sim.run(until=1)
                total = sim.metrics()["leaf_folds"]["total"]
                self.assertEqual(total["fold_events"], 2)
                self.assertEqual(total["service_ms"], 0)
                self.assertEqual(total["repeat_service_ms"], 0)
                self.assertEqual(total["repeat_service_share"], -1)

    def test_histogram_boundaries_and_exact_percentiles_at_1000_nodes(self):
        observer = LeafFolds(1000, circuit_basis=False)
        arena = Arena()
        values = [1, 2, 3, 4, 5, 8, 9, 16, 17, 32, 33, 64, 65, 128,
                  129, 256, 257, 512, 513, 1000]
        for multiplicity in values:
            leaf = arena.new_leaf(KIND_PQ, 0)
            for node_id in range(multiplicity):
                observer.record(node_id, [leaf], 1, (0, 0))
        arena.new_leaf(KIND_PRIVACY, 0)  # Never folded: outside this population.
        total = observer.metrics()["total"]
        distribution = total["multiplicity"]
        self.assertEqual(total["leaves_folded"], len(values))
        self.assertEqual(total["fold_events"], sum(values))
        self.assertEqual(distribution["mean"], sum(values) / len(values))
        self.assertEqual(distribution["p50"], 32)
        self.assertEqual(distribution["p95"], 513)
        self.assertEqual(distribution["max"], 1000)
        self.assertEqual(distribution["histogram"], [
            {"minimum": 1, "maximum": 1, "count": 1},
            {"minimum": 2, "maximum": 2, "count": 1},
            {"minimum": 3, "maximum": 4, "count": 2},
            {"minimum": 5, "maximum": 8, "count": 2},
            {"minimum": 9, "maximum": 16, "count": 2},
            {"minimum": 17, "maximum": 32, "count": 2},
            {"minimum": 33, "maximum": 64, "count": 2},
            {"minimum": 65, "maximum": 128, "count": 2},
            {"minimum": 129, "maximum": 256, "count": 2},
            {"minimum": 257, "maximum": 512, "count": 2},
            {"minimum": 513, "maximum": None, "count": 2},
        ])


class TestLeafFoldNeutrality(unittest.TestCase):
    def test_disabled_observer_preserves_mainnet_shaped_run(self):
        results = []
        for enabled in (False, True):
            stream = io.StringIO()
            sim = make_sim(
                trace=stream, seed=7, capacity=1000, chain_notify="overlay",
                aggregation_trigger="aligned", fold_plan="binary",
                aggregation_interval=20, slot_length=100,
                on_tick_overrun="abandon", proving_budget=1000,
                costs=CostTable(prove_recursive=calibrated_prover(4, 2),
                                jitter_frac=0.2, seed=7),
                verify_cost=NativeVerifyCost(0.1, 0.5),
                prove_cost_basis="circuit")
            if not enabled:
                sim.leaf_folds = None
                sim.witness_folds = None
            for index, kind in enumerate(mixed_kinds(12, 0.25, seed=7)):
                sim.schedule_birth(index * 7, kind, node_id=index % 2)
            sim.schedule_ticks(10)
            sim.schedule_slots(2)
            with mock.patch.object(Arena, "claim", autospec=True,
                                   side_effect=Arena.claim) as claims:
                with mock.patch.object(Arena, "coverage", autospec=True,
                                       side_effect=Arena.coverage) as coverage:
                    sim.run(until=250)
                    record = sim.metrics()
            if enabled:
                self.assertGreater(record["leaf_folds"]["total"]["fold_events"], 0)
                self.assertGreater(record["leaf_folds"]["by_kind"]["privacy"][
                    "fold_events"], 0)
                self.assertEqual(json.dumps(record), json.dumps(sim.metrics()))
                self.assertNotIn("leaf_folds", sim.variant())
                self.assertNotIn("witness_folds", sim.variant())
            else:
                self.assertEqual(record["leaf_folds"], -1)
                self.assertEqual(record["witness_folds"], -1)
            del record["leaf_folds"]
            del record["witness_folds"]
            results.append((stream.getvalue(), csv_row(record),
                            sim.engine.events_processed, claims.call_count,
                            coverage.call_count, sim.costs._rng.getstate(),
                            json.dumps(record)))
        self.assertEqual(results[0], results[1])

    def test_metrics_have_stable_order_and_detached_nested_snapshots(self):
        sim, leaves = circuit_fixture()
        admit(sim, leaves)
        sim._tick(0, 1)
        sim.run(until=10)
        first = sim.metrics()["leaf_folds"]
        expected = json.dumps(first)
        self.assertEqual(list(first), [
            "total", "by_kind", "fold_events_by_node", "repeat_events_by_node",
            "repeat_service_ms_by_node", "covered_at_start_events_by_node",
            "covered_at_start_service_ms_by_node", "completed_service_ms",
            "unsupported_leaf_events"])
        self.assertEqual(list(first["by_kind"]), ["pq", "privacy"])
        first["total"]["multiplicity"]["histogram"][0]["count"] = -99
        first["by_kind"]["pq"]["service_ms"] = -99
        first["fold_events_by_node"][0] = -99
        first["repeat_events_by_node"][0] = -99
        first["repeat_service_ms_by_node"][0] = -99
        self.assertEqual(json.dumps(sim.metrics()["leaf_folds"]), expected)


if __name__ == "__main__":
    unittest.main()
