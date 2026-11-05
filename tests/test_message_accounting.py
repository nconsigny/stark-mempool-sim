"""Attempted demand and message refusals at the two admission boundaries."""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from bench import overload
from starksim.accounting import MessageCounts
from starksim.arena import KIND_PQ, KIND_PRIVACY
from starksim.costs import CostTable
from starksim.engine import Job
from starksim.netsim import NetworkSim, csv_row
from starksim.network import Overlay, SizeModel
from starksim.payload import WrapperCore
from starksim.reporting import message_accounting

from tests.support import two_node_overlay


def mixed_refusals(trace=None):
    # Two simultaneous sends from one entry overfill its egress. Another
    # entry's send arrives while the receiver verifies the accepted first one.
    overlay = Overlay.from_adjacency([[1], [0, 2], [1]], base_latency=1.0)
    sim = NetworkSim(overlay, seed=7, capacity=1000.0,
                     egress_queue_bytes=0, cpu_queue_jobs=0,
                     costs=CostTable(verify_recv=100.0), trace=trace)
    for node_id in (0, 0, 2):
        sim.schedule_birth(0.0, KIND_PQ, node_id=node_id)
    sim.run(until=10.0)
    return sim


def payloads(sim):
    signature = sim.arena.new_leaf(KIND_PQ, 0.0)
    privacy = sim.arena.new_leaf(KIND_PRIVACY, 0.0)
    claimed = sim.arena.claim(signature)
    aggregate = sim.arena.new_aggregate([signature], 0.0, 0,
                                        claimed=claimed)
    recursive = WrapperCore(0, aggregate.id, 0, 1, 1, claimed, 1)
    witnesses = WrapperCore(1, aggregate.id, 0, 1, 0, claimed, 1)
    sizes = sim.size_model
    return {
        "signature": sizes.object_payload(signature),
        "privacy": sizes.object_payload(privacy),
        "announce": sizes.announce(signature.id),
        "want": sizes.want(signature.id),
        "recursive_wrapper": sizes.wrapper(recursive, claimed),
        "witness_wrapper": sizes.wrapper(
            witnesses, claimed, witness_bytes=sizes.sig_witness),
        "block": sizes.block(0, 1, claimed),
    }


