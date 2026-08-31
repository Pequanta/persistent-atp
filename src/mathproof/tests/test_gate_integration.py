"""End-to-end: adapter-shaped results through the real gate and journal.

The soundness gates were specified against adapter payloads; these tests
pin the full path — MathsAIFormalATP output committed via
CommitGate.commit into the SQLite journal, projected back into a view by
replaying ops, then promoted (or refused) under the promotion rules.
"""

import unittest

from commit_gate import (
    AddEdge,
    CommitGate,
    JournalStore,
    MemoryView,
    Proposal,
    Reason,
    RemoveEdge,
    SetField,
    UpsertNode,
)
from commit_gate.apply import apply_ops

from mathproof.maths_ai_atp import MathsAIFormalATP
from mathproof.tests.test_maths_ai_adapter import (
    REQUEST,
    StubReasoner,
    dead_graph,
    pln_solved_graph,
)


class TestAdapterThroughTheGate(unittest.TestCase):
    def setUp(self):
        self.view = MemoryView()
        self.store = JournalStore()
        self.gate = CommitGate(self.view, self.store)
        self.revision = 0
        self.token = self.store.acquire_lease("p1", "lease-e2e")

    def proposal(self, actor, *ops):
        return Proposal(
            proof_id="p1",
            actor=actor,
            worker_class="coordinator",
            ops=tuple(ops),
            base_revision=self.revision,
            lease_id="lease-e2e",
            fencing_token=self.token,
        )

    def commit(self, proposal):
        result = self.gate.commit(proposal)
        if result.accepted:
            apply_ops(self.view, proposal.ops)
            self.revision = result.revision
        return result

    def states_from_result(self, result):
        return [
            UpsertNode(
                "FormalState",
                s["state_id"],
                {k: v for k, v in s.items() if k != "state_id"},
            )
            for s in result["states"]
        ]

    def test_stagnated_run_commits_with_dead_edge_diagnostics(self):
        atp = MathsAIFormalATP(reasoner=StubReasoner(dead_graph()))
        run = atp.formal_search_start(REQUEST)

        dead = run["tactic_edges"][0]
        self.assertEqual(dead["executor_result"], "lean-rejected")
        self.assertIn("diagnostic_artifact", dead)

        result = self.commit(self.proposal("atp-worker", *self.states_from_result(run)))
        self.assertTrue(result.accepted, [r.to_dict() for r in result.rejections])
        self.assertEqual(len(self.store.read_events("p1")), 1)
        self.assertEqual(self.store.read_rejections("p1"), [])

    def test_refused_proposal_journals_without_touching_the_chain(self):
        atp = MathsAIFormalATP(reasoner=StubReasoner(dead_graph()))
        run = atp.formal_search_start(REQUEST)
        self.commit(self.proposal("atp-worker", *self.states_from_result(run)))

        broken = self.proposal(
            "atp-worker",
            UpsertNode(
                "TacticApplication",
                "p1/ta-x",
                {"executor_result": "lean-accepted"},
            ),
        )
        result = self.commit(broken)

        self.assertFalse(result.accepted)
        trail = self.store.read_rejections("p1")
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["payload"]["actor"], "atp-worker")
        self.assertEqual(len(self.store.read_events("p1")), 1)

    def test_pln_only_search_downgrades_and_never_mints_a_certificate(self):
        atp = MathsAIFormalATP(reasoner=StubReasoner(pln_solved_graph()))
        run = atp.formal_search_start(REQUEST)

        self.assertEqual(run["disposition"], "budget-exhausted")
        self.assertNotIn("certificate", run)
        self.assertIn("p1/fs-1", run["checkpoint"]["frontier_state_ids"])

        root = next(s for s in run["states"] if s["state_id"] == "p1/fs-1")
        self.assertEqual(root["status"], "open")


class TestPromotionFlowOverCommittedEvidence(unittest.TestCase):
    """The claim-promotion rules against evidence that lands event by event."""

    def setUp(self):
        self.view = MemoryView()
        self.store = JournalStore()
        self.gate = CommitGate(self.view, self.store)
        self.revision = 0
        self.token = self.store.acquire_lease("p1", "lease-e2e")

    def proposal(self, actor, *ops):
        return Proposal(
            proof_id="p1",
            actor=actor,
            worker_class="coordinator",
            ops=tuple(ops),
            base_revision=self.revision,
            lease_id="lease-e2e",
            fencing_token=self.token,
        )

    def commit(self, proposal):
        result = self.gate.commit(proposal)
        if result.accepted:
            apply_ops(self.view, proposal.ops)
            self.revision = result.revision
        return result

    PROMOTE = SetField(
        "Claim", "p1/c", "status", "lean-verified", prior="formally-closed"
    )

    def test_full_promotion_lifecycle_over_independent_evidence(self):
        self.commit(
            self.proposal(
                "producer-alpha",
                UpsertNode("Claim", "p1/c", {"status": "formally-closed"}),
                UpsertNode("Certificate", "p1/cert", {"actor": "producer-alpha"}),
                AddEdge("PROVED_BY", "p1/c", "p1/cert", "p1/e0"),
            )
        )

        first = self.commit(self.proposal("coordinator-1", self.PROMOTE))
        got = {r.reason for r in first.rejections}
        self.assertFalse(first.accepted)
        self.assertIn(Reason.PROMOTION_WITHOUT_REPLAY, got)
        self.assertIn(Reason.PROMOTION_WITHOUT_ALIGNMENT, got)

        self.commit(
            self.proposal(
                "producer-alpha",
                UpsertNode(
                    "LeanReplay",
                    "p1/rp",
                    {
                        "actor": "producer-alpha",
                        "status": "verified",
                        "sorry_detected": False,
                    },
                    
                ),
                UpsertNode(
                    "Alignment",
                    "p1/al",
                    {"lifecycle": "reviewed", "verdict": "aligned"},
                    
                ),
                AddEdge("REPLAYED_BY", "p1/cert", "p1/rp", "p1/e1"),
                AddEdge("ALIGNS_CLAIM", "p1/al", "p1/c", "p1/e2"),
            )
        )
        second = self.commit(self.proposal("coordinator-1", self.PROMOTE))
        self.assertFalse(second.accepted)
        self.assertIn(Reason.SELF_CERTIFICATION, {r.reason for r in second.rejections})

        self.commit(
            self.proposal(
                "replayer-beta",
                UpsertNode(
                    "LeanReplay",
                    "p1/rp2",
                    {
                        "actor": "replayer-beta",
                        "status": "verified",
                        "sorry_detected": False,
                    },
                    
                ),
                AddEdge("REPLAYED_BY", "p1/cert", "p1/rp2", "p1/e3"),
            )
        )
        third = self.commit(self.proposal("coordinator-1", self.PROMOTE))
        self.assertFalse(third.accepted)
        self.assertEqual(
            {r.reason for r in third.rejections}, {Reason.SELF_CERTIFICATION}
        )

        self.commit(self.proposal("maintenance", RemoveEdge("REPLAYED_BY", "p1/e1")))
        final = self.commit(self.proposal("coordinator-1", self.PROMOTE))
        self.assertTrue(final.accepted, [r.to_dict() for r in final.rejections])
        self.assertEqual(self.store.verify_chain("p1"), final.revision)


if __name__ == "__main__":
    unittest.main()
