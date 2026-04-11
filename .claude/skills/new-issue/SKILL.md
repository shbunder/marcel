---
description: Create a new issue in project/issues/open/ and commit it
---

Create a new issue for: $ARGUMENTS

## Steps

1. **Read conventions**
   Read `project/issues/CLAUDE.md` to refresh yourself on the issue template, file naming, and git commit format.

2. **Find the next issue number**
   Run: `find ./project/issues -name 'ISSUE-*.md' | grep -oE 'ISSUE-[0-9]+' | sort -u -t- -k2 -n | tail -1`
   Increment the number by 1. Zero-pad to 3 digits.
   **Important:** The `-u` flag deduplicates (same issue number may appear in multiple dirs). Always verify the chosen number doesn't already exist: `ls project/issues/*/ISSUE-{NNN}-* 2>/dev/null` — if any match, increment again.

3. **Derive title slug**
   Produce a short kebab-case slug from the request (3–5 words, no stop words).
   Filename: `project/issues/open/ISSUE-{NNN}-{slug}.md`

4. **Write the issue file**
   Use the template from `project/issues/CLAUDE.md`. Fill in:
   - **Status:** Open
   - **Created:** today's date
   - **Capture → Original request:** verbatim from the argument above
   - **Resolved intent:** one paragraph in your own words
   - **Description:** what and why, derived from the request
   - **Tasks:** a concrete, testable checklist for this issue
   - Leave Relationships empty unless you can infer dependencies from existing issues

5. **Commit only the issue file**
   ```
   git add ./project/issues/open/ISSUE-{NNN}-{slug}.md
   git commit -m "📝 [ISSUE-{NNN}] created: {one-line description}"
   ```

6. **Report back**
   Tell me the issue number, file path, and the task list you created so I can confirm or adjust scope before work begins.
