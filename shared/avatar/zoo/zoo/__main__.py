"""Command line: build a sprite sheet or a preview.

python -m zoo sheet --animal giraffe --color rose --personality professional [--out DIR]
python -m zoo sheet --from-name Emma
python -m zoo preview --animal frog --personality bold [--mood happy] [--out frog.png]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import ACCESSORIES, from_name
from .sheet import check_look, preview, sheet, slug


def _look(args: argparse.Namespace) -> dict[str, str | None]:
    picked = from_name(args.from_name) if args.from_name else {}
    return {
        'species': args.animal or picked.get('species'),
        'color': args.color or picked.get('color'),
        'personality': args.personality or picked.get('personality'),
        'accessory': args.accessory,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m zoo', description='Pixel Zoo avatars.')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, text in (('sheet', 'write <look>.png and <look>.json for the app'), ('preview', 'write a preview PNG')):
        p = sub.add_parser(name, help=text)
        p.add_argument('--animal')
        p.add_argument('--color', help='a colour name, "natural" or #rrggbb')
        p.add_argument('--personality')
        p.add_argument('--accessory', choices=list(ACCESSORIES))
        p.add_argument('--from-name', help='pick the look from a name (flags above still win)')
        p.add_argument('--out', help='a folder for sheet, a file for preview (default: out/)')
        if name == 'preview':
            p.add_argument('--mood', help='show all 16 frames of one mood (default: every mood once)')
            p.add_argument('--scale', type=int, default=4, choices=range(1, 17), metavar='1-16')
    args = parser.parse_args(argv)
    try:
        look = check_look(_look(args))
        if args.command == 'sheet':
            out = Path(args.out or 'out')
            out.mkdir(parents=True, exist_ok=True)
            image, meta = sheet(look)
            (out / meta['image']).write_bytes(image)
            json_path = out / f'{slug(look)}.json'
            json_path.write_text(json.dumps(meta, indent=2) + '\n')
            print(f'Wrote {out / meta["image"]} and {json_path} ({meta["frame_count"]} frames).')
        else:
            path = Path(args.out or f'out/{slug(look)}-preview.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(preview(look, args.mood, args.scale))
            print(f'Wrote {path}.')
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
