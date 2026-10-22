// A minimal vitest config for the task's oracle: Nest's own logger
// tests, which are mocha tests, run under vitest's mocha-style globals
// with TypeScript decorators enabled, without the repository's gulp and
// mocha toolchain.
export default {
  esbuild: { tsconfigRaw: { compilerOptions: { experimentalDecorators: true, emitDecoratorMetadata: false } } },
  test: {
    globals: true,
    include: ['packages/common/test/services/logger.service.spec.ts'],
    setupFiles: ['reflect-metadata'],
  },
}
