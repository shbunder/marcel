"""Pixel Zoo: old-school pixel avatars (animal x colour x personality, in any of 25 moods).

from zoo import frame, from_name, sheet
png_bytes, meta = sheet({'species': 'giraffe', 'color': 'rose', 'personality': 'professional'})
"""

from .core import (
    ACCESSORIES,
    COLORS,
    MOODS,
    PERSONALITIES,
    SPECIES,
    STATES,
    N,
    frame,
    from_name,
    loop,
    name_hash,
    palette,
    rgba_sha256,
    to_rgba,
)
from .sheet import LOOPS, check_look, preview, sheet, slug

__all__ = [
    'ACCESSORIES',
    'COLORS',
    'LOOPS',
    'MOODS',
    'N',
    'PERSONALITIES',
    'SPECIES',
    'STATES',
    'check_look',
    'frame',
    'from_name',
    'loop',
    'name_hash',
    'palette',
    'preview',
    'rgba_sha256',
    'sheet',
    'slug',
    'to_rgba',
]
