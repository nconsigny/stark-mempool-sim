"""Decision-time observations must not confuse completion with useful output."""

import io
import unittest
from unittest import mock

from starksim.aggregation import eip_priority
from starksim.arena import Arena
from starksim.costs import CircuitCost, CostTable, VERIFY_RECV
from starksim.decision_metrics import SelectionDecisions, SelectionObservation
from starksim.engine import Job
from starksim.netsim import csv_row

from tests.support import make_sim
from tests.test_work_metrics import proving_fixture


class TestPlanDeadlines(unittest.TestCase):
    def counts(self, sim):
        record = sim.metrics()["plan_deadlines"]
        for row in [record["total"]] + record["by_node"]:
            self.assertEqual(
                row["submitted"], row["completed_within_interval"]
                + row["completed_late"] + row["terminal_without_completion"]
                + row["still_outstanding"])
            self.assertEqual(row["still_outstanding"],
                             row["outstanding_censored"]
                             + row["outstanding_overdue"])
            self.assertEqual(row["terminal_without_completion"],
                             row["cancelled"] + row["refused_queue"]
                             + row["refused_over_capacity"])
        for key, value in record["total"].items():
            if isinstance(value, int):
                self.assertEqual(value, sum(row[key] for row in record["by_node"]))
        work = sim.metrics()["plan_work"]["total"]
        counts = record["total"]
        self.assertEqual(counts["submitted"], work["submitted"])
        self.assertEqual(counts["completed_within_interval"]
                         + counts["completed_late"], work["completed"])
        self.assertEqual(counts["still_outstanding"], work["still_pending"])
        return counts

    def test_exact_deadline_and_late_completion_include_queue_wait(self):
        for waiting in (0, 5):
            sim = proving_fixture(count=2, aggregation_interval=10)
            sim.run(until=5)
            if waiting:
                sim.cpu[0].submit(Job(waiting, kind=VERIFY_RECV))
            run = sim._tick(0, 1)
            self.assertEqual(run.decision_ms, 5)
            sim.run(until=15 + waiting)
            counts = self.counts(sim)
            self.assertEqual(counts["completed_within_interval"], int(waiting == 0))
            self.assertEqual(counts["completed_late"], int(waiting != 0))
            for name, latency in (("start", waiting), ("root", 10 + waiting),
                                  ("emission", 10 + waiting)):
                histogram = counts["decision_to_" + name + "_ms"]
                self.assertEqual(histogram["count"], 1)
                self.assertEqual(histogram["sum"], latency)
                self.assertEqual(histogram["square_sum"], latency ** 2)
                self.assertEqual(histogram["max"], latency)
                self.assertEqual(sum(histogram["histogram"]), 1)
            self.assertIsNone(run.decision_ms)
            self.assertEqual(sim.plan_deadlines.outstanding, {})

    def test_immature_unfinished_plan_is_censored_not_a_late_completion(self):
        sim = proving_fixture(aggregation_interval=10)
        sim.cpu[0].submit(Job(20, kind=VERIFY_RECV))
        sim._tick(0, 1)
        for now, censored, overdue in ((9, 1, 0), (10, 1, 0), (11, 0, 1)):
            sim.run(until=now)
            counts = self.counts(sim)
            self.assertEqual(counts["outstanding_censored"], censored)
            self.assertEqual(counts["outstanding_overdue"], overdue)
            self.assertEqual(counts["completed_late"], 0)
            self.assertEqual(counts["decision_to_start_ms"]["count"], 0)
            self.assertEqual(counts["decision_to_root_ms"]["max"], -1)
        sim.run(until=30)
        self.assertEqual(self.counts(sim)["completed_late"], 1)

    def test_deadline_equality_uses_calendar_time_not_subtraction_roundoff(self):
        sim = proving_fixture(count=2, aggregation_interval=0.2,
                              costs=CostTable(prove_recursive=0.2))
        sim.run(until=0.1)
        sim._tick(0, 1)
        sim.run(until=0.1 + 0.2)
        self.assertEqual(self.counts(sim)["completed_within_interval"], 1)
        self.assertEqual(self.counts(sim)["completed_late"], 0)

    def test_refusal_and_cancellation_do_not_wait_for_a_deadline(self):
        sim = proving_fixture(prove_cost_basis="circuit",
                              costs=CostTable(prove_recursive=CircuitCost(
                                  1, trace_cap=1)))
        sim._tick(0, 1)
        counts = self.counts(sim)
        self.assertEqual(counts["refused_over_capacity"], 1)
        self.assertEqual(counts["decision_to_start_ms"]["count"], 0)
        self.assertEqual(counts["still_outstanding"], 0)
        sim = proving_fixture(fold_plan="binary", cpu_queue_jobs=0)
        sim._tick(0, 1)
        self.assertEqual(self.counts(sim)["refused_queue"], 1)
        sim.run(until=10)
        self.assertEqual(self.counts(sim)["decision_to_root_ms"]["count"], 0)
        self.assertEqual(sim.plan_deadlines.outstanding, {})
        sim = proving_fixture(fold_plan="binary", on_tick_overrun="abandon")
        first = sim._tick(0, 1)
        sim.run(until=1)
        sim._tick(0, 2)
        self.assertEqual(self.counts(sim)["cancelled"], 1)
        self.assertIsNone(first.decision_ms)
        sim.run(until=40)
        self.assertEqual(self.counts(sim)["cancelled"], 1)

    def test_expiry_while_queued_can_complete_without_emitting(self):
        sim = make_sim(relay_policy="wrapper_only", aggregation_interval=10,
                       costs=CostTable(prove_recursive=10))
        sim._birth("q", 0, lifetime=2)
        sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
        run = sim._tick(0, 1)
        sim.run(until=15)
        counts = self.counts(sim)
        self.assertEqual(run.claims[run.plan.root], 0)
        self.assertEqual(counts["completed_late"], 1)
        self.assertEqual(counts["completed_without_emission"], 1)
        self.assertEqual(counts["decision_to_root_ms"]["sum"], 15)
        self.assertEqual(counts["decision_to_emission_ms"]["count"], 0)
        self.assertEqual(counts["decision_to_emission_ms"]["max"], -1)

    def test_local_emission_does_not_wait_for_egress_acceptance(self):
        sim = proving_fixture(capacity=1, egress_queue_bytes=0)
        sim._send(0, 1, sim.size_model.object_payload(sim.arena.leaves[0]))
        sim._tick(0, 1)
        sim.run(until=10)
        self.assertEqual(self.counts(sim)["decision_to_emission_ms"]["sum"], 10)
        self.assertEqual(sim.plan_work.metrics()["total"]["roots_emitted"], 0)
        self.assertEqual(sim.egress_refused.messages_by_kind["wrapper"], 1)

    def test_unclocked_deadline_statistics_are_unavailable(self):
        sim = proving_fixture(aggregation_trigger="when_ready", proving_budget=100)
        sim._tick(0, 1)
        sim.run(until=10)
        self.assertEqual(sim.metrics()["plan_deadlines"], -1)
        self.assertEqual(sim.plan_deadlines.outstanding, {})

    def test_first_start_is_once_for_a_multijob_plan_and_totals_span_nodes(self):
        sim = proving_fixture(fold_plan="binary")
        for leaf in sim.arena.leaves:
            sim.nodes[1].admit(leaf, 0)
        sim._tick(0, 1)
        sim._tick(1, 1)
        sim.run(until=30)
        counts = self.counts(sim)
        self.assertEqual(counts["decision_to_start_ms"]["count"], 2)
        self.assertEqual(counts["decision_to_root_ms"]["sum"], 60)
        self.assertEqual(counts["completed_within_interval"], 2)


