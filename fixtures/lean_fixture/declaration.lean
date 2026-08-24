import Mathlib.Data.Nat.Basic

/-- Phase 0 fixture (B1): the cheapest Mathlib-backed commutativity lemma.
    Stable across CI runs; complete proof term, no placeholders or extra
    axioms beyond Mathlib's defaults. -/
theorem add_comm_nat_trivial (a b : Nat) : a + b = b + a := Nat.add_comm a b
