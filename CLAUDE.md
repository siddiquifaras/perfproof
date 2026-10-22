@AGENTS.md

## Claude Code

- Start a plan task with `/run-task <ID>` in its own worktree session.
  It walks the task from reading the spec to opening the PR.
- Before opening a PR, run the `reviewer` subagent on your diff. If the
  diff touches `src/perfproof/stats/` or `tests/calibration/`, or
  changes the decision rule, gates or metrics in `docs/method.md`, also
  run `stats-auditor` (wording and per-language notes don't need it).
  Fix every blocking finding or say why it is wrong. One review pass:
  real defects get fixed, nits go to the work log as follow-ups
  (Faras, 2026-10-23: no gold plating).
- `/verify` runs the full local check. Run it before you say anything
  works.
- When a step doesn't need input, keep going. Stop and ask only when you
  can't continue without a decision, before paid compute, or before
  anything destructive.
- The lead session in the main checkout merges reviewed PRs and pushes
  plan, docs and work-log updates to `main` without asking. A PR merges
  when the `reviewer` (and `stats-auditor` for statistics) passed on its
  head, the checks are green on that head, the head contains current
  `main` (`git merge-base --is-ancestor origin/main <head>`), and its
  Done-when is shown. When a green PR only needs main merged in and the
  merge touches none of its files, rerun just the PR's own tests, not
  the full checks; the full Mac and Linux checks then run once on
  `main` after each batch of merges (Faras, 2026-10-23). While GitHub Actions is manual-only (minutes nearly spent),
  "the checks" means `/verify` on this Mac plus its Linux Docker step,
  and CI runs once before release.
  Record each merge in `.claude/worklog.md`.
- Give subagents bounded units (one module, one command) and check the
  evidence they return before accepting it.
- A hook formats and lints every Python file you edit. Fix what it
  reports before moving on.
- End every task with four headings: Blocked on me, Changed, Evidence,
  Not verified.
