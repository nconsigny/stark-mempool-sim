"""Waiting emptiness, transport quiescence and useful recovery are distinct."""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from bench import overload
from starksim.arena import KIND_PQ
from starksim.costs import CostTable
from starksim.engine import Job
from starksim.recovery import (RecoverySample, assess_useful_recovery,
                               recovery_state)

from tests.support import make_sim


def sending(**settings):
    sim = make_sim(relay_policy="wrapper_only", **settings)
    leaf = sim._birth(KIND_PQ, 0)
    payload = sim.size_model.want(leaf.id)
    return sim, payload


class TestRecoveryState(unittest.TestCase):
    def assert_message_partition(self, sim):
        state = recovery_state(sim)
        egress = state["egress_work"]
        self.assertEqual(sim.messages_offered,
                         sim.messages_delivered + state["pending_deliveries"]
                         + egress["active_jobs"] + egress["waiting_jobs"])

    def test_empty_waiting_queue_does_not_mean_finished_transmission(self):
        sim, payload = sending(capacity=8.0, base_latency=10.0)
        size = payload.size.total()
        sim._send(0, 1, payload)
        sim.run(until=size / 16.0)
        state = recovery_state(sim)
        self.assertEqual(state["egress_work"]["active_jobs"], 1)
        self.assertEqual(state["egress_work"]["waiting_jobs"], 0)
        self.assertEqual(state["egress_work"]["active_remaining_bytes"], size / 2)
        self.assertTrue(state["egress_waiting_empty"])
        self.assertFalse(state["transport_idle"])
        self.assertFalse(state["transport_quiescent"])
        self.assertEqual(state["pending_deliveries"], 0)
        self.assertEqual(sim.total_offered_bytes(), sim.total_wire_bytes()
                         + state["egress_work"]["active_remaining_bytes"]
                         + state["egress_work"]["waiting_bytes"])
        snapshot = overload.snapshot(sim, 2, sim.engine.now)
        self.assertEqual(snapshot["queue_bytes"], 0)
        self.assert_message_partition(sim)

    def test_unbounded_waiting_bytes_are_bytes_not_server_queue_weights(self):
        sim, payload = sending(capacity=8.0)
        size = payload.size.total()
        sim._send(0, 1, payload)
        sim._send(0, 1, payload)
        state = recovery_state(sim)
        self.assertEqual(state["egress_work"]["waiting_jobs"], 1)
        self.assertEqual(state["egress_work"]["waiting_bytes"], size)
        self.assertEqual(sim.egress[0].queued_weight, 1)
        self.assertFalse(state["egress_waiting_empty"])
        self.assert_message_partition(sim)

    def test_cpu_active_and_waiting_costs_are_separate(self):
        sim = make_sim()
        sim.cpu[0].submit(Job(10.0))
        sim.cpu[0].submit(Job(20.0))
        sim.run(until=5.0)
        state = recovery_state(sim)
        cpu = state["cpu_work"]
        self.assertEqual(cpu["active_jobs"], 1)
        self.assertEqual(cpu["waiting_jobs"], 1)
        self.assertEqual(cpu["active_remaining_ms"], 5.0)
        self.assertEqual(cpu["waiting_ms"], 20.0)
        self.assertEqual(cpu["active_remaining_ms"] + cpu["waiting_ms"], 25.0)
        self.assertFalse(state["transport_quiescent"])
        sim.run(until=30.0)
        self.assertTrue(recovery_state(sim)["transport_quiescent"])
        self.assertEqual(state["cpu_work"]["waiting_jobs"], 1)

    def test_pending_deliveries_cover_link_waiting_service_and_propagation(self):
        sim, payload = sending(capacity=8.0, link_capacity=4.0,
                               base_latency=20.0)
        size = payload.size.total()
        sim._send(0, 1, payload)
        sim._send(0, 1, payload)
        # Both node-egress services have finished; the first link service has
        # not. Equal payloads are two physical messages, not one set member.
        sim.run(until=size / 4.0)
        state = recovery_state(sim)
        self.assertEqual(state["pending_deliveries"], 2)
        self.assertEqual(state["pending_delivery_bytes"], 2 * size)
        self.assertEqual(state["link_work"][0]["active_jobs"], 1)
        self.assertEqual(state["link_work"][0]["waiting_jobs"], 1)
        self.assertTrue(state["egress_waiting_empty"])
        self.assertFalse(state["transport_quiescent"])
        self.assert_message_partition(sim)
        sim.run(until=size / 8.0 + 2 * size / 4.0)
        state = recovery_state(sim)
        self.assertEqual(state["pending_deliveries"], 2)
        self.assertEqual(state["link_work"][0]["active_jobs"], 0)
        self.assertEqual(state["link_work"][0]["waiting_jobs"], 0)
        self.assertFalse(state["transport_idle"])
        self.assert_message_partition(sim)
        sim.run()
        self.assertEqual(recovery_state(sim)["pending_deliveries"], 0)
        self.assertTrue(recovery_state(sim)["transport_quiescent"])
        self.assertEqual(sim.pending_deliveries, {})
        self.assert_message_partition(sim)

    def test_propagation_is_counted_without_a_link_cap(self):
        sim, payload = sending(base_latency=10.0)
        sim._send(0, 1, payload)
        sim.run(until=5.0)
        state = recovery_state(sim)
        self.assertEqual(state["link_work"], [])
        self.assertEqual(state["pending_deliveries"], 1)
        self.assertFalse(state["transport_quiescent"])
        self.assert_message_partition(sim)

    def test_ingress_ends_flight_but_not_verification_or_the_request(self):
        sim, _ = sending(base_latency=0.0,
                         costs=CostTable(verify_recv=10.0))
        leaf = sim.arena.leaves[0]
        sim.nodes[1].mark_requested(leaf)
        sim._send(0, 1, sim.size_model.object_payload(leaf))
        sim.run(until=5.0)
        state = recovery_state(sim)
        self.assertEqual(state["pending_deliveries"], 0)
        self.assertEqual(state["cpu_work"]["by_node"][1]["active_jobs"], 1)
        self.assertEqual(state["cpu_work"]["active_remaining_ms"], 5.0)
        self.assertEqual(state["unresolved_requests"], 1)
        self.assertFalse(state["transport_quiescent"])
        self.assert_message_partition(sim)
        sim.run(until=10.0)
        self.assertEqual(recovery_state(sim)["unresolved_requests"], 0)
        self.assertTrue(recovery_state(sim)["transport_quiescent"])

    def test_refused_sends_never_become_pending_deliveries(self):
        sim, payload = sending(capacity=8.0, egress_queue_bytes=0)
        self.assertTrue(sim._send(0, 1, payload))
        self.assertFalse(sim._send(0, 1, payload))
        self.assert_message_partition(sim)
        sim.run(until=payload.size.total() / 8.0)
        self.assertEqual(recovery_state(sim)["pending_deliveries"], 1)
        self.assert_message_partition(sim)

    def test_idle_transport_with_an_unanswered_want_is_not_quiescent(self):
        sim = make_sim(relay_policy="announce_fetch")
        leaf = sim.arena.new_leaf(KIND_PQ, 0.0)
        sim.nodes[1].mark_requested(leaf)
        sim._request(1, leaf.id, 0)
        sim.run()
        state = recovery_state(sim)
        self.assertEqual(sim.unserved_wants, 1)
        self.assertEqual(state["pending_deliveries"], 0)
        self.assertEqual(state["unresolved_requests_by_node"], [0, 1])
        self.assertEqual(state["unresolved_requests"], 1)
        self.assertTrue(state["transport_idle"])
        self.assertFalse(state["transport_quiescent"])
        self.assertEqual(state["useful_service_recovered"], -1)

    def test_source_history_and_spent_timeouts_are_not_unresolved_requests(self):
        sim = make_sim(relay_policy="announce_fetch", request_timeout=1000.0)
        leaf = sim.arena.new_leaf(KIND_PQ, 0.0)
        sim.nodes[0].admit(leaf, 0.0)
        sim.nodes[1].mark_requested(leaf)
        sim._request(1, leaf.id, 0)
        sim.run(until=50.0)
        self.assertIn((1, leaf.id), sim._request_sources)
        self.assertTrue(len(sim.engine.calendar))
        state = recovery_state(sim)
        self.assertEqual(state["unresolved_requests"], 0)
        self.assertTrue(state["transport_quiescent"])

    def test_future_births_do_not_change_instantaneous_quiescence(self):
        sim = make_sim()
        sim.schedule_birth(100.0, KIND_PQ)
        state = recovery_state(sim)
        self.assertTrue(state["transport_quiescent"])
        self.assertEqual(state["useful_service_recovered"], -1)

    def test_repeated_snapshots_do_not_change_events_trace_or_metrics(self):
        def run(observe):
            trace = io.StringIO()
            sim, payload = sending(trace=trace, capacity=8.0,
                                   link_capacity=4.0)
            sim._send(0, 1, payload)
            sim._send(0, 1, payload)
            sim.run(until=5.0)
            if observe:
                for _ in range(3):
                    sample = RecoverySample(sim, 5.0)
                    sample.state["cpu_work"]["by_node"][0]["active_jobs"] = 99
            sim.run(until=100.0)
            return (trace.getvalue(), json.dumps(sim.metrics(), sort_keys=True),
                    sim.engine.events_processed)

        self.assertEqual(run(True), run(False))