class TestSelectionDecisions(unittest.TestCase):
    def fixture(self, budget=100):
        sim = make_sim(relay_policy="wrapper_only", proving_budget=budget,
                       costs=CostTable(prove_recursive=lambda n: n))
        leaves = [sim.arena.new_leaf("q", 0, fee=fee)
                  for fee in (0, 0, 0, 0, 10, 20)]
        node = sim.nodes[0]
        previous = sim.arena.new_aggregate(leaves[:1], 0, 0, claimed=1)
        peers = [sim.arena.new_aggregate(leaves[1:3], 0, 1, claimed=6),
                 sim.arena.new_aggregate(leaves[2:4], 0, 1, claimed=12)]
        for obj in [previous] + peers + leaves[4:]:
            node.admit(obj, 0)
        sim.own_aggregate[0] = previous
        return sim, leaves, previous, peers

    def test_previous_overlapping_peers_and_fee_ranked_leaves(self):
        sim, leaves, previous, peers = self.fixture()
        sim.selection_decisions = SelectionDecisions(2, sample_limit=1)
        run = sim._tick(0, 1)
        self.assertEqual(run.plan.selection.inputs,
                         (previous.id, peers[0].id, peers[1].id,
                          leaves[5].id, leaves[4].id))
        result = sim.metrics()["selection_decisions"]
        expected = {"previous_root": {"objects": 1, "marginal_live_txs": 1},
                    "peer_aggregates": {"objects": 2, "marginal_live_txs": 3},
                    "fee_leaves": {"objects": 2, "marginal_live_txs": 2}}
        self.assertEqual(result["total"]["tiers"], expected)
        self.assertEqual(result["total"]["selected_live_union"]["sum"], 6)
        self.assertEqual(result["total"]["remaining_eligible_union"]["sum"], 0)
        self.assertEqual(result["samples"][0]["tiers"], expected)
        self.assertEqual(result["samples"][0]["budget_stop"], "none")
        sim._tick(0, 2)
        self.assertEqual(len(sim.metrics()["selection_decisions"]["samples"]), 1)

    def test_budget_leaves_union_not_additive_claim_widths(self):
        sim, leaves, previous, peers = self.fixture(budget=3)
        sim.selection_decisions = SelectionDecisions(2, sample_limit=1)
        sim._tick(0, 1)
        record = sim.metrics()["selection_decisions"]
        sample = record["samples"][0]
        self.assertEqual(sample["budget_stop"], "both")
        self.assertEqual(sample["selected_live_union"], 3)
        # Remaining peer claim 1100 overlaps selected 0111 in bit 2;
        # only bit 3 plus the two fee leaves are left behind.
        self.assertEqual(sample["remaining_eligible_union"], 3)
        self.assertEqual(record["total"]["tiers"]["peer_aggregates"]["objects"], 1)
        self.assertEqual(record["total"]["tiers"]["fee_leaves"]["objects"], 0)

    def test_expired_claims_are_not_locally_eligible(self):
        sim, leaves, previous, peers = self.fixture(budget=3)
        sim.chain.expire(1 << leaves[4].leaf_index)
        sim._tick(0, 1)
        counts = sim.metrics()["selection_decisions"]["total"]
        self.assertEqual(counts["remaining_eligible_union"]["sum"], 2)

    def test_budget_stop_in_either_tier_and_floor_is_not_a_stop(self):
        for budget, reason, selected, remaining in (
                (4, "fee_leaves", 4, 2), (100, "none", 6, 0)):
            sim, _, _, _ = self.fixture(budget=budget)
            sim._tick(0, 1)
            counts = sim.metrics()["selection_decisions"]["total"]
            self.assertEqual(counts["budget_stops"][reason], 1)
            self.assertEqual(counts["selected_live_union"]["sum"], selected)
            self.assertEqual(counts["remaining_eligible_union"]["sum"], remaining)
        sim, leaves, _, _ = self.fixture(budget=3)
        for leaf in leaves[4:]:
            sim.nodes[0].view.held.pop(leaf.id)
        sim._tick(0, 1)
        self.assertEqual(sim.metrics()["selection_decisions"]["total"]
                         ["budget_stops"]["peer_aggregates"], 1)
        sim = proving_fixture(count=2, proving_budget=1)
        sim._tick(0, 1)
        counts = sim.metrics()["selection_decisions"]["total"]
        self.assertEqual(counts["budget_stops"]["none"], 1)
        self.assertEqual(counts["selected_live_union"]["sum"], 2)

    def test_optional_observation_preserves_claim_reads_and_selection(self):
        selections = []
        calls = []
        for enabled in (False, True):
            sim, _, previous, _ = self.fixture(budget=3)
            observation = SelectionObservation() if enabled else None
            with mock.patch.object(Arena, "claim", autospec=True,
                                   side_effect=Arena.claim) as reads:
                result = eip_priority(sim.nodes[0], sim.arena, 1,
                                      sim._selection_costs, 3, previous,
                                      observation=observation)
                calls.append(reads.call_count)
            selections.append((result.inputs, result.claimed, result.estimated_cost))
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(selections[0], selections[1])

    def test_unsupported_selector_is_not_zero_tier_choices(self):
        sim = proving_fixture(selection_strategy="random_pair")
        sim._tick(0, 1)
        self.assertEqual(sim.metrics()["selection_decisions"], -1)
        with self.assertRaises(ValueError):
            SelectionDecisions(2, sample_limit=-1)

    def test_empty_decision_is_observed_zero_not_an_unavailable_selector(self):
        sim = make_sim()
        sim._tick(0, 1)
        counts = sim.metrics()["selection_decisions"]["total"]
        self.assertEqual(counts["decisions"], 1)
        self.assertEqual(counts["selected_live_union"]["count"], 1)
        self.assertEqual(counts["selected_live_union"]["max"], 0)
        self.assertEqual(counts["remaining_eligible_union"]["max"], 0)
        self.assertEqual(sim.metrics()["selection_decisions"]["samples"], [])


