# Plugin API

Marcel is moving toward a clean kernel / userspace split. The kernel is [`marcel_core`](https://github.com/shbunder/marcel) — harness, runner, storage, agent loop, tool protocol. The userspace is **marcel-zoo**, a separate repository containing the modular components (habitats) a Marcel install actually runs: integrations, skills, channels, jobs, agents. Zoo habitats are installed under `<data_root>/` (default `~/.marcel/`) and discovered at startup.

`marcel_core.plugin` is the stable surface zoo habitats import from. Anything re-exported there is a stability promise; anything else in `marcel_core` is internal and may change between versions.

!!! note "Status"
    The plugin surface currently covers **integrations only**. Skill, channel, job, and agent surfaces are being added incrementally — see the open issues in `project/issues/open/` for the roadmap (ISSUE-6ad5c7, ISSUE-7d6b3f, ISSUE-a7d69a, ISSUE-e22176).

## Integration habitat

An external integration lives at `<data_root>/integrations/<name>/` and is discovered automatically on the next registry load. The directory is treated as a Python package — `__init__.py` is required and runs at discovery time to trigger `@register` decorators.

### Minimal example

`<data_root>/integrations/demo/__init__.py`:

```python
from marcel_core.plugin import get_logger, register

log = get_logger(__name__)


@register("demo.ping")
async def ping(params: dict, user_slug: str) -> str:
    log.info("demo.ping called for %s", user_slug)
    return "pong"
```

Calling `integration(id="demo.ping")` from the agent dispatches to the handler above. No changes to kernel code, no entry in `skills.json`, no restart beyond whatever the user's normal reload path is.

### Directory-name ↔ handler-namespace rule

The directory name must match the `family` segment of every handler name registered by the package:

| Integration dir | Allowed handler names | Rejected handler names |
|---|---|---|
| `demo/` | `demo.ping`, `demo.status` | `container.start`, `other.x` |
| `banking/` | `banking.balance`, `banking.transactions` | `money.total` |

If any handler registered by the package falls outside the namespace, **the entire integration is rolled back**: no partial state leaks into the registry. The failure is logged at ERROR level; discovery of sibling integrations continues normally.

This rule exists so the integration's dotted handler prefix is a stable reverse-lookup to its source directory — useful for skills that declare `depends_on:` (see [Skills](skills.md), ISSUE-6ad5c7).

### Error isolation

Errors in one external integration never abort discovery of its siblings:

- `__init__.py` raises at import time → logged, that integration is skipped, siblings load.
- Handler registered outside the directory's namespace → logged, that integration rolled back, siblings load.
- Directory without `__init__.py` → logged as a warning, treated as not a habitat.

The net effect is that a user's marcel-zoo checkout can have one broken habitat without taking the rest of the install down.

### What `marcel_core.plugin` exposes

```python
from marcel_core.plugin import register, IntegrationHandler, get_logger
```

| Symbol | Purpose |
|---|---|
| `register(skill_name)` | Decorator that registers an async handler. Validates the `family.action` naming convention. |
| `IntegrationHandler` | Type alias for the handler signature: `Callable[[dict, str], Awaitable[str]]`. |
| `get_logger(name)` | Returns a module logger. Prefer this over `logging.getLogger` directly so future plugin-specific filtering hooks can be added without rewriting habitats. |

Anything not listed above is internal — zoo code that imports it owns the breakage on any future Marcel upgrade.

### First-party vs. external integrations

Internally, Marcel still ships several first-party integrations inside `src/marcel_core/skills/integrations/` (banking, icloud, news, settings, docker). These continue to work unchanged during the zoo migration — they are discovered via the same `discover()` entry point alongside external habitats. They will move to the zoo over the following issues (ISSUE-6ad5c7 onwards).

## See also

- [Skills](skills.md) — integration handlers vs. skill docs (SKILL.md, SETUP.md, `depends_on:`).
- [Storage](storage.md) — where `<data_root>` resolves and how per-user data is organized.
- [Architecture](architecture.md) — kernel / userspace model and where plugins sit in the overall design.
