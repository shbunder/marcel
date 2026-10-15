import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-pool-workers';
import { defineConfig } from 'vitest/config';

// The tests run inside the Workers runtime (Miniflare) with a local D1; nothing reaches Cloudflare or Apple.
export default defineConfig({
  plugins: [
    cloudflareTest(async () => ({
      wrangler: { configPath: './wrangler.jsonc' },
      miniflare: {
        bindings: { TEST_MIGRATIONS: await readD1Migrations('./migrations') },
      },
    })),
  ],
  test: {
    include: ['test/**/*.test.ts'],
  },
});
