// The Worker's entry point. The runtime takes only handlers from this file, so the routes live in
// relay.ts (test/relay.test.ts checks that this file exports nothing else).

import { handle, type Env } from './relay';

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handle(request, env, { fetch: (input, init) => fetch(input, init), now: () => new Date() });
  },
} satisfies ExportedHandler<Env>;