class TestDecisionNeutrality(unittest.TestCase):
    def test_disabled_observers_preserve_trace_csv_events_and_claim_reads(self):
        results = []
        for enabled in (False, True):
            trace = io.StringIO()
            sim = make_sim(trace=trace, seed=4, capacity=1000,
                           aggregation_trigger="aligned", fold_plan="binary",
                           aggregation_interval=20, slot_length=100,
                           on_tick_overrun="abandon",
                           costs=CostTable(prove_recursive=2, verify_recv=1,
                                           jitter_frac=0.2, seed=4))
            if not enabled:
                sim.plan_deadlines = None
                sim.selection_decisions = None
            for index in range(6):
                sim.schedule_birth(index * 4, "q", node_id=index % 2)
            sim.schedule_ticks(6)
            sim.schedule_slots(2)
            with mock.patch.object(Arena, "claim", autospec=True,
                                   side_effect=Arena.claim) as reads:
                sim.run(until=250)
                result = sim.metrics()
                results.append((trace.getvalue(), csv_row(result),
                                sim.engine.events_processed, reads.call_count,
                                sim.costs._rng.getstate()))
            if enabled:
                self.assertEqual(result, sim.metrics())
                result["plan_deadlines"]["total"]["submitted"] = -99
                result["selection_decisions"]["total"]["tiers"][
                    "fee_leaves"]["objects"] = -99
                self.assertNotEqual(result, sim.metrics())
            else:
                self.assertEqual(result["plan_deadlines"], -1)
                self.assertEqual(result["selection_decisions"], -1)
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