def service_windows(*, background=False, expiry=False, overload_confirmation=False,
                    synthetic_egress=False):
    sim = make_sim(relay_policy="wrapper_only", slot_length=10.0)
    if background:
        sim.cpu[0].submit(Job(1000.0))
    if synthetic_egress:
        sim.egress[0].submit(Job(1000.0))
    sim.schedule_birth(1.0, KIND_PQ, node_id=0)
    sim.schedule_birth(21.0, KIND_PQ, node_id=0)
    lifetime = None
    if expiry:
        lifetime = 1.0
    sim.schedule_birth(31.0, KIND_PQ, node_id=0, lifetime=lifetime)
    sim.schedule_slots(4)
    samples = [RecoverySample(sim, 0.0)]
    for boundary in (10.0, 20.0, 30.0, 40.0):
        sim.run(until=boundary)
        if overload_confirmation and boundary == 40.0:
            sim.cpu[0].submit(Job(100.0))
        samples.append(RecoverySample(sim, boundary))
    return sim, samples[:2], samples[2:4], samples[3:5]


class TestUsefulRecovery(unittest.TestCase):
    def test_useful_service_can_recover_without_transport_quiescence(self):
        sim, reference, recovery, confirmation = service_windows(background=True)
        result = assess_useful_recovery(reference, recovery, confirmation)
        self.assertTrue(result["useful_service_recovered"])
        self.assertEqual(result["included_per_ms_bounds"], [0.1, 0.1])
        self.assertEqual(result["sampled_work_limits"]["cpu_jobs"], 1)
        self.assertFalse(recovery_state(sim)["transport_quiescent"])
        sim.run(until=2000.0)
        self.assertEqual(assess_useful_recovery(reference, recovery, confirmation),
                         result)

    def test_expiry_can_empty_work_without_restoring_useful_service(self):
        sim, reference, recovery, confirmation = service_windows(expiry=True)
        self.assertTrue(recovery_state(sim)["transport_quiescent"])
        result = assess_useful_recovery(reference, recovery, confirmation)
        self.assertFalse(result["useful_service_recovered"])
        self.assertEqual(result["confirmation"]["included_per_ms"], 0.0)
        self.assertEqual(result["confirmation"]["expired_txs"], 1)

    def test_pending_work_must_stay_in_the_reference_envelope(self):
        _, reference, recovery, confirmation = service_windows(
            overload_confirmation=True)
        result = assess_useful_recovery(reference, recovery, confirmation)
        self.assertEqual(result["confirmation"]["included_per_ms"], 0.1)
        self.assertFalse(result["useful_service_recovered"])
        self.assertEqual(result["confirmation"]["peak_sampled_work"]["cpu_jobs"], 1)
        self.assertEqual(result["sampled_work_limits"]["cpu_jobs"], 0)

    def test_explicit_tolerances_reach_the_assessment_and_its_record(self):
        _, reference, recovery, confirmation = service_windows(
            background=True, overload_confirmation=True)
        self.assertFalse(assess_useful_recovery(reference, recovery, confirmation)
                         ["useful_service_recovered"])
        result = assess_useful_recovery(reference, recovery, confirmation,
                                        rate_tolerance=0.1, work_tolerance=1.0)
        self.assertTrue(result["useful_service_recovered"])
        self.assertEqual(result["work_tolerance"], 1.0)
        self.assertEqual(result["rate_tolerance"], 0.1)
        self.assertEqual(result["sampled_work_limits"]["cpu_jobs"], 2)
        self.assertAlmostEqual(result["included_per_ms_bounds"][0], 0.09)
        self.assertAlmostEqual(result["included_per_ms_bounds"][1], 0.11)

    def test_missing_confirmation_or_reference_service_is_unmeasurable(self):
        _, reference, recovery, confirmation = service_windows()
        self.assertEqual(assess_useful_recovery(reference, recovery, [])
                         ["useful_service_recovered"], -1)
        sim = make_sim()
        samples = [RecoverySample(sim, 0.0)]
        for boundary in (10.0, 20.0, 30.0):
            sim.run(until=boundary)
            samples.append(RecoverySample(sim, boundary))
        result = assess_useful_recovery(samples[:2], samples[1:3], samples[2:])
        self.assertEqual(result["useful_service_recovered"], -1)

    def test_unsized_synthetic_egress_work_is_not_a_zero_byte_measurement(self):
        _, reference, recovery, confirmation = service_windows(synthetic_egress=True)
        self.assertEqual(reference[0].state["egress_work"]
                         ["active_remaining_bytes"], -1)
        self.assertEqual(assess_useful_recovery(reference, recovery, confirmation)
                         ["useful_service_recovered"], -1)

    def test_samples_require_reached_boundaries_and_matching_windows(self):
        sim, reference, recovery, confirmation = service_windows()
        for boundary in (35.0, 45.0):
            with self.assertRaises(ValueError):
                RecoverySample(sim, boundary)
        with self.assertRaises(ValueError):
            assess_useful_recovery(reference, recovery, reference)
        with self.assertRaises(ValueError):
            assess_useful_recovery(reference, recovery[:1] + confirmation,
                                   confirmation)
        for tolerance in (-1, 1, float("nan")):
            with self.assertRaises(ValueError):
                assess_useful_recovery(reference, recovery, confirmation,
                                       rate_tolerance=tolerance)


