"""Default MARCEL.md and skill files seeded on first startup.

If the data root does not yet contain a MARCEL.md or skills/ directory,
the bundled defaults are copied from this package.  Existing files are
never overwritten — the data root is the authoritative source.
"""

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULTS_DIR = Path(__file__).resolve().parent


def seed_defaults(data_root: Path) -> None:
    """Copy bundled defaults to *data_root* if they don't already exist.

    Only files that are missing are copied; existing files are left intact.
    This runs once at startup to ensure a fresh install has working config.
    """
    # Seed MARCEL.md
    target_marcel = data_root / 'MARCEL.md'
    if not target_marcel.exists():
        src = _DEFAULTS_DIR / 'MARCEL.md'
        if src.exists():
            target_marcel.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target_marcel)
            log.info('Seeded %s from defaults', target_marcel)

    # Seed skills directory
    src_skills = _DEFAULTS_DIR / 'skills'
    if not src_skills.is_dir():
        return

    target_skills = data_root / 'skills'
    target_skills.mkdir(parents=True, exist_ok=True)

    # ISSUE-072 migration: the `browser` skill was renamed to `web` when the
    # browser tools were consolidated into the `web` god-tool. If an older
    # install still has `skills/browser/` and the new `skills/web/` hasn't
    # been seeded yet, remove the stale directory so the fresh `web/` can
    # seed cleanly below. Idempotent — runs at most once per install.
    legacy_browser = target_skills / 'browser'
    new_web = target_skills / 'web'
    if legacy_browser.is_dir() and not new_web.exists():
        shutil.rmtree(legacy_browser)
        log.info('Removed stale skills/browser/ (renamed to skills/web/ in ISSUE-072)')

    for skill_dir in sorted(src_skills.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith(('_', '.')):
            continue
        target_skill = target_skills / skill_dir.name
        if not target_skill.exists():
            shutil.copytree(skill_dir, target_skill)
            log.info('Seeded skill %s from defaults', skill_dir.name)
        else:
            # Seed individual missing files into existing skill directories
            for src_file in skill_dir.iterdir():
                if src_file.is_file():
                    target_file = target_skill / src_file.name
                    if not target_file.exists():
                        shutil.copy2(src_file, target_file)
                        log.info('Seeded %s into existing skill %s', src_file.name, skill_dir.name)

    # Seed channel prompt files
    src_channels = _DEFAULTS_DIR / 'channels'
    if not src_channels.is_dir():
        return

    target_channels = data_root / 'channels'
    target_channels.mkdir(parents=True, exist_ok=True)

    for channel_file in sorted(src_channels.glob('*.md')):
        target_file = target_channels / channel_file.name
        if target_file.exists():
            continue  # Don't overwrite existing channel customizations
        shutil.copy2(channel_file, target_file)
        log.info('Seeded channel prompt %s from defaults', channel_file.name)