class TestMessageAccounting(unittest.TestCase):
    def assert_accounting(self, sim):
        metrics = sim.metrics()
        self.assertEqual(metrics["attempted_bytes"],
                         metrics["offered_bytes"]
                         + metrics["egress_refused_bytes"])
        self.assertEqual(metrics["attempted_messages"],
                         metrics["messages_offered"]
                         + metrics["egress_refused_messages"])
        for unit in ("bytes", "messages"):
            self.assertEqual(metrics[f"dropped_{unit}"],
                             metrics[f"egress_refused_{unit}"]
                             + metrics[f"cpu_refused_{unit}"])
            for prefix in ("attempted", "egress_refused", "cpu_refused"):
                total = metrics[f"{prefix}_{unit}"]
                by_kind = metrics[f"{prefix}_{unit}_by_kind"]
                by_node = metrics[f"{prefix}_{unit}_by_node"]
                by_node_kind = metrics[f"{prefix}_{unit}_by_node_kind"]
                self.assertEqual(total, sum(by_kind.values()))
                self.assertEqual(total, sum(by_node))
                for node_id, counts in enumerate(by_node_kind):
                    self.assertEqual(by_node[node_id], sum(counts.values()))
                for kind, count in by_kind.items():
                    self.assertEqual(
                        count, sum(counts.get(kind, 0)
                                   for counts in by_node_kind))
        for node_id, offered in enumerate(sim.offered_bytes):
            self.assertEqual(metrics["attempted_bytes_by_node"][node_id],
                             offered
                             + metrics["egress_refused_bytes_by_node"][node_id])
            attempted = metrics["attempted_bytes_by_node_kind"][node_id]
            refused = metrics["egress_refused_bytes_by_node_kind"][node_id]
            for kind, size in attempted.items():
                self.assertEqual(size, sim.offered_kind[node_id].get(kind, 0)
                                 + refused.get(kind, 0))
        for kind, count in metrics["attempted_messages_by_kind"].items():
            self.assertEqual(
                count, sim.offered_messages_by_kind.get(kind, 0)
                + metrics["egress_refused_messages_by_kind"].get(kind, 0))

    def test_attempted_equals_accepted_plus_egress_refused_not_cpu(self):
        sim = mixed_refusals()
        metrics = sim.metrics()
        sizes = sim.size_model
        size = sizes.tx_body + sizes.triple + sizes.sig_witness
        self.assertEqual(metrics["attempted_messages"], 3)
        self.assertEqual(metrics["attempted_bytes"], 3 * size)
        self.assertEqual(metrics["messages_offered"], 2)
        self.assertEqual(metrics["offered_bytes"], 2 * size)
        self.assertEqual(metrics["egress_refused_messages"], 1)
        self.assertEqual(metrics["egress_refused_bytes"], size)
        self.assertEqual(metrics["cpu_refused_messages"], 1)
        self.assertEqual(metrics["cpu_refused_bytes"], size)
        self.assertEqual(metrics["ingress_bytes"], 2 * size)
        self.assertEqual(sim.total_sent_bytes(), 2 * size)
        self.assertEqual(metrics["attempted_messages_by_node"], [2, 0, 1])
        self.assertEqual(metrics["egress_refused_bytes_by_node"], [size, 0, 0])
        self.assertEqual(metrics["cpu_refused_bytes_by_node"], [0, size, 0])
        self.assertEqual(metrics["cpu_refused_messages_by_kind"], {"object": 1})
        self.assertEqual(sim.dropped_by_kind, {"object": 1, "verify_recv": 1})
        self.assertNotEqual(metrics["attempted_bytes"],
                            metrics["offered_bytes"] + metrics["dropped_bytes"])
        self.assert_accounting(sim)
        sim.run()
        self.assert_accounting(sim)

    def test_every_payload_kind_counts_at_attempt_and_egress_refusal(self):
        for name in ("signature", "privacy", "announce", "want",
                     "recursive_wrapper", "witness_wrapper", "block"):
            with self.subTest(payload=name):
                sim = NetworkSim(two_node_overlay(), capacity=1.0,
                                 egress_queue_bytes=0)
                payload = payloads(sim)[name]
                size = payload.size.total()
                self.assertTrue(sim._send(0, 1, payload))
                self.assertFalse(sim._send(0, 1, payload))
                metrics = sim.metrics()
                self.assertEqual(metrics["attempted_bytes"], 2 * size)
                self.assertEqual(metrics["egress_refused_bytes"], size)
                self.assertEqual(metrics["attempted_messages_by_kind"],
                                 {payload.kind: 2})
                self.assertEqual(metrics["egress_refused_bytes_by_kind"],
                                 {payload.kind: size})
                self.assertEqual(metrics["wire_bytes"], 0)
                self.assertEqual(metrics["ingress_bytes"], 0)
                self.assert_accounting(sim)

    def test_cpu_refusals_count_payload_kinds_on_each_verification_path(self):
        for name in ("signature", "privacy", "recursive_wrapper",
                     "witness_wrapper", "block"):
            with self.subTest(payload=name):
                sim = NetworkSim(two_node_overlay(1.0), cpu_queue_jobs=0,
                                 costs=CostTable(verify_recv=10.0))
                payload = payloads(sim)[name]
                size = payload.size.total()
                sim.cpu[1].submit(Job(100.0))
                self.assertTrue(sim._send(0, 1, payload))
                sim.run(until=5.0)
                metrics = sim.metrics()
                self.assertEqual(metrics["cpu_refused_messages"], 1)
                self.assertEqual(metrics["cpu_refused_bytes"], size)
                self.assertEqual(metrics["cpu_refused_bytes_by_kind"],
                                 {payload.kind: size})
                self.assertEqual(metrics["cpu_refused_bytes_by_node"], [0, size])
                self.assertEqual(metrics["egress_refused_bytes"], 0)
                self.assertEqual(metrics["ingress_bytes"], size)
                self.assert_accounting(sim)

    def test_same_send_schedule_is_independent_of_buffer_bound(self):
        for waiting_slots in (None, 0, 1):
            with self.subTest(waiting_slots=waiting_slots):
                sizes = SizeModel()
                size = sizes.tx_body + sizes.triple + sizes.sig_witness
                bound = None
                expected_accepted = 3
                if waiting_slots is not None:
                    bound = waiting_slots * size
                    expected_accepted = 1 + waiting_slots
                sim = NetworkSim(two_node_overlay(), capacity=1.0,
                                 size_model=sizes, egress_queue_bytes=bound)
                payload = payloads(sim)["signature"]
                for _ in range(3):
                    sim._send(0, 1, payload)
                metrics = sim.metrics()
                self.assertEqual(metrics["attempted_messages"], 3)
                self.assertEqual(metrics["attempted_bytes"], 3 * size)
                self.assertEqual(metrics["messages_offered"], expected_accepted)
                self.assertEqual(metrics["offered_bytes"],
                                 expected_accepted * size)
                self.assert_accounting(sim)

    def test_proving_and_build_refusals_are_not_message_refusals(self):
        sim = NetworkSim(two_node_overlay(), relay_policy="wrapper_only",
                         cpu_queue_jobs=0, costs=CostTable(
                             prove_recursive=10.0, build_block=10.0))
        sim._birth(KIND_PQ, 0)
        sim._birth(KIND_PQ, 0)
        sim.cpu[0].submit(Job(100.0))
        sim._tick(0, 1)
        sim._build_block(1)
        self.assertGreater(sim.plans_abandoned, 0)
        self.assertEqual(sim.build_jobs_refused, 1)
        self.assertEqual(sim.metrics()["cpu_refused_messages"], 0)
        self.assertEqual(sim.metrics()["attempted_messages"], 0)
        self.assert_accounting(sim)

    def test_unbounded_and_idle_counts_are_defined_not_missing(self):
        sim = NetworkSim(two_node_overlay())
        self.assertEqual(sim.metrics()["attempted_bytes"], 0)
        self.assert_accounting(sim)
        sim.schedule_birth(0.0, KIND_PQ, node_id=0)
        sim.run()
        self.assertEqual(sim.metrics()["attempted_messages"], 1)
        self.assertEqual(sim.metrics()["egress_refused_bytes"], 0)
        self.assertEqual(sim.metrics()["cpu_refused_bytes"], 0)
        self.assert_accounting(sim)

    def test_counting_does_not_change_trace_or_existing_csv(self):
        counted_trace = io.StringIO()
        counted = mixed_refusals(counted_trace)
        counted.run()
        disabled_trace = io.StringIO()
        with mock.patch.object(MessageCounts, "record", return_value=None):
            disabled = mixed_refusals(disabled_trace)
            disabled.run()
        self.assertEqual(counted_trace.getvalue(), disabled_trace.getvalue())
        self.assertEqual(csv_row(counted.metrics()), csv_row(disabled.metrics()))
        config = json.loads(counted_trace.getvalue().splitlines()[0])
        self.assertTrue(set(message_accounting(counted)).isdisjoint(config))

    def test_snapshots_and_returned_containers_are_independent(self):
        sim = mixed_refusals()
        before = message_accounting(sim)
        saved = json.dumps(before, sort_keys=True)
        sim.run()
        self.assertEqual(json.dumps(before, sort_keys=True), saved)
        self.assertGreater(sim.metrics()["attempted_messages"],
                           before["attempted_messages"])
        for prefix in ("attempted", "egress_refused", "cpu_refused"):
            before[f"{prefix}_bytes_by_kind"]["object"] = -10
            before[f"{prefix}_bytes_by_node"][0] = -10
            before[f"{prefix}_bytes_by_node_kind"][0]["object"] = -10
            before[f"{prefix}_messages_by_kind"]["object"] = -10
            before[f"{prefix}_messages_by_node"][0] = -10
            before[f"{prefix}_messages_by_node_kind"][0]["object"] = -10
        self.assert_accounting(sim)

    def test_storage_tracks_nodes_and_kinds_not_message_count(self):
        counts = MessageCounts(2)
        for _ in range(100):
            counts.record(0, "object", 10)
            counts.record(1, "announce", 2)
        self.assertEqual(counts.messages, 200)
        self.assertEqual(counts.bytes, 1200)
        self.assertEqual(counts.messages_by_kind,
                         {"object": 100, "announce": 100})
        self.assertEqual(counts.bytes_by_node_kind,
                         [{"object": 1000}, {"announce": 200}])


