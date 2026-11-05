"""Participation changes proving and forwarding without changing default runs."""

import hashlib
import io
import math
import unittest
from unittest import mock

from bench.common import random_regular_overlay, schedule_horizon
from bench.mainnet import calibrated_prover
from starksim.arena import KIND_PQ, KIND_PRIVACY
from starksim.costs import (CostTable, NativeVerifyCost, PROVE_RECURSIVE,
                            VERIFY_RECV)
from starksim.engine import Job, Server
from starksim.netsim import NetworkSim, csv_row
from starksim.network import Overlay, bytes_per_ms
from starksim.outcomes import outcomes_at_age
from starksim.payload import ObjectPayload, SizeBreakdown, WrapperCore


def star():
    return Overlay.from_adjacency([[1], [0, 2, 3], [1], [1]],
                                  base_latency=1.0)


def relay_sim(**settings):
    options = dict(aggregator_share=0.75, proposer_id=1,
                   proposer_role="relay", aggregation_trigger="aligned")
    options.update(settings)
    return NetworkSim(star(), **options)


def proof_at(sim, source, leaves=3, claimed=None):
    inputs = [sim.arena.new_leaf(KIND_PQ, 0.0, source)
              for _ in range(leaves)]
    proof = sim.arena.new_aggregate(inputs, 0.0, source, claimed=claimed)
    sim.nodes[source].finish_receive(proof, 0.0, sim.arena.coverage(proof))
    return proof


def send_proof(sim, proof, sender=0, receiver=1, mode=1):
    claim = sim.arena.claim(proof)
    core = WrapperCore(proof.id, proof.id, sender, 0, mode,
                       claim, claim.bit_count())
    witnesses = sim._mode_zero_bytes(claim) if mode == 0 else None
    sim._send(sender, receiver, sim.size_model.wrapper(
        core, claim, witness_bytes=witnesses))


class TestDefaultCompatibility(unittest.TestCase):
    def test_mainnet_shaped_run_matches_pre_role_trace_csv_and_events(self):
        # Digests captured before adding participation, with the same complete
        # trace and row contracts as test_netsim_characterization.
        trace = io.StringIO()
        sim = NetworkSim(random_regular_overlay(6, 2, 100.0, 13), seed=13,
                         capacity=bytes_per_ms(25), slot_length=12000.0,
                         costs=CostTable(
                             prove_recursive=calibrated_prover(417, 1), seed=13),
                         verify_cost=NativeVerifyCost(0.1, 15.6), trace=trace)
        for index in range(24):
            kind = KIND_PRIVACY if index % 4 == 0 else KIND_PQ
            sim.schedule_birth(index * 500, kind, fee=index % 5)
        schedule_horizon(sim, 36000)
        sim.run(until=36000)
        sim.finish_trace()
        self.assertEqual(hashlib.sha256(trace.getvalue().encode()).hexdigest(),
                         "e313390aaa520f9aa68c8c4a40a015400"
                         "a1131ff04ee186e92e876ec0e425328")
        self.assertEqual(hashlib.sha256(csv_row(sim.metrics()).encode())
                         .hexdigest(),
                         "ec722075c43d4cf2389ff4fe27375f448"
                         "2dbe70d02a5970cc06e3f5f06d6c3c9")
        self.assertEqual(sim.engine.events_processed, 1442)


class TestNoRelayProves(unittest.TestCase):
    def test_mixed_population_under_every_rule_and_trigger(self):
        original_submit = Server.submit
        for rule in ("best", "all", "none"):
            for trigger in ("spread", "aligned", "when_ready"):
                with self.subTest(rule=rule, trigger=trigger):
                    sim = NetworkSim(
                        random_regular_overlay(6, 2, 10.0, 7), seed=7,
                        aggregator_share=2 / 3, relay_forwarding=rule,
                        aggregation_trigger=trigger, proving_budget=1000,
                        costs=CostTable(prove_recursive=10, verify_recv=2))
                    submitted = []

                    def submit(server, job):
                        if job.kind == PROVE_RECURSIVE:
                            submitted.append(int(server.name.split(":")[1]))
                        return original_submit(server, job)

                    for index in range(18):
                        kind = KIND_PQ if index % 2 else KIND_PRIVACY
                        sim.schedule_birth(index * 100, kind, node_id=index % 6)
                    schedule_horizon(sim, 8000)
                    with mock.patch.object(Server, "submit", submit):
                        sim.run(until=8000)
                    self.assertTrue(submitted)
                    self.assertTrue(set(submitted) <= sim.aggregator_nodes)
                    self.assertEqual(len(sim.relay_nodes), 2)
                    for node in sim.relay_nodes:
                        self.assertEqual(sim.plan_work.nodes[node]["proofs"], 0)
                    self.assertEqual(sim.metrics()["roles"]["relay"]
                                     ["proofs_completed"], 0)
                    sim.assert_invariants()


