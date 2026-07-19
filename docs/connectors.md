# Writing a connector

A **connector** is an MCP server plus the per-user authentication layer needed to
use it ([ADR-260718-231cad][adr]). It is the habitat kind you reach for when the
capability already exists as an MCP server — anywhere in the ecosystem — and what
Marcel needs to add is *whose account* the call goes out as.

Compared with a [toolkit](plugins.md), which is Python you write and Marcel runs
in-process, a connector is a server Marcel *talks to*. That difference is the
whole point: you get the MCP ecosystem, and each family member gets their own
credentials.

```
connectors/<name>/
  connector.yaml       # the manifest (schema below)
  SETUP.md             # optional — admin-facing setup notes
  server/              # optional — a bundled FastMCP server for inprocess/stdio
```

Connectors resolve through the same three-root, per-user scoping chain as
[skills](skills.md), most-specific winning on a name collision:

1. `<MARCEL_ZOO_DIR>/connectors/` — global
2. `<MARCEL_ZOO_DIR>/users/<slug>/connectors/` — git-managed, one user
3. `<MARCEL_DATA_DIR>/users/<slug>/connectors/` — runtime-installed, one user

A malformed connector is logged and skipped; it never breaks discovery of the
others, and never breaks the agent build for other family members.

## The manifest

```yaml
name: weather                      # must equal the directory name
description: Local forecasts and severe-weather alerts
server:
  transport: http                  # http | stdio | inprocess
  url: https://mcp.weather.example/mcp
auth:
  mode: api_key                    # oauth | api_key | none
  per_user: true
  credential_keys: [WEATHER_API_KEY]
tools: [forecast, alerts]          # optional allowlist; omit for all
scope: all                         # all | admin
discovery: deferred                # deferred (default) | eager
default_enabled: all               # all | admin | none
```

`name` must equal the directory name and match `^[a-z0-9][a-z0-9._-]*$` — it is
used as a filesystem path component for the user's token store, so the charset is
enforced at load time.

### Transports

| `transport` | Locator | Shape |
|---|---|---|
| `http` | `url:` (https, or `http://localhost` in dev) | One shared upstream; each request carries the calling user's credential |
| `stdio` | `command: [argv…]` | One subprocess **per (connector, user)**; the credential arrives in its environment at spawn |
| `inprocess` | `module:` (dotted path to a module exposing `mcp`) | A bundled FastMCP server running inside Marcel's own process |

