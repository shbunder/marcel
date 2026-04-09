"""Install Marcel integration skills into .marcel/skills/.

Skills now live directly in ``.marcel/skills/`` in the repository and are
tracked in git.  This script is a no-op kept for backwards compatibility with
the ``make install-skills`` target; it can be safely called and will exit
cleanly without modifying any files.

Previously this script symlinked or copied files from
``src/marcel_core/skills/docs/`` into ``.claude/skills/``.  That pattern was
replaced in ISSUE-032: skills now live in ``.marcel/skills/`` and are loaded
directly by the skill loader without any install step.

Usage:
    python -m marcel_core.skills.install_skills       # no-op
    python -m marcel_core.skills.install_skills --copy # no-op
"""

if __name__ == '__main__':
    print('Skills are managed directly in .marcel/skills/ — no install step needed.')
