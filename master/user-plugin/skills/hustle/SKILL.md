---
name: hustle
description: Plan a hustle (a long-running goal in this repository) as a plan of increments and submit it to the hustlemaster, which works on it in parallel headless sessions across usage limits. Use when the user says "/hustle", wants Claude to keep working on a goal across sessions, wants to convert a GOAL.md into a hustle, or asks about or wants to change a hustle's plan.
---

# hustle — plan a hustle and hand it to the hustlemaster

The **hustlemaster** is a background service, usually on another machine (the
hustle host). It keeps its own repository of every project, stores every
hustle's plan, starts one headless hustlebot per increment in its own git
worktree there, merges verified results into `main` of that repository, and
shares the subscription's session points between the hustles. Your job is the planning: understand the
goal, cut it into increments, and submit the plan. You never run increments
yourself.

You reach the hustlemaster only through its MCP tools: `list_hustles`,
`read_hustle`, `submit_hustle`, `change_plan`, `read_settings`. If they are
missing, the hustlemaster is not installed or not running: say so and stop.
Do not fall back to writing a `GOAL.md`.

Pick the entry that fits:

- **A new goal** → §1 to §5.
- **The project has a `GOAL.md` with a `## Plan` checklist** (the old
  one-increment-per-run loop), or the user asks to convert one → §6, then §4
  and §5.
- **A question about a hustle, or a change to one** → §7.

If the user's message already answers a question below (for example "no
pushing", "the check is `make test`", "submit without asking"), take the
answer and do not ask again.

## 1. Orient

- Call `read_settings`: `allow_fable` and `total_points`. Session points per
  running hustlebot: haiku 1, sonnet 2, opus 4, fable 8.
- Call `list_hustles`: what already runs in this project. A new hustle whose
  increments touch the same files as a running one will collide on merge; say
  so and plan around it.
- The project's **name** on the hustle host is the repository root's folder
  name (`basename "$(git rev-parse --show-toplevel)"`) unless the user names
  another; letters, digits, `.`, `_`, `-`. The hustlemaster branches off and
  merges into `main`; if the default branch has another name, tell the user
  before planning.
