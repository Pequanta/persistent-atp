"""Adaptive routing: trigger fixtures fire the right action.

Each rule gets a fixture built from exactly what ATP commits -- checkpoints,
dead-edge failure families, typed obstructions -- because the scheduler reads
triggers, it never computes them itself.
"""

import unittest

from commit_gate.apply import apply_ops
from commit_gate.gate import CommitGate
from commit_gate.state import MemoryView
from commit_gate.store import JournalStore
from mathproof.cycle import run_cycle
from mathproof.dispatch import ScriptedDispatcher
from mathproof.routing import RoutingConfig, evaluate_run
from mathproof.scheduler import GlobalScheduler

FAMILY = "apply-failure-no-matching-conclusion"


def add_dead_tactic(view: MemoryView, serial: int, state_id: str, family: str) -> None:
    tactic_id = f"p1/ta-{serial}"
    view.add_node(
        tactic_id,
        "TacticApplication",
        {
            "tactic_label": f"tac{serial}",
            "subgoal_count": 0,
            "executor_result": "lean-rejected",
            "annotations": {"failure_family": family},
        },
    )
    view.add_edge("HAS_TACTIC", state_id, tactic_id, f"{state_id}-tac-{serial}")


def add_checkpoint(
    view: MemoryView, run_id: str, serial: int, epoch_ms: int, frontier: list[str]
) -> str:
    checkpoint_id = f"p1/fc-{serial}"
    view.add_node(checkpoint_id, "FormalCheckpoint", {"epoch_ms": epoch_ms})
    view.add_edge("HAS_CHECKPOINT", run_id, checkpoint_id, f"{run_id}-fc-{serial}")
    for position, state_id in enumerate(frontier):
        view.add_edge(
            "CHECKPOINT_FRONTIER",
            checkpoint_id,
            state_id,
            f"{checkpoint_id}-{position}",
        )
    return checkpoint_id


def add_obstruction(
    view: MemoryView, serial: int, run_id: str, kind: str, state_id: str
) -> None:
    obstruction_id = f"p1/obs-{serial}"
    view.add_node(obstruction_id, "Obstruction", {"kind": kind, "actor": "atp"})
    view.add_edge("RAISED_OBSTRUCTION", run_id, obstruction_id, f"{obstruction_id}-raised")
    view.add_edge("AT_STATE", obstruction_id, state_id, f"{obstruction_id}-at")


class RoutingCase(unittest.TestCase):
    def setUp(self):
        self.view = MemoryView()
        self.view.add_node("p1/fs-1", "FormalState", {"status": "expanded"})
        self.view.add_node("p1/fs-2", "FormalState", {"status": "open"})
        self.view.add_node("p1/fr-1", "FormalRun", {"status": "searching"})
        self.view.add_edge("HAS_ROOT", "p1/fr-1", "p1/fs-1", "p1/e-root")


