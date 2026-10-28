# Ideas for the next version — multi-chain hustlebot

Status: Brainstorm, not decided. Input for a grilling session once the repo
restructure is done. Captured 2026-10-28.

Today one project has one chain, and each chain arms its own one-shot timer
(ADR 0003). These ideas turn hustlebot into a small operating system for
chains: many chains, one scheduler, one dashboard.

## 1. Parallel chains

Support several hustlebot chains (across projects, maybe several per project)
at the same time. Currently not foreseen anywhere: slug-per-project services,
one `.hustle/` per project, one monitor per project, each on its own port.

## 2. Dashboard becomes a web app

The per-project monitor grows into one central web app that shows all chains,
lets you see that they run side by side, and manage them (start, pause, stop,
reprioritise).

## 3. The web app as scheduler ("multitasking router")

Borrow the principles of an OS process scheduler. Chains are processes; the
shared resource is the subscription budget (session and week windows), not CPU
time. Chains take turns: the scheduler decides which chain gets the next run.
Natural time slice: one increment (one headless run).

## 4. Priorities

Priority levels 0, 1, 2, 3, ...

- **Prio 0** is exclusive: while any prio-0 chain is runnable, only prio-0
  chains run.
- **Prio n gets twice the share of prio n+1** (prio 1 : prio 2 : prio 3 =
  4 : 2 : 1). Every non-zero chain keeps making progress; nothing starves.

This is weighted fair-share scheduling (cf. stride / lottery scheduling).

## 5. True multitasking inside one chain

Opt-in per chain, aimed at finishing a project as fast as possible: break the
chain's work down (e.g. with Matt Pocock's "Wayfinder", to be checked), find
the tasks that can run in parallel, and hand those to subagents.

## Open questions for the grilling

- Who owns the timer? A central scheduler daemon replaces per-chain
  self-arming — a real change to ADR 0003.
- "Parallel" vs. "taking turns": do two chains ever run headless sessions at
  the same instant, or only interleave? Simultaneous runs burn the session
  window twice as fast.
- How do the budget guard and parking at limits work when several chains
  share one subscription?
- Where does the central app live: per machine, as its own service? What
  happens to the existing per-project monitors?
- Subagent parallelism: how to avoid git conflicts (worktrees per task?), and
  how does that fit ADR 0007 (direct to main)?
- Priority of plan-mode vs. charter-mode chains — same scheme?
