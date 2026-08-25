# Pending formal links — Phase 4 planning (D3)

Per Section 14.5 this fixture is imported **without** formal subprojects or
claim-to-Lean alignment records. Nothing here creates a `FormalDeclaration`
or an `Alignment`; this file is the tracked TODO for when that work starts
(Phase 4).

## Claims that will eventually need a `formal-declaration` link

| Legacy id | Why |
|-----------|-----|
| `nseries/N105` | Root target of the progression; first candidate for formalization. |
| `nseries/N106` | The conceptual compression; formalizing it may subsume N105+N107 obligations. |
| `nseries/N108` | Counterexample-guided strengthening; needs an exact statement before any ATP run. |

## Claims deliberately left unlinked

- `nseries/N107` — superseded *for routing purposes* by R-bypass; revisit if
  the bypass route fails formally.
- `nseries/N109` — still conjectural with only heuristic/sampling evidence;
  speculative outputs stay separate from verified dependencies (Section 12).

When Phase 4 lands: allocate `fd-*` ids from `mathproof.ids.IdAllocator`,
create `Alignment` records through the commit gate, and delete each row above
as it is linked. Do **not** backfill alignments silently — the import event
must remain auditable in the journal.
