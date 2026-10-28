# ADR 0002 — Plane as the machine-readable backlog

Status: Accepted
Date: 2026-07-27

## Context

The loop tracked work as `- [ ]` checkboxes in `GOAL.md`. That is opaque to a
human away from the terminal. The user runs a local **Plane** instance
(`plane.fritz.box`) and wants to review the bot's work from holiday, and to model
a real PO ↔ Dev flow. Verified: `X-API-Key` auth works, the bot has its own Plane
identity ("Claude Code"). The public v1 API is workspace-slug-scoped
(`/api/v1/workspaces/{slug}/projects/…`); there is no bare "list my workspaces".

## Decision

Move the backlog into Plane. Roles map to the PO ↔ Dev flow:

- **Planner (PO)** writes work items into the mapped Plane projects and encodes an
  execution order.
- **Executor (Dev)** pulls the next available work item, does it, moves it to Done.

**Project mapping (chosen: explicit table in `CHARTER.md`).** Each Plane project
id maps to exactly one repo the Executor may touch for that project's items. The
bot never infers the repo from names.

**Common project.** A non-repo project holding cross-service **epics**. It is a
planning/tracking layer, NOT an execution source — see the invariant below.

## Invariant — one work item, one repo

Every *executable* work item belongs to exactly one repo (via its project's
mapping). This keeps git workflow and blast-radius reasoning sane. Cross-service
epics live in the Common project and are decomposed by the Planner into
repo-scoped child items in the mapped projects; the Executor only ever pulls
executable, repo-scoped items. (How epics decompose and how "next available"
is chosen: later ADRs.)

## Consequences

- New config: `PLANE_BASE_URL`, workspace slug, and the API key (as a secret, not
  in `.hustle/config` plaintext — storage TBD).
- `GOAL.md` checkboxes are no longer the Executor's input. What (if anything)
  `GOAL.md` still holds under this model is open (tracked separately).
- The wrapper's "plan empty?" dispatch check (ADR-pending) becomes "are there
  available work items in Plane?" — a Plane query, not a grep.
- HTTPS is not available on the instance; traffic is plaintext HTTP on the LAN.
  Key rotation advised since it transited the chat.
