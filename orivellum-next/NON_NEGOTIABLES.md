# NON-NEGOTIABLES

Twelve rules. Each has a test that fails when it is broken. If a milestone needs
one of these relaxed, the milestone is wrong.

---

**1. Three clarifying facets, maximum.**
A trained clarifier matched the best general models with **41% fewer questions**
(3.0 vs 5.1) by ranking on task relevance and answerability. The ceiling is the
feature. Rank by what would change the output and drop the rest.
→ `test_facet_ceiling_enforced`

**2. Every facet discloses its default and where the default comes from.**
No `default_value`, no `default_source` — the facet is refused at the insert path.
This is the whole reason the gate is trustworthy: skipping becomes an informed
choice instead of a silent assumption.
→ `test_default_disclosure_is_mandatory`, `test_default_source_is_mandatory`

**3. Every facet carries options AND accepts free text.**
A bare question demands expertise the person may not have yet. Options-only is a
cage. Both, always — `allow_freeform=False` is available but is the exception.
→ `test_options_are_mandatory`, `test_freeform_allowed_and_blockable`

**4. A gate must state its cost.**
Units or minutes, one of them minimum. A gate in front of cheap reversible work
is friction wearing a governance costume.
→ `test_cost_is_mandatory`, `test_should_gate_decision`

**5. Skipping applies the disclosed defaults and records that it happened.**
Never a silent assumption. The record carries the value, the source, and the risk.
→ `test_skip_applies_disclosed_defaults_and_records_them`

**6. Two to four next actions. Three is the target.**
Six or more rebuilds the decision paralysis the pattern exists to remove.
→ `test_set_size_floor_and_ceiling`

**7. Exactly one recommendation per set, or none with a stated reason.**
Two recommendations is no recommendation. When nothing earns it, the set says why
rather than promoting a weak option.
→ `test_exactly_one_recommendation`, `test_no_recommendation_requires_a_reason`

**8. A recommendation requires a rationale. Every action requires an anchor_ref.**
Evidence or it did not happen — carried straight over from the book work. A
suggestion with no evidence pointer is a guess dressed as a next step.
→ `test_recommendation_requires_rationale`, `test_anchor_ref_required`

**9. `auto_runnable` is computed, never asserted.**
`compute_auto_runnable()` reads reversibility, cost, budget, blockers, unresolved
clarification, and policy. A supplied value is ignored and recomputed.
→ `test_model_cannot_assert_it`

**10. Nothing irreversible auto-runs. Ever.**
No policy dial, no override, no force flag. Same class of rule as "the judge is
never the narrator."
→ `test_irreversible_never_runs`

**11. A new answer expires the previous set.**
A stale chip must not be tappable, and selecting an expired action raises.
→ `test_new_set_expires_the_old_one`, `test_expired_action_cannot_be_selected`

**12. An unvalidated anchor is discarded, not corrected.**
Deterministic code gathers the facts; the model phrases them. Any proposal whose
`anchor_ref` is not in the gathered fact set is dropped and counted — the discard
count is a quality signal about the phraser, so it is reported.
→ `test_unvalidated_anchor_is_discarded_not_corrected`, `test_broken_probe_never_guesses`

---

## Two more that are not tests but are still rules

**Chips never block the composer.** Ignoring every suggestion must cost nothing.
If they ever intercept typing, the pattern has become a menu.

**Measure the lift or delete the badge.** `nextaction.stats()` reports
`recommendation_lift` — the take rate of the recommended chip minus the take rate
of any chip. If that number is not positive after a few weeks of real use, the
recommender is decoration and should be turned off rather than tuned forever.