class TestForwardBest(unittest.TestCase):
    def _ready_relay_with_proof(self):
        sim = relay_sim(aggregation_trigger="when_ready", proving_budget=1000,
                        costs=CostTable(prove_recursive=1e6, verify_recv=1))
        proof = proof_at(sim, 0, leaves=3)
        send_proof(sim, proof)
        sim.run(until=5)
        self.assertEqual(sim.wrappers_by_node[1], 1)
        return sim, proof

    def test_when_ready_births_and_leaf_admissions_do_not_wake_relay(self):
        sim, proof = self._ready_relay_with_proof()
        decisions = sim._next_generation[1]
        sim.schedule_birth(6, KIND_PQ, node_id=1)
        sim.schedule_birth(7, KIND_PRIVACY, node_id=0)
        sim.run(until=20)
        self.assertEqual(len(sim.nodes[1].view.held), 3)
        self.assertIn(proof.id, sim.nodes[1].view.held)
        self.assertEqual(sim.wrappers_by_node[1], 1)
        self.assertEqual(sim._next_generation[1], decisions)

    def test_when_ready_larger_new_proof_emits_once_after_raw_arrivals(self):
        sim, first = self._ready_relay_with_proof()
        sim.schedule_birth(6, KIND_PQ, node_id=1)
        sim.run(until=10)
        larger = proof_at(sim, 0, leaves=5)
        send_proof(sim, larger)
        sim.run(until=20)
        self.assertEqual(sim.wrappers_by_node[1], 2)
        sim._wake_if_ready(1)
        sim.run(until=30)
        self.assertEqual(sim.wrappers_by_node[1], 2)
        self.assertIn(first.id, sim.nodes[1].view.held)

    def test_when_ready_smaller_new_proof_does_not_reemit_best(self):
        sim, first = self._ready_relay_with_proof()
        smaller = proof_at(sim, 0, leaves=2)
        send_proof(sim, smaller)
        sim.run(until=15)
        self.assertIn(smaller.id, sim.nodes[1].view.held)
        self.assertIn(first.id, sim.nodes[1].view.held)
        self.assertEqual(sim.wrappers_by_node[1], 1)

    def test_when_ready_settlement_can_change_best_without_new_proof(self):
        sim, first = self._ready_relay_with_proof()
        smaller = proof_at(sim, 0, leaves=2)
        send_proof(sim, smaller)
        sim.run(until=15)
        decisions = sim._next_generation[1]
        sim.chain.expire(1)
        sim._wake_if_ready(1)
        self.assertEqual(sim._next_generation[1], decisions + 1)
        self.assertEqual(sim.wrappers_by_node[1], 1)
        sim.chain.expire(2)
        sim._wake_if_ready(1)
        self.assertEqual(sim.wrappers_by_node[1], 2)
        self.assertEqual(sim._relay_last_best[1], smaller.id)
        self.assertIn(first.id, sim.nodes[1].view.held)

    def test_when_ready_full_aggregate_admission_changes_best(self):
        sim, first = self._ready_relay_with_proof()
        larger = proof_at(sim, 0, leaves=5)
        sim._send(0, 1, sim.size_model.object_payload(
            larger, dep_count=sim.arena.claim(larger).bit_count()))
        sim.run(until=15)
        self.assertEqual(sim.wrappers_by_node[1], 2)
        self.assertEqual(sim._relay_last_best[1], larger.id)
        self.assertIn(first.id, sim.nodes[1].view.held)

    def test_clocked_relay_keeps_emitting_unchanged_best(self):
        for trigger in ("aligned", "spread"):
            with self.subTest(trigger=trigger):
                sim = relay_sim(aggregation_trigger=trigger)
                proof = proof_at(sim, 0)
                send_proof(sim, proof)
                sim.run(until=5)
                sim._tick(1, 1)
                sim.run(until=10)
                sim._tick(1, 2)
                self.assertEqual(sim.wrappers_by_node[1], 2)

    def test_largest_live_claim_is_rewrapped_by_relay(self):
        sim = relay_sim()
        # Larger structural coverage is deliberately less useful after local
        # settlement. Only claim and the relay's knowledge may decide.
        broad = proof_at(sim, 0, 5)
        useful = proof_at(sim, 0, 3)
        sim.chain.include(sim.arena.claim(broad) & ~1, 0)
        send_proof(sim, broad)
        send_proof(sim, useful)
        sim.run(until=5)
        with mock.patch.object(NetworkSim, "_emit_wrapper",
                               wraps=sim._emit_wrapper) as emit:
            sim._tick(1, 1)
        self.assertEqual(emit.call_count, 1)
        node, chosen, tick = emit.call_args.args
        self.assertEqual((node.id, chosen.id, tick), (1, useful.id, 1))
        self.assertEqual(sim.proofs_completed, 0)
        self.assertEqual(sim.wrappers_by_node[1], 1)
        core = sim.egress[1].current_job.tag.payload.core
        self.assertEqual(core.creator, 1)
        self.assertEqual(core.proof_object_id, useful.id)

    def test_no_recursive_proof_and_witness_claim_emit_nothing(self):
        for rule in ("best", "all"):
            with self.subTest(rule=rule):
                sim = relay_sim(relay_forwarding=rule)
                sim._birth(KIND_PQ, 1)
                sim._tick(1, 1)
                proof = proof_at(sim, 0)
                send_proof(sim, proof, mode=0)
                sim.run(until=5)
                self.assertIn(proof.id, sim.nodes[1].view.witness_backed)
                sim._tick(1, 2)
                self.assertEqual(sim.wrappers_by_node[1], 0)
                self.assertEqual(sim.proofs_completed, 0)

    def test_ties_use_lowest_proof_id(self):
        sim = relay_sim()
        first = proof_at(sim, 0)
        second = proof_at(sim, 0)
        send_proof(sim, second)
        send_proof(sim, first)
        sim.run(until=5)
        sim._tick(1, 1)
        self.assertEqual(sim.egress[1].current_job.tag.payload.core
                         .proof_object_id, first.id)

    def test_best_honours_every_wrapper_economy(self):
        for economy in ("full", "skip_unchanged", "proof_by_ref", "lean"):
            with self.subTest(economy=economy):
                sim = relay_sim(wrapper_economy=economy)
                proof = proof_at(sim, 0)
                send_proof(sim, proof)
                sim.run(until=5)
                sim.nodes[1].confirm_bodies_delivered(0, sim.arena.claim(proof))
                with mock.patch.object(NetworkSim, "_send") as send:
                    sim._tick(1, 1)
                back = [call.args[2] for call in send.call_args_list
                        if call.args[1] == 0]
                if economy in ("skip_unchanged", "lean"):
                    self.assertEqual(back, [])
                else:
                    self.assertEqual(len(back), 1)
                    self.assertEqual(back[0].proof_by_ref,
                                     economy == "proof_by_ref")

    def test_witness_upgrade_waits_until_recursive_verification_finishes(self):
        for rule in ("best", "all"):
            with self.subTest(rule=rule):
                sim = relay_sim(relay_forwarding=rule,
                                costs=CostTable(verify_recv=10))
                proof = proof_at(sim, 0)
                send_proof(sim, proof, mode=0)
                sim.run(until=2)
                send_proof(sim, proof)
                sim.run(until=12)
                # Witnesses have verified, while the proof upgrade is queued
                # on the same CPU. Held claim identity is insufficient.
                self.assertIn(proof.id, sim.nodes[1].view.held)
                self.assertIn(proof.id, sim.nodes[1].view.upgrading)
                sim._tick(1, 1)
                self.assertEqual(sim.wrappers_by_node[1], 0)
                sim.run(until=22)
                sim._tick(1, 2)
                self.assertEqual(sim.wrappers_by_node[1], 1)


