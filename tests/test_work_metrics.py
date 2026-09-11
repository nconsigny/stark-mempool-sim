"""Work counts distinguish waiting, service, refusal and useful output."""

import io
import unittest
from unittest import mock

from starksim.costs import (BUILD_BLOCK, PROVE_RECURSIVE, VERIFY_RECV,
                            CircuitCost, CostTable)
from starksim.engine import Job
from starksim.netsim import NetworkSim, csv_row
from starksim.statistics import POWER_MINIMUMS, Moments
from starksim.work_metrics import PlanWork

from tests.support import two_node_overlay
from tests.test_aggregation_metrics import fixture


def proving_fixture(count=4, **settings):
    sim, leaves = fixture(count, **settings)
    for leaf in leaves:
        sim.nodes[0].admit(leaf, 0.0)
    sim.nodes[0].view.bodies = (1 << count) - 1
    return sim


class TestCPUAccounting(unittest.TestCase):
    def test_wait_moments_and_active_service_by_kind(self):
        sim = NetworkSim(two_node_overlay())
        cpu = sim.cpu[0]
        cpu.submit(Job(2, kind=VERIFY_RECV))
        cpu.submit(Job(3, kind=PROVE_RECURSIVE))
        cpu.submit(Job(4, kind=VERIFY_RECV))
        cpu.submit(Job(1, kind=BUILD_BLOCK))
        sim.run(until=4)
        record = sim.metrics()["cpu_accounting"]["total"]
        self.assertEqual(record[PROVE_RECURSIVE]["service_ms"], 2)
        wait = record[PROVE_RECURSIVE]["wait"]
        self.assertEqual((wait["count"], wait["sum"], wait["square_sum"],
                          wait["max"]), (1, 2, 4, 2))
        self.assertEqual(record[VERIFY_RECV]["wait"]["count"], 1)
        self.assertEqual(record[BUILD_BLOCK]["wait"]["max"], -1)
        self.assertEqual(sum(item["service_ms"] for item in record.values()),
                         cpu.busy_time_at(sim.engine.now))
        sim.run(until=10)
        record = sim.metrics()["cpu_accounting"]["total"]
        wait = record[VERIFY_RECV]["wait"]
        self.assertEqual((wait["count"], wait["sum"], wait["square_sum"],
                          wait["max"]), (2, 5, 25, 5))
        self.assertEqual(sum(wait["histogram"]), 2)
        self.assertEqual(wait["histogram"][0], 1)
        self.assertEqual(record[VERIFY_RECV]["service_ms"], 6)
        self.assertEqual(record[PROVE_RECURSIVE]["service_ms"], 3)
        self.assertEqual(record[BUILD_BLOCK]["service_ms"], 1)

    def test_refused_and_cancelled_jobs_are_not_zero_wait_samples(self):
        sim = NetworkSim(two_node_overlay(), cpu_queue_jobs=1)
        cpu = sim.cpu[0]
        cpu.submit(Job(10, kind=VERIFY_RECV))
        cpu.submit(Job(20, kind=PROVE_RECURSIVE))
        self.assertFalse(cpu.submit(Job(30, kind=PROVE_RECURSIVE)))
        cpu.cancel_waiting(lambda job: True)
        sim.run(until=5)
        record = sim.metrics()["cpu_accounting"]["total"]
        self.assertEqual(record[PROVE_RECURSIVE]["wait"]["count"], 0)
        self.assertEqual(record[PROVE_RECURSIVE]["wait"]["max"], -1)
        self.assertEqual(record[PROVE_RECURSIVE]["refused_jobs"], 1)
        self.assertEqual(record[PROVE_RECURSIVE]["refused_estimated_ms"], 30)
        self.assertEqual(record[VERIFY_RECV]["service_ms"], 5)
        self.assertEqual(sim.metrics()["cpu_refused_estimated_ms"], 0)

    def test_refused_message_duration_matches_existing_message_count(self):
        sim = NetworkSim(two_node_overlay(1), cpu_queue_jobs=0,
                         costs=CostTable(verify_recv=10))
        sim.schedule_birth(0, "q", node_id=0)
        sim.schedule_birth(0, "q", node_id=0)
        sim.run(until=2)
        record = sim.metrics()
        self.assertEqual(record["cpu_refused_messages"], 1)
        self.assertEqual(record["cpu_refused_estimated_ms"], 10)
        self.assertEqual(record["cpu_accounting"]["by_node"][1][VERIFY_RECV][
            "refused_estimated_ms"], 10)

    def test_fixed_histogram_edges_underflow_overflow_and_detachment(self):
        moments = Moments()
        values = (0, 2 ** -21, 2 ** -20, 1, 2, 2 ** 40, 2 ** 41)
        for value in values:
            moments.record(value)
        record = moments.record_dict()
        self.assertEqual(record["count"], len(values))
        self.assertEqual(record["sum"], sum(values))
        self.assertEqual(record["square_sum"], sum(v * v for v in values))
        self.assertEqual(record["max"], 2 ** 41)
        self.assertEqual(len(record["histogram"]), len(POWER_MINIMUMS) + 2)
        self.assertEqual(record["histogram"][:3], [1, 1, 1])
        self.assertEqual(record["histogram"][-1], 2)
        record["histogram"][0] = 99
        self.assertEqual(moments.histogram[0], 1)

    def test_priority_order_and_cross_node_totals(self):
        sim = NetworkSim(two_node_overlay(), cpu_discipline="priority")
        sim.cpu[0].submit(Job(2, kind=BUILD_BLOCK))
        sim.cpu[0].submit(Job(4, kind=PROVE_RECURSIVE))
        sim.cpu[0].submit(Job(3, kind=VERIFY_RECV))
        sim.cpu[1].submit(Job(10, kind=VERIFY_RECV))
        sim.run(until=6)
        record = sim.metrics()["cpu_accounting"]
        self.assertEqual(record["total"][VERIFY_RECV]["wait"]["sum"], 2)
        self.assertEqual(record["total"][VERIFY_RECV]["wait"]["count"], 2)
        self.assertEqual(record["total"][PROVE_RECURSIVE]["wait"]["sum"], 5)
        self.assertEqual(record["total"][VERIFY_RECV]["service_ms"], 9)
        self.assertEqual(sum(item["service_ms"]
                             for item in record["total"].values()),
                         sum(cpu.busy_time_at(sim.engine.now) for cpu in sim.cpu))