`http` is the default choice, and the **only** one where the server never touches
the host. Prefer it for anything third-party — see [Trust model](#trust-model).

`inprocess` is constrained by the schema to `auth: none`. Python caches
modules, so the server object is a singleton shared by every user: it has no way
to know whose turn it is serving, and so cannot hold a per-user credential. It
is the right shape for a bundled, first-party, credential-free server — which
may be family-visible (`scope: all`); the trust decision is made at *install*
time, since the server runs in Marcel's process whoever triggers it — and the
wrong shape for anything carrying a credential.

### Auth modes

| `mode` | Where the credential comes from |
|---|---|
| `oauth` | OAuth 2.1 + PKCE; tokens stored encrypted per user under `users/<slug>/connectors/<name>/tokens.enc` |
| `api_key` | `per_user: true` → the user's own vault; `per_user: false` → a shared env var from system config |
| `none` | No credential (`per_user` must be false) |

`credential_keys` and `oauth.client_secret_key` name **environment variables**,
never values. They may not reference Marcel's own secrets — the prefixes
`MARCEL_`, `ANTHROPIC_`, `OPENAI_`, `TELEGRAM_` and `AWS_` are rejected at load
time, because a manifest that chooses both which secret to read and which host to
send it to would otherwise be an exfiltration primitive.

An OAuth connector needs a `client_id` (there is no dynamic client registration —
family members are not OAuth clients). A confidential client also names an env
var in `client_secret_key`; the value itself lives in system config, never in the
habitat and never in a user's vault.

### Discovery

`deferred` (the default) keeps the connector's tool schemas out of context until
the model reaches for them — via `ToolSearch`, or because a skill named the
connector in its `marcel-connectors` metadata. `eager` opts a hot-path connector
out. Context cost scales with use, not with install count.

## Linking an account

For `mode: oauth`, Marcel drives the standard dance:

1. Linking is initiated for a user; Marcel mints a PKCE verifier/challenge and an
   unguessable `state`, bound server-side to that user and connector.
2. The user opens the authorization URL and approves.
3. The provider redirects to `<MARCEL_PUBLIC_URL>/connectors/callback`, which
   exchanges the code and stores the tokens encrypted.

Set `MARCEL_PUBLIC_URL` to the address Marcel is reachable at from outside
(https in production; an `http://localhost` tunnel is accepted in development).
Register `<MARCEL_PUBLIC_URL>/connectors/callback` as the redirect URI with the
provider. If it is unset or non-https, linking degrades to a readable message
rather than failing obscurely.

Access tokens refresh transparently before expiry. **How** they refresh differs
by transport, and it matters:

- **`http`** re-resolves the credential on *every request*, so a refresh lands
  mid-conversation with no reconnect.
- **`stdio`/`inprocess`** receive their credential once, at spawn. They cannot
  pick up a refreshed token mid-life — that happens when the instance is recycled
  and respawns. Consequently a spawned connector whose token has expired reports
  "needs reconnection" rather than starting a server that would fail every call.

## What the user sees

A connector the user has not set up still appears in their catalog, but as a
tool-less entry marked "— needs setup" whose instructions explain what is
missing. Marcel can then tell them plainly instead of calling a tool that cannot
work. The same applies when a spawned connector fails to start: it degrades to a
readable message, never a stack trace.

`scope: admin` connectors are absent from a non-admin's catalog entirely — they
are filtered before capabilities are built, so the model cannot reason its way
into one.

## Pairing with a skill

A [skill](skills.md) names its connectors in frontmatter:

```yaml
---
name: forecast
description: Talk about the weather
metadata:
  marcel-connectors: weather
---
```

Loading that skill activates the named connectors in the same step, so the model
gets the guidance and the tools together rather than having to search for them.

## Lifecycle (stdio / inprocess)

Spawned connectors get one instance per `(connector, user)`, started lazily on
first use and reused across turns. Idle instances are stopped; a connector that
fails to start backs off with a doubling delay up to a ceiling, so a broken
habitat cannot spin the host, and one user's failure never blocks another's.

## Trust model

**Read this before installing a third-party server.**

The per-`(connector, user)` pairing exists to keep credential *attribution*
correct — so Bob's calls never go out carrying Alice's token. It is **not** an
isolation boundary. Marcel runs as a single container under one uid, so:

- a `stdio` subprocess runs as `marcel`, with the container filesystem in reach;
- an `inprocess` server shares Marcel's own memory, including the credential vault.

Connector code is therefore **trusted code**, on the same footing as in-process
toolkit habitats. Install a connector only if you would run it as yourself.

Practically:

- Prefer **`http`** for anything third-party. The server never touches the host.
- Treat **`stdio`** as a deliberate admin decision, not the path of least
  resistance — an `npx`-fetched server is arbitrary code with filesystem access.
- **`inprocess`** is credential-free by construction; use it for bundled
  first-party servers only. Who may *use* it is a `scope:` choice — the trust
  decision was already made when an admin installed it.

Marcel never forwards its own tokens upstream. A connector receives only the
credential resolved for it, from the user's vault or their token store — there is
no code path by which a Marcel-audience token or another user's credential could
reach an outbound request. That property is structural, not a check.

## Gateways

A gateway (Docker MCP Gateway, IBM ContextForge) is just an `http` URL in
`connector.yaml`. Nothing in the kernel knows about gateways, and none is
required.

[adr]: https://github.com/shbunder/marcel-admin