class TestForwardAll(unittest.TestCase):
    def test_every_proof_once_to_each_peer_missing_it(self):
        for economy in ("full", "skip_unchanged", "proof_by_ref", "lean"):
            with self.subTest(economy=economy):
                sim = relay_sim(relay_forwarding="all", wrapper_economy=economy)
                first = proof_at(sim, 0)
                second = proof_at(sim, 0, 2)
                send_proof(sim, first)
                send_proof(sim, second)
                sim.run(until=5)
                sim.nodes[1].note_proof_at_peer(2, second.id)
                # Peer 2 already has one body from the first proof.
                sim.nodes[1].confirm_bodies_delivered(2, 1)
                with mock.patch.object(NetworkSim, "_send") as send:
                    sim._tick(1, 1)
                    sent = [(call.args[1], call.args[2].core.proof_object_id)
                            for call in send.call_args_list]
                    self.assertCountEqual(sent, [(2, first.id), (3, first.id),
                                                 (3, second.id)])
                    first_to_two = next(call.args[2]
                                        for call in send.call_args_list
                                        if call.args[1] == 2)
                    self.assertEqual(first_to_two.body_mask,
                                     sim.arena.claim(first) & ~1)
                    for call in send.call_args_list:
                        self.assertEqual(call.args[2].core.creator, 1)
                        self.assertFalse(call.args[2].proof_by_ref)
                    send.reset_mock()
                    sim._tick(1, 2)
                    send.assert_not_called()
                self.assertEqual(sim.wrappers_by_node[1], 2)
                self.assertEqual(sim.proofs_completed, 0)

    def test_duplicate_admission_does_not_forward_an_old_proof_again(self):
        sim = relay_sim(relay_forwarding="all")
        proof = proof_at(sim, 0)
        send_proof(sim, proof)
        sim.run(until=5)
        sim._tick(1, 1)
        # Explicit re-admission models receiving again after dropping evidence.
        sim.nodes[1].finish_receive(proof, 5)
        sim._note_relay_proof(sim.nodes[1], proof)
        sim._tick(1, 2)
        self.assertEqual(sim.wrappers_by_node[1], 1)

    def test_when_ready_single_proof_wakes_once_after_verification(self):
        for rule in ("all", "best"):
            with self.subTest(rule=rule):
                sim = relay_sim(relay_forwarding=rule,
                                aggregation_trigger="when_ready",
                                proving_budget=1000,
                                costs=CostTable(verify_recv=10))
                proof = proof_at(sim, 0)
                send_proof(sim, proof)
                sim.run(until=5)
                self.assertEqual(sim.wrappers_by_node[1], 0)
                sim.run(until=12)
                self.assertEqual(sim.wrappers_by_node[1], 1)
                for _ in range(10):
                    sim._wake_if_ready(1)
                self.assertEqual(sim.wrappers_by_node[1], 1)
                self.assertEqual(sim.plan_work.nodes[1]["proofs"], 0)

    def test_raw_birth_at_relay_reaches_aggregator_and_block(self):
        for policy in ("flood", "announce_fetch"):
            for rule in ("all", "best", "none"):
                with self.subTest(policy=policy, rule=rule):
                    sim = relay_sim(relay_policy=policy, relay_forwarding=rule,
                                    proposer_id=0, proposer_role="aggregator",
                                    slot_length=4000)
                    entry = sim.relay_nodes[0]
                    sim.schedule_birth(0, KIND_PQ, node_id=entry)
                    sim.schedule_ticks(3)
                    sim.schedule_slots(1)
                    sim.run()
                    self.assertEqual(sim.chain.included_count(), 1)
                    self.assertTrue(any(0 in sim.nodes[node].view.seen
                                        for node in sim.aggregator_nodes))
                    self.assertEqual(sim.entry_of[0], entry)