class TestPlanAccounting(unittest.TestCase):
    def assert_identity(self, sim):
        record = sim.metrics()["plan_work"]
        for counts in [record["total"]] + record["by_node"]:
            self.assertEqual(counts["submitted"], counts["completed"]
                             + counts["cancelled"] + counts["refused"]
                             + counts["still_pending"])
            self.assertEqual(counts["refused"], counts["refused_queue"]
                             + counts["refused_over_capacity"])
            self.assertLessEqual(counts["started"], counts["submitted"])
        for key in ("submitted", "started", "completed", "cancelled",
                    "refused", "still_pending", "completed_charged_ms"):
            self.assertEqual(record["total"][key],
                             sum(row[key] for row in record["by_node"]))
        return record["total"]

    def test_pending_started_completed_and_plan_shape_cost(self):
        for shape, charged in (("wide", 10), ("binary", 30)):
            sim = proving_fixture(fold_plan=shape)
            sim.cpu[0].submit(Job(5, kind=VERIFY_RECV))
            run = sim._tick(0, 1)
            counts = self.assert_identity(sim)
            self.assertEqual((counts["submitted"], counts["started"],
                              counts["still_pending"]), (1, 0, 1))
            sim.run(until=6)
            counts = self.assert_identity(sim)
            self.assertEqual((counts["started"], counts["completed"]), (1, 0))
            sim.run(until=40)
            counts = self.assert_identity(sim)
            self.assertEqual(counts["completed"], 1)
            self.assertEqual(counts["completed_estimated_ms"],
                             run.plan.selection.estimated_cost)
            self.assertEqual(counts["completed_estimated_ms"], 10)
            self.assertEqual(counts["completed_charged_ms"], charged)
            self.assertEqual(counts["charged_estimate_ratio"]["sum"], charged / 10)
            self.assertEqual(counts["charged_estimate_ratio"]["count"], 1)
            lower = 1
            if shape == "binary":
                lower = 2
            bucket = POWER_MINIMUMS.index(lower) + 2
            self.assertEqual(counts["charged_estimate_ratio"]["histogram"][
                bucket], 1)
            self.assertEqual(counts["internal_nonroot_proofs"], charged / 10 - 1)

    def test_cancellation_counted_once_despite_running_internal_job(self):
        sim = proving_fixture(fold_plan="binary", on_tick_overrun="abandon")
        sim._tick(0, 1)
        sim._tick(0, 2)
        counts = self.assert_identity(sim)
        self.assertEqual((counts["submitted"], counts["cancelled"],
                          counts["still_pending"]), (2, 1, 1))
        sim.run(until=5)
        counts = self.assert_identity(sim)
        self.assertEqual(counts["cancelled"], 1)
        self.assertEqual(sim.metrics()["cpu_accounting"]["total"][
            PROVE_RECURSIVE]["service_ms"], 5)
        sim.run(until=40)
        counts = self.assert_identity(sim)
        self.assertEqual((counts["cancelled"], counts["completed"]), (1, 1))

    def test_queue_refusal_counts_plan_and_exact_refused_job_duration(self):
        sim = proving_fixture(fold_plan="binary", cpu_queue_jobs=0)
        sim._tick(0, 1)
        counts = self.assert_identity(sim)
        self.assertEqual((counts["submitted"], counts["started"],
                          counts["refused_queue"]), (1, 1, 1))
        self.assertEqual(counts["refused_estimated_ms"], 10)
        sim.run(until=10)
        counts = self.assert_identity(sim)
        self.assertEqual(counts["proofs"], 1)
        self.assertEqual(counts["roots_completed"], 0)
        self.assertEqual(counts["completed_charged_ms"], 0)
        self.assertEqual(sim.metrics()["cpu_accounting"]["total"][
            PROVE_RECURSIVE]["refused_estimated_ms"], 10)

    def test_capacity_refusal_before_any_cpu_submission(self):
        cost = CircuitCost(1, trace_cap=1)
        sim = proving_fixture(costs=CostTable(prove_recursive=cost),
                              prove_cost_basis="circuit")
        sim._tick(0, 1)
        counts = self.assert_identity(sim)
        self.assertEqual((counts["submitted"], counts["started"],
                          counts["refused_over_capacity"]), (1, 0, 1))
        self.assertEqual(counts["refused_estimated_ms"], 2 * cost.sphincs_hashes)
        self.assertEqual(sim.cpu[0].jobs_in_system(), 0)

    def test_free_plan_ratio_is_unavailable_not_zero(self):
        sim = proving_fixture(costs=CostTable())
        sim._tick(0, 1)
        sim.run(until=1)
        counts = self.assert_identity(sim)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["ratio_unavailable"], 1)
        self.assertEqual(counts["charged_estimate_ratio"]["count"], 0)
        self.assertEqual(counts["charged_estimate_ratio"]["max"], -1)

    def test_multiple_nodes_reconcile(self):
        sim = proving_fixture()
        for leaf in sim.arena.leaves:
            sim.nodes[1].admit(leaf, 0)
        sim._tick(0, 1)
        sim._tick(1, 1)
        sim.run(until=10)
        self.assertEqual(self.assert_identity(sim)["completed"], 2)


