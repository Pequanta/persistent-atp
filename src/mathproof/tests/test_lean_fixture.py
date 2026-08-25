"""Phase 0 Lean fixture (B1-B3).

B1 pins one trivial Mathlib-backed declaration plus a full environment record
(Section 13.2). B2 wires the fixture through the real commit gate along the
identity chain ``claim -> alignment -> formal-declaration -> environment``
using allocator-issued IDs. B3 hand-writes the gold formal-run trace that
Phase 1's real adapter must reproduce.
"""

import hashlib
import json
import unittest
from pathlib import Path

from commit_gate.apply import apply_ops
from commit_gate.canon import content_hash
from commit_gate.gate import CommitGate
from commit_gate.ops import AddEdge, SetField, UpsertNode
from commit_gate.proposal import Proposal
from commit_gate.reasons import Reason
from commit_gate.state import MemoryView
from commit_gate.store import JournalStore

from mathproof.ids import IdAllocator, IdType, parse_local_id
from mathproof.schemas import validate
from mathproof.soundness import validate_formal_search_result

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "lean_fixture"

MANIFEST_REQUIRED_KEYS = (
    "environment_id",
    "lean_version",
    "mathlib_revision",
    "pantograph_revision",
    "lake_manifest_digest",
    "imports",
    "source_tree_digest",
    "deny_sorry",
    "allowed_axioms",
    "platform",
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def environment_hash() -> str:
    """The environment identity: canonical content hash of the pinned record."""
    return content_hash(load("environment.json"))


class TestB1EnvironmentManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load("environment.json")
        cls.declaration = (FIXTURES / "declaration.lean").read_text(encoding="utf-8")

    def test_manifest_pins_every_section_13_2_field(self):
        missing = [k for k in MANIFEST_REQUIRED_KEYS if k not in self.manifest]
        self.assertEqual(missing, [])

    def test_production_policy_is_deny_sorry_with_no_axioms(self):
        self.assertIs(self.manifest["deny_sorry"], True)
        self.assertEqual(self.manifest["allowed_axioms"], [])

    def test_source_tree_digest_matches_the_shipped_declaration(self):
        expected = "sha256:" + hashlib.sha256(
            self.declaration.encode("utf-8")
        ).hexdigest()
        self.assertEqual(self.manifest["source_tree_digest"], expected)

    def test_lake_manifest_digest_matches_the_shipped_manifest_file(self):
        lake = (FIXTURES / "lake-manifest.json").read_bytes()
        expected = "sha256:" + hashlib.sha256(lake).hexdigest()
        self.assertEqual(self.manifest["lake_manifest_digest"], expected)

    def test_declaration_is_the_trivial_commutativity_lemma(self):
        self.assertIn("theorem add_comm_nat_trivial", self.declaration)
        self.assertNotIn("sorry", self.declaration)


class TestB2IdentityChainThroughTheGate(unittest.TestCase):
    """claim -> alignment -> formal-declaration -> environment, all committed."""

    def setUp(self):
        self.view = MemoryView()
        self.store = JournalStore()
        self.gate = CommitGate(self.view, self.store)
        self.alloc = IdAllocator("p1")
        self.manifest = load("environment.json")
        self.revision = 0
        self._edges = 0

    def _eid(self) -> str:
        self._edges += 1
        return f"p1/edge-{self._edges}"

    def commit(self, *ops) -> None:
        proposal = Proposal(
            proof_id="p1",
            actor="coordinator-alpha",
            worker_class="coordinator",
            ops=ops,
            base_revision=self.revision,
        )
        result = self.gate.commit(proposal)
        self.assertTrue(result.accepted, result.rejections)
        apply_ops(self.view, ops)
        self.revision += 1

    def build_chain(self):
        claim_id = self.alloc.next(IdType.CLAIM)
        env_id = self.alloc.next(IdType.ENVIRONMENT)
        fd_id = self.alloc.next(IdType.FORMAL_DECLARATION)
        al_id = self.alloc.next(IdType.ALIGNMENT)

        # 1. Target claim + pinned environment record. (The proof scope "p1"
        # itself is not a node: every node id must live *under* it.)
        self.commit(
            UpsertNode(
                "Claim",
                claim_id,
                {
                    "status": "conjectural",
                    "claim_text": "Addition on the naturals commutes.",
                },
            ),
            UpsertNode(
                "Environment",
                env_id,
                {
                    **self.manifest,
                    "environment_hash": environment_hash(),
                },
            ),
        )

        # 2. Formal declaration pinned to that environment.
        self.commit(
            UpsertNode(
                "FormalDeclaration",
                fd_id,
                {
                    "status": "draft",
                    "lean_name": "add_comm_nat_trivial",
                    "lean_type": "\u2200 (a b : Nat), a + b = b + a",
                    "module_path": "Fixtures.AddComm",
                },
            ),
            AddEdge("PINNED_ENVIRONMENT", fd_id, env_id, self._eid()),
        )

        # 3. Alignment binds the informal claim to the declaration.
        self.commit(
            UpsertNode(
                "Alignment",
                al_id,
                {"lifecycle": "draft"},
            ),
            AddEdge("ALIGNS_CLAIM", al_id, claim_id, self._eid()),
            AddEdge("ALIGNS_DECLARATION", al_id, fd_id, self._eid()),
        )
        return claim_id, env_id, fd_id, al_id

    def test_chain_commits_and_journals_end_to_end(self):
        claim_id, env_id, fd_id, al_id = self.build_chain()

        self.assertEqual([c for c, _, _ in self.store.read_chain("p1")], [1, 2, 3])
        self.assertEqual(self.view.node(claim_id).label, "Claim")
        self.assertEqual(self.view.node(env_id).label, "Environment")
        self.assertEqual(self.view.node(fd_id).label, "FormalDeclaration")
        self.assertEqual(self.view.node(al_id).label, "Alignment")

    def test_ids_are_allocator_issued_in_final_shape(self):
        claim_id, env_id, fd_id, al_id = self.build_chain()

        for node_id, id_type in [
            (claim_id, IdType.CLAIM),
            (env_id, IdType.ENVIRONMENT),
            (fd_id, IdType.FORMAL_DECLARATION),
            (al_id, IdType.ALIGNMENT),
        ]:
            with self.subTest(node_id=node_id):
                self.assertEqual(parse_local_id(node_id.rsplit("/", 1)[1])[0], id_type)
        self.assertEqual(self.alloc.used(IdType.CLAIM), 1)

    def test_environment_node_carries_the_derived_identity(self):
        _, env_id, fd_id, _ = self.build_chain()

        env = dict(self.view.node(env_id).fields)
        self.assertEqual(env["environment_hash"], environment_hash())
        self.assertEqual(env["deny_sorry"], True)
        self.assertTrue(
            any(
                e.dst_id == env_id
                for e in self.view.edges_from(fd_id, "PINNED_ENVIRONMENT")
            )
        )

    def test_status_changes_require_the_write_lease(self):
        """Advancing the claim's status is status-class: no lease, no commit.

        (The reviewer's `verdict`, by contrast, is immutable once the
        alignment record exists -- a second guard this flow must respect.)
        """
        claim_id, _, _, _ = self.build_chain()

        def promotion():
            return SetField(
                "Claim", claim_id, "status", "provisional", prior="conjectural"
            )

        refused = self.gate.commit(
            Proposal(
                proof_id="p1",
                actor="coordinator-alpha",
                worker_class="alignment-reviewer",
                ops=(promotion(),),
                base_revision=self.revision,
            )
        )
        self.assertFalse(refused.accepted)
        self.assertEqual(
            [r.reason for r in refused.rejections],
            [Reason.MISSING_CONCURRENCY_TOKEN],
        )

        token = self.store.acquire_lease("p1", "lease-coordinator")
        leased = Proposal(
            proof_id="p1",
            actor="coordinator-alpha",
            worker_class="alignment-reviewer",
            ops=(promotion(),),
            base_revision=self.revision,
            lease_id="lease-coordinator",
            fencing_token=token,
        )
        accepted = self.gate.commit(leased)
        self.assertTrue(accepted.accepted, accepted.rejections)
        apply_ops(self.view, leased.ops)
        self.assertEqual(self.view.node(claim_id).fields["status"], "provisional")

    def test_lifecycle_walks_the_review_flow_under_a_lease(self):
        _, _, _, al_id = self.build_chain()
        token = self.store.acquire_lease("p1", "lease-reviewer")

        steps = [("draft", "review-needed"), ("review-needed", "reviewed")]
        for step, (prior, new) in enumerate(steps):
            proposal = Proposal(
                proof_id="p1",
                actor="alignment-reviewer-1",
                worker_class="alignment-reviewer",
                ops=(SetField("Alignment", al_id, "lifecycle", new, prior=prior),),
                base_revision=self.revision + step,
                lease_id="lease-reviewer",
                fencing_token=token,
            )
            result = self.gate.commit(proposal)
            self.assertTrue(result.accepted, result.rejections)
            apply_ops(self.view, proposal.ops)

        self.assertEqual(self.view.node(al_id).fields["lifecycle"], "reviewed")


class TestB3GoldRunTrace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = load("gold_run.json")
        cls.manifest = load("environment.json")

    def test_validates_against_the_formal_search_result_schema(self):
        errors = validate("formal-search-result", self.gold)
        self.assertEqual(errors, [], errors)

    def test_passes_structural_soundness_validation(self):
        self.assertEqual(validate_formal_search_result(self.gold), ())

    def test_run_was_searched_under_the_pinned_environment(self):
        self.assertEqual(self.gold["environment_hash"], environment_hash())
        state = self.gold["states"][0]
        self.assertEqual(state["environment_hash"], environment_hash())

    def test_closure_is_a_zero_goal_lean_transition(self):
        root_id = self.gold["root_state_id"]
        closer = next(
            e
            for e in self.gold["tactic_edges"]
            if e["source_state_id"] == root_id
        )
        self.assertEqual(closer["executor_result"], "lean-accepted")
        self.assertEqual(closer["subgoal_count"], 0)
        self.assertEqual(closer["produced_goal_ids"], [])
        self.assertEqual(closer["tactic_label"], "exact")

    def test_certificate_is_a_candidate_pending_independent_replay(self):
        certificate = self.gold["certificate"]
        self.assertEqual(certificate["status"], "candidate")
        self.assertNotIn("replay_result", certificate)
        self.assertEqual(
            {a["role"] for a in self.gold["artifacts"]},
            {"certificate", "lean-source"},
        )

    def test_exact_hash_and_semantic_signature_stay_separate(self):
        state = self.gold["states"][0]
        self.assertIn("exact_state_hash", state)
        self.assertIn("semantic_signature", state)
        self.assertNotEqual(state["exact_state_hash"], state["semantic_signature"])


if __name__ == "__main__":
    unittest.main()