class TestForwardNone(unittest.TestCase):
    def test_none_relays_never_emit_or_decide_under_every_trigger(self):
        for trigger in ("spread", "aligned", "when_ready"):
            with self.subTest(trigger=trigger):
                sim = NetworkSim(
                    random_regular_overlay(6, 2, 10.0, 7), seed=7,
                    aggregator_share=2 / 3, relay_forwarding="none",
                    aggregation_trigger=trigger, proving_budget=1000,
                    costs=CostTable(prove_recursive=10, verify_recv=2))
                for index in range(18):
                    sim.schedule_birth(index * 100, KIND_PQ, node_id=index % 6)
                schedule_horizon(sim, 8000)
                with mock.patch.object(NetworkSim, "_relay_proofs",
                                       wraps=sim._relay_proofs) as scan:
                    sim.run(until=8000)
                self.assertEqual(scan.call_count, 0)
                for node in sim.relay_nodes:
                    self.assertEqual(sim.wrappers_by_node[node], 0)
                self.assertGreater(sum(sim.wrappers_by_node[node]
                                       for node in sim.aggregator_nodes), 0)
                sim.assert_invariants()

    def test_builder_only_has_one_aggregator_the_proposer_and_includes(self):
        overlay = random_regular_overlay(12, 3, 10.0, 3)
        sim = NetworkSim(overlay, seed=3, aggregator_share=1 / 12,
                         relay_forwarding="none", proposer_role="aggregator",
                         proposer_id=5, slot_length=4000,
                         costs=CostTable(prove_recursive=10, verify_recv=2))
        self.assertEqual(sim.aggregator_nodes, {5})
        for index in range(12):
            sim.schedule_birth(index * 100, KIND_PQ, node_id=index)
        sim.schedule_ticks(8)
        sim.schedule_slots(2)
        sim.run()
        self.assertEqual(sim.chain.included_count(), 12)
        self.assertEqual(sum(sim.wrappers_by_node[node]
                             for node in sim.relay_nodes), 0)
        for node in sim.relay_nodes:
            self.assertEqual(sim.plan_work.nodes[node]["proofs"], 0)
        sim.assert_invariants()


