"""Fixed-age denominators and utilisation windows cannot follow the drain."""

import io
import unittest

from starksim.arena import KIND_PQ
from starksim.engine import Job
from starksim.outcomes import (BusySample, compare_cohorts, outcomes_at_age,
                               transaction_outcomes, utilisation_window)

from tests.support import make_sim


class TestFixedAgeOutcomes(unittest.TestCase):
    def mixed_run(self):
        sim = make_sim(relay_policy="wrapper_only", slot_length=10.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0, lifetime=30.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=1, lifetime=10.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=1)
        sim.schedule_birth(95.0, KIND_PQ, node_id=0)
        sim.schedule_birth(95.0, KIND_PQ, node_id=1, lifetime=3.0)
        sim.schedule_slots(10)
        sim.run(until=100.0)
        return sim

    def assert_partition(self, summary):
        self.assertEqual(summary["born_txs"],
                         summary["mature_txs"] + summary["censored_txs"])
        self.assertEqual(summary["mature_txs"],
                         sum(summary[key + "_txs"] for key in
                             ("included", "expired", "pending", "unknown")))

    def test_timestamps_are_birth_indexed_and_expiry_is_not_inclusion(self):
        sim = self.mixed_run()
        self.assertEqual(sim.inclusion_times, {0: 10.0, 3: 100.0})
        self.assertEqual(sim.expiry_times, {1: 10.0, 4: 98.0})
        rows = sim.metrics()["transaction_outcomes"]
        self.assertEqual(sim.metrics()["outcomes_observed_until_ms"], 100.0)
        self.assertEqual(rows, [
            {"tx": 0, "born_ms": 0.0, "included_ms": 10.0, "expired_ms": -1},
            {"tx": 1, "born_ms": 0.0, "included_ms": -1, "expired_ms": 10.0},
            {"tx": 2, "born_ms": 0.0, "included_ms": -1, "expired_ms": -1},
            {"tx": 3, "born_ms": 95.0, "included_ms": 100.0, "expired_ms": -1},
            {"tx": 4, "born_ms": 95.0, "included_ms": -1, "expired_ms": 98.0},
        ])
        self.assertEqual(len(sim.inclusion_times), sim.chain.included_count())
        self.assertEqual(len(sim.expiry_times), sim.chain.expired_count())
        self.assertTrue(sim.inclusion_times.keys().isdisjoint(sim.expiry_times))
        rows[0]["included_ms"] = -99
        self.assertEqual(sim.metrics()["transaction_outcomes"][0]
                         ["included_ms"], 10.0)

    def test_immature_births_are_neither_success_failure_nor_pending(self):
        sim = self.mixed_run()
        summary = outcomes_at_age(sim, 20.0, 0.0, 100.0)
        self.assert_partition(summary)
        self.assertEqual(summary["born_txs"], 5)
        self.assertEqual(summary["mature_txs"], 3)
        self.assertEqual(summary["censored_txs"], 2)
        for outcome in ("included", "expired", "pending"):
            self.assertEqual(summary[outcome + "_txs"], 1)
            self.assertEqual(summary[outcome + "_fraction"], 1 / 3)
        late = outcomes_at_age(sim, 20.0, 90.0, 100.0)
        self.assert_partition(late)
        self.assertEqual(late["censored_txs"], 2)
        for outcome in ("included", "expired", "pending"):
            self.assertEqual(late[outcome + "_txs"], 0)
            self.assertEqual(late[outcome + "_fraction"], -1.0)
        contrast = compare_cohorts(sim, 20.0, (0.0, 50.0), (50.0, 100.0))
        self.assertFalse(contrast["comparable"])
        self.assertEqual(contrast["included_fraction_late_minus_early"], -1.0)
        sim.run(until=115.0)
        late = outcomes_at_age(sim, 20.0, 90.0, 100.0)
        self.assert_partition(late)
        self.assertEqual(late["censored_txs"], 0)
        self.assertEqual(late["included_fraction"], 0.5)
        self.assertEqual(late["expired_fraction"], 0.5)

    def test_future_settlement_does_not_rewrite_status_at_an_earlier_age(self):
        sim = self.mixed_run()
        before = outcomes_at_age(sim, 9.0, 0.0, 50.0)
        self.assertEqual(before["pending_txs"], 3)
        at = outcomes_at_age(sim, 10.0, 0.0, 50.0)
        self.assertEqual(at["included_txs"], 1)
        self.assertEqual(at["expired_txs"], 1)
        self.assertEqual(at["pending_txs"], 1)
        sim.run(until=500.0)
        after = outcomes_at_age(sim, 9.0, 0.0, 50.0)
        before.pop("observed_until_ms")
        after.pop("observed_until_ms")
        self.assertEqual(before, after)

    def test_a_boundary_with_same_time_events_pending_is_not_yet_reached(self):
        sim = make_sim(slot_length=10.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0, lifetime=10.0)
        sim.schedule_slots(1)
        sim.run(until=9.0)
        # Two events are due at 10: the expiry and the slot. Stop after one,
        # through the engine, since the wiring's run() has no event limit.
        sim.engine.run(max_events=1)
        self.assertEqual(sim.engine.now, 10.0)
        self.assertEqual(sim.engine.calendar.peek_time(), 10.0)
        at = outcomes_at_age(sim, 10.0, 0.0, 1.0)
        self.assertFalse(at["boundary_complete"])
        self.assertEqual(at["censored_txs"], 1)
        self.assertEqual(at["mature_txs"], 0)
        self.assertFalse(compare_cohorts(sim, 10.0, (0.0, 1.0), (0.0, 1.0))
                         ["comparable"])
        sim.run(until=10.0)
        at = outcomes_at_age(sim, 10.0, 0.0, 1.0)
        self.assertTrue(at["boundary_complete"])
        self.assertEqual(at["expired_fraction"], 1.0)

    def test_expiry_wins_at_slot_boundary_and_does_not_require_slots(self):
        for slots in (0, 1):
            sim = make_sim(slot_length=10.0)
            sim.schedule_birth(0.0, KIND_PQ, node_id=0, lifetime=10.0)
            sim.schedule_slots(slots)
            sim.run(until=10.0)
            result = outcomes_at_age(sim, 10.0, 0.0, 1.0)
            self.assertEqual(result["expired_fraction"], 1.0)
            self.assertEqual(result["included_fraction"], 0.0)
            self.assertEqual(result["pending_fraction"], 0.0)
            self.assertEqual(sim.inclusion_times, {})
            self.assertEqual(sim.expiry_times, {0: 10.0})

    def test_equal_age_cohorts_compare_equally_despite_different_terminal_ages(self):
        sim = make_sim(slot_length=10.0)
        sim.schedule_birth(5.0, KIND_PQ, node_id=0)
        sim.schedule_birth(105.0, KIND_PQ, node_id=0)
        sim.schedule_slots(15)
        sim.run(until=150.0)
        for age, expected in ((4.0, 0.0), (5.0, 1.0)):
            comparison = compare_cohorts(sim, age, (0.0, 50.0), (100.0, 150.0))
            self.assertTrue(comparison["comparable"])
            self.assertEqual(comparison["early"]["included_fraction"], expected)
            self.assertEqual(comparison["late"]["included_fraction"], expected)
            self.assertEqual(comparison["included_fraction_late_minus_early"], 0)

    def test_minus_one_can_also_be_a_measured_difference(self):
        sim = make_sim(relay_policy="wrapper_only", slot_length=10.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.schedule_birth(50.0, KIND_PQ, node_id=1)
        sim.schedule_slots(10)
        sim.run(until=100.0)
        comparison = compare_cohorts(sim, 20.0, (0.0, 50.0), (50.0, 100.0))
        self.assertTrue(comparison["comparable"])
        self.assertEqual(comparison["included_fraction_late_minus_early"], -1.0)

    def test_empty_or_still_open_cohorts_cannot_support_a_comparison(self):
        sim = make_sim()
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.run(until=10.0)
        empty = outcomes_at_age(sim, 0.0, 1.0, 10.0)
        self.assertEqual(empty["included_fraction"], -1.0)
        open_cohort = outcomes_at_age(sim, 0.0, 0.0, 20.0)
        self.assertFalse(open_cohort["cohort_closed"])
        self.assertEqual(open_cohort["mature_txs"], 1)
        self.assertFalse(compare_cohorts(sim, 0.0, (0.0, 1.0),
                                         (1.0, 20.0))["comparable"])

    def test_birth_interval_is_half_open_and_zero_age_is_observable(self):
        sim = make_sim()
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.schedule_birth(10.0, KIND_PQ, node_id=0)
        sim.run(until=10.0)
        first = outcomes_at_age(sim, 0.0, 0.0, 10.0)
        second = outcomes_at_age(sim, 0.0, 10.0, 20.0)
        self.assertEqual(first["born_txs"], 1)
        self.assertEqual(second["mature_txs"], 1)
        self.assertEqual(second["pending_txs"], 1)

    def test_unobserved_settlement_is_not_pending_or_a_zero_fraction(self):
        sim = make_sim()
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.run(until=10.0)
        sim.chain.expire(1)
        result = outcomes_at_age(sim, 5.0, 0.0, 10.0)
        self.assert_partition(result)
        self.assertEqual(result["unknown_txs"], 1)
        self.assertEqual(result["pending_txs"], 0)
        self.assertEqual(result["expired_fraction"], -1.0)

    def test_invalid_horizons_and_cohort_bounds_are_refused(self):
        sim = make_sim()
        for age in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                outcomes_at_age(sim, age, 0.0, 10.0)
        for bounds in ((-1, 10), (10, 10), (10, 5), (0, float("inf"))):
            with self.assertRaises(ValueError):
                outcomes_at_age(sim, 1.0, *bounds)

    def test_leaf_indices_survive_interleaved_aggregate_ids(self):
        sim = make_sim(relay_policy="wrapper_only", slot_length=10.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.run(until=1.0)
        sim.arena.new_aggregate([sim.arena.leaves[0]], 1.0, creator=0)
        sim.schedule_birth(2.0, KIND_PQ, node_id=0)
        sim.schedule_slots(1)
        sim.run(until=11.0)
        leaf = sim.arena.leaves[1]
        self.assertNotEqual(leaf.id, leaf.leaf_index)
        self.assertEqual(set(sim.inclusion_times), {0, 1})
        self.assertEqual([row["tx"] for row in transaction_outcomes(sim)], [0, 1])
        result = outcomes_at_age(sim, 10.0, 0.0, 3.0)
        self.assertEqual(result["mature_txs"], 1)
        self.assertEqual(result["censored_txs"], 1)

    def test_queries_do_not_change_trace_events_cache_or_variant(self):
        def run(observe):
            trace = io.StringIO()
            sim = make_sim(trace=trace, slot_length=10.0)
            sim.schedule_birth(0.0, KIND_PQ, node_id=0, lifetime=20.0)
            sim.schedule_slots(2)
            sim.run(until=5.0)
            if observe:
                sample = BusySample(sim, 5.0)
                outcomes_at_age(sim, 10.0, 0.0, 10.0)
                transaction_outcomes(sim)
            sim.run(until=30.0)
            if observe:
                utilisation_window(sample, BusySample(sim, 30.0))
            return trace.getvalue(), sim.metrics(), sim.engine.events_processed

        observed = run(True)
        control = run(False)
        self.assertEqual(observed, control)


class TestUtilisationWindows(unittest.TestCase):
    def test_idle_part_of_window_is_not_whole_run_utilisation(self):
        sim = make_sim()
        sim.cpu[0].submit(Job(10.0))
        sim.run(until=5.0)
        start = BusySample(sim, 5.0)
        sim.run(until=15.0)
        result = utilisation_window(start, BusySample(sim, 15.0))
        self.assertEqual(result["cpu_busy_ms_by_node"], [5.0, 0.0])
        self.assertEqual(result["cpu_utilisation_by_node"], [0.5, 0.0])
        self.assertEqual(result["cpu_utilisation"], 0.25)
        self.assertEqual(sim.mean_cpu_utilisation(), 1 / 3)

    def test_counter_differences_include_active_work_and_survive_a_long_drain(self):
        sim = make_sim()
        sim.cpu[0].submit(Job(10.0))
        sim.cpu[0].submit(Job(20.0))
        sim.egress[1].submit(Job(40.0))
        sim.run(until=5.0)
        start = BusySample(sim, 5.0)
        sim.run(until=15.0)
        end = BusySample(sim, 15.0)
        window = utilisation_window(start, end)
        self.assertEqual(window, {
            "start_ms": 5.0, "end_ms": 15.0,
            "cpu_busy_ms_by_node": [10.0, 0.0],
            "egress_busy_ms_by_node": [0.0, 10.0],
            "cpu_utilisation_by_node": [1.0, 0.0],
            "egress_utilisation_by_node": [0.0, 1.0],
            "cpu_utilisation": 0.5, "egress_utilisation": 0.5,
        })
        for kind in ("cpu", "egress"):
            self.assertEqual(sum(window[kind + "_busy_ms_by_node"]),
                             window[kind + "_utilisation"] * 10.0 * 2)
        sim.run(until=100.0)
        self.assertEqual(utilisation_window(start, end), window)
        self.assertNotEqual(sim.mean_cpu_utilisation(), window["cpu_utilisation"])

    def test_unreached_and_historical_counter_queries_are_refused(self):
        sim = make_sim()
        sim.run(until=10.0)
        for boundary in (5.0, 15.0):
            with self.assertRaises(ValueError):
                BusySample(sim, boundary)
        start = BusySample(sim, 10.0)
        with self.assertRaises(ValueError):
            utilisation_window(start, start)
        sim.run(until=20.0)
        with self.assertRaises(ValueError):
            utilisation_window(BusySample(sim, 20.0), start)
        other = make_sim()
        other.run(until=20.0)
        with self.assertRaises(ValueError):
            utilisation_window(start, BusySample(other, 20.0))


if __name__ == "__main__":
    unittest.main()
