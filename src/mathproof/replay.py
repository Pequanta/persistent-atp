"""Independent Lean replay of candidate certificates (6.11).

``lean_replay_fn`` builds the production :data:`mathproof.formal_atp.ReplayFn`:
it re-runs a certificate's recorded tactic script against its goal in a fresh
Pantograph session and reports what the kernel says, never what the producer
claimed. The verdict carries exactly the fields the commit gate journals on a
LeanReplay node -- ``status``, ``sorry_detected``, ``rejection_reason`` -- so
the replayer worker can upsert it verbatim.

A certificate that arrives without its recorded script (``goal_text`` plus
``tactics``) cannot be checked; it is rejected as
``certificate-payload-unavailable`` rather than trusted. Retrieving the script
by ``artifact_hash`` from the artifact store is the replayer worker's job.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Mapping, Protocol

__all__ = [
    "SORRY_PATTERN",
    "ReplayDriver",
    "scan_for_sorry",
    "lean_replay_fn",
    "pantograph_driver_factory",
]

SORRY_PATTERN = re.compile(r"\b(sorry|admit)\b")
"""Any tactic text containing these terminates replay as unsound."""

REJECTION_ENVIRONMENT_DRIFT = "environment-drift"
REJECTION_NO_PAYLOAD = "certificate-payload-unavailable"
REJECTION_SORRY = "sorry-detected-in-script"
REJECTION_GOALS_REMAINING = "goals-remaining"


class ReplayDriver(Protocol):
    """The Pantograph subset replay needs.

    `start` opens a proof of ``goal_text``; `run` applies one tactic string
    and returns the next state; `goals_remaining` reports how many goals the
    state still carries. Zero after the final tactic means verified.
    """

    def start(self, goal_text: str) -> Any: ...

    def run(self, state: Any, tactic_text: str) -> Any: ...

    def goals_remaining(self, state: Any) -> int: ...


def scan_for_sorry(text: str | None) -> bool:
    return bool(text and SORRY_PATTERN.search(text))


def _verdict(
    status: str,
    environment_hash: str,
    sorry_detected: bool,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "environment_hash": environment_hash,
        "sorry_detected": sorry_detected,
    }
    if rejection_reason is not None:
        payload["rejection_reason"] = rejection_reason
    return payload


def _tactic_text(step: Mapping[str, Any]) -> str:
    parts = [str(step.get("label", ""))]
    args = step.get("arguments") or step.get("args") or []
    parts.extend(str(a) for a in args)
    return " ".join(p for p in parts if p)


def lean_replay_fn(driver_factory: Callable[[], ReplayDriver]) -> Any:
    """Build a ReplayFn that checks the script instead of trusting it.

    Order of refusal, cheapest first: environment drift, missing payload,
    sorry in the script (no Lean session is even started), then kernel
    outcomes. Every failure mode names itself in ``rejection_reason``.
    """

    def replay(certificate: Mapping[str, Any], environment_hash: str) -> dict[str, Any]:
        cert_env = certificate.get("environment_hash")
        if cert_env is not None and cert_env != environment_hash:
            return _verdict(
                "rejected",
                environment_hash,
                False,
                f"{REJECTION_ENVIRONMENT_DRIFT}: produced under {cert_env!r}",
            )

        goal_text = certificate.get("goal_text")
        steps = certificate.get("tactics")
        if not goal_text or steps is None:
            return _verdict("rejected", environment_hash, False, REJECTION_NO_PAYLOAD)

        scripts = [_tactic_text(step) for step in steps]
        if any(scan_for_sorry(script) for script in scripts):
            return _verdict("rejected", environment_hash, True, REJECTION_SORRY)

        try:
            driver = driver_factory()
            state = driver.start(str(goal_text))
            for index, script in enumerate(scripts):
                state = driver.run(state, script)
            remaining = driver.goals_remaining(state)
        except Exception as exc:
            return _verdict(
                "rejected",
                environment_hash,
                False,
                f"replay-crash:{type(exc).__name__}",
            )

        if remaining != 0:
            return _verdict(
                "rejected", environment_hash, False, REJECTION_GOALS_REMAINING
            )
        return _verdict("verified", environment_hash, False)

    return replay


def pantograph_driver_factory(**server_kwargs: Any) -> Callable[[], ReplayDriver]:
    """A fresh Pantograph server per replay; ``maths_ai``/torch stay lazy."""

    def factory() -> ReplayDriver:
        from pantograph.server import Server

        server = asyncio.run(Server.create(**server_kwargs))
        return _PantographDriver(server)

    return factory


class _PantographDriver:
    """Adapt Pantograph's async Server to the sync three-call protocol."""

    def __init__(self, server: Any):
        self._server = server

    def start(self, goal_text: str) -> Any:
        return asyncio.run(self._server.goal_start_async(goal_text))

    def run(self, state: Any, tactic_text: str) -> Any:
        return asyncio.run(self._server.goal_tactic_async(state, tactic_text))

    def goals_remaining(self, state: Any) -> int:
        return len(getattr(state, "goals", ()) or ())