def bench_output(extra=()):
    argv = ["overload", "--nodes", "2", "--degree", "1", "--tps", "2",
            "--window", "500", "--drain", "1000", "--seeds", "1"]
    stream = io.StringIO()
    with mock.patch.object(sys, "argv", argv + list(extra)):
        with contextlib.redirect_stdout(stream):
            overload.main()
    return stream.getvalue()


class TestOverloadRecoveryRecords(unittest.TestCase):
    def test_json_keeps_legacy_drained_and_new_states_separate(self):
        output = bench_output(("--json",))
        self.assertEqual(output, bench_output(("--json",)))
        self.assertEqual(output, bench_output(("--json", "--recovery-stats")))
        record = json.loads(output)
        metrics = record["metrics"]
        self.assertEqual(metrics["drained"], metrics["end"]["queue_bytes"] == 0)
        self.assertEqual(metrics["useful_service_recovered"], -1)
        for key in ("egress_waiting_empty", "transport_quiescent"):
            self.assertEqual(metrics[key], metrics["end"][key])
            self.assertNotIn(key, record["variant"])
        self.assertNotIn("recovery_stats", record["workload"])

    def test_optional_columns_append_without_relabelling_legacy_drained(self):
        ordinary = bench_output().splitlines()
        extended = bench_output(("--recovery-stats",)).splitlines()
        self.assertEqual(len(ordinary), len(extended))
        changed = []
        for before, after in zip(ordinary, extended):
            if before != after:
                self.assertTrue(after.startswith(before))
                changed.append(after)
        self.assertEqual(len(changed), 3)
        self.assertIn("drained", changed[0])
        self.assertIn("egress empty", changed[0])
        self.assertEqual(changed[-1].split()[-3:], ["1/1", "1/1", "-1"])


if __name__ == "__main__":
    unittest.main()