class TestUsefulRoots(unittest.TestCase):
    def assert_chain(self, sim):
        counts = sim.metrics()["plan_work"]["total"]
        chain = [counts[key] for key in (
            "proofs", "roots_completed", "roots_emitted",
            "roots_emitted_contributed")]
        self.assertEqual(chain, sorted(chain, reverse=True))
        self.assertEqual(counts["proofs"], sim.proofs_completed)
        self.assertEqual(counts["roots_completed"], sim.plan_roots.count)
        return counts

    def test_wire_completion_and_local_inclusion_are_independent(self):
        sim = proving_fixture(capacity=100, fold_plan="binary")
        sim._tick(0, 1)
        sim.run(until=30)
        self.assertEqual(self.assert_chain(sim)["roots_emitted"], 0)
        self.assertEqual(sim.wrappers_emitted, 1)
        sim._build_block(1)
        counts = self.assert_chain(sim)
        self.assertEqual(counts["roots_contributed"], 1)
        self.assertEqual(counts["roots_emitted_contributed"], 0)
        send = sim.egress[0].current_job
        sim.run(until=send.started_at + send.duration)
        counts = self.assert_chain(sim)
        self.assertEqual(counts["roots_emitted"], 1)
        self.assertEqual(counts["roots_emitted_contributed"], 1)
        sim._emit_wrapper(sim.nodes[0], sim.own_aggregate[0], 2)
        send = sim.egress[0].current_job
        sim.run(until=send.started_at + send.duration)
        sim._build_block(2)
        self.assertEqual(self.assert_chain(sim)["roots_emitted"], 1)
        self.assertEqual(self.assert_chain(sim)["roots_contributed"], 1)

    def test_refused_wrapper_does_not_count_as_emitted(self):
        sim = proving_fixture(egress_queue_bytes=0, capacity=1)
        sim._send(0, 1, sim.size_model.object_payload(sim.arena.leaves[0]))
        sim._tick(0, 1)
        sim.run(until=10)
        counts = self.assert_chain(sim)
        self.assertEqual(counts["roots_completed"], 1)
        self.assertEqual(counts["roots_emitted"], 0)
        self.assertEqual(sim.egress_refused.messages_by_kind["wrapper"], 1)

    def test_overlapping_claims_only_credit_new_marginal_inclusion(self):
        work = PlanWork(2)
        work.root(10, 0)
        work.root(11, 1)
        work.root(12, 1)
        for object_id in (10, 11, 12):
            work.emitted(object_id)
        # Claims 011 and 110 overlap, but contribute 011 and 100 in order.
        work.contributed([(10, 2, 2, 0b011), (11, 2, 2, 0b100),
                          (12, 2, 2, 0)], 0b111)
        work.contributed([(12, 2, 2, 0b110)], 0)
        work.contributed([(10, 2, 2, 0b011)], 0b011)
        self.assertEqual(work.metrics()["total"]["roots_emitted_contributed"], 2)
        self.assertEqual(work.metrics()["by_node"][1]["roots_contributed"], 1)

    def test_overlapping_roots_use_real_block_observation(self):
        sim, leaves = fixture(4)
        for leaf in leaves[:3]:
            sim.nodes[0].admit(leaf, 0)
        for leaf in leaves[1:]:
            sim.nodes[1].admit(leaf, 0)
        sim.nodes[0].view.bodies = 0b1111
        sim._tick(0, 1)
        sim._tick(1, 1)
        sim.run(until=10)
        sim.nodes[0].admit(sim.own_aggregate[1], 10)
        block = sim._build_block(1)
        self.assertEqual(block.count(), 4)
        self.assertEqual(sim.plan_roots.claim_sum, 6)
        self.assertEqual(self.assert_chain(sim)["roots_contributed"], 2)
        self.assertEqual(self.assert_chain(sim)["roots_emitted_contributed"], 2)
        self.assertEqual([item["marginal_included_txs"] for item in
                          sim.block_observations[-1]["selected"]], [3, 1])


