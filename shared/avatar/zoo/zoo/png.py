"""Tiny PNG writer and reader (RGBA, 8 bit) on zlib, so the zoo needs no packages."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode(width: int, height: int, rgba: bytes) -> bytes:
    """RGBA bytes, row by row -> a PNG file."""
    if len(rgba) != width * height * 4:
        raise ValueError(f'expected {width * height * 4} bytes for {width}x{height}, got {len(rgba)}')
    stride = width * 4
    raw = b''.join(b'\x00' + rgba[y * stride : (y + 1) * stride] for y in range(height))
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return _SIGNATURE + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(raw, 9)) + _chunk(b'IEND', b'')


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> tuple[int, int, bytes]:
    """A PNG (8-bit RGBA, not interlaced) -> (width, height, RGBA bytes). Enough to check our own files."""
    if not data.startswith(_SIGNATURE):
        raise ValueError('not a PNG')
    pos, width, height, idat = 8, 0, 0, b''
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b'IHDR':
            width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', body)
            if (depth, color, interlace) != (8, 6, 0):
                raise ValueError('only 8-bit RGBA, non-interlaced PNGs are supported')
        elif kind == b'IDAT':
            idat += body
    raw = zlib.decompress(idat)
    stride = width * 4
    out = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride if kind else 0):  # filter 0 (what `encode` writes) needs no work
            a = line[i - 4] if i >= 4 else 0
            b, c = prev[i], prev[i - 4] if i >= 4 else 0
            line[i] = (line[i] + (0, a, b, (a + b) // 2, _paeth(a, b, c))[kind]) & 0xFF
        out += line
        prev = line
    return width, height, bytes(out)


def read(path: Path | str) -> tuple[int, int, bytes]:
    return decode(Path(path).read_bytes())
