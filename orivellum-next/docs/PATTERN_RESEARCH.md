# The evidence behind each rule

Every non-negotiable traces to something here. Kept in the package so a future
session can check whether a rule still holds rather than re-litigating it.

---

## Next-step suggestions

**Two to four, three is right.** The pattern libraries converge on 2–4
high-relevance options; six or more "recreates the decision paralysis" the chips
were meant to remove, and competes with the primary actions (copy, cite,
regenerate) already sitting under an answer.

**Anchor in what just happened.** The consistent guidance is to base each
suggestion on the system's last response or the user's prior action, and to make
the connection visible ("You could also ask…", "Related topics include…").
Perplexity's follow-ups reference specific facts from the answer; generic chips
train people to stop reading chips. Hence `anchor` (human-readable) and
`anchor_ref` (structured, validated) being mandatory.

**Mix depth and breadth.** One or two "zoom in" suggestions plus one "zoom out"
gives directional control without overwhelming — which is exactly the
narrow/widen/act triple in `pick_kinds()`.

**Editable before it sends.** Named as an anti-pattern: chips that submit
without allowing an edit force unwanted side quests. `select(edited_prompt=...)`
records `edited` distinctly from `selected`, so the telemetry shows how often the
phrasing was wrong.

**Regenerate, don't repeat.** Repeating the same chip after every answer with no
novelty is listed as an anti-pattern; a new reply should produce fresh
suggestions. Hence `expire_thread()` on every new set.

**Keep them out of the way.** Visually separate from the model's output so new
content is distinguishable from next-step prompts, and never blocking the
composer. Avoid entirely in dense professional tools where they fight primary
actions, when the answer already contains a checklist of next steps, and
**mid-flight during an agent run**, where they interrupt a plan the person is
watching.

---

## Clarifying questions

**The uncomfortable finding first.** *Knowing but Not Showing: LLMs Recognize
Ambiguity but Rarely Ask Clarifying Questions* (arXiv:2605.25284) — models
identify ambiguity when explicitly asked to judge it, yet in normal QA they
overwhelmingly default to answering anyway. And **retrieved context widens that
gap**: it improves answerability while making the model even *less* likely to
ask. Orivellum is retrieval-grounded everywhere, so the gate has to be enforced
outside the model, like every other invariant in the platform.

**Fewer, better questions.** *Asking What Matters: Reward-Driven Clarification*
(arXiv:2604.14624) — a trained clarification model reached the strongest general
model's performance with **41% fewer questions (3.0 vs 5.1)** by prioritising
task relevance and user answerability. That is the source of the three-facet
ceiling.

**Ask the question that splits the candidates.** *Active Task Disambiguation
with LLMs* (Kobalczyk, ICLR 2025 spotlight) — models that select questions by
explicitly reasoning over multiple **self-generated candidate solutions** handle
ambiguity far better than those relying on implicit reasoning. So the rule is
"ask what would change the output," not "ask about whatever looks vague."

**One question per facet, in parallel, with options and a progress indicator.**
*AmbigChat* (UIST '25) — earlier QA systems asked clarifying questions with no
suggested options, which requires domain knowledge the user may lack; later
systems supply options as UI chips. Generating one question per underspecified
**facet** minimises redundant disambiguation turns, and the gap they identify in
prior work is the absence of any **progress indicator** during clarification.
Hence named facets, mandatory options, and `"n of m answered"`.

**Specific beats generic.** Users prefer specific, contextual clarifying
questions; out-of-the-box LLMs frequently produce generic, shallow ones poorly
aligned with the actual trouble source. That is why `why` is a required field —
it must cite a real observation.

**Clarify before spending.** The purpose of a pre-generation gate is to avoid
wasted compute and effort on the wrong direction; for compute-heavy work the
clarification precedes the generation. Hence mandatory cost, and
`should_gate()` refusing to gate cheap reversible work.

---

## Autonomy

**Continuation is a harness property, not a model property.** Long-running agents
need durable state and execution controls *outside* the model: session logs,
checkpoints, traces, policy checks, cost limits. Needing to say "continue" is a
harness problem. The chips reduce thinking; the bridge plus the existing runner
is what removes the turn.

**Budget the chain, not just the run.** The runner already caps each run. The
`Chain` here caps the number of self-continued *steps*, because a loop that
qualifies each step individually can still walk a long way in the wrong
direction. The chain report leads with what it spent and what it did not reach —
the same shape as every other report in the platform.

---

## What is not from the literature

Three things in this package are inventions for Orivellum specifically, and
should be judged on their own merit rather than cited:

1. **Default disclosure on every facet** — showing the exact assumption that
   would apply if you skip. This is the abstain contract given a UI form.
2. **`recommendation_lift`** — the take rate of the recommended chip minus the
   take rate of any chip. Without it there is no way to tell whether the
   recommendation is judgement or decoration.
3. **`auto_runnable` as a computed permission** with an attached reason, so the
   queue can always tell you *why* something waited.
