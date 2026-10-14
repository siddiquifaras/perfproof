# skills

Playbooks that perfproof installs into the user's agent: one per target
(Python, native, PyTorch, CUDA, LLM serving). They are read by agents we
don't control, in repos we have never seen.

## Rules

- Agent Skills format: `skills/<name>/SKILL.md`, frontmatter with only
  `name` and `description`. Other fields break some clients.
- Tool-neutral wording. Refer to perfproof's MCP tools by name, never to
  one client's built-in tools or slash commands.
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