class TestTriggerTable(RoutingCase):
    def test_a_shrinking_frontier_continues_the_search(self):
        add_checkpoint(self.view, "p1/fr-1", 1, epoch_ms=10, frontier=["p1/fs-1", "p1/fs-2"])
        add_checkpoint(self.view, "p1/fr-1", 2, epoch_ms=20, frontier=["p1/fs-2"])

        decision = evaluate_run(self.view, "p1/fr-1")

        self.assertEqual(decision.rule, "healthy-closing-rate")
        self.assertEqual(decision.action, "continue-search")

    def test_a_repeated_failure_family_widens_premises(self):
        add_dead_tactic(self.view, 1, "p1/fs-1", FAMILY)
        add_dead_tactic(self.view, 2, "p1/fs-1", FAMILY)

        decision = evaluate_run(
            self.view, "p1/fr-1", config=RoutingConfig(failure_family_repeat=2)
        )

        self.assertEqual(decision.rule, "repeated-failure-family")
        self.assertEqual(decision.action, "widen-premises")
        self.assertEqual(decision.params["failure_family"], FAMILY)
        self.assertTrue(decision.params["widen_retrieval"])

    def test_one_failure_is_not_yet_a_pattern(self):
        add_dead_tactic(self.view, 1, "p1/fs-1", FAMILY)
        self.assertIsNone(evaluate_run(self.view, "p1/fr-1"))

    def test_converging_obstructions_propose_a_bridge_lemma(self):
        add_obstruction(self.view, 1, "p1/fr-1", "missing-lemma", "p1/fs-2")
        add_obstruction(self.view, 2, "p1/fr-1", "missing-lemma", "p1/fs-2")

        decision = evaluate_run(self.view, "p1/fr-1")

        self.assertEqual(decision.rule, "obstruction-convergence")
        self.assertEqual(decision.action, "propose-bridge-lemma")
        self.assertEqual(decision.params["at_state"], "p1/fs-2")

    def test_proved_pending_replay_holds_the_run_before_anything_else(self):
        """A complete trace must never be re-leased, whatever else fires."""
        add_obstruction(self.view, 1, "p1/fr-1", "missing-lemma", "p1/fs-2")
        add_obstruction(self.view, 2, "p1/fr-1", "missing-lemma", "p1/fs-2")
        node = self.view.nodes["p1/fr-1"]
        self.view.set_field("p1/fr-1", "status", "proved-pending-replay")
        del node

        decision = evaluate_run(self.view, "p1/fr-1")

        self.assertEqual(decision.action, "hold-for-replay")

    def test_terminal_runs_without_triggers_fire_nothing(self):
        self.view.set_field("p1/fr-1", "status", "stagnated")
        self.assertIsNone(evaluate_run(self.view, "p1/fr-1"))

    def test_unknown_runs_fire_nothing(self):
        self.assertIsNone(evaluate_run(self.view, "p1/fr-99"))


class TestRoutingThroughCycle(unittest.TestCase):
    def test_a_formal_cycle_converging_on_an_obstruction_mints_a_bridge_move(self):
        view = MemoryView()
        view.add_node(
            "p1/fd-1",
            "FormalDeclaration",
            {
                "status": "aligned",
                "lean_name": "hard",
                "lean_type": "P -> Q",
                "lean_value": "hard : P -> Q := fun h => h",
            },
        )
        store = JournalStore()
        scheduler = GlobalScheduler(view, store)

        result = {
            "run_id": "p1/fr-1",
            "disposition": "budget-exhausted",
            "root_state_id": "p1/fs-1",
            "states": [
                {
                    "state_id": "p1/fs-1",
                    "kind": "or",
                    "goal_text": "P -> Q",
                    "exact_state_hash": "sha256:" + "aa" * 32,
                    "semantic_signature": "sha256:" + "bb" * 32,
                    "status": "open",
                }
            ],
            "tactic_edges": [],
            "checkpoint": {"epoch_ms": 1, "frontier_state_ids": ["p1/fs-1"]},
            "obstructions": [
                {
                    "obstruction_id": "p1/obs-1",
                    "kind": "missing-lemma",
                    "formal_run_id": "p1/fr-1",
                    "formal_state_ids": ["p1/fs-1"],
                },
                {
                    "obstruction_id": "p1/obs-2",
                    "kind": "missing-lemma",
                    "formal_run_id": "p1/fr-1",
                    "formal_state_ids": ["p1/fs-1"],
                },
            ],
            "artifacts": [],
        }
        adapter = type("AdapterStub", (), {})()
        adapter.formal_search_start = lambda request: dict(result)
        adapter.formal_search_resume = lambda run_id: dict(result)

        digest = run_cycle(
            "p1",
            view=view,
            gate=CommitGate(view, store),
            scheduler=scheduler,
            dispatcher=ScriptedDispatcher({}),
            adapters={"formal-atp": adapter},
            worker_class="formal-atp",
            maintenance=lambda proposal, commit: apply_ops(view, proposal.ops),
        )

        self.assertTrue(digest.accepted)
        self.assertIsNotNone(digest.routing)
        self.assertEqual(digest.routing.action, "propose-bridge-lemma")
        moves = [
            node_id
            for node_id in view.nodes
            if node_id.rsplit("/", 1)[-1].startswith("rm-")
        ]
        self.assertEqual(len(moves), 1)
        self.assertEqual(view.node(moves[0]).fields["status"], "queued")


if __name__ == "__main__":
    unittest.main()
