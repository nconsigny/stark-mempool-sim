"""Transaction evidence is a claim admission, not a raw object identity."""

import io
import json
import unittest
from unittest import mock

from bench.placement import diffusion_time
from starksim.arena import Arena
from starksim.costs import CostTable
from starksim.engine import Job
from starksim.evidence import (FirstEvidence, diffusion, duplicate_burden,
                               streamed_duplicate_burden)
from starksim.netsim import NetworkSim, csv_row
from starksim.payload import (SizeBreakdown, WantPayload, WireMessage,
                              WrapperCore, WrapperPayload)

from tests.support import two_node_overlay
from tests.test_seed_start import output


def fixture(**settings):
    stream = io.StringIO()
    options = dict(relay_policy="wrapper_only", aggregation_trigger="aligned",
                   costs=CostTable(verify_recv=2, prove_recursive=5),
                   trace=stream, trace_follow=[0, 1])
    options.update(settings)
    sim = NetworkSim(two_node_overlay(10), **options)
    return sim, stream


def wrapper(sim, aggregate, bodies=0, mode=1, by_ref=False, size=17, claim=None):
    if claim is None:
        claim = aggregate.claimed
    core = WrapperCore(1, aggregate.id, 0, 1, mode, claim, claim.bit_count())
    payload = WrapperPayload(core, bodies, SizeBreakdown(proof=size), by_ref)
    return WireMessage(0, 1, payload)


