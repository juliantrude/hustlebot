# Charter mode — consolidated design

A second mode for the hustle loop: instead of executing a fixed human-authored
plan and stopping, the bot runs as a **product owner + developer pair** against a
**Plane** backlog, inventing and building work within a human-owned **Charter**,
until a feature-freeze date or a manual abort. The original plan mode is untouched.

See ADRs 0001–0007 for the rationale of each decision. `CONTEXT.md` has the
glossary.

## Roles (two models, two processes)

- **Planner (PO)** — strong model (e.g. Opus). Reads the Charter, invents/refines
  features, writes Plane work items into the mapped projects, assigns each a model
  label and an execution order, decomposes Common-project epics into repo-scoped
  child items. Runs `/generate-plan`.
- **Executor (Dev)** — fast model (e.g. Sonnet, or per-issue via label). Pulls one
  Todo item, does it, verifies, moves it to Done. Runs `/advance-goal` (shared
  engine with plan mode).

## End-to-end run flow (headless chain)

Each run, the wrapper (`hustle-session.sh`) reads `.hustle/next-phase` and picks
command + `--model`:

1. **bootstrap / queue empty → Planner (`/generate-plan`, `HUSTLE_PLAN_MODEL`)**
   - deadline check: `now > freeze date` → feature freeze (plan nothing new).
   - else invent/refine within Charter; `review` mode → new feature epics land in
     Common **Backlog** (`bot-proposed`) awaiting a human nudge; approved/human work
     → Todo items in mapped projects, ordered + model-labelled.
   - write `.hustle/next-phase = executor:<model of first item>`; nothing to plan →
     park (long re-check) or terminal.
2. **work available → Executor (`/advance-goal`, model from marker)**
   - **recover**: resume any In-Progress item assigned to the bot (else pull next).
   - **budget pre-check**: `session ≥ pause%` → claim nothing, arm at reset, exit.
   - **claim**: next Todo → In Progress + assign + comment (the lock).
   - **do** one increment; **verify**; on success commit (one per item, refs issue
     id) + push to `origin/main` → **Done**. Blocker → comment + back to Todo;
     hard block → label `blocked` + skip. Resume count ≥ 2 → label `too-big`, hand
     back to Planner.
   - peek Plane: more work → `next-phase = executor:<next model>`; empty →
     `next-phase = planner`; frozen + drained → write `STATUS: COMPLETE`.
3. **terminal** (freeze date passed AND backlog drained, or manual abort): Planner
   writes the **closing report** (Plane Page, fallback Common issue) from Done items
   + git log, then `STATUS: COMPLETE`; the wrapper stops the chain.

## State & config layout

- **`CHARTER.md`** (hustle project root, human-owned): mission, hard constraints,
  quality bar, Plane url + slug, project mapping (+ Common), termination
  (freeze date | infinite), feature approval (auto | review), model policy, sizing
  rule, git workflow (direct-main | branch-mr), budget thresholds. No secrets.
- **`.hustle/secrets`** (chmod 600, gitignored): `PLANE_API_KEY`, sourced into the
  run env; kept out of `.hustle/config` (which the monitor parses/shows).
- **`.hustle/config`**: adds `HUSTLE_MODE=charter`, `HUSTLE_PLAN_MODEL`.
- **`.hustle/next-phase`**: `planner` | `executor:<model>` handoff marker.
- **No `GOAL.md`** in charter mode — Plane holds plan/status/log; the marker holds
  next-action. Plan mode keeps `GOAL.md` unchanged.

## Backlog model (Plane)

- Workspace `final-lift`, one workspace / many projects. States: Backlog / Todo /
  In Progress / Done / Cancelled.
- Executor pulls only **Todo** in **mapped** projects; **Common** (BRAINSTORM) is
  epics/overview only, never an execution source.
- Per-issue model via label `model:opus` / `model:sonnet` (fallback `HUSTLE_MODEL`).
- One executable item ⇒ one repo (via mapping); cross-service epics decomposed by
  the Planner.

## Files to create / change (implementation plan)

New:
- `skills/grill-charter/SKILL.md` (+ `CHARTER.template.md`) — the charter interview.
- `skills/generate-plan/SKILL.md` — the Planner.

Changed:
- `skills/advance-goal/SKILL.md` — Executor gains the charter branch: Plane pull
  instead of checkbox, claim/verify/Done lifecycle, budget pre-check, marker write.
  Guarded by `HUSTLE_MODE`; plan mode behaviour unchanged.
- `scripts/hustle-session.sh` — marker-based dispatch (command + model), source
  `.hustle/secrets`, honour `STATUS: COMPLETE` terminal (already present).
- `skills/loop-setup/SKILL.md` + `scripts/hustle-setup.sh` — write the new config
  keys and `.hustle/secrets`.
- `skills/hustle/SKILL.md` — offer plan vs charter at setup; run the right grill.

## Status (2026-07-27)

Implemented and plumbing-verified: marker dispatch (all cases), secrets sourcing,
`.hustle/config` keys (`HUSTLE_MODE`, `HUSTLE_PLAN_MODEL`), `.hustle/secrets` (0600),
installer end-to-end, `plugin.json` → 0.4.0. Plane **read** endpoints spiked live
(states, `?state__group=`, labels, members, `users/me`, issue schema, pages =
`POST`-only). New skills written: `/grill-charter` (+ `CHARTER.template.md`),
`/generate-plan`; `/advance-goal` gained the charter branch; `/hustle`,
`/loop-setup`, installer made mode-aware.

Not yet exercised (creates real side effects — do on first real ignition): a live
Planner run creating Plane issues; a live Executor claim→commit→Done; the exact
**write** payloads (create/patch issue, comment, label) and the Page `description_html`
format — the issue fallback covers a page-format hiccup. `README.md` not yet updated.
```
