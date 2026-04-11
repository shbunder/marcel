# Artifacts

Artifacts are Marcel's mechanism for delivering rich content to users — calendars, charts, checklists, and images that don't render well as plain text messages. When a Telegram response contains rich content, it's stored as an artifact and a "View in app" button lets the user open it in the Mini App.

## How it works

```
Agent generates response with rich content (table, calendar, chart)
    ▼
Telegram webhook detects rich content (has_rich_content)
    ▼
Content stored as artifact (JSON file + optional binary)
    ▼
"View in app" button added to Telegram message
    ▼
User taps button → Mini App opens → fetches artifact via API
    ▼
Artifact rendered in viewer (markdown/HTML/image/chart)
```

## Content types

| Type | Description | Storage |
|------|-------------|---------|
| `markdown` | Tables, formatted text, checklists | Content stored as markdown string |
| `calendar` | Calendar/schedule views | Content stored as markdown string |
| `checklist` | Task lists with checkboxes | Content stored as markdown string |
| `html` | Pre-rendered HTML content | Content stored as HTML string |
| `image` | Photos and generated images | Filename stored; binary in `files/` dir |
| `chart_data` | Matplotlib-generated charts | Filename stored; PNG in `files/` dir |

## Storage layout

```
~/.marcel/artifacts/
  {id}.json              # artifact metadata + content
  files/
    {id}.png             # binary files (charts, images)
```

Each artifact JSON file contains:

```json
{
  "id": "a1b2c3d4...",
  "user_slug": "shaun",
  "conversation_id": "seg-0003",
  "content_type": "markdown",
  "content": "| Name | Score |\n|---|---|\n| Alice | 95 |",
  "title": "Score Table",
  "created_at": "2026-04-11T14:30:00+00:00"
}
```

## API endpoints

### `GET /api/artifact/{id}`

Fetch a single artifact by ID. Requires Telegram `initData` authentication.

**Response:**
```json
{
  "id": "a1b2c3d4...",
  "content_type": "markdown",
  "content": "...",
  "title": "Score Table",
  "created_at": "2026-04-11T14:30:00+00:00"
}
```

### `GET /api/artifacts`

List artifact summaries for the authenticated user.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `conversation` | string | — | Filter by conversation ID |
| `limit` | int | 20 | Max artifacts to return (1-100) |

**Response:**
```json
{
  "artifacts": [
    {"id": "...", "title": "...", "content_type": "markdown", "created_at": "..."}
  ]
}
```

### `GET /api/artifact/{id}/file`

Serve the binary file for an image artifact. Returns the file directly with appropriate content type.

## Rich content detection

The `has_rich_content` function in `channels/telegram/bot.py` detects when a response should trigger artifact creation:

- Markdown tables (3+ pipe characters per row)
- Task lists (`- [x]` / `- [ ]`)
- Calendar content (time ranges + date names)

When rich content is detected, a `reply_markup` with an inline keyboard is added to the Telegram message, containing a "View in app" Web App button.

## Chart generation

The `generate_chart` tool (`tools/charts.py`) creates matplotlib charts and stores them as image artifacts. On Telegram, charts are sent as native photos with a "View in app" button for the full-resolution version.
