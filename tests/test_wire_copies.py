"""Completed copies are neither offered messages nor verified reach."""

from bisect import bisect_right
import io
import json
import unittest
from unittest import mock

from bench import mainnet
from bench.placement import diffusion_time
from starksim.arena import Arena
from starksim.costs import CostTable
from starksim.evidence import diffusion, streamed_duplicate_burden
from starksim.netsim import csv_row
from starksim.outcomes import TrafficSample, traffic_window
from starksim.payload import ObjectPayload, SizeBreakdown, WrapperCore
from starksim.recovery import recovery_state
from starksim.statistics import POWER_MINIMUMS
from starksim.wire_copies import wire_copies

from tests.support import make_sim
from tests.test_mainnet import SMALL, bench_records, bench_stdout


class TestWireCopies(unittest.TestCase):
    def test_wrapper_only_body_carriage_needs_no_raw_admission(self):
        trace = io.StringIO()
        sim = make_sim(relay_policy="wrapper_only", trace=trace,
                       costs=CostTable(prove_recursive=1, verify_recv=1))
        leaf = sim._birth("q", 0)
        sim._tick(0, 1)
        sim.run(until=20)
        row = wire_copies(sim, per_transaction=True)["by_transaction"][0]
        self.assertEqual(row, {"tx": 0, "raw_object_bodies": 0,
                              "wrapper_bodies": 1,
                              "wrapper_claim_mentions": 1})
        self.assertEqual(diffusion(sim)["reached_txs"], 1)
        self.assertEqual(diffusion_time(trace.getvalue(), 2), (None, 0, 1))
        admissions = [json.loads(line) for line in trace.getvalue().splitlines()
                      if json.loads(line)["e"] == "adm"]
        self.assertTrue(admissions)
        self.assertTrue(all(record["o"] != leaf.id for record in admissions))

    def test_two_copies_finish_before_verification(self):
        sim = make_sim(relay_policy="wrapper_only", capacity=10000,
                       costs=CostTable(verify_recv=100))
        leaf = sim._birth("q", 0)
        payload = sim.size_model.object_payload(leaf)
        sim._send(0, 1, payload)
        sim._send(0, 1, payload)
        sim.run(until=20)
        self.assertEqual(wire_copies(sim)["classes"]["raw_object_bodies"]
                         ["sum"], 2)
        self.assertEqual(sim.transaction_evidence.evidence[1], {})
        self.assertEqual(diffusion(sim)["reached_txs"], 0)
        self.assertTrue(sim.cpu[1].is_busy())

    def test_reference_only_mentions_claim_but_carries_no_body(self):
        sim = make_sim(base_latency=1000)
        leaf = sim.arena.new_leaf("q", 0)
        core = WrapperCore(0, leaf.id, 0, 1, 1, 1, 1)
        payload = sim.size_model.wrapper(core, 0, proof_by_ref=True)
        sim._send(0, 1, payload)
        sim.run(until=1)
        self.assertEqual(wire_copies(sim, per_transaction=True)
                         ["by_transaction"], [{
                             "tx": 0, "raw_object_bodies": 0,
                             "wrapper_bodies": 0, "wrapper_claim_mentions": 1,
                         }])

    def test_partial_and_refused_sends_are_not_completed_copies(self):
        sim = make_sim(capacity=10, egress_queue_bytes=1, base_latency=1000)
        leaf = sim.arena.new_leaf("q", 0)
        payload = ObjectPayload(leaf.id, SizeBreakdown(bodies=100))
        start = TrafficSample(sim, 0)
        self.assertTrue(sim._send(0, 1, payload))
        self.assertFalse(sim._send(0, 1, payload))
        sim.run(until=5)
        traffic = traffic_window(start, TrafficSample(sim, 5))
        self.assertGreater(traffic["wire_bytes_per_second"], 0)
        self.assertEqual(recovery_state(sim)["egress_work"]["active_jobs"], 1)
        self.assertEqual(wire_copies(sim)["classes"]["raw_object_bodies"]
                         ["sum"], 0)
        sim.run(until=10)
        self.assertEqual(wire_copies(sim)["classes"]["raw_object_bodies"]
                         ["sum"], 1)
        self.assertEqual(sim.egress_refused.messages, 1)

    def test_leaf_indices_zero_population_and_summary_distribution(self):
        sim = make_sim(base_latency=1000)
        empty = wire_copies(sim)
        self.assertEqual(empty["total_txs"], 0)
        for stats in empty["classes"].values():
            self.assertEqual((stats["mean"], stats["max"]), (-1, -1))
            self.assertEqual((stats["count"], stats["sum"]), (0, 0))
        first = sim.arena.new_leaf("q", 0)
        aggregate = sim.arena.new_aggregate([first], 0, creator=0, claimed=1)
        second = sim.arena.new_leaf("p", 0)
        third = sim.arena.new_leaf("q", 0)
        self.assertNotEqual(second.id, second.leaf_index)
        # The aggregate's provenance must not be used to widen either mask.
        with mock.patch.object(Arena, "coverage", side_effect=AssertionError):
            for _ in range(2):
                sim._send(0, 1, sim.size_model.object_payload(second))
            for claim, bodies in ((3, 1), (6, 4)):
                core = WrapperCore(claim, aggregate.id, 0, 1, 1,
                                   claim, claim.bit_count())
                sim._send(0, 1, sim.size_model.wrapper(core, bodies))
            sim.run(until=1)
            result = wire_copies(sim, per_transaction=True)
        self.assertEqual(result["by_transaction"], [
            {"tx": 0, "raw_object_bodies": 0, "wrapper_bodies": 1,
             "wrapper_claim_mentions": 1},
            {"tx": 1, "raw_object_bodies": 2, "wrapper_bodies": 0,
             "wrapper_claim_mentions": 2},
            {"tx": 2, "raw_object_bodies": 0, "wrapper_bodies": 1,
             "wrapper_claim_mentions": 1},
        ])
        raw = result["classes"]["raw_object_bodies"]
        self.assertEqual((raw["count"], raw["sum"], raw["max"]), (3, 2, 2))
        self.assertEqual(raw["mean"], 2 / 3)
        self.assertEqual(raw["histogram"][0], 2)
        self.assertEqual(raw["histogram"][1 + bisect_right(POWER_MINIMUMS, 2)], 1)
        self.assertEqual(sum(raw["histogram"]), 3)
        for kind, stats in result["classes"].items():
            self.assertEqual(stats["sum"],
                             sum(row[kind] for row in result["by_transaction"]))
        result["by_transaction"][1]["raw_object_bodies"] = 99
        self.assertEqual(wire_copies(sim)["classes"]["raw_object_bodies"]
                         ["sum"], 2)
        self.assertEqual(third.leaf_index, 2)

    def test_disabling_observers_preserves_trajectory_and_provenance_reads(self):
        results = []
        original = Arena.coverage
        for enabled in (False, True):
            with mock.patch.object(Arena, "coverage", autospec=True,
                                   side_effect=original) as reads:
                trace = io.StringIO()
                sim = make_sim(trace=trace, trace_follow=[0, 1],
                               aggregation_interval=20, slot_length=100,
                               costs=CostTable(verify_recv=2, prove_recursive=5,
                                               jitter_frac=0.2))
                if not enabled:
                    sim.wire_copies = None
                    sim.tracer.duplicate_accumulator = None
                for index in range(6):
                    sim.schedule_birth(index * 4, "q", node_id=index % 2)
                sim.schedule_ticks(6)
                sim.schedule_slots(2)
                sim.run(until=250)
                if enabled:
                    self.assertGreater(wire_copies(sim)["classes"]
                                       ["raw_object_bodies"]["sum"], 0)
                    self.assertGreater(streamed_duplicate_burden(sim)
                                       ["traced_duplicate_messages"], 0)
                else:
                    self.assertEqual(wire_copies(sim, per_transaction=True), -1)
                    self.assertEqual(streamed_duplicate_burden(sim), -1)
                results.append((trace.getvalue(), csv_row(sim.metrics()),
                                sim.engine.events_processed, reads.call_count,
                                sim.rng_entry.getstate(), sim.costs._rng.getstate()))
        self.assertEqual(results[0], results[1])


