"""The iOS app bundles Marcel's sheet (giraffe, rose, professional: the hub's OWNER_LOOK) as the
default avatar. It must be exactly what the zoo draws; refresh it with `make -C ios/AvatarKit sheet`."""

from __future__ import annotations

import json
from pathlib import Path

from zoo import sheet

APP = Path(__file__).parents[4] / 'ios' / 'AvatarKit' / 'Sources' / 'AvatarKit' / 'Resources'
MARCEL = {'species': 'giraffe', 'color': 'rose', 'personality': 'professional'}


def test_the_apps_default_avatar_is_what_the_zoo_draws_for_marcel():
    image, meta = sheet(MARCEL)
    bundled = json.loads((APP / 'giraffe-rose-professional.json').read_text())
    assert bundled == meta, 'run `make -C ios/AvatarKit sheet`'
    assert (APP / meta['image']).read_bytes() == image, 'run `make -C ios/AvatarKit sheet`'
    assert meta['frame'] == {'width': 32, 'height': 32}, 'the zoo native size'


def test_the_app_bundles_no_other_sheet():
    assert sorted(p.name for p in APP.iterdir()) == [
        'giraffe-rose-professional.json',
        'giraffe-rose-professional.png',
    ]
