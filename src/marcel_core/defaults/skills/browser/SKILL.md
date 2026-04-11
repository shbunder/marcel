---
name: browser
description: Browse the web — navigate pages, read content, click, type, take screenshots
requires:
  packages:
    - playwright
---

## Browser Tools

You have access to a headless Chromium browser for interacting with web pages. Use these tools to navigate, read, and interact with websites.

### Workflow

The typical pattern is:

1. **Navigate** — `browser_navigate` to open a URL. Returns the page title and an accessibility snapshot.
2. **Read** — Use `browser_snapshot` to get the current page structure as a ref-indexed accessibility tree. Each element gets a `[ref]` number.
3. **Interact** — Use `browser_click` or `browser_type` with a ref number to interact with elements.
4. **Verify** — Use `browser_snapshot` again to see the updated page, or `browser_screenshot` for visual confirmation.

### Tools

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Go to a URL. Returns page title + accessibility snapshot. |
| `browser_screenshot` | Take a PNG screenshot of the page (visual verification). |
| `browser_snapshot` | Get the accessibility tree with ref numbers (structured reading). |
| `browser_click` | Click an element by ref, CSS selector, or coordinates. |
| `browser_type` | Type text into an input (by ref or selector). |
| `browser_scroll` | Scroll the page (up/down/left/right). |
| `browser_press_key` | Press a keyboard key (Enter, Escape, Tab, arrows, etc.). |
| `browser_tab` | Manage tabs (list, new, switch, close). |
| `browser_evaluate` | Run JavaScript in the page and return the result. |
| `browser_content` | Get raw HTML of the page or a CSS-selected element. |
| `browser_close` | Close the browser session when done. |

### Snapshot vs Screenshot

- Use **`browser_snapshot`** as your primary way to read and interact with pages. It returns structured text with ref numbers you can use for clicking and typing. Fast and token-efficient.
- Use **`browser_screenshot`** when you need to see visual layout, images, CAPTCHAs, or verify appearance. Returns a PNG image. More expensive in tokens.

### Ref-based Interaction

After calling `browser_snapshot`, you get output like:

```
[1] heading "Welcome"
[2] textbox "Email" focused
[3] textbox "Password"
[4] button "Sign In"
[5] link "Forgot password?"
```

You can then use `browser_click(ref="4")` to click the Sign In button, or `browser_type(ref="2", text="user@example.com")` to fill the email field.

**Important:** Refs are invalidated after any page change (navigation, form submission, AJAX updates). Always re-run `browser_snapshot` after an action that modifies the page before using refs again.

### Tips

- Always close the browser when done with `browser_close` to free resources.
- If a page requires scrolling to see more content, use `browser_scroll` then `browser_snapshot`.
- For multi-step flows (login, form filling), snapshot after each step to confirm the page state.
- Some elements may not appear in the accessibility tree. Use `browser_evaluate` to extract data via JavaScript, or `browser_content` to read raw HTML. Only fall back to `browser_screenshot` as a last resort.
- When `browser_snapshot` returns "(Could not read page accessibility tree)", use `browser_evaluate` with a DOM query to extract the data you need. Example: `browser_evaluate(script="[...document.querySelectorAll('article h2')].map(h => h.textContent.trim())")`
- Navigation to private networks (localhost, 10.x, 192.168.x, etc.) is blocked for security.
