/-
Statements the `bounded_mean` card is grounded on.

Two of them, deliberately in different states: one closed under the kernel
axioms, one still `sorry`. A card that grounds on both should say so.

Not typechecked in CI -- MAGNET reads `index.yaml` beside this file, which is
what a formalization repository exports. This is here so the index describes
something real.
-/
import Mathlib

namespace MagnetExample.BoundedMean

open Finset

/-- The mean of finitely many reals, each within `[lo, hi]`, lies in `[lo, hi]`. -/
theorem mean_mem_Icc {n : ℕ} (hn : 0 < n) (lo hi : ℝ) (xs : Fin n → ℝ)
    (hlo : ∀ i, lo ≤ xs i) (hhi : ∀ i, xs i ≤ hi) :
    lo ≤ (∑ i, xs i) / n ∧ (∑ i, xs i) / n ≤ hi := by
  have hn' : (0 : ℝ) < n := by exact_mod_cast hn
  constructor
  · rw [le_div_iff₀ hn']
    calc lo * n = ∑ _i : Fin n, lo := by simp [mul_comm]
    _ ≤ ∑ i, xs i := Finset.sum_le_sum fun i _ => hlo i
  · rw [div_le_iff₀ hn']
    calc ∑ i, xs i ≤ ∑ _i : Fin n, hi := Finset.sum_le_sum fun i _ => hhi i
    _ = hi * n := by simp [mul_comm]

/-- The sample mean of `n` i.i.d. draws bounded in `[lo, hi]` is within `ε` of
the population mean except with probability `2 * exp (-2 * n * ε ^ 2 / (hi - lo) ^ 2)`.

Hoeffding. Stated here, not yet proved. -/
theorem abs_sampleMean_sub_mean_le {n : ℕ} (hn : 0 < n) (lo hi ε : ℝ)
    (hrange : lo < hi) (hε : 0 < ε)
    (X : Fin n → Ω → ℝ) (hiid : IsIID X) (hbdd : ∀ i ω, X i ω ∈ Set.Icc lo hi) :
    ℙ {ω | |(∑ i, X i ω) / n - 𝔼[X 0]| ≥ ε}
      ≤ 2 * Real.exp (-2 * n * ε ^ 2 / (hi - lo) ^ 2) := by
  sorry

end MagnetExample.BoundedMean
