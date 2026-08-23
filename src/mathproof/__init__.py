"""The mathproof protocol layer.

Phase 0 modules that sit above the commit gate:

* :mod:`mathproof.ids` -- the Appendix A identifier namespace and allocator.
* :mod:`mathproof.schemas` -- JSON Schema loading for ``schemas/``.
* :mod:`mathproof.formal_atp` -- the FormalATPAdapter boundary + fake ATP.
* :mod:`mathproof.soundness` -- structural validation of search results.
"""

from .formal_atp import (
    EMITTABLE_DISPOSITIONS,
    FakeFormalATP,
    FormalATPAdapter,
    build_result,
    stub_replay,
)
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
from .soundness import (
    SoundnessReason,
    SoundnessViolation,
    validate_formal_search_result,
    violation_counts,
)

__all__ = [
    # ids
    "DERIVED_ID_TYPES",
    "ID_PREFIXES",
    "LOCAL_ID_RE",
    "IdAllocator",
    "IdType",
    "full_id",
    "is_valid_local_id",
    "local_id",
    "parse_local_id",
    # formal_atp
    "EMITTABLE_DISPOSITIONS",
    "FakeFormalATP",
    "FormalATPAdapter",
    "build_result",
    "stub_replay",
    # soundness
    "SoundnessReason",
    "SoundnessViolation",
    "validate_formal_search_result",
    "violation_counts",
]
