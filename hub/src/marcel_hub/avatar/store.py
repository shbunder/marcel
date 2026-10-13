"""Sprite sheets and previews, drawn by the Pixel Zoo (`shared/avatar/zoo`) and kept on disk.

A sheet is drawn once per look and kept as `<key>.png` and `<key>.json` under `<data_dir>/avatars/`;
a preview (one 32 x 32 frame) as `preview-<key>.png`. The key is a hash of the look and of the zoo's
version, so it is also the sheet's `ETag`: it changes with the look, and with a new zoo release
(bump `shared/avatar/zoo`'s version when a drawing changes, or old sheets stay cached). Writes go to
a temporary file first, so a half-written file is never served.
"""

import hashlib
import json
import os
import threading
from importlib.metadata import version
from pathlib import Path
from typing import Any

import zoo
from zoo import png

from marcel_hub.models import Agent

ZOO_VERSION = version('marcel-avatar-zoo')


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
    canonical = json.dumps({'look': look, 'mood': mood, 'zoo': ZOO_VERSION}, sort_keys=True)
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
            if not (image.is_file() and meta.is_file()):
                drawn, data = zoo.sheet(look)
                _write(image, drawn)
                _write(meta, json.dumps(data, separators=(',', ':')).encode())
            return image.read_bytes(), json.loads(meta.read_bytes())

    def preview(self, look: dict[str, Any], mood: str | None) -> bytes:
        """One 32 x 32 PNG of the look in `mood` (None: the personality's resting face)."""
        image = self.folder / f'preview-{key(look, mood)}.png'
        with self._lock:
            if not image.is_file():
                pixels = zoo.to_rgba(zoo.frame(zoo.check_look(look), mood))
                _write(image, png.encode(zoo.N, zoo.N, pixels))
            return image.read_bytes()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_bytes(data)
    temp.replace(path)