class TestMainnetCopies(unittest.TestCase):
    def test_same_manifest_under_relay_changes_has_different_copy_counts(self):
        names = ",".join("eip_faithful+relay_policy=" + relay for relay in
                         ("flood", "announce_fetch", "wrapper_only"))
        rows = bench_records(SMALL + ["--tps", "2", "--privacy-share", "0",
                                     "--presets", names, "--emit-manifest"])
        for row in rows:
            self.assertEqual(row["workload"]["manifest"],
                             rows[0]["workload"]["manifest"])
            self.assertEqual(row["identity"]["topology_fingerprint"],
                             rows[0]["identity"]["topology_fingerprint"])
            self.assertNotIn("by_transaction", row["metrics"]["wire_copies"])
            self.assertNotIn("wire_copies", row["variant"])
        copies = [row["metrics"]["wire_copies"]["classes"] for row in rows]
        self.assertGreater(copies[0]["raw_object_bodies"]["sum"], 0)
        self.assertGreater(copies[1]["raw_object_bodies"]["sum"], 0)
        self.assertEqual(copies[2]["raw_object_bodies"]["sum"], 0)
        self.assertGreater(copies[2]["wrapper_bodies"]["sum"], 0)

    def test_diffusion_is_json_only_opt_in_without_identity_changes(self):
        argv = SMALL + ["--tps", "2", "--privacy-share", "0"]
        plain = bench_records(argv)[0]
        captured = []
        original = mainnet.one_run

        def capture(*args):
            result = original(*args)
            captured.append(result[1])
            return result

        with mock.patch.object(mainnet, "one_run", side_effect=capture):
            measured = bench_records(argv + ["--diffusion"])[0]
        for kind in ("evidence", "body"):
            key = kind + "_diffusion"
            self.assertNotIn(key, plain["metrics"])
            values = measured["metrics"].pop(key)
            self.assertEqual(values, [diffusion(captured[0], fraction, kind)
                                      for fraction in (0.9, 1.0)])
        self.assertEqual(plain, measured)
        self.assertNotIn("diffusion", measured["workload"])
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                bench_stdout([arg for arg in argv if arg != "--json"]
                             + ["--diffusion"])


if __name__ == "__main__":
    unittest.main()