class TestWorkNeutrality(unittest.TestCase):
    def trajectory(self):
        trace = io.StringIO()
        sim = NetworkSim(two_node_overlay(1.0), seed=7, trace=trace,
                         chain_notify="overlay", capacity=1000.0,
                         aggregation_trigger="aligned", fold_plan="binary",
                         aggregation_interval=20.0, slot_length=100.0,
                         cpu_queue_jobs=3, on_tick_overrun="abandon",
                         costs=CostTable(prove_recursive=2.0, verify_recv=1.0,
                                         jitter_frac=0.25, seed=7))
        for index in range(8):
            sim.schedule_birth(index * 5.0, "q", node_id=index % 2)
        sim.schedule_ticks(10)
        sim.schedule_slots(2)
        sim.run(until=250.0)
        return sim, trace.getvalue()

    def test_observers_do_not_change_jittered_trajectory(self):
        enabled, enabled_trace = self.trajectory()
        with mock.patch.object(Moments, "record"), \
                mock.patch.object(PlanWork, "submit"), \
                mock.patch.object(PlanWork, "start"), \
                mock.patch.object(PlanWork, "finish"), \
                mock.patch.object(PlanWork, "root"), \
                mock.patch.object(PlanWork, "emitted"), \
                mock.patch.object(PlanWork, "contributed"):
            disabled, disabled_trace = self.trajectory()
        self.assertEqual(enabled_trace, disabled_trace)
        self.assertEqual(csv_row(enabled.metrics()), csv_row(disabled.metrics()))
        self.assertEqual(enabled.engine.events_processed,
                         disabled.engine.events_processed)
        self.assertEqual(enabled.costs._rng.getstate(),
                         disabled.costs._rng.getstate())

    def test_metrics_are_detached_repeatable_and_read_only(self):
        sim, _ = self.trajectory()
        before = sim.metrics()
        record = sim.metrics()
        record["cpu_accounting"]["total"][VERIFY_RECV]["wait"]["histogram"][0] = 99
        record["plan_work"]["by_node"][0]["completed"] = 99
        record["plan_work"]["total"]["charged_estimate_ratio"]["histogram"][0] = 99
        self.assertEqual(sim.metrics(), before)
        repeated, _ = self.trajectory()
        self.assertEqual(repeated.metrics(), before)


if __name__ == "__main__":
    unittest.main()