- The project's **remote** on the hustle host is optional and at most one
  (`git remote -v`; usually `origin`'s URL). With a remote, the hustlemaster
  fetches from it; without one, the user pushes their state straight into the
  repository on the hustle host. Ask which the user wants for this project.
- Find the **ignored files the project needs** to build and test: `.env`,
  local config, credentials files (`git status --ignored --short`, the
  README's setup). They never travel through git or MCP; they are `required`
  and the user copies them to the hustle host once.
- Read `CLAUDE.md` (and `AGENTS.md`, `CONTEXT.md`, the README), look at the
  structure, find the project's check command (tests, linters, build).
- Is the repository public? (`gh repo view --json visibility`, or the remote
  URL.) It matters for the push question in §4.

## 2. Grill the goal

Interview the user one question at a time, each with your recommended answer.
Answer from the code what the code can answer. Settle:

- **North star**: what exists when the hustle is done, in two or three sentences.
- **Definition of Done**: checkable items, each one verifiable on `main` by
  running or reading something. "Works well" is not an item; "`uv run pytest`
  passes and covers X" is.
- **Agreements** for this hustle only: what must not be touched, constraints,
  conventions that are not in `CLAUDE.md`. Project-wide rules belong in
  `CLAUDE.md`, not here.
- **The check command** the hustlemaster runs after each rebase onto `main`
  (e.g. `uv run pytest -q`). Empty if the project has none.

Stop when every Definition of Done item is checkable and you could write each
increment's prompt without guessing.

The **brief** is what every hustlebot reads before its increment. Write it in
Markdown: `## North star`, `## Definition of Done` (a list), `## Agreements`.

## 3. Cut the plan

An increment is one task for one headless session. The plan is a dependency
graph, not a list: increments without a dependency between them run at once.

- **One task per increment**, sized to fit a session with at most about
  **600k tokens** of context: a handful of files read closely, a few changed,
  one check. Bigger → split. Smaller pieces that need each other's files →
  merge them.
- **A self-contained prompt.** The hustlebot sees only the brief and this
  prompt, in a fresh worktree, with no memory of this conversation. Name the
  files, the approach, the decisions already made, what not to touch, and the
  exact command that verifies it. Never "as discussed" or "see above".
- **Dependencies only where needed**: one increment needs another's result, or
  both change the same files. The order you thought of them in is not a
  dependency. Increments that may run together must touch disjoint files.
- **Ids** are short and stable (letters, digits, `-`): dependencies, progress
  and worktrees hang on them. Titles are a few words.
- **Model per increment**: `haiku` for mechanical work with a clear pattern,
  `sonnet` as the default for implementation, `opus` for design, hard
  debugging and anything where a wrong decision is expensive, `fable` only if
  `allow_fable` is on and Opus would likely fail. In doubt, the cheaper model:
  an overseer upgrades after a failure. No increment may cost more points than
  `total_points`.
- **Milestone overseers** (optional): an increment in mode `overseer` after a
  foundation phase, to sharpen the rest of the plan once the base exists.
- **The acceptance increment** ends every plan: mode `overseer`, id `accept`,
  depending (directly or through others) on every other increment, on the
  strongest allowed model (`fable` if `allow_fable` is on, else `opus`). Its
  prompt: "Check the Definition of Done of the brief against `main`", with the
  items listed. It checks and plans the gaps; it does not fix them.
- Every other increment is mode `worker`.

## 4. Show it and settle pushing

Show the user the plan before submitting: a table of id, title, model, mode
and dependencies, which increments start at once, and the points the widest
moment needs. Take their changes.

Ask about **pushing**, only for a project with a remote: `off` (the default),
`branch` (after each merge the hustlemaster pushes its `main` to the hustle's
own branch `hustle/<id>` on the remote), or `main` (straight to the remote's
`main`). A hustlebot never pushes. A failed push never holds the hustle up; the
dashboard shows it and has a Push button. For a public repository ask
explicitly and recommend `off` or `branch`. Without a remote nothing is pushed;
the user fetches the results from the hustle host. Pushing, the remote, the
check command and the required files are settings of the project, shared by
all its hustles.

**Hand the state over** before submitting: the plan is made against the
current commit (`git rev-parse HEAD`), and it must be in `main` on the hustle
host. With a remote: the user pushes it there (`git push`). Without one: the
user pushes it straight to the hustle host —
`git push <host>:<repository> HEAD:main`, where the repository is what
`submit_hustle` answered for an earlier hustle of the project, or what the
error of a first try names. Uncommitted changes do not travel; say so if
`git status` shows any.

Then ask for the go to submit, unless the user already said to submit.

## 5. Submit

Call `submit_hustle` with `project` (the name), `commit` (the full hash of
`HEAD` the plan was made against), `title`, `brief`, `increments` (each with
`id`, `title`, `prompt`, `after`, `model`, `mode`), `remote` (the URL, or ""
for none), `push`, `verify` and `required` (the list of ignored files).

Always pass `remote`, `push`, `verify` and `required` for a project with no
hustle in `list_hustles` yet: without them a new project gets no check after
each rebase and the acceptance overseer is the only safety net. Leave them out
only when the project already has hustles and the user did not change them.

A first submit without a remote creates an empty repository on the hustle
host and fails because the commit is not there yet; its error names the
repository. Have the user push there, then submit again. The answer names the
`repository` and any `missing_files`: give the user the command to copy each,
e.g. `scp .env <host>:<repository>/.env`. The hustle cannot be activated
until they are there.

A rejected plan comes back with every problem listed (cycles, unknown
dependencies, missing acceptance increment, Fable not allowed, a model above
the total points). Fix them all and submit again.

Then tell the user the hustle's id and that it arrives **paused**: they
activate it in the hustlemaster's dashboard, where they can still edit prompts
and models first. To review results later: `git fetch` from the remote (the
hustle's branch, or `main`), or without a remote straight from the hustle host
(`git fetch <host>:<repository> main`). The first activation confirms the plan; later growth is
measured against it.

## 6. Convert a `GOAL.md`

The old loop worked a `GOAL.md` one increment per run, in order. Read it
whole, then build the brief and the plan from it:

- `## North star` and `## Definition of Done` → the brief's north star and
  Definition of Done.
- `## Working Agreements` → the brief's agreements, **without** what the
  hustlemaster now does itself: the order of work per run, branching,
  committing, pushing, merging, budget and scheduling rules, rules about
  `GOAL.md` and its log.
- Checked increments (`- [x]`) are done; their work is on `main`. They are
  not part of the plan. Read `## Log` for what they built, decisions and
  blockers, and put what a later increment needs to know into its prompt.
- Every unchecked increment (`- [ ]`) becomes an increment of the plan, one
  for one unless it is too big (§3). The `**Next action:**` line often
  details the first unchecked one; use that detail in its prompt.
- Find the real dependencies (§3). The checklist order is not one.
- Add the acceptance increment.
- Settle the check command (§2) and pushing (§4) as for a new goal: the
  `GOAL.md` names the check in its agreements or increments more often than
  not, and its "never push" rules answer the push question.

Then §4 and §5. Leave `GOAL.md` as it is. Tell the user to stop the old loop
before activating the hustle (`.hustle/bin/hustle-scheduler.sh disarm`),
or both will work on the same increments.

## 7. Existing hustles

- `read_hustle` with the hustle's id shows its brief, plan version and each
  increment's `state` (`open`, `running`, `done`, `blocked`), `attempts` and
  dependencies.
- Change the plan with `change_plan`: pass **every** increment, also the
  unchanged ones, keeping their ids; progress carries over by id. Leave
  `done` and `running` increments as they are. Keep the acceptance increment
  after everything. Say in `note` why.
- A change that upgrades a model beyond the hustle's share, or grows the plan
  by more than 25 %, waits for the user's approval in the dashboard; the
  answer names what waits. Tell the user; do not shrink the change to dodge
  the approval.
- Activation, weights, approvals, total points, thresholds, `allow_fable` and
  auto-approve are the dashboard's alone. Point the user there; you cannot
  change them.
