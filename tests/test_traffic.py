"""Boundary traffic separates accepted work, partial service and copies."""

import io
import unittest

from starksim.arena import KIND_PQ
from starksim.costs import CostTable
from starksim.engine import Job
from starksim.netsim import csv_row
from starksim.network import SizeModel
from starksim.outcomes import TrafficSample, traffic_window
from starksim.payload import (AnnouncePayload, BlockPayload, ObjectPayload,
                              SizeBreakdown, WantPayload, WireMessage,
                              WrapperCore, WrapperPayload)

from tests.support import line_overlay, make_sim


class TestTrafficWindows(unittest.TestCase):
    def test_all_payload_kinds_share_the_meter_including_control_messages(self):
        sim = make_sim(capacity=10.0, base_latency=1000.0)
        start = TrafficSample(sim, 0.0)
        size = SizeBreakdown(control=100)
        core = WrapperCore(0, 0, 0, 1, 1, 1, 1)
        for payload in (ObjectPayload(0, size), AnnouncePayload(0, size),
                        WantPayload(0, size), BlockPayload(0, 1, 1, 1, size),
                        WrapperPayload(core, 0, SizeBreakdown(proof=100))):
            sim._send(0, 1, payload)
        sim.run(until=45.0)
        end = TrafficSample(sim, 45.0)
        self.assertEqual(end.bytes_by_node_kind["wire"][0], {
            "object": 100, "announce": 100, "want": 100, "block": 100,
            "wrapper": 50.0,
        })
        result = traffic_window(start, end)
        self.assertAlmostEqual(
            sum(result["wire_bytes_per_second_by_node_kind"][0].values())
            * 0.045, sim.wire_bytes(0))
        self.assertEqual(result["wrapper_copies_by_encoding"]["full_proof"], 0)
        sim.run(until=55.0)
        result = traffic_window(end, TrafficSample(sim, 55.0))
        self.assertEqual(result["wrapper_copies_by_encoding"]["full_proof"], 1)
        self.assertEqual(result["wire_bytes_per_second_by_node_kind"][0]
                         ["wrapper"], 5000.0)

    def test_partial_message_and_kind_rates_reconcile_with_wire_delta(self):
        sim = make_sim(capacity=10.0, base_latency=1000.0)
        sim._send(0, 1, ObjectPayload(0, SizeBreakdown(bodies=100)))
        sim._send(0, 1, AnnouncePayload(0, SizeBreakdown(control=200)))
        sim.run(until=2.0)
        start = TrafficSample(sim, 2.0)
        self.assertEqual(start.bytes_by_node["completed"], (0, 0))
        self.assertEqual(start.bytes_by_node["wire"], (20.0, 0.0))
        sim.run(until=12.0)
        end = TrafficSample(sim, 12.0)
        result = traffic_window(start, end)
        self.assertEqual(result["completed_bytes_per_second_by_node"],
                         [10000.0, 0.0])
        self.assertEqual(result["wire_bytes_per_second_by_node_kind"][0], {
            "object": 8000.0, "wrapper": 0.0, "announce": 2000.0,
            "want": 0.0, "block": 0.0,
        })
        self.assertEqual(result["wire_bytes_per_second_by_kind"]["object"],
                         4000.0)
        self.assertEqual(result["wire_bytes_per_second"], 5000.0)
        for node_id, row in enumerate(
                result["wire_bytes_per_second_by_node_kind"]):
            self.assertAlmostEqual(
                sum(row.values()) * (end.time_ms - start.time_ms) / 1000,
                end.bytes_by_node["wire"][node_id]
                - start.bytes_by_node["wire"][node_id])
        # Retained readings cannot change when the active message completes.
        sim.run(until=100.0)
        self.assertEqual(traffic_window(start, end), result)

    def test_refusal_is_attempted_demand_not_accepted_or_wire(self):
        sim = make_sim(capacity=10.0, egress_queue_bytes=1,
                       base_latency=1000.0)
        start = TrafficSample(sim, 0.0)
        self.assertTrue(sim._send(
            0, 1, ObjectPayload(0, SizeBreakdown(bodies=100))))
        self.assertFalse(sim._send(
            0, 1, AnnouncePayload(0, SizeBreakdown(control=200))))
        sim.run(until=5.0)
        result = traffic_window(start, TrafficSample(sim, 5.0))
        self.assertEqual(result["attempted_bytes_per_second_by_node"],
                         [60000.0, 0.0])
        self.assertEqual(result["refused_bytes_per_second_by_node"],
                         [40000.0, 0.0])
        self.assertEqual(result["accepted_bytes_per_second_by_node"],
                         [20000.0, 0.0])
        self.assertEqual(result["wire_bytes_per_second_by_node"],
                         [10000.0, 0.0])
        # Receive-side refusal cannot be added to sender attempted demand.
        sim._cpu_overflow(WireMessage(
            1, 0, ObjectPayload(0, SizeBreakdown(bodies=300))), "verify_full")
        self.assertEqual(traffic_window(start, TrafficSample(sim, 5.0)), result)
        for stage in ("accepted", "completed", "wire"):
            self.assertEqual(
                result[stage + "_bytes_per_second_by_node_kind"][0]
                ["announce"], 0.0)
        for node_id in range(2):
            for kind in result["attempted_bytes_per_second_by_kind"]:
                self.assertEqual(
                    result["attempted_bytes_per_second_by_node_kind"]
                    [node_id][kind],
                    result["accepted_bytes_per_second_by_node_kind"]
                    [node_id][kind]
                    + result["refused_bytes_per_second_by_node_kind"]
                    [node_id][kind])

    def test_idle_interval_is_zero_even_after_traffic(self):
        sim = make_sim(capacity=10.0, base_latency=1000.0)
        sim._send(0, 1, ObjectPayload(0, SizeBreakdown(bodies=100)))
        sim.run(until=20.0)
        start = TrafficSample(sim, 20.0)
        sim.run(until=30.0)
        result = traffic_window(start, TrafficSample(sim, 30.0))
        for stage in ("attempted", "refused", "accepted", "completed", "wire"):
            self.assertEqual(result[stage + "_bytes_per_second"], 0.0)
            for row in result[stage + "_bytes_per_second_by_node_kind"]:
                self.assertEqual(set(row.values()), {0.0})
        self.assertEqual(set(result["wrapper_copies_by_encoding"].values()),
                         {0})

    def test_unmetered_egress_has_classified_instantaneous_wire_bytes(self):
        sim = make_sim(base_latency=1000.0)
        start = TrafficSample(sim, 0.0)
        sim._send(0, 1, ObjectPayload(0, SizeBreakdown(bodies=100)))
        sim.run(until=1.0)
        end = TrafficSample(sim, 1.0)
        self.assertEqual(end.bytes_by_node_kind["wire"],
                         end.bytes_by_node_kind["completed"])
        result = traffic_window(start, end)
        self.assertEqual(result["wire_bytes_per_second_by_node"],
                         [100000.0, 0.0])

    def test_missing_classification_is_not_a_false_zero(self):
        sim = make_sim(capacity=10.0)
        sim.wrapper_copies = None
        start = TrafficSample(sim, 0.0)
        sim.egress[0].submit(Job(10.0))
        for boundary in (5.0, 15.0):
            sim.run(until=boundary)
            result = traffic_window(start, TrafficSample(sim, boundary))
            self.assertEqual(
                set(result["wire_bytes_per_second_by_node_kind"][0].values()),
                {-1.0})
            self.assertEqual(
                set(result["wire_bytes_per_second_by_kind"].values()), {-1.0})
            self.assertGreater(result["wire_bytes_per_second"], 0)
            self.assertEqual(
                set(result["wrapper_copies_by_encoding"].values()), {-1})

    def test_reference_uses_actual_degrees_size_and_clock(self):
        for trigger in ("aligned", "spread", "when_ready"):
            settings = dict(aggregation_trigger=trigger,
                            size_model=SizeModel(recursive_proof=123))
            if trigger != "when_ready":
                settings["aggregation_interval"] = 500.0
            else:
                settings["proving_budget"] = 1000.0
            sim = make_sim(line_overlay(3), **settings)
            start = TrafficSample(sim, 0.0)
            sim.run(until=10.0)
            result = traffic_window(start, TrafficSample(sim, 10.0))
            expected = [246.0, 492.0, 246.0]
            mean = 328.0
            if trigger == "when_ready":
                expected = [-1.0, -1.0, -1.0]
                mean = -1.0
            self.assertEqual(
                result["reference_cadence_bytes_per_second_by_node"], expected)
            self.assertEqual(result["reference_cadence_bytes_per_second"], mean)

    def test_unreached_incomplete_cross_run_and_empty_windows_are_refused(self):
        sim = make_sim(relay_policy="wrapper_only")
        start = TrafficSample(sim, 0.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=1)
        for advance in (False, True):
            if advance:
                sim.engine.run(max_events=1)
            with self.assertRaisesRegex(ValueError, "complete boundary"):
                TrafficSample(sim, 0.0)
        sim.run(until=10.0)
        end = TrafficSample(sim, 10.0)
        for boundary in (-1, 0, 20, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                TrafficSample(sim, boundary)
        for first, last in ((start, start), (end, start),
                            (TrafficSample(make_sim(), 0.0), end)):
            with self.assertRaises(ValueError):
                traffic_window(first, last)

    def test_observer_disabled_preserves_trace_csv_events_and_random_streams(self):
        results = []
        for observe in (True, False):
            trace = io.StringIO()
            sim = make_sim(trace=trace, capacity=10000.0,
                           aggregation_interval=20.0,
                           aggregation_trigger="aligned",
                           costs=CostTable(prove_recursive=1, verify_recv=1))
            if observe:
                start = TrafficSample(sim, 0.0)
            else:
                sim.wrapper_copies = None
            for time in (0.0, 2.0):
                sim.schedule_birth(time, KIND_PQ, node_id=0)
            sim.schedule_ticks(5)
            sim.run(until=50.0)
            if observe:
                end = TrafficSample(sim, 50.0)
                self.assertEqual(traffic_window(start, end),
                                 traffic_window(start, end))
            sim.run(until=150.0)
            if observe:
                self.assertGreater(sum(sum(row.values())
                                       for row in sim.wrapper_copies), 0)
            results.append((trace.getvalue(), csv_row(sim.metrics()),
                            sim.engine.events_processed,
                            sim.rng_entry.getstate(), sim.costs._rng.getstate()))
        self.assertEqual(results[0], results[1])


class TestWrapperCopies(unittest.TestCase):
    def test_reference_encoding_takes_precedence_over_mode_zero(self):
        sim = make_sim(base_latency=1000.0)
        start = TrafficSample(sim, 0.0)
        core = WrapperCore(0, 0, 0, 1, 0, 1, 1)
        payload = sim.size_model.wrapper(core, 0, proof_by_ref=True,
                                         witness_bytes=2500)
        self.assertEqual(payload.size.proof, sim.size_model.hash_ref)
        sim._send(0, 1, payload)
        sim.run(until=1.0)
        result = traffic_window(start, TrafficSample(sim, 1.0))
        self.assertEqual(result["wrapper_copies_by_encoding"], {
            "full_proof": 0, "proof_reference": 1, "mode_zero_witnesses": 0,
        })

    def run_emissions(self, economy="full", mode="recursive", capacity=None):
        sim = make_sim(wrapper_economy=economy, wrapper_mode=mode,
                       relay_policy="wrapper_only", capacity=capacity,
                       costs=CostTable(verify_recv=0.0))
        start = TrafficSample(sim, 0.0)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.run(until=0.0)
        leaf = sim.arena.leaves[0]
        aggregate = sim.arena.new_aggregate([leaf], 0.0, creator=0, claimed=1)
        sim._emit_wrapper(sim.nodes[0], aggregate, 1)
        sim.run(until=100.0)
        sim._emit_wrapper(sim.nodes[0], aggregate, 2)
        sim.run(until=200.0)
        return sim, traffic_window(start, TrafficSample(sim, 200.0))

    def test_full_reference_and_suppression_are_recipient_copies_not_cores(self):
        for economy, full, reference, suppressed in (
                ("full", 2, 0, 0), ("proof_by_ref", 1, 1, 0),
                ("skip_unchanged", 1, 0, 1), ("lean", 1, 0, 1)):
            with self.subTest(economy=economy):
                sim, result = self.run_emissions(economy)
                self.assertEqual(result["wrapper_copies_by_encoding"], {
                    "full_proof": full, "proof_reference": reference,
                    "mode_zero_witnesses": 0,
                })
                self.assertEqual(result["wrapper_copies_by_node_encoding"][0],
                                 result["wrapper_copies_by_encoding"])
                self.assertEqual(
                    set(result["wrapper_copies_by_node_encoding"][1].values()),
                    {0})
                self.assertEqual(sim.wrappers_emitted, 2)
                self.assertEqual(sim.wrapper_sends_suppressed, suppressed)
                start = TrafficSample(sim, 200.0)
                sim.run(until=300.0)
                idle = traffic_window(start, TrafficSample(sim, 300.0))
                self.assertEqual(
                    set(idle["wrapper_copies_by_encoding"].values()), {0})

    def test_mode_zero_witness_copies_and_unfinished_wrapper(self):
        sim, result = self.run_emissions(mode="cheapest")
        self.assertEqual(sim.mode_zero_wrappers, 2)
        self.assertEqual(result["wrapper_copies_by_encoding"], {
            "full_proof": 0, "proof_reference": 0, "mode_zero_witnesses": 2,
        })
        sim, result = self.run_emissions(capacity=1.0)
        self.assertEqual(sim.wrappers_emitted, 2)
        self.assertEqual(set(result["wrapper_copies_by_encoding"].values()), {0})
        self.assertEqual(result["completed_bytes_per_second"], 0)
        self.assertGreater(result["wire_bytes_per_second"], 0)


if __name__ == "__main__":
    unittest.main()