class TestRelayReviewRepairs(unittest.TestCase):
    def _settlement_repro(self, chain_notify, proposer):
        sim = NetworkSim(star(), seed=5, aggregator_share=0.5,
                         proposer_id=proposer, chain_notify=chain_notify,
                         aggregation_trigger="when_ready", proving_budget=1000,
                         block_budget=0, slot_length=20,
                         costs=CostTable(prove_recursive=1e6))
        self.assertEqual(sim.aggregator_nodes, {0, 3})
        first = proof_at(sim, 2, leaves=3)
        second = proof_at(sim, 2, leaves=2)
        send_proof(sim, first, sender=2)
        send_proof(sim, second, sender=2)
        sim.schedule_slots(2)
        return sim

    def test_r25_instant_blocks_wake_relay(self):
        sim = self._settlement_repro("instant", 3)
        sim.run(until=40)
        self.assertEqual([block.count() for block in sim.chain.blocks], [3, 2])
        self.assertEqual(sim.wrappers_by_node[1], 2)

    def test_r25_local_proposer_settlement_wakes_relay(self):
        sim = self._settlement_repro("overlay", 1)
        sim.run(until=20)
        self.assertEqual(sim.chain.blocks[0].count(), 3)
        self.assertEqual(sim.wrappers_by_node[1], 2)

    def test_r25_eviction_on_birth_and_raw_admission_reselects_after_limit(self):
        for entry in (1, 0):
            with self.subTest(entry=entry):
                sim = relay_sim(aggregation_trigger="when_ready",
                                proving_budget=1000, candidate_limit=2,
                                eviction_policy="fee_value",
                                costs=CostTable(prove_recursive=1e6))
                first = proof_at(sim, 2, leaves=3)
                second = proof_at(sim, 2, leaves=2)
                for leaf in sim.arena.leaves_in(sim.arena.claim(second)):
                    leaf.fee = 10
                send_proof(sim, first, sender=2)
                send_proof(sim, second, sender=2)
                sim.run(until=5)
                self.assertEqual(sim.wrappers_by_node[1], 1)
                sim.schedule_birth(6, KIND_PQ, node_id=entry, fee=100)
                sim.run(until=15)
                self.assertNotIn(first.id, sim.nodes[1].view.held)
                self.assertIn(second.id, sim.nodes[1].view.held)
                self.assertEqual(len(sim.nodes[1].view.held), 2)
                self.assertEqual(sim.wrappers_by_node[1], 2)
                self.assertEqual(sim._relay_last_best[1], second.id)

    def test_r25_pruning_invalidates_stamp_without_repeated_decisions(self):
        sim = self._settlement_repro("instant", 3)
        sim.run(until=5)
        generation = sim._relay_proof_generation[1]
        sim.run(until=20)
        self.assertGreater(sim._relay_proof_generation[1], generation)
        self.assertEqual(sim._woke_at[1][0], sim._relay_proof_generation[1])
        decisions = sim._next_generation[1]
        sim._wake_if_ready(1)
        self.assertEqual(sim._next_generation[1], decisions)

    def test_r25_relay_forwards_at_admission_with_verification_queue_busy(self):
        for rule in ("best", "all"):
            with self.subTest(rule=rule):
                sim = relay_sim(aggregation_trigger="when_ready",
                                relay_forwarding=rule, proving_budget=1000,
                                costs=CostTable(verify_recv=10))
                proof = proof_at(sim, 2)
                send_proof(sim, proof, sender=2)
                sim.run(until=2)
                for _ in range(20):
                    sim.cpu[1].submit(Job(10, kind=VERIFY_RECV))
                sim.run(until=12)
                self.assertEqual(sim.nodes[1].view.held[proof.id], 11)
                self.assertTrue(sim.cpu[1].is_busy())
                self.assertTrue(sim.cpu[1].waiting)
                self.assertEqual(sim.wrappers_by_node[1], 1)
                self.assertIsNone(sim.cpu[1].on_idle)
                sim.run(until=250)
                self.assertEqual(sim.wrappers_by_node[1], 1)

    def _fill_relay_egress(self, sim, proof, waiting_bytes):
        sim._send(1, 0, ObjectPayload(proof.id,
                                     SizeBreakdown(proof=4_000_000)))
        sim._send(1, 0, ObjectPayload(proof.id,
                                     SizeBreakdown(proof=waiting_bytes)))

    def test_r25_all_retries_refused_peers_without_repeating_accepted_peers(self):
        for waiting_bytes in (4_000_000, 3_700_000):
            with self.subTest(waiting_bytes=waiting_bytes):
                sim = relay_sim(relay_forwarding="all", capacity=bytes_per_ms(25),
                                egress_queue_bytes=4_000_000)
                proof = proof_at(sim, 0)
                send_proof(sim, proof)
                sim.run(until=200)
                self._fill_relay_egress(sim, proof, waiting_bytes)
                sim._tick(1, 1)
                sim.run(until=3000)
                missing = [peer for peer in (2, 3)
                           if proof.id not in sim.nodes[peer].view.held]
                self.assertTrue(missing)
                original_send = NetworkSim._send
                with mock.patch.object(NetworkSim, "_send", autospec=True,
                                       side_effect=original_send) as send:
                    sim._tick(1, 2)
                targets = [call.args[2] for call in send.call_args_list]
                self.assertCountEqual(targets, missing)
                sim.run(until=4000)
                for peer in (2, 3):
                    self.assertIn(proof.id, sim.nodes[peer].view.held)
                self.assertEqual(sim._relay_forwarded[1][proof.id], {2, 3})
                self.assertNotIn(proof.id, sim._relay_pending[1])

    def test_r25_tickless_refusal_waits_for_another_trigger_without_spinning(self):
        sim = relay_sim(relay_forwarding="all", capacity=bytes_per_ms(25),
                        egress_queue_bytes=4_000_000,
                        aggregation_trigger="when_ready", proving_budget=1000,
                        costs=CostTable(prove_recursive=1e6))
        proof = proof_at(sim, 0)
        self._fill_relay_egress(sim, proof, 4_000_000)
        send_proof(sim, proof)
        sim.run(until=200)
        self.assertEqual(sim.wrappers_by_node[1], 1)
        decisions = sim._next_generation[1]
        sim.run(until=3000)
        self.assertEqual(sim._next_generation[1], decisions)
        self.assertNotIn(proof.id, sim.nodes[2].view.held)
        other = proof_at(sim, 0, leaves=2)
        send_proof(sim, other)
        sim.run(until=4000)
        self.assertIn(proof.id, sim.nodes[2].view.held)
        self.assertIn(other.id, sim.nodes[2].view.held)

    def test_r25_full_aggregate_waits_for_clocked_forwarding_decision(self):
        for rule in ("best", "all"):
            for policy in ("flood", "announce_fetch"):
                with self.subTest(rule=rule, policy=policy):
                    sim = relay_sim(relay_forwarding=rule, relay_policy=policy)
                    proof = proof_at(sim, 0)
                    sim._send(0, 1, sim.size_model.object_payload(
                        proof, dep_count=sim.arena.claim(proof).bit_count()))
                    sim.run(until=5)
                    self.assertIn(proof.id, sim.nodes[1].view.held)
                    self.assertEqual(sim.offered_kind[1], {})
                    for peer in (2, 3):
                        self.assertNotIn(proof.id, sim.nodes[peer].view.held)
                    sim._tick(1, 1)
                    sim.run(until=10)
                    for peer in (2, 3):
                        self.assertIn(proof.id, sim.nodes[peer].view.held)

    def test_r25_unchanged_stamp_skips_relay_proof_scan(self):
        sim = relay_sim(aggregation_trigger="when_ready", proving_budget=1000)
        proof = proof_at(sim, 2)
        send_proof(sim, proof, sender=2)
        sim.run(until=5)
        before = (sim.engine.events_processed, sim.ticks_run,
                  sim.wrappers_emitted, sim.chain.included_count())
        with mock.patch.object(NetworkSim, "_relay_proofs",
                               wraps=sim._relay_proofs) as scan:
            for _ in range(20):
                sim._wake_if_ready(1)
        self.assertEqual(scan.call_count, 0)
        self.assertEqual(before, (sim.engine.events_processed, sim.ticks_run,
                                  sim.wrappers_emitted,
                                  sim.chain.included_count()))


