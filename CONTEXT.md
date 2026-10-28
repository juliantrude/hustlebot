# CONTEXT — hustlebot

Ubiquitous language for the hustlebot plugin (the cross-session, self-paced goal
loop). Terms here are canonical; use them in code, docs and skills.

## Glossary

- **Hustle loop** — the cross-session chain that runs one increment of work per
  headless run and re-arms itself, surviving session/week budget resets.
- **Charter** *(new)* — the stable, human-owned mandate that outlives individual
  plans. Lives in `CHARTER.md`. Holds the mission/theme, hard constraints
  (what may never be touched), the termination policy (deadline date or
  infinite), and the standard by which self-generated goals are judged. The
  bot may only invent goals that serve the charter. Produced by `/grill-goal`.
- **Plan** — the current, concrete, dependency-ordered list of increments the
  loop is executing. Lives in `GOAL.md` under `## Plan`. Now **bot-owned and
  regenerable**: derived from the Charter, not hand-written.
- **Increment** — the smallest shippable step; exactly one is done per run by
  `/advance-goal`. Checked off only when done AND verified.
- **Goal generation** *(new)* — the phase that produces a fresh Plan from the
  Charter. Runs whenever there is no active Plan: initially (no `GOAL.md` yet)
  and after a Plan is exhausted (instead of stopping the chain). Implemented as
  its own skill `/generate-plan` so it can run under a different model than the
  executor.
- **Planner** *(new)* — the model role that runs `/generate-plan` (a strong
  model, e.g. Opus). Reads the Charter + history, writes the next Plan.
- **Executor** *(new)* — the model role that runs `/advance-goal` (a fast/cheap
  model, e.g. Sonnet), doing one increment per run. Same engine in both modes.
- **Plan cycle** *(new)* — one full Charter→Plan→exhaustion span. Under the
  charter-bounded model the loop runs successive plan cycles until the
  termination policy fires.
- **Termination policy** *(new)* — what ends the whole chain: a **deadline**
  (calendar date/time) or **infinite** (only budget and the human stop it).
  Distinct from a Plan's Definition of Done, which only ends one plan cycle.
- **Definition of Done (DoD)** — checkable criteria that mark a *single Plan*
  finished. No longer the terminal state of the chain; it now triggers goal
  generation for the next cycle (unless the termination policy has fired).

- **Backlog** *(new)* — the machine-readable work queue. Now lives in **Plane**
  (`plane.fritz.box`), not in `GOAL.md` checkboxes. The Planner writes into it,
  the Executor pulls from it. Auth: `X-API-Key`, base `http://plane.fritz.box/api/v1`.
- **Work item** *(new)* — one Plane issue = one increment. The unit the Executor
  pulls, does, and moves to Done. Replaces a `GOAL.md` `- [ ]` line.
- **Project mapping** *(new)* — explicit table in `CHARTER.md`: each Plane project
  (by id) → the one repo the Executor may touch for its items. Deterministic;
  the bot never guesses which repo an item belongs to.
- **Common project** *(new)* — a Plane project that is NOT 1:1 with a repo. Holds
  cross-service **epics** (umbrella items spanning multiple repos). Not an
  execution source: the Executor never pulls from it directly (see ADR 0002).

## Notes

- The skills repo is itself hustled (it has its own `GOAL.md`); do not confuse
  that operational file with the design terms above.
