# relay — the Marcel push relay

The one service the owner runs for every Marcel install (ADR-013, `plan/05-install-anywhere.md` § 2): a Cloudflare
Worker that holds the app's APNs key and passes **sealed** notifications on to phones. API:
[`contracts/push-relay.yaml`](../contracts/push-relay.yaml).

- A phone registers once (`POST /v1/register`) and gets a `handle` and a `send_secret`, which it gives only to its own
  hub.
- The hub pushes with `POST /v1/push` and that secret as a bearer token. The relay checks it, counts the push and hands
  the sealed bytes, untouched, to APNs inside `{aps: {alert: {title: "Marcel", body: "You have news"},
  "mutable-content": 1}, sealed: …}`. The phone's Notification Service Extension opens them.
- `DELETE /v1/handles/{handle}` forgets a phone.

## What the relay sees

| It sees | It keeps | It never has |
|---|---|---|
| A random handle per phone (22 characters) | The handle | The notification text: it arrives sealed for the phone, and the relay has no key to open it |
| The phone's APNs token | The APNs token | The `send_secret` itself: only its SHA-256 is stored |
| The `sandbox` flag | The flag | Any request body in a log or in storage |
| How many pushes a phone got today | The count and the day (reset at midnight UTC) | Who the phone belongs to, or which hub sent the push |
| The sealed bytes, while passing them on | Nothing | — |

Cloudflare's request logs (Workers Logs) are **off** in `wrangler.jsonc`, so Cloudflare keeps no per-request record
for the relay. The relay prints one fixed line of its own, "APNs did not take a push.", with no request data; you see
it only while you run `npx wrangler tail`. `test/never-logs.test.ts` fails if a `console.log` or a console
line with anything but fixed words is added.

Limits: 500 pushes a day per handle (429 after that), 3 KB per request (413). The count moves in the same statement
that checks the secret, so two pushes at once never both take the last place. If the relay is down, pushes stop and
the app still works.

## The owner's one-time deploy

You need the Apple developer account (already needed for TestFlight) and a free Cloudflare account. Nothing here
runs on the NUC.

1. **Make the APNs key (Apple).** In developer.apple.com → Certificates, IDs & Profiles → Keys → **+**, tick **Apple
   Push Notifications service (APNs)**, and download `AuthKey_<KEYID>.p8`. Apple lets you download it **once**; keep
   it safe. Write down the **Key ID** (10 characters, in the file name) and your **Team ID** (top right of the
   developer site). One key works for both the sandbox and production.
2. **Install the tools.** From the repo:
   ```bash
   cd relay
   npm ci
   ```
3. **Log in to Cloudflare** (opens a browser): `npx wrangler login`
4. **Make the database:** `npx wrangler d1 create marcel-relay`. It prints a `database_id`; paste it over the zeros
   in `wrangler.jsonc` (`d1_databases[0].database_id`). The id is not a secret.
5. **Make its table:** `npx wrangler d1 migrations apply marcel-relay --remote`
6. **Set the four secrets.** Each command asks for the value, or reads it from a pipe. The first one asks to create
   the `marcel-relay` Worker, since it is not deployed yet: answer yes.
   ```bash
   npx wrangler secret put APNS_KEY < ~/Downloads/AuthKey_<KEYID>.p8   # the whole .p8 file
   npx wrangler secret put APNS_KEY_ID      # the 10-character Key ID
   npx wrangler secret put APNS_TEAM_ID     # your 10-character Team ID
   npx wrangler secret put APNS_TOPIC       # the app's bundle id, e.g. the one in ios/Config
   ```
   Then delete the `.p8` from Downloads, or move it somewhere safe and offline.
7. **Deploy:** `npx wrangler deploy`. On a new Cloudflare account it first asks you to pick a `workers.dev`
   subdomain. It prints the relay's address, e.g. `https://marcel-relay.<you>.workers.dev`.
8. **Put the address in the app build:** `MARCEL_RELAY_URL` in `ios/Config` (S-22.16), then build and upload to
   TestFlight as usual.
9. **Check it answers** (a made-up token; nothing is sent to Apple):
   ```bash
   curl -s -X POST https://marcel-relay.<you>.workers.dev/v1/register -H 'content-type: application/json' \
     -d '{"apns_token":"9f2c4e1a7b3d5f60819a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70","sandbox":true}'
   ```
   A `201` with a `handle` and a `send_secret` means it works. Forget that test handle again:
   `curl -s -X DELETE https://marcel-relay.<you>.workers.dev/v1/handles/<handle> -H 'authorization: Bearer <send_secret>'`
   (answers `204`).

**Later updates** (after a change to `relay/` lands on main): `cd relay && npm ci && npm run deploy`. It applies any
new migration, then deploys. A new APNs key: `npx wrangler secret put APNS_KEY` and `APNS_KEY_ID` again.

The free Workers plan allows 100,000 requests a day; one push is one request.

## Developing

```bash
npm ci
npm run check     # types (tsc --strict), then vitest inside the Workers runtime (Miniflare, local D1)
npm run dev       # wrangler dev on localhost; put fake APNS_* values in .dev.vars (ignored by git)
```

The tests use a fake APNs and a real P-256 key made per test; nothing reaches Apple or Cloudflare. `make relay` at the
root runs `npm ci` and the check, as CI does.

**Dependencies** (all dev-only, pinned exactly): `wrangler` (build, dev, deploy, D1), `@cloudflare/vitest-pool-workers`
(runs vitest inside the Workers runtime; it bundles Miniflare and the same `wrangler` version),
`@cloudflare/workers-types` (the runtime's types; `wrangler` asks for it as a peer), `vitest` and `typescript`. The
Worker itself has no runtime dependency: JWT signing and hashing use the runtime's Web Crypto. `.npmrc` sets
`legacy-peer-deps`, because vitest 4's optional browser add-ons trip npm 10.9's peer resolver ("reading edgesOut").
`npm audit` reports 5 high findings, all inside the local test tooling (Miniflare's `undici` and `sharp`); none is in
the deployed Worker. They go when `@cloudflare/vitest-pool-workers` moves past 0.22.0.

## Layout

- `src/index.ts`: the Worker's entry point (the runtime takes only handlers from it).
- `src/relay.ts`: the three routes. `src/validate.ts`: the request bodies, exactly as the contract says.
- `src/store.ts`: the one D1 table (`migrations/0001_handles.sql`) and the atomic daily count.
- `src/apns.ts`: the ES256 provider token (kept 50 minutes) and the APNs request.
