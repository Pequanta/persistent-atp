from __future__ import annotations

from typing import Any

from neo4j import Driver

from .constants import GRAPH_LABELS

# Relationship types accepted by the generic add_relation() linker.
# Keeping an explicit allowlist means relationship type is never interpolated
# from untrusted input.
REL_WHITELIST = {
    # search DAG
    "SUPERSEDES", "ALTERNATIVE_TO", "GENERALIZES", "REFORMULATES", "FORMALIZES",
    "CONTRADICTS", "STRENGTHENS_ROUTE", "LEAVES_OPEN", "REDUCES_TARGET",
    "EXPOSES_BARRIER", "BYPASSES",
    # justification DAG
    "SUPPORTED_BY", "PROVED_BY", "CONTRADICTED_BY", "VERIFIED_BY",
    "VERIFIED_BY_EXPERIMENT", "INVALIDATES",
    # state -> claim reference (used for taint reopening)
    "USES_CLAIM",
    # speculative layer
    "SUGGESTS", "EXPECTS", "SOURCE_CONCEPT", "RELATED_TO", "FALSIFIED_BY",
    "ELABORATED_INTO",
}

# All node labels in the metagraph, from the shared vocabulary.
LABELS = tuple(sorted(GRAPH_LABELS))


def ensure_constraints(driver: Driver) -> None:
    """Create composite (proof_id, id) UNIQUE constraints and status indexes."""
    with driver.session() as s:
        for label in LABELS:
            s.run(
                f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE (n.proof_id, n.id) IS UNIQUE"
            )
        for label, prop in (("State", "status"), ("Move", "status"), ("Claim", "status")):
            s.run(
                f"CREATE INDEX {label.lower()}_{prop} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.proof_id, n.{prop})"
            )