class TestFirstEvidence(unittest.TestCase):
    def test_raw_object_evidence_and_body_wait_for_verification(self):
        sim, _ = fixture(relay_policy="flood")
        leaf = sim._birth("q", 0)
        sim.run(until=11)
        self.assertEqual(sim.transaction_evidence.evidence[1], {})
        self.assertEqual(sim.transaction_evidence.bodies[1], {})
        sim.run(until=12)
        self.assertEqual(sim.transaction_evidence.evidence[1], {0: 12})
        self.assertEqual(sim.transaction_evidence.bodies[1], {0: 12})
        sim.run(until=20)
        message = WireMessage(0, 1, sim.size_model.object_payload(leaf))
        sim._on_deliver(sim.engine, message)
        self.assertEqual(sim.transaction_evidence.evidence[1], {0: 12})
        self.assertEqual(sim.metrics()["duplicate_traffic"][
            "duplicate_messages_by_kind"]["object"], 1)

    def test_wrapper_only_reaches_transaction_without_raw_admission(self):
        sim, stream = fixture()
        leaf = sim._birth("q", 0)
        sim._tick(0, 1)
        sim.run(until=16)
        self.assertEqual(diffusion(sim)["time_to_fraction_ms"], [-1])
        sim.run(until=20)
        self.assertEqual(sim.transaction_evidence.evidence, [{0: 0}, {0: 17}])
        self.assertEqual(diffusion(sim)["time_to_fraction_ms"], [17])
        self.assertEqual(diffusion(sim)["reached_txs"], 1)
        self.assertEqual(diffusion_time(stream.getvalue(), 2), (None, 0, 1))
        admissions = [json.loads(line) for line in stream.getvalue().splitlines()
                      if json.loads(line)["e"] == "adm"]
        self.assertEqual(len(admissions), 1)
        self.assertNotEqual(admissions[0]["o"], leaf.id)
        self.assertEqual(admissions[0]["via"], "wrapper")

    def test_body_required_body_arrives_after_evidence(self):
        sim, _ = fixture(proposer_id=1)
        leaf = sim._birth("q", 0)
        aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
        sim._on_deliver(sim.engine, wrapper(sim, aggregate))
        sim.run(until=2)
        self.assertEqual(diffusion(sim)["time_to_fraction_ms"], [2])
        self.assertEqual(diffusion(sim, kind="body")["time_to_fraction_ms"], [-1])
        self.assertEqual(sim._build_block(1).count(), 0)
        sim.run(until=10)
        sim._on_deliver(sim.engine, wrapper(sim, aggregate, bodies=1))
        self.assertEqual(sim._build_block(2).count(), 1)
        self.assertEqual(diffusion(sim, kind="body")["time_to_fraction_ms"], [10])
        self.assertEqual(sim.transaction_evidence.evidence[1][0], 2)
        sim.nodes[1].prune_discarded(sim.arena)
        self.assertEqual(diffusion(sim)["time_to_fraction_ms"], [2])

    def test_pending_bodies_wait_for_verification(self):
        sim, _ = fixture()
        leaf = sim._birth("q", 0)
        aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
        sim._on_deliver(sim.engine, wrapper(sim, aggregate, bodies=1))
        self.assertEqual(sim.transaction_evidence.bodies[1], {})
        self.assertEqual(sim.transaction_evidence.evidence[1], {})
        sim.run(until=2)
        self.assertEqual(sim.transaction_evidence.bodies[1], {0: 2})
        self.assertEqual(sim.transaction_evidence.evidence[1], {0: 2})

    def test_cpu_refusal_and_unresolved_reference_keep_bodies_not_evidence(self):
        for by_ref in (False, True):
            sim, _ = fixture(cpu_queue_jobs=0)
            leaf = sim._birth("q", 0)
            aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
            sim.cpu[1].submit(Job(100))
            sim._on_deliver(sim.engine, wrapper(sim, aggregate, bodies=1,
                                                by_ref=by_ref))
            self.assertEqual(sim.transaction_evidence.bodies[1], {0: 0})
            self.assertEqual(sim.transaction_evidence.evidence[1], {})
            self.assertEqual(diffusion(sim)["mean_ms"], -1)

    def test_expired_while_verifying_retains_body_without_admitting_evidence(self):
        sim, _ = fixture()
        leaf = sim._birth("q", 0, lifetime=1)
        aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
        sim._on_deliver(sim.engine, wrapper(sim, aggregate, bodies=1))
        sim.run(until=2)
        self.assertEqual(sim.transaction_evidence.evidence[1], {})
        self.assertEqual(sim.transaction_evidence.bodies[1], {0: 2})
        self.assertEqual(sim.chain.expired_count(), 1)

    def test_witnesses_are_evidence_and_narrow_claim_is_not_coverage(self):
        sim, _ = fixture()
        leaves = [sim._birth("q", 0) for _ in range(2)]
        aggregate = sim.arena.new_aggregate(leaves, 0, 0, claimed=0b11)
        sim._on_deliver(sim.engine, wrapper(sim, aggregate, mode=0, claim=1))
        sim.run(until=2)
        self.assertEqual(sim.transaction_evidence.evidence[1], {0: 2})
        self.assertNotIn(aggregate.id, sim.nodes[1].view.verified_proofs)
        self.assertEqual(diffusion(sim)["time_to_fraction_ms"], [2, -1])
        self.assertEqual(diffusion(sim)["mean_ms"], 2)

    def test_leaf_indices_and_birth_relative_delays(self):
        sim, _ = fixture()
        first = sim._birth("q", 0)
        sim._tick(0, 1)
        sim.run(until=20)
        second = sim._birth("q", 0)
        self.assertNotEqual(second.id, second.leaf_index)
        self.assertEqual(diffusion(sim, fraction=0.5)["time_to_fraction_ms"], [0, 0])
        self.assertEqual(sim.transaction_evidence.evidence[0],
                         {first.leaf_index: 0, second.leaf_index: 20})

    def test_query_validation_disabled_and_detached_tables(self):
        sim, _ = fixture()
        sim._birth("q", 0)
        for fraction in (0, -1, 1.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                diffusion(sim, fraction)
        with self.assertRaises(ValueError):
            diffusion(sim, kind="coverage")
        record = sim.transaction_evidence.metrics()
        record["evidence_ms_by_node"][0][0] = 99
        self.assertEqual(sim.transaction_evidence.evidence[0][0], 0)
        # metrics() carries counts, never the nodes x transactions tables.
        self.assertEqual(sim.metrics()["transaction_evidence"],
                         {"nodes": 2, "evidence_entries": 1, "body_entries": 1})
        sim.transaction_evidence = None
        self.assertEqual(sim.metrics()["transaction_evidence"], -1)
        self.assertEqual(diffusion(sim)["reached_txs"], -1)

    def test_storage_is_two_timestamp_tables_and_first_write_wins(self):
        observer = FirstEvidence(2)
        for now in range(100):
            observer.admit(0, 0b11, now)
            observer.body(1, 0b11, now)
        self.assertEqual(observer.evidence, [{0: 0, 1: 0}, {}])
        self.assertEqual(observer.bodies, [{}, {0: 0, 1: 0}])

    def test_repeated_claims_do_not_walk_their_bits_again(self):
        observer = FirstEvidence(1)
        wide = (1 << 5000) - 1
        with mock.patch.object(FirstEvidence, "_record",
                               wraps=FirstEvidence._record) as walks:
            observer.admit(0, wide, 1.0)
            self.assertEqual(walks.call_count, 1)
            for now in range(2, 50):
                observer.admit(0, wide, float(now))
            self.assertEqual(walks.call_count, 1)
            observer.admit(0, wide | (1 << 6000), 60.0)
            self.assertEqual(walks.call_count, 2)
            # Only the novel bit was walked, and the old ones kept their time.
            self.assertEqual(walks.call_args[0][1], 1 << 6000)
        self.assertEqual(len(observer.evidence[0]), 5001)
        self.assertEqual(observer.evidence[0][4999], 1.0)
        self.assertEqual(observer.evidence[0][6000], 60.0)


class TestDuplicateBurden(unittest.TestCase):
    def test_streamed_shares_keep_zero_followed_ids_without_message_history(self):
        sim, stream = fixture(costs=CostTable(), trace_follow=[0, 1, 2],
                              trace_sample=10)
        leaves = [sim._birth("q", 0) for _ in range(3)]
        aggregate = sim.arena.new_aggregate(leaves[:2], 0, 0, claimed=3)
        message = wrapper(sim, aggregate)
        sim._on_deliver(sim.engine, message)
        for _ in range(100):
            sim._on_deliver(sim.engine, message)
        streamed = streamed_duplicate_burden(sim)
        self.assertEqual(streamed, duplicate_burden(sim, stream.getvalue()))
        self.assertEqual(streamed["by_transaction_bytes"], {0: 900, 1: 800, 2: 0})
        self.assertEqual(streamed["traced_duplicate_messages"], 100)
        self.assertEqual(streamed["attributed_duplicate_bytes"], 1700)
        self.assertEqual(streamed["followed_transactions"], [0, 1, 2])
        self.assertEqual(len(sim.tracer.duplicate_accumulator.attributed), 3)
        streamed["by_transaction_bytes"][0] = -99
        self.assertEqual(streamed_duplicate_burden(sim)
                         ["by_transaction_bytes"][0], 900)

    def test_noninteger_custom_sizes_refuse_the_query_not_the_run(self):
        sim, stream = fixture(costs=CostTable())
        leaf = sim._birth("q", 0)
        aggregate = sim.arena.new_aggregate([leaf], 0, 0, claimed=1)
        message = wrapper(sim, aggregate, size=17.5)
        sim._on_deliver(sim.engine, message)
        sim._on_deliver(sim.engine, message)
        self.assertEqual(sim.duplicate_bytes, 17.5)
        for query in (lambda: duplicate_burden(sim, stream.getvalue()),
                      lambda: streamed_duplicate_burden(sim)):
            with self.assertRaisesRegex(ValueError, "nonnegative integers"):
                query()

    def test_wrapper_bytes_split_once_and_block_and_want_totals_retained(self):
        sim, stream = fixture(costs=CostTable(), chain_notify="overlay")
        leaves = [sim._birth("q", 0) for _ in range(2)]
        aggregate = sim.arena.new_aggregate(leaves, 0, 0, claimed=3)
        message = wrapper(sim, aggregate)
        sim._on_deliver(sim.engine, message)
        sim._on_deliver(sim.engine, message)
        block = sim.size_model.block(0, 1, 0, 0)
        block_message = WireMessage(0, 1, block)
        sim._on_deliver(sim.engine, block_message)
        sim._on_deliver(sim.engine, block_message)
        want = WantPayload(leaves[0].id, SizeBreakdown(control=7))
        sim._on_deliver(sim.engine, WireMessage(1, 0, want))
        burden = duplicate_burden(sim, stream.getvalue())
        self.assertEqual(streamed_duplicate_burden(sim), burden)
        self.assertEqual(burden["by_transaction_bytes"], {0: 9, 1: 8})
        self.assertEqual(burden["traced_duplicate_bytes"], 17)
        self.assertEqual(burden["traced_duplicate_messages"], 1)
        self.assertEqual(burden["duplicate_bytes"], 17 + block.size.total())
        self.assertEqual(burden["ingress_bytes_by_kind"]["want"], 7)
        self.assertEqual(burden["duplicate_messages_by_kind"]["want"], 0)
        self.assertEqual(burden["duplicate_messages_by_kind"]["block"], 1)
        self.assertEqual(burden["ingress_bytes"], 34 + 2 * block.size.total() + 7)
        self.assertEqual(burden["attributed_duplicate_bytes"]
                         + burden["unattributed_duplicate_bytes"],
                         burden["duplicate_bytes"])
        self.assertLessEqual(sum(burden["by_transaction_bytes"].values()),
                             burden["duplicate_bytes"])
        counts = sim.metrics()["duplicate_traffic"]
        self.assertEqual(counts["duplicate_messages"], sim.duplicates)
        self.assertEqual(counts["duplicate_bytes"], sim.duplicate_bytes)

    def test_unfollowed_or_focused_out_duplicates_are_not_global_zeros(self):
        for options in ({"trace_follow": []}, {"trace_focus": [0]}):
            sim, stream = fixture(costs=CostTable(), **options)
            leaf = sim._birth("q", 0)
            aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
            message = wrapper(sim, aggregate)
            sim._on_deliver(sim.engine, message)
            sim._on_deliver(sim.engine, message)
            burden = duplicate_burden(sim, stream.getvalue())
            self.assertEqual(streamed_duplicate_burden(sim), burden)
            self.assertEqual(burden["duplicate_bytes"], 17)
            self.assertEqual(burden["traced_duplicate_bytes"], 0)
            if "trace_follow" in options:
                self.assertEqual(burden["attributed_duplicate_bytes"], -1)
            else:
                self.assertEqual(burden["attributed_duplicate_bytes"], 0)
                self.assertEqual(burden["unattributed_duplicate_bytes"], 17)

    def test_duplicate_trace_cannot_exceed_message_accounting(self):
        sim, stream = fixture(costs=CostTable())
        leaf = sim._birth("q", 0)
        aggregate = sim.arena.new_aggregate((leaf,), 0, 0, claimed=1)
        message = wrapper(sim, aggregate)
        sim._on_deliver(sim.engine, message)
        sim._on_deliver(sim.engine, message)
        trace = stream.getvalue()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            duplicate_burden(sim, trace + trace)


class TestEvidenceNeutrality(unittest.TestCase):
    def trajectory(self, enabled):
        sim, stream = fixture(costs=CostTable(verify_recv=2, prove_recursive=5,
                                             jitter_frac=0.2),
                               aggregation_interval=20, slot_length=100)
        if not enabled:
            sim.transaction_evidence = None
        for index in range(6):
            sim.schedule_birth(index * 4, "q", node_id=index % 2)
        sim.schedule_ticks(6)
        sim.schedule_slots(2)
        sim.run(until=250)
        return sim, stream.getvalue()

    def test_observer_changes_no_trace_csv_events_or_provenance_reads(self):
        results = []
        original = Arena.coverage
        for enabled in (False, True):
            with mock.patch.object(Arena, "coverage", autospec=True,
                                   side_effect=original) as reads:
                sim, trace = self.trajectory(enabled)
                results.append((trace, csv_row(sim.metrics()),
                                sim.engine.events_processed, reads.call_count))
        self.assertEqual(results[0], results[1])

    def test_placement_json_adds_evidence_without_changing_default_text(self):
        text = output("placement")
        with mock.patch.object(FirstEvidence, "admit"), \
                mock.patch.object(FirstEvidence, "body"):
            self.assertEqual(output("placement"), text)
        rows = [json.loads(line) for line in output("placement", "--json").splitlines()]
        for row in rows:
            self.assertIn("evidence_diffusion", row["metrics"])
            self.assertIn("body_diffusion", row["metrics"])
            self.assertIn("time_to_90_ms", row["metrics"])
            self.assertEqual(row["metrics"]["duplicate_burden"][
                "attributed_duplicate_bytes"], -1)


if __name__ == "__main__":
    unittest.main()
