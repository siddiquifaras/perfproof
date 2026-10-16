// Runs a no-op interceptor through InterceptorsConsumer.intercept
// 100,000 times, the per-request chain the change rewrites (it wraps
// each step in AsyncResource.bind before the change). Copied into
// packages/core/test/ and run under the repository's own vitest config.
import { lastValueFrom } from 'rxjs';
import { describe, expect, it } from 'vitest';
import { InterceptorsConsumer } from '../interceptors/interceptors-consumer.js';

describe('eval workload', () => {
  it('runs one no-op interceptor 100,000 times', async () => {
    const consumer = new InterceptorsConsumer();
    const interceptor = {
      intercept: (_ctx: unknown, next: { handle: () => unknown }) =>
        next.handle(),
    };
    class Controller {}
    const instance = new Controller();
    let total = 0;
    for (let i = 0; i < 100000; i++) {
      const result = await consumer.intercept(
        [interceptor as never],
        [],
        instance as never,
        () => undefined,
        async () => 1,
      );
      total += (await lastValueFrom(result as never)) as number;
    }
    expect(total).toBe(100000);
  }, 600000);
});
