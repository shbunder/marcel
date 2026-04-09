---
name: plex
description: Manage the Plex Media Server on the home NUC
requires:
  env:
    - DOCKER_HOST
---

Help the user with: $ARGUMENTS

You have access to the `integration` tool to manage Plex Media Server.

## Available commands

### plex.restart

Restart the Plex Media Server Docker container.

```
integration(skill="plex.restart")
```

Use this when the user reports Plex is unresponsive, buffering, or not showing new media. A restart typically resolves most transient issues.

## Notes

- Plex runs as a Docker container named `plex-server` on the home NUC.
- The restart command takes a few seconds. Plex will be briefly unavailable during the restart.
