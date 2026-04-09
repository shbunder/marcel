---
name: settings
description: Manage Marcel's preferences — change the AI model per channel, list available models
---

You can change which AI model Marcel uses on any channel, or ask what models are available.

## Available commands

### settings.list_models

List all models available to Marcel.

```
integration(skill="settings.list_models")
```

Returns a list of model IDs and their display names.

### settings.get_model

Get the current model for a specific channel.

```
integration(skill="settings.get_model", params={"channel": "telegram"})
integration(skill="settings.get_model", params={"channel": "cli"})
```

| Param   | Type   | Required | Description                              |
|---------|--------|----------|------------------------------------------|
| channel | string | yes      | Channel name: telegram, cli, app, websocket |

### settings.set_model

Set the preferred model for a channel. The choice is saved and persists across sessions.

```
integration(skill="settings.set_model", params={"channel": "telegram", "model": "claude-opus-4-6"})
integration(skill="settings.set_model", params={"channel": "cli", "model": "gpt-4o"})
```

| Param   | Type   | Required | Description                                   |
|---------|--------|----------|-----------------------------------------------|
| channel | string | yes      | Channel name: telegram, cli, app, websocket   |
| model   | string | yes      | Model ID from the list_models output          |

## Usage patterns

- User asks "what models are available?" → call `settings.list_models`, present the list clearly
- User says "use opus" / "switch to opus" → clarify which channel if ambiguous, then call `settings.set_model`
- User says "what model are you using?" → call `settings.get_model` for the current channel
- When channel is clear from context (e.g., user is in Telegram), use that channel directly without asking

**Current channel** is available in your context as the originating channel for this conversation.
