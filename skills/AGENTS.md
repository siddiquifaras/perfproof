# skills

The `/perfproof` skill and one playbook per target class (Python,
native, web services, LLM serving, GPU kernels and PyTorch, agentic
apps), installed into the user's agent. They are read by agents we
don't control, in repos we have never seen, and on repos their
developers know far better than we do.

## Rules

- Agent Skills format: `skills/<name>/SKILL.md`, frontmatter with only
  `name` and `description`. Other fields break some clients.
- Tool-neutral wording. Refer to `perfproof` CLI commands and their
  `--json` output (`docs/cli.md`), never to one client's built-in tools
  or slash commands.
- A playbook gives judgment, not enforcement. Never tell the agent to
  "make sure" of something the CLI already enforces; tell it what to
  look at and which cheap test to run. If a rule must hold, it belongs
  in the CLI and `docs/failure-modes.md`.
- The description says when to use the skill and when not to, in the
  first sentence. Clients match on it.
- Teach the method, not one codebase: how to pick a benchmark that
  reflects real use, which profiler answers which question, what usually
  causes the hotspot class, and when to stop.
- Keep `SKILL.md` short. Move depth into sibling files the skill links
  to, so clients load it only when needed.
- A skill is done when an agent with no other context has used it to
  get a correct verdict on one of the `examples/` programs. Record that
  run in the PR.
