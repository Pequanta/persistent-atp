"""The read contract the gate needs against committed state.

Four questions: what is this node, what is this edge, what leaves a node, what
enters it. A graph backend satisfies these four and the validators do not care
which backend it is. `MemoryView` is the in-process implementation used by
tests and by replay.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = ["NodeRecord", "EdgeRecord", "ReadView", "MemoryView"]


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """A committed node: its identity, its label, and its current fields."""

    node_id: str
    label: str
    fields: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    """A committed edge: its identity, its type, and its endpoints."""

    edge_id: str
    rel_type: str
    src_id: str
    dst_id: str
    fields: Mapping[str, Any]


@runtime_checkable
class ReadView(Protocol):
    """Read-only access to one proof's committed state."""

    def node(self, node_id: str) -> NodeRecord | None:
        """The node, or None if nothing is committed under that id."""
        ...

    def edge(self, edge_id: str) -> EdgeRecord | None:
        """The edge, or None if nothing is committed under that id."""
        ...

    def edges_from(self, node_id: str, rel_type: str) -> tuple[EdgeRecord, ...]:
        """Edges of `rel_type` leaving `node_id`."""
        ...

    def edges_to(self, node_id: str, rel_type: str) -> tuple[EdgeRecord, ...]:
        """Edges of `rel_type` entering `node_id`."""
        ...


@dataclass(slots=True)
class MemoryView:
    """A `ReadView` held in dictionaries.

    Mutators are for building fixtures and for replaying a journal in process;
    the gate itself only ever reads through the `ReadView` methods.
    """

    nodes: dict[str, NodeRecord] = field(default_factory=dict)
    edges: dict[str, EdgeRecord] = field(default_factory=dict)
    _out: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))
    _in: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_node(self, node_id: str, label: str, fields: Mapping[str, Any] | None = None) -> None:
        self.nodes[node_id] = NodeRecord(node_id, label, dict(fields or {}))

    def set_field(self, node_id: str, name: str, value: Any) -> None:
        current = self.nodes[node_id]
        merged = {**current.fields, name: value}
        self.nodes[node_id] = NodeRecord(node_id, current.label, merged)

    def add_edge(
        self,
        rel_type: str,
        src_id: str,
        dst_id: str,
        edge_id: str,
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        self.edges[edge_id] = EdgeRecord(edge_id, rel_type, src_id, dst_id, dict(fields or {}))
        self._out[(src_id, rel_type)].append(edge_id)
        self._in[(dst_id, rel_type)].append(edge_id)

    def remove_edge(self, edge_id: str) -> None:
        record = self.edges.pop(edge_id, None)
        if record is None:
            return
        self._out[(record.src_id, record.rel_type)].remove(edge_id)
        self._in[(record.dst_id, record.rel_type)].remove(edge_id)

    def node(self, node_id: str) -> NodeRecord | None:
        return self.nodes.get(node_id)

    def edge(self, edge_id: str) -> EdgeRecord | None:
        return self.edges.get(edge_id)

    def edges_from(self, node_id: str, rel_type: str) -> tuple[EdgeRecord, ...]:
        return tuple(self.edges[e] for e in self._out.get((node_id, rel_type), ()))

    def edges_to(self, node_id: str, rel_type: str) -> tuple[EdgeRecord, ...]:
        return tuple(self.edges[e] for e in self._in.get((node_id, rel_type), ()))
