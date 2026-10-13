"""Sprite sheets and previews, drawn by the Pixel Zoo (`shared/avatar/zoo`) and kept on disk.

A sheet is drawn once per look and kept as `<key>.png` and `<key>.json` under `<data_dir>/avatars/`;
a preview (one 32 x 32 frame) as `preview-<key>.png`. The key is a hash of the look and of the zoo's
drawing code (`ZOO_DRAWING`: its version and a fingerprint of its source files), so it is also the
sheet's `ETag`: it changes with the look, and with any change to a drawing, with no version bump
needed. Writes go to a temporary file first, so a half-written file is never served; a folder that
cannot be written only costs the cache (the drawing is served all the same).
"""

import hashlib
import json
import logging
import os
import threading
from importlib.metadata import version
from pathlib import Path
from typing import Any

import zoo
from zoo import png

from marcel_hub.models import Agent

log = logging.getLogger(__name__)


def drawing_fingerprint(package: Path) -> str:
    """The zoo's version and a hash of its source files: changes whenever a drawing does."""
    digest = hashlib.sha256(version('marcel-avatar-zoo').encode())
    for source in sorted(package.glob('*.py')):
        digest.update(source.name.encode() + b'\0' + source.read_bytes())
    return digest.hexdigest()[:16]


ZOO_DRAWING = drawing_fingerprint(Path(zoo.__file__).parent)


def look_of(agent: Agent) -> dict[str, str]:
    """The zoo look of an agent: its `look` (0.7), or for an agent that has none yet, one made from
    its older `animal` and `palette` fields with the professional personality. Either way the same
    agent always draws the same avatar."""
    if agent.look_animal and agent.look_color and agent.look_personality:
        return {
            'species': agent.look_animal,
            'color': agent.look_color,
            'personality': agent.look_personality,
        }
    return {
        'species': agent.animal if agent.animal in zoo.SPECIES else 'giraffe',
        'color': agent.palette if agent.palette in zoo.COLORS else 'natural',
        'personality': 'professional',
    }


def look_from_name(name: str) -> dict[str, str]:
    """A contract `Look` picked from a name by the zoo: the same name always gives the same look.
    For an agent made without one (a new person's, or one from before 0.7)."""
    picked = zoo.from_name(name)
    return {
        'animal': picked['species'],
        'color': picked['color'],
        'personality': picked['personality'],
    }


def key(look: dict[str, Any], mood: str | None = None) -> str:
    canonical = json.dumps({'look': look, 'mood': mood, 'zoo': ZOO_DRAWING}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:20]


def etag(look: dict[str, Any]) -> str:
    """The sheet's `ETag` (quoted, as HTTP sends it); `avatar_etag` in `agent.updated`."""
    return f'"{key(look)}"'


class AvatarStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()  # one drawing at a time: two requests never race a file

    def sheet(self, look: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        """The sheet's PNG and its JSON (`contracts/avatar-sheet.schema.json`), drawn if needed."""
        name = key(look)
        image, meta = self.folder / f'{name}.png', self.folder / f'{name}.json'
        with self._lock:
            if image.is_file() and meta.is_file():
                return image.read_bytes(), json.loads(meta.read_bytes())
            drawn, data = zoo.sheet(look)
            _write(image, drawn)
            _write(meta, json.dumps(data, separators=(',', ':')).encode())
            return drawn, data

    def preview(self, look: dict[str, Any], mood: str | None) -> bytes:
        """One 32 x 32 PNG of the look in `mood` (None: the personality's resting face)."""
        image = self.folder / f'preview-{key(look, mood)}.png'
        with self._lock:
            if image.is_file():
                return image.read_bytes()
            pixels = zoo.to_rgba(zoo.frame(zoo.check_look(look), mood))
            drawn = png.encode(zoo.N, zoo.N, pixels)
            _write(image, drawn)
            return drawn


def _write(path: Path, data: bytes) -> None:
    """Keep a drawing; when the folder cannot be written, log it once per file and go on."""
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        temp.replace(path)
    except OSError as exc:
        log.warning('Could not keep the avatar %s (%s); it is drawn again next time.', path, exc)