class TestOverloadDemand(unittest.TestCase):
    def test_load_and_end_snapshots_keep_their_own_attempted_counts(self):
        sim = mixed_refusals()
        at_load = overload.snapshot(sim, 3, sim.engine.now)
        sim.run(until=250.0)
        at_end = overload.snapshot(sim, 3, sim.engine.now)
        self.assertEqual(at_load["attempted_messages"], 3)
        self.assertEqual(at_end["attempted_messages"], 4)
        for snapshot in (at_load, at_end):
            self.assertEqual(snapshot["attempted_bytes"], snapshot["offered"]
                             + snapshot["egress_refused_bytes"])

    def test_json_repeats_and_ratio_uses_load_window_not_drain(self):
        argv = ["overload", "--nodes", "8", "--degree", "2", "--tps", "5",
                "--window", "500", "--drain", "500", "--seeds", "1",
                "--egress-bytes", "1", "--json"]
        outputs = []
        for _ in range(2):
            stream = io.StringIO()
            with mock.patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(overload.main(), 0)
            outputs.append(stream.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        record = json.loads(outputs[0])
        metrics = record["metrics"]
        load = metrics["load"]
        ceiling = 8 * record["variant"]["capacity"] * load["t"]
        self.assertEqual(metrics["attempted_ratio"],
                         load["attempted_bytes"] / ceiling)
        self.assertGreater(load["egress_refused_bytes"], 0)
        self.assertGreater(metrics["attempted_ratio"], metrics["offered_ratio"])
        self.assertEqual(metrics["attempted_bytes"],
                         metrics["end"]["attempted_bytes"])
        self.assertNotIn("attempted_bytes", record["variant"])

    def test_attempted_ratio_without_an_observation_window_is_unavailable(self):
        result = overload.one_run(
            nodes=8, degree=2, uplink_mbps=25.0, latency=1.0,
            policy="flood", spacing=100.0, window=0.0, drain=100.0,
            egress_bytes=None, cpu_jobs=None, request_timeout=None, seed=1)
        self.assertEqual(result["attempted_ratio"], -1.0)


class TestPayloadSizes(unittest.TestCase):
    def test_every_proof_payload_charges_its_own_models_proof(self):
        # The variant reports the configured model's proof size; a payload
        # priced from any other model would charge the wire a size no record
        # shows (review 23: a wrapper on a fresh default model passed every
        # test).
        sim = NetworkSim(two_node_overlay(), seed=1,
                         size_model=SizeModel(recursive_proof=777))
        signature = sim.arena.new_leaf(KIND_PQ, 0.0)
        claimed = sim.arena.claim(signature)
        aggregate = sim.arena.new_aggregate([signature], 0.0, 0,
                                            claimed=claimed)
        sizes = payloads(sim)
        self.assertEqual(sizes["recursive_wrapper"].size.proof, 777)
        self.assertEqual(sizes["block"].size.proof, 777)
        self.assertEqual(sim.size_model.object_payload(
            aggregate, dep_count=1).size.proof, 777)
        self.assertEqual(sim.variant()["size_recursive_proof"], 777)


if __name__ == "__main__":
    unittest.main()
