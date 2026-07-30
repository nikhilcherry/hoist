#!/usr/bin/env python3
"""Render generated QR codes to PNG and decode them with a real scanner.

The unit tests pin a golden matrix; this proves the encoder still produces
symbols an actual decoder accepts. Requires `zbar-tools` and `pillow`, so it
runs in CI rather than in the dependency-free test suite.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hoist import qr  # noqa: E402

CASES = [
    ("https://demo.example.com", "M"),
    ("http://192.168.1.42:8080", "M"),
    ("https://a.b.tech", "L"),
    ("https://hoist-test.example.com/path?x=1", "M"),
    ("HELLO", "H"),
    ("https://" + "x" * 40 + ".example.com", "M"),
    ("https://" + "y" * 90 + ".example.com/some/long/path", "M"),
    ("https://" + "z" * 150 + ".example.com/really/long", "L"),
    ("https://" + "w" * 200 + ".example.com/xx", "L"),
    ("unicode: café ☕ 日本", "M"),
]

SCALE = 8
QUIET = 4


def to_png(text: str, ecl: str, path: Path) -> int:
    matrix = qr.encode(text, ecl)
    n = len(matrix)
    size = (n + QUIET * 2) * SCALE
    image = Image.new("L", (size, size), 255)
    pixels = image.load()
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                for dy in range(SCALE):
                    for dx in range(SCALE):
                        pixels[(c + QUIET) * SCALE + dx, (r + QUIET) * SCALE + dy] = 0
    image.save(path)
    return (n - 17) // 4


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for index, (text, ecl) in enumerate(CASES):
            path = Path(tmp) / f"qr_{index}.png"
            version = to_png(text, ecl, path)
            result = subprocess.run(
                ["zbarimg", "-q", "--raw", str(path)],
                capture_output=True,
                text=True,
            )
            decoded = result.stdout.strip()
            if decoded == text:
                print(f"PASS  v{version:<2} {ecl}  {text[:44]!r}")
            else:
                failures += 1
                print(f"FAIL  v{version:<2} {ecl}  {text[:44]!r}")
                print(f"      decoded {decoded[:60]!r}")

    total = len(CASES)
    print(f"\n{total - failures}/{total} decoded successfully")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