class TestPlacement(unittest.TestCase):
    def test_random_count_seed_reproducibility_and_stream_independence(self):
        overlay = random_regular_overlay(12, 2, 1, 0)
        assignments = set()
        for seed in range(6):
            sim = NetworkSim(overlay, seed=seed, aggregator_share=0.4)
            again = NetworkSim(overlay, seed=seed, aggregator_share="0.4")
            self.assertEqual(len(sim.aggregator_nodes), 5)
            self.assertEqual(sim.aggregator_nodes, again.aggregator_nodes)
            assignments.add(sim.aggregator_nodes)
            default = NetworkSim(overlay, seed=seed)
            self.assertEqual(sim.rng_entry.getstate(), default.rng_entry.getstate())
            self.assertEqual(sim.costs._rng.getstate(), default.costs._rng.getstate())
        self.assertGreater(len(assignments), 1)
        self.assertEqual(len(NetworkSim(overlay, aggregator_share=0.001)
                             .aggregator_nodes), 1)
        self.assertEqual(len(NetworkSim(overlay, aggregator_share=0.375)
                             .aggregator_nodes), 4)  # round(4.5), ties to even

    def test_central_uses_bfs_closeness_and_node_id_ties(self):
        sim = NetworkSim(star(), aggregator_share=0.25,
                         aggregator_placement="central")
        self.assertEqual(sim.aggregator_nodes, {1})
        path = Overlay.from_adjacency([[1], [0, 2], [1, 3], [2]], base_latency=1)
        sim = NetworkSim(path, aggregator_share=0.25,
                         aggregator_placement="central")
        self.assertEqual(sim.aggregator_nodes, {1})
        sim = NetworkSim(path, aggregator_share=0.5,
                         aggregator_placement="central")
        self.assertEqual(sim.aggregator_nodes, {1, 2})

    def test_forced_fixed_proposer_preserves_count(self):
        for role, proposer, expected in (("relay", 1, {0, 2}),
                                         ("aggregator", 3, {1, 3})):
            with self.subTest(role=role):
                sim = NetworkSim(star(), aggregator_share=0.5,
                                 aggregator_placement="central",
                                 proposer_id=proposer, proposer_role=role)
                self.assertEqual(sim.aggregator_nodes, expected)
                self.assertEqual(proposer in sim.aggregator_nodes,
                                 role == "aggregator")

    def test_invalid_settings_name_the_field_to_change(self):
        cases = [({"aggregator_share": share}, "aggregator_share")
                 for share in (0, -0.1, 1.1, math.nan, math.inf, None, "bad")]
        cases += [({"aggregator_share": 0.5, "relay_policy": "wrapper_only"},
                   "relay_policy"),
                  ({"proposer_role": "relay"}, "aggregator_share")]
        for mode in ("rotating", "random", "dedicated"):
            for role in ("aggregator", "relay"):
                cases.append(({"proposer_mode": mode, "proposer_role": role},
                              "proposer_mode"))
        for field in ("aggregator_placement", "relay_forwarding", "proposer_role"):
            cases.append(({field: "typo"}, field))
        for settings, field in cases:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(ValueError, field):
                    NetworkSim(star(), **settings)
        # Rounding can leave all nodes aggregating even at a share below one.
        NetworkSim(star(), aggregator_share=0.99, relay_policy="wrapper_only")


