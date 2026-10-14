---
name: run-task
description: Carry one task from .claude/plan.md from spec to open PR in the current worktree. Use when the user says /run-task <ID> or asks you to pick up a plan task.
disable-model-invocation: true
argument-hint: "[task-id]"
---

Task: `$ARGUMENTS` (the task ID the user gave).

1. **Read the task** in `.claude/plan.md`: what it owns, what it needs,
   and its done-when. Read every file the "Where to read before editing"
   table in `AGENTS.md` lists for the paths it owns.
2. **Check it can start.** Every task listed under "Needs" must be
   merged into `main`. Check with `git log origin/main --oneline`. If one
   isn't, stop and say which.
3. **Branch.** `git switch -c task/<id>-<short-slug>` from an up-to-date
   `origin/main`.
4. **Build it test-first.** Write the tests that define done, watch them
   fail, then implement. Stay inside the owned paths. If the task can't
   be done without touching something else, stop and report what and
   why.
5. **Verify.** Run the `verify` skill, then the done-when command from
   the plan, and keep its output.
6. **Review.** Run the `reviewer` subagent on `git diff origin/main`. For
   changes under `src/perfproof/stats/`, `tests/calibration/` or
   `docs/method.md`, also run `stats-auditor`. In a client without
   subagents, apply `.claude/agents/<name>.md` to the diff yourself.
   Fix blocking findings, or answer each with evidence that it is
   wrong.
7. **Read the whole diff** yourself. Remove anything unrelated to the
   task.
8. **Commit, push, open a PR** titled `<ID>: <task title>`. Body:

   ```
   Task: <ID>
   ## Changed
   ## Evidence
   <each command run, and its result in numbers>
   ## Not verified
   ```

Do not edit `.claude/plan.md`. Do not merge.
