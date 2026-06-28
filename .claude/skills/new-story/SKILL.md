---
name: new-story
description: Create a new Story (with subtasks) under an existing Feature on the marcel-admin board. Use when breaking a feature into concrete units of work. To create the parent feature itself, use /new-feature.
---

Create a new story for: $ARGUMENTS

Conventions: `$MARCEL_ADMIN_DIR/JIRA/CLAUDE.md`. Let the CLI do the mechanical parts; stage by name.

```bash
ADMIN="${MARCEL_ADMIN_DIR:-$HOME/projects/marcel-admin}"
JIRA="$ADMIN/JIRA/jira.py"
```

## Steps

### 1. Identify the parent feature

The request must name (or imply) a `FEAT-…`. If ambiguous, run `python3 "$JIRA" list features` and confirm with the user before creating.

### 2. Allocate + scaffold + link

```bash
python3 "$JIRA" new-story FEAT-… "<Title>" --priority <High|Medium|Low>
```

This allocates `STORY-{YYMMDD}-{hash}`, writes the story file, and registers it in the feature's `stories:` frontmatter and `## Stories` list automatically.

### 3. Fill in the prose

In `$ADMIN/JIRA/stories/STORY-…-slug.md`:
- **Description** — what & why, enough for someone else to pick up; set the frontmatter `description:`.
- **Acceptance criteria** — observable, testable outcomes (`- [ ]` each). These drive the tests.

### 4. Add subtasks — the mechanical steps

```bash
python3 "$JIRA" add-subtask STORY-… "Write the failing test"
python3 "$JIRA" add-subtask STORY-… "Implement the handler"
```

### 5. Commit on the marcel-admin board (NOT the code repo)

```bash
git -C "$ADMIN" add JIRA/stories/STORY-….md JIRA/features/FEAT-*.md log.md
git -C "$ADMIN" commit -m "STORY-…: create <slug> (under FEAT-…)"
```

### 6. Report back

The story id, its parent feature, and the subtasks created, so the user can adjust scope. As you implement on the `feat/FEAT-…` branch in the code repo, prefix commits `[STORY-…]` and track progress with `python3 "$JIRA" check STORY-… <n>` and `python3 "$JIRA" note STORY-… "…"` (committed in marcel-admin).
