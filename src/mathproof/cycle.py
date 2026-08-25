"""One scheduling cycle: lease -> dispatch -> propose -> commit.

`run_cycle` is the thin outer loop of the reference algorithm. It owns no
state of its own: leases come from the scheduler, results come from adapters
or the dispatcher seam, and every write goes through the commit gate as an
inert proposal. An empty frontier is not a failure -- it is the coordinator's
cue to audit or expand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from commit_gate.gate import CommitGate, CommitResult
from commit_gate.proposal import Proposal
from commit_gate.state import ReadView
from .context_compiler import compile_context, compile_formal_request
from .dispatch import Dispatcher, result_to_proposal
from .ids import IdType
from .routing import RoutingDecision, evaluate_run
from .scheduler import GlobalScheduler, Lease

__all__ = ["CycleDigest", "run_cycle", "category_of"]


@dataclass(frozen=True, slots=True)
class CycleDigest:
    """What one cycle did, for the outer coordinator to react to."""

    lease_issued: bool = False
    selected_move_id: str | None = None
    worker_class: str | None = None
    accepted: bool = False
    revision: int | None = None
    rejections: tuple = field(default=())
    audit_recommended: bool = False
    routing: RoutingDecision | None = None

    @classmethod
    def empty_frontier(cls) -> "CycleDigest":
        return cls(audit_recommended=True)


def category_of(move_id: str) -> str:
    """Which frontier category a selected move belongs to, by its ID type."""
    local = move_id.rsplit("/", 1)[-1]
    if local.startswith("rm-"):
        return "research-move"
    if local.startswith(("fd-", "fr-")):
        return "formal-run"
    if local.startswith("c-"):
        return "critic-task"
    raise ValueError(f"cannot infer a frontier category from {move_id!r}")


def run_cycle(
    proof_id: str,
    *,
    view: ReadView,
    gate: CommitGate,
    scheduler: GlobalScheduler,
    dispatcher: Dispatcher,
    adapters: Mapping[str, Any] | None = None,
    worker_class: str = "llm-research",
    ttl_seconds: float = 600.0,
    maintenance: Callable[[Any, CommitResult], None] | None = None,
    search_policy: str = "gnn-pln-best-first-v1",
) -> CycleDigest:
    """Lease the best move, run it under its worker class, commit the result."""
    lease = scheduler.lease_next(proof_id, worker_class, ttl_seconds=ttl_seconds)
    if lease is None:
        return CycleDigest.empty_frontier()

    result, proposal = _dispatch(
        lease,
        view=view,
        dispatcher=dispatcher,
        adapters=adapters or {},
        search_policy=search_policy,
    )
    commit = gate.commit(proposal)
    scheduler.update_statistics(commit, category_of(lease.selected_move_id))

    if commit.accepted:
        if maintenance is not None:
            maintenance(proposal, commit)
        if result.get("terminal", True):
            scheduler.release(proof_id, lease.lease_id)

    routing = _route_after_commit(
        result,
        view=view,
        gate=gate,
        store=scheduler._store,
        proof_id=proof_id,
        maintenance=maintenance,
    )

    return CycleDigest(
        lease_issued=True,
        selected_move_id=lease.selected_move_id,
        worker_class=lease.worker_class,
        accepted=commit.accepted,
        revision=commit.revision,
        rejections=commit.rejections,
        routing=routing,
    )


def _route_after_commit(
    result: Mapping[str, Any],
    *,
    view: ReadView,
    gate: CommitGate,
    store,
    proof_id: str,
    maintenance: Callable[[Any, CommitResult], None] | None = None,
) -> RoutingDecision | None:
    """Evaluate the adaptive routing trigger table over the run just committed.

    A bridge-lemma convergence fires a follow-up research-move proposal
    through the gate; every other decision rides the digest back to the
    coordinator to fold into the next cycle (e.g. widened retrieval).
    """
    run_id = result.get("run_id")
    if not run_id or view.node(run_id) is None:
        return None
    decision = evaluate_run(view, run_id)
    if decision is None or decision.action != "propose-bridge-lemma":
        return decision

    at_state = decision.params.get("at_state")
    if at_state is None or view.node(at_state) is None:
        return decision

    from commit_gate.ops import AddEdge, UpsertNode
    from .dispatch import _next_serial

    # An obstruction converges into a fresh research state whose first move
    # is the bridge-lemma request; PROPOSES hangs off ResearchState only.
    state_id = f"{proof_id}/rs-{_next_serial(view, 'rs')}"
    move_id = f"{proof_id}/rm-{_next_serial(view, 'rm')}"
    detail = (
        f"bridge lemma for {decision.params.get('obstruction_kind')} "
        f"converging at {at_state}"
    )
    ops = (
        UpsertNode(
            "ResearchState",
            state_id,
            {"status": "open", "origin_state": at_state},
        ),
        UpsertNode("ResearchMove", move_id, {"status": "queued", "detail": detail}),
        AddEdge("PROPOSES", state_id, move_id, f"{move_id}-proposed"),
    )
    proposal = Proposal(
        proof_id=proof_id,
        actor="scheduler-routing",
        worker_class="llm-research",
        ops=ops,
        base_revision=store.head(proof_id)[0],
    )
    follow_up = gate.commit(proposal)
    if follow_up.accepted and maintenance is not None:
        maintenance(proposal, follow_up)
    return decision


def _dispatch(
    lease: Lease,
    *,
    view: ReadView,
    dispatcher: Dispatcher,
    adapters: Mapping[str, Any],
    search_policy: str,
) -> tuple[Mapping[str, Any], Any]:
    """Run the worker and bind its output into a gate-ready Proposal."""
    if lease.worker_class == "formal-atp":
        adapter = adapters.get("formal-atp")
        if adapter is None:
            raise ValueError("no formal ATP adapter registered")
        resuming = lease.selected_move_id.rsplit("/", 1)[-1].startswith("fr-")
        request = compile_formal_request(
            lease,
            view,
            search_policy=search_policy,
            run_id=None
            if resuming
            else f"{lease.proof_id}/{IdType.FORMAL_RUN.value}-{_next_run(view)}",
        )
        result = (
            adapter.formal_search_resume(request["run_id"])
            if resuming
            else adapter.formal_search_start(request)
        )
        result["proof_id"] = lease.proof_id
    else:
        packet = compile_context(lease, view)
        result = dict(dispatcher.run(lease, packet))

    attempt_serial = _next_attempt(view)
    attempt_id = f"{lease.proof_id}/{IdType.ATTEMPT.value}-{attempt_serial}"
    proposal = result_to_proposal(result, lease, view, attempt_id=attempt_id)
    return result, proposal


def _next_attempt(view: ReadView) -> int:
    from .dispatch import _next_serial

    return _next_serial(view, "at")


def _next_run(view: ReadView) -> int:
    from .dispatch import _next_serial

    return _next_serial(view, "fr")
