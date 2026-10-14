@AGENTS.md

## Claude Code

- Start a plan task with `/run-task <ID>` in its own worktree session.
  It walks the task from reading the spec to opening the PR.
- Before opening a PR, run the `reviewer` subagent on your diff. If the
  diff touches `src/perfproof/stats/`, `tests/calibration/` or
  `docs/method.md`, also run `stats-auditor`. Fix every blocking
  finding or say why it is wrong.
- `/verify` runs the full local check. Run it before you say anything
  works.
- When a step doesn't need input, keep going. Stop and ask only when you
  can't continue without a decision, before paid compute, or before
  anything destructive.
- Give subagents bounded units (one adapter, one module) and check the
  evidence they return before accepting it.
- A hook formats and lints every Python file you edit. Fix what it
  reports before moving on.
- End every task with four headings: Blocked on me, Changed, Evidence,
  Not verified.
