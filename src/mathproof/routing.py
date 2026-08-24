"""Adaptive routing triggers (Section 8.7 / Section 5 of the scheduler spec).

An ordered rule table evaluated over committed formal-run data after each
cycle -- never invented by the scheduler: failure-family signatures,
obstruction kinds and checkpoint frontiers are read from what ATP already
committed. First match wins. The alignment-review trigger needs a fourth
worker class and stays deferred; re-ranking after a promotion is structural
(scoring reads committed state fresh every cycle, so there is no cache to
invalidate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from commit_gate.state import ReadView
from commit_gate.vocab import (
    ExecutorResult,
    FormalStateStatus,
    RunDisposition,
)

__all__ = ["RoutingDecision", "RoutingConfig", "evaluate_run"]


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    """Thresholds for the trigger table; configuration, not code."""

    healthy_frontier_shrink: int = 1
    failure_family_repeat: int = 2


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """One triggered adaptation, carried back to the coordinator."""

    run_id: str
    rule: str
    action: str  # hold-for-replay | continue-search | widen-premises | propose-bridge-lemma
    detail: str
    params: Mapping[str, Any] = field(default_factory=dict)


def evaluate_run(
    view: ReadView, run_id: str, config: RoutingConfig | None = None
) -> RoutingDecision | None:
    """Evaluate the trigger table for one formal run; None means no fire."""
    cfg = config or RoutingConfig()
    run = view.node(run_id)
    if run is None or run.label != "FormalRun":
        return None

    disposition = run.fields.get("status")

    # Guard first: a complete trace awaiting independent replay must not be
    # re-leased for further search, whatever else the table would say.
    if disposition == RunDisposition.PROVED_PENDING_REPLAY.value:
        return RoutingDecision(
            run_id=run_id,
            rule="complete-trace",
            action="hold-for-replay",
            detail="proved-pending-replay; independent replay owns this run now",
        )

    searching = disposition == RunDisposition.SEARCHING.value
    frontier_by_epoch = _checkpoint_frontiers(view, run_id)

    # 8.7 row 1: closing states at a healthy rate -> keep going.
    if searching and _closing_healthily(frontier_by_epoch, cfg.healthy_frontier_shrink):
        return RoutingDecision(
            run_id=run_id,
            rule="healthy-closing-rate",
            action="continue-search",
            detail=f"checkpoint frontier shrank {frontier_by_epoch}",
        )

    # Escalations fire for live AND terminated runs: a stagnated search is
    # precisely when research moves matter most.
    family = _repeated_failure_family(view, run_id, cfg.failure_family_repeat)
    if family is not None:
        return RoutingDecision(
            run_id=run_id,
            rule="repeated-failure-family",
            action="widen-premises",
            detail=f"failure family {family!r} unchanged across checkpoints",
            params={"widen_retrieval": True, "failure_family": family},
        )

    convergence = _converged_obstruction(view, run_id)
    if convergence is not None:
        state_id, kind = convergence
        return RoutingDecision(
            run_id=run_id,
            rule="obstruction-convergence",
            action="propose-bridge-lemma",
            detail=f"{kind} obstruction raised twice at {state_id}",
            params={"at_state": state_id, "obstruction_kind": kind},
        )

    return None


# -- condition probes -----------------------------------------------------------


def _checkpoint_frontiers(view: ReadView, run_id: str) -> list[int]:
    """Open-state count per checkpoint, in epoch order."""
    entries: list[tuple[int, int]] = []
    for edge in view.edges_from(run_id, "HAS_CHECKPOINT"):
        checkpoint = view.node(edge.dst_id)
        if checkpoint is None:
            continue
        epoch = checkpoint.fields.get("epoch_ms", 0)
        open_states = 0
        for frontier_edge in view.edges_from(checkpoint.node_id, "CHECKPOINT_FRONTIER"):
            state = view.node(frontier_edge.dst_id)
            if state is not None and state.fields.get("status") in (
                FormalStateStatus.OPEN.value,
                FormalStateStatus.EXPANDED.value,
            ):
                open_states += 1
        entries.append((epoch, open_states))
    return [count for _, count in sorted(entries)]


def _closing_healthily(frontier_sizes: list[int], shrink: int) -> bool:
    """True when the newest checkpoint's frontier shrank by at least `shrink`."""
    if len(frontier_sizes) < 2:
        return False
    return frontier_sizes[-1] <= frontier_sizes[0] - shrink


def _repeated_failure_family(
    view: ReadView, run_id: str, repeat: int
) -> str | None:
    """A dead-edge failure family seen at least `repeat` times under this run."""
    families: dict[str, int] = {}

    # Tactic applications hang off states; reach them via the run's root and
    # checkpoint-frontier links.
    state_ids: set[str] = set()
    state_ids.update(e.dst_id for e in view.edges_from(run_id, "HAS_ROOT"))
    for checkpoint_edge in view.edges_from(run_id, "HAS_CHECKPOINT"):
        for frontier_edge in view.edges_from(
            checkpoint_edge.dst_id, "CHECKPOINT_FRONTIER"
        ):
            state_ids.add(frontier_edge.dst_id)

    visited_tactics = set()
    for state_id in state_ids:
        for tactic_edge in view.edges_from(state_id, "HAS_TACTIC"):
            tactic = view.node(tactic_edge.dst_id)
            if (
                tactic is None
                or tactic.node_id in visited_tactics
                or tactic.fields.get("executor_result")
                != ExecutorResult.LEAN_REJECTED.value
            ):
                continue
            visited_tactics.add(tactic.node_id)
            annotations = tactic.fields.get("annotations") or {}
            family = annotations.get("failure_family")
            if family:
                families[family] = families.get(family, 0) + 1
    winner = max(families.items(), key=lambda item: item[1], default=None)
    if winner and winner[1] >= repeat:
        return winner[0]
    return None


def _converged_obstruction(
    view: ReadView, run_id: str
) -> tuple[str, str] | None:
    """The same obstruction kind raised at one state more than once."""
    seen: dict[tuple[str, str], int] = {}
    for edge in view.edges_from(run_id, "RAISED_OBSTRUCTION"):
        obstruction = view.node(edge.dst_id)
        if obstruction is None:
            continue
        kind = obstruction.fields.get("kind")
        for at_state in view.edges_from(obstruction.node_id, "AT_STATE"):
            key = (at_state.dst_id, kind)
            seen[key] = seen.get(key, 0) + 1
    for (state_id, kind), count in sorted(seen.items()):
        if count >= 2:
            return state_id, kind
    return None