class TestRoleMetrics(unittest.TestCase):
    def test_role_counts_outcomes_and_costs_add_up(self):
        for share in (1.0, 0.5):
            with self.subTest(share=share):
                sim = NetworkSim(star(), aggregator_share=share,
                                 proposer_mode="rotating", slot_length=12000,
                                 capacity=1000,
                                 costs=CostTable(prove_recursive=10,
                                                 verify_recv=3, build_block=5))
                for index in range(12):
                    sim.schedule_birth(index * 500, KIND_PQ, node_id=index % 4)
                schedule_horizon(sim, 36000)
                sim.run(until=36000)
                metrics = sim.metrics()
                roles = metrics["roles"]
                self.assertEqual(list(roles), ["aggregator", "relay"])
                for key, total in (("nodes", 4), ("born", sim.arrived_txs),
                                    ("included", sim.chain.included_count()),
                                    ("proofs_completed", sim.proofs_completed),
                                    ("wrappers_emitted", sim.wrappers_emitted)):
                    self.assertEqual(sum(row[key] for row in roles.values()), total)
                for kind in ("cpu", "egress"):
                    weighted = sum(row[kind + "_utilisation"]["mean"]
                                   * row["nodes"] for row in roles.values()) / 4
                    self.assertAlmostEqual(weighted, metrics[kind + "_utilisation"])
                bytes_rate = sum(row["all_bytes_sent_per_node_second"]
                                 * row["nodes"] for row in roles.values())
                self.assertAlmostEqual(bytes_rate * 36, sim.total_wire_bytes())
                blocks = metrics["blocks_by_proposer_role"]
                self.assertEqual(sum(row["blocks_built"] for row in blocks.values()), 3)
                self.assertEqual(sum(row["included_txs"] for row in blocks.values()),
                                 sim.chain.included_count())
                if share == 1:
                    aggregator = roles["aggregator"]
                    for key in ("cpu_utilisation", "egress_utilisation"):
                        self.assertEqual(aggregator[key]["mean"], metrics[key])
                    self.assertEqual(aggregator["delay_mean_slots"], sim.delay_mean())
                    self.assertEqual(aggregator["included_within_24s_share"],
                                     outcomes_at_age(sim, 24000, 0, 6000)
                                     ["included_fraction"])
                    self.assertEqual(metrics["relay_node_ids"], [])
                    for value in roles["relay"].values():
                        if isinstance(value, dict):
                            self.assertTrue(all(item == 0 for item in value.values()))
                        else:
                            self.assertEqual(value, 0)

    def test_fixed_age_excludes_included_but_immature_births(self):
        sim = relay_sim()
        sim.schedule_birth(0, KIND_PQ, node_id=0)
        sim.schedule_birth(23000, KIND_PQ, node_id=1)
        sim.schedule_slots(6)
        sim.run(until=24000)
        roles = sim.metrics()["roles"]
        self.assertEqual(roles["aggregator"]["mature_24s"], 1)
        self.assertEqual(roles["relay"]["included"], 1)
        self.assertEqual(roles["relay"]["censored_24s"], 1)
        self.assertEqual(roles["relay"]["included_within_24s_share"], -1)
        self.assertEqual(sim.metrics()["blocks_by_proposer_role"], {
            "aggregator": {"blocks_built": 0, "included_txs": 0},
            "relay": {"blocks_built": 6, "included_txs": 2},
        })

    def test_partial_wrapper_bytes_are_measured_as_wire_bytes(self):
        sim = relay_sim(capacity=100)
        proof = proof_at(sim, 0)
        sim.nodes[1].finish_receive(proof, 0)
        sim._tick(1, 1)
        sim.run(until=10)
        row = sim.metrics()["roles"]["relay"]
        self.assertEqual(row["wrapper_bytes_sent_per_node_second"], 100000)
        self.assertEqual(row["all_bytes_sent_per_node_second"], 100000)

    def test_metrics_are_detached_and_do_not_schedule_or_warm_coverage(self):
        sim = relay_sim()
        sim.schedule_birth(0, KIND_PQ, node_id=1)
        sim.run(until=10)
        state = (sim.engine.events_processed, len(sim.engine.calendar),
                 sim.arena.coverage_cache_bytes(), sim.costs._rng.getstate())
        first = sim.metrics()
        self.assertEqual(state,
                         (sim.engine.events_processed, len(sim.engine.calendar),
                          sim.arena.coverage_cache_bytes(), sim.costs._rng.getstate()))
        first["roles"]["relay"]["nodes"] = 999
        first["relay_node_ids"].append(999)
        second = sim.metrics()
        self.assertEqual(second["roles"]["relay"]["nodes"], 1)
        self.assertEqual(second["relay_node_ids"], [1])

    def test_r25_fold_width_p95_over_distinct_completed_root_widths(self):
        sim = NetworkSim(Overlay.from_adjacency([[]], base_latency=0),
                         block_budget=0, costs=CostTable(prove_recursive=1))
        root_widths = [2] * 17 + [3, 5, 9]
        for tick, width in enumerate(root_widths, 1):
            for _ in range(width):
                sim._birth(KIND_PQ, 0)
            sim._tick(0, tick)
            sim.run(until=float(tick))
            sim._build_block(tick)
        widths = sim.metrics()["fold_width"]
        self.assertEqual(widths["roots_completed"], 20)
        self.assertEqual(widths["mean"], 2.55)
        # Nearest rank ceil(0.95 * 20) is the nineteenth sample: five.
        # The mean, minimum and maximum all differ from that percentile.
        self.assertEqual(widths["p95"], 5)
        self.assertEqual(widths["children_mean"], 0)
        self.assertEqual(widths["leaves_mean"], 2.55)

    def test_fold_width_counts_completed_root_inputs_not_rewraps(self):
        for plan, expected_width in (("wide", 4), ("binary", 2)):
            with self.subTest(plan=plan):
                sim = relay_sim(fold_plan=plan)
                child = proof_at(sim, 0, 2)
                for _ in range(3):
                    sim._birth(KIND_PQ, 0)
                sim._tick(0, 1)
                sim.run(until=1)
                widths = sim.metrics()["fold_width"]
                self.assertEqual(widths["roots_completed"], 1)
                self.assertEqual(widths["mean"], expected_width)
                self.assertEqual(widths["p95"], expected_width)
                self.assertEqual(widths["children_mean"] + widths["leaves_mean"],
                                 expected_width)
                if plan == "wide":
                    self.assertEqual(widths["children_mean"], 1)
                    self.assertEqual(widths["leaves_mean"], 3)
                sim._tick(0, 2)
                self.assertEqual(sim.metrics()["fold_width"], widths)


if __name__ == "__main__":
    unittest.main()
