// A minimal vitest config for the task's oracle: hono's own context and
// app tests, without the repository's config, whose coverage and
// runtime projects need dependencies not installed here.
export default {
  test: {
    globals: true,
    include: ['src/context.test.ts', 'src/hono.test.ts'],
  },
}
