"""The mathproof protocol layer.

Phase 0 modules that sit above the commit gate:

* :mod:`mathproof.ids` -- the Appendix A identifier namespace and allocator.
"""

from .ids import (
    DERIVED_ID_TYPES,
    ID_PREFIXES,
    LOCAL_ID_RE,
    IdAllocator,
    IdType,
    full_id,
    is_valid_local_id,
    local_id,
    parse_local_id,
)

__all__ = [
    "DERIVED_ID_TYPES",
    "ID_PREFIXES",
    "LOCAL_ID_RE",
    "IdAllocator",
    "IdType",
    "full_id",
    "is_valid_local_id",
    "local_id",
    "parse_local_id",
]
