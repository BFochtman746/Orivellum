---
name: Series continuity (trilogy-wide canon)
description: Invariants for series-scoped canon, overrides, supersede lifecycle, and membership mutations.
---

# Series continuity — durable invariants

- **One visibility rule, used everywhere.** A single shared SQL clause decides which canon facts a book sees (own / legacy global / its series / strictly earlier volumes, minus its own active overrides), and every consumer — continuity checking, drafting context, fact listings — must use it. **Why:** duplicating the direction logic per consumer is how backward leaks happen. **How to apply:** never write a bespoke fact-visibility WHERE clause; extend the shared one.
- **Forward-only, never backward.** Earlier volumes bind later ones; book 1 never sees book 2's canon. World-state replay folds prior volumes in volume order; precedence is fold order.
- **Supersede changes what a fact SAYS, never where it applies.** A revision inherits its predecessor's scope (book / series / global AND override target); any rescope attempt is refused — rescoping = retract, then establish anew. **Why:** a bare supersede once let a revision silently move series canon to global, or resurrect an overridden series fact.
- **Retract is the only way to remove a per-book departure** (it restores the series fact for that book) — this asymmetry with supersede is deliberate.
- **Membership mutations are continuity-guarded:** a member can't leave while its canon binds later volumes or it holds an active override into the series; a canonized book can only JOIN as the latest volume; reordering is refused once any member canon exists ("order is authority"); series deletion is refused until members are removed latest-volume-first. Guard both the store methods AND surface each refusal as a 4xx at the API — an uncaught store refusal becomes a 500.
- **Cross-book finding labels must be validated:** same series AND source volume strictly earlier. "Fact belongs to a different work" alone counts stale facts from removed members as false drift.
- Voice/persona inheritance: nearest earlier volume wins when the book has none; local approved always beats inherited; unapproved never inherits; resolution carries provenance so surfaces can say where a baseline came from.
