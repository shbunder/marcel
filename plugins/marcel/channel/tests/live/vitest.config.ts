import { defineConfig } from 'vitest/config';

// The live channel test (S-05.2): run by hand on the NUC, never in the gate (`test/**` only).
export default defineConfig({
  test: {
    include: ['tests/live/**/*.live.test.ts'],
    testTimeout: 300_000,
    hookTimeout: 120_000,
    sequence: { concurrent: false },
  },
});
