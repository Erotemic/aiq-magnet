/-
Statements the `held_out_model` card is grounded on.

The card's question is the BAA's: given an evaluation result, how far can a
prediction about non-evaluated questions be trusted? These are the two things
the answer rests on -- that an accuracy is an accuracy, and that a sample of
questions pins the pool it was drawn from to within a computable width.

As in `BoundedMean.lean`, one is closed under the kernel axioms and one is
still `sorry`, and the card reports both states rather than averaging over
them.

Not typechecked in CI -- MAGNET reads `index.yaml` beside this file, which is
what a formalization repository exports. This is here so the index describes
something real.
-/
import Mathlib

namespace MagnetExample.HeldOutModel

open Finset

/-- An accuracy is a mean of 0/1 scores, and so lies in `[0, 1]`.

The mundane half, and worth stating: every downstream bound is about a
quantity in the unit interval, and a scorer that emitted anything else would
put the estimator outside the range the concentration statement assumes. -/
theorem accuracy_mem_Icc {n : ℕ} (hn : 0 < n) (scores : Fin n → ℝ)
    (hscore : ∀ i, scores i = 0 ∨ scores i = 1) :
    (∑ i, scores i) / n ∈ Set.Icc (0 : ℝ) 1 := by
  have hn' : (0 : ℝ) < n := by exact_mod_cast hn
  have hlo : ∀ i, (0 : ℝ) ≤ scores i := by
    intro i; rcases hscore i with h | h <;> simp [h]
  have hhi : ∀ i, scores i ≤ 1 := by
    intro i; rcases hscore i with h | h <;> simp [h]
  constructor
  · exact div_nonneg (Finset.sum_nonneg fun i _ => hlo i) hn'.le
  · rw [div_le_one hn']
    calc ∑ i, scores i ≤ ∑ _i : Fin n, (1 : ℝ) := Finset.sum_le_sum fun i _ => hhi i
    _ = n := by simp

/-- The accuracy measured on a held-out half of `n` questions is within
`Real.sqrt (Real.log (2 / δ) / (2 * n))` of the pool accuracy, except with
probability `δ`.

Hoeffding at unit range, inverted to give the half-width the card reports as
its certified limit. Stated here, not yet proved.

`hexch` is the hypothesis the card cannot discharge: the prediction is carried
across from a *different* model's calibration half, which is only licensed if
the held-out model is exchangeable with the cohort it was predicted from. For
three simulated models over one shared difficulty function that holds by
construction; for real architectures it is the open question, and the card's
theory ledger records it as a gap rather than pretending otherwise. -/
theorem abs_heldOutError_le {n : ℕ} (δ : ℝ) (hn : 0 < n) (hδ : 0 < δ) (hδ1 : δ < 1)
    (S : Fin n → Ω → ℝ) (μ : ℝ)
    (hbdd : ∀ i ω, S i ω ∈ Set.Icc (0 : ℝ) 1)
    (hiid : IsIID S) (hsplit : 𝔼[S 0] = μ) (hexch : Exchangeable S) :
    ℙ {ω | |(∑ i, S i ω) / n - μ| ≥ Real.sqrt (Real.log (2 / δ) / (2 * n))} ≤ δ := by
  sorry

end MagnetExample.HeldOutModel
