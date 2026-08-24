"""Phase 0 D1-D3: the N105-N109 legacy progression, imported as-is.

D1 ports the five-claim research progression from the earlier OmegaClaw-Math
package unchanged (journal-shaped proposal dicts, legacy identifiers). D2
replays it through the real commit gate + journal and asserts all five
original dynamics still trigger. D3 pins the *absence* of formal-layer
objects: no alignments or declarations may appear yet (Section 14.5).
"""

import json
import unittest
from pathlib import Path

from commit_gate.apply import apply_ops
from commit_gate.gate import CommitGate
from commit_gate.proposal import Proposal
from commit_gate.state import MemoryView
from commit_gate.store import JournalStore

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "n105_n109"
PROGRESSION = FIXTURES / "progression.json"

PROOF_ID = "nseries"


def load_progression() -> dict:
    return json.loads(PROGRESSION.read_text(encoding="utf-8"))


class TestD1ImportAsIs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_progression()

    def test_five_legacy_claims_present_under_their_original_names(self):
        ids = {
            op["id"]
            for proposal in self.fixture["proposals"]
            for op in proposal["ops"]
            if op["op"] == "upsert_node" and op["label"] == "Claim"
        }
        self.assertEqual(
            ids,
            {f"{PROOF_ID}/N{n}" for n in range(105, 110)},
        )

    def test_import_is_data_only(self):
        """The fixture is journal-shaped proposals, not code."""
        for proposal in self.fixture["proposals"]:
            with self.subTest(actor=proposal["actor"]):
                rebuilt = Proposal.from_dict(
                    {**proposal, "proof_id": PROOF_ID}
                )
                self.assertTrue(rebuilt.ops)


class TestD2FiveOriginalDynamics(unittest.TestCase):
    """Replay the whole progression and check each dynamic is observable."""

    def setUp(self):
        self.fixture = load_progression()
        self.view = MemoryView()
        self.store = JournalStore()
        self.gate = CommitGate(self.view, self.store)

        # The two status-changing proposals carry the importer's lease.
        self.token = self.store.acquire_lease(PROOF_ID, "lease-import")

        self.revisions = []
        for index, raw in enumerate(self.fixture["proposals"]):
            proposal = Proposal.from_dict({**raw, "proof_id": PROOF_ID, "base_revision": index})
            result = self.gate.commit(proposal)
            self.assertTrue(result.accepted, (index, result.rejections))
            apply_ops(self.view, proposal.ops)
            self.revisions.append(result.revision)

    def test_every_proposal_committed_and_chain_verifies(self):
        self.assertEqual(self.store.verify_chain(PROOF_ID), len(self.revisions))
        self.assertEqual(self.revisions, list(range(1, len(self.revisions) + 1)))

    def test_dynamic_1_conceptual_compression(self):
        """N106 compresses N105+N107 into one generalized claim."""
        compressed = {
            e.dst_id for e in self.view.edges_from(f"{PROOF_ID}/N106", "COMPRESSES")
        }
        self.assertEqual(compressed, {f"{PROOF_ID}/N105", f"{PROOF_ID}/N107"})
        self.assertEqual(
            self.view.node(f"{PROOF_ID}/N106").fields["status"], "provisional"
        )

    def test_dynamic_2_route_level_bypass(self):
        """A route exists that skips the N107 lemma the old route needed."""
        bypass_targets = {
            e.dst_id
            for e in self.view.edges_from(f"{PROOF_ID}/R-bypass", "BYPASSES")
        }
        old_targets = {
            e.dst_id
            for e in self.view.edges_from(f"{PROOF_ID}/R-old", "ROUTES_TO")
        }
        self.assertEqual(bypass_targets, old_targets)
        self.assertIn(f"{PROOF_ID}/R-bypass", dict(self.view.nodes).keys())

    def test_dynamic_3_obstruction_inversion(self):
        """The obstruction blocking R-old became research input via RESOLVES."""
        obstruction = f"{PROOF_ID}/OBS-1"
        self.assertEqual(
            self.view.node(obstruction).fields["kind"], "search-policy"
        )
        blocked = [
            e.src_id for e in self.view.edges_to(obstruction, "BLOCKED")
        ]
        self.assertEqual(blocked, [f"{PROOF_ID}/R-old"])

        resolvers = [e.src_id for e in self.view.edges_to(obstruction, "RESOLVES")]
        self.assertEqual(resolvers, [f"{PROOF_ID}/N108"])
        # ...and inverting it promoted the resolving claim.
        self.assertEqual(
            self.view.node(f"{PROOF_ID}/N108").fields["status"], "provisional"
        )

    def test_dynamic_4_random_deterministic_hybridization(self):
        """N109 carries both a sampling attempt and an exact symbolic one."""
        attempts = [
            node
            for node in self.view.nodes.values()
            if node.label == "Attempt"
        ]
        evidence = {
            node.fields["evidence"]: node.fields["worker_class"] for node in attempts
        }
        self.assertEqual(
            evidence,
            {"random-sampling": "experiment", "exact-symbolic": "hyperon"},
        )
        for node in attempts:
            with self.subTest(attempt=node.node_id):
                targets = [
                    e.dst_id
                    for e in self.view.edges_from(node.node_id, "TARGETS")
                ]
                self.assertEqual(targets, [f"{PROOF_ID}/N109"])

    def test_dynamic_5_residual_open_target(self):
        """N109 ends the progression exactly as open as it began."""
        self.assertEqual(
            self.view.node(f"{PROOF_ID}/N109").fields["status"], "conjectural"
        )


class TestD3NoFormalLayerYet(unittest.TestCase):
    def test_no_alignment_or_declaration_objects_anywhere_in_the_fixture(self):
        fixture = load_progression()
        forbidden_labels = {
            "Alignment",
            "FormalDeclaration",
            "FormalState",
            "FormalRun",
            "Certificate",
            "LeanReplay",
            "Environment",
        }
        labels = {
            op.get("label")
            for proposal in fixture["proposals"]
            for op in proposal["ops"]
            if op["op"] == "upsert_node"
        }
        self.assertEqual(labels & forbidden_labels, set())

    def test_phase4_todos_are_tracked_without_creating_links(self):
        fixture = load_progression()
        tracked = fixture["phase4_todo_claims_needing_formal_declarations"]
        self.assertEqual(
            sorted(tracked),
            sorted([f"{PROOF_ID}/N105", f"{PROOF_ID}/N106", f"{PROOF_ID}/N108"]),
        )
        todo_file = FIXTURES / "PENDING_FORMAL_LINKS.md"
        text = todo_file.read_text(encoding="utf-8")
        for claim in tracked:
            with self.subTest(claim=claim):
                self.assertIn(claim, text)


if __name__ == "__main__":
    unittest.main()
