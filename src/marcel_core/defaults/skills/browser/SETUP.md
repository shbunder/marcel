---
name: browser
description: Setup guide for browser automation
---

## Browser Skill — Setup Required

The browser skill requires Playwright to be installed. To enable it:

```bash
# Install the playwright package
pip install playwright

# Install the Chromium browser binary
playwright install chromium
```

After installation, restart Marcel. The browser tools will become available automatically.

### Configuration (optional)

Add to `.env.local` if needed:

```
# Run in headed mode (shows browser window, useful for debugging)
BROWSER_HEADLESS=false

# Allow navigation to specific internal hosts (comma-separated)
BROWSER_URL_ALLOWLIST=*.internal.example.com,dashboard.local

# Navigation timeout in seconds (default: 30)
BROWSER_TIMEOUT=60
```
