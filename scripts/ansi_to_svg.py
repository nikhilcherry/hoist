#!/usr/bin/env python3
"""Render captured terminal output (with ANSI colour) to an SVG image.

The screenshots in docs/ are produced from *real* hoist runs, not mock-ups:

    script -qec "hoist up ./demo --local" /dev/null > capture.ansi
    python3 scripts/ansi_to_svg.py capture.ansi docs/demo-up.svg --title "hoist up ./demo"

Only the handful of SGR codes hoist emits are supported (bold, dim, the six
basic colours, and the bright-white/black pairs the QR encoder uses).

QR rows are drawn as rectangles rather than half-block glyphs, so the code
stays square and scannable no matter what font the viewer has.

Everything else is written with presentation attributes and an explicit
`textLength`, so the layout survives being rendered with an unknown font.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, replace

CHAR_W = 9.0
LINE_H = 21.0
FONT_SIZE = 15.0
PAD_X = 22.0
PAD_TOP = 46.0  # room for the window chrome
PAD_BOTTOM = 22.0
RADIUS = 10.0

FONT = (
    "ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,"
    "Liberation Mono,DejaVu Sans Mono,monospace"
)

BG = "#0d1117"
FG = "#c9d1d9"
CHROME = "#161b22"
BORDER = "#30363d"

PALETTE = {
    30: "#0d1117",  # black
    31: "#f85149",  # red
    32: "#3fb950",  # green
    33: "#d29922",  # yellow
    34: "#58a6ff",  # blue
    35: "#bc8cff",  # magenta
    36: "#39c5cf",  # cyan
    37: "#c9d1d9",  # white
    97: "#ffffff",  # bright white
}
BG_PALETTE = {40: "#0d1117", 107: "#ffffff"}

DIM = "#6e7681"
BOLD_FG = "#f0f6fc"

SGR = re.compile(r"\033\[([0-9;]*)m")


@dataclass(frozen=True)
class Style:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False

    def colour(self) -> str:
        if self.fg:
            return self.fg
        if self.dim:
            return DIM
        if self.bold:
            return BOLD_FG
        return FG


def apply_sgr(style: Style, params: str) -> Style:
    if params in ("", "0"):
        return Style()
    for raw in params.split(";"):
        if raw == "":
            continue
        code = int(raw)
        if code == 0:
            style = Style()
        elif code == 1:
            style = replace(style, bold=True)
        elif code == 2:
            style = replace(style, dim=True)
        elif code == 22:
            style = replace(style, bold=False, dim=False)
        elif code in PALETTE:
            style = replace(style, fg=PALETTE[code])
        elif code == 39:
            style = replace(style, fg=None)
        elif code in BG_PALETTE:
            style = replace(style, bg=BG_PALETTE[code])
        elif code == 49:
            style = replace(style, bg=None)
    return style


def parse(text: str) -> list[list[tuple[str, Style]]]:
    """Turn an ANSI capture into rows of (character, style) cells."""
    rows: list[list[tuple[str, Style]]] = []
    style = Style()
    for raw_line in text.replace("\r\n", "\n").replace("\r", "").split("\n"):
        row: list[tuple[str, Style]] = []
        pos = 0
        for match in SGR.finditer(raw_line):
            for ch in raw_line[pos : match.start()]:
                row.append((ch, style))
            style = apply_sgr(style, match.group(1))
            pos = match.end()
        for ch in raw_line[pos:]:
            row.append((ch, style))
        rows.append(row)
    while rows and not any(ch.strip() for ch, _ in rows[-1]):
        rows.pop()
    return rows


def is_qr_row(row: list[tuple[str, Style]]) -> bool:
    return bool(row) and all(ch == "▀" and st.bg for ch, st in row)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def runs(row: list[tuple[str, Style]]) -> list[tuple[int, str, Style]]:
    """Group a row into (start column, text, style) runs.

    Runs are trimmed of surrounding blanks and re-anchored to the column the
    text actually starts at, so nothing depends on how a renderer treats
    leading or trailing whitespace inside a `textLength`.
    """

    def emit(start: int, text: str, style: Style) -> tuple[int, str, Style] | None:
        stripped = text.strip()
        if not stripped:
            return None
        return start + (len(text) - len(text.lstrip())), stripped, style

    out: list[tuple[int, str, Style]] = []
    start = 0
    buf = ""
    current: Style | None = None
    for col, (ch, st) in enumerate(row):
        if current is not None and st == current:
            buf += ch
            continue
        run = emit(start, buf, current or Style())
        if run:
            out.append(run)
        start, buf, current = col, ch, st
    run = emit(start, buf, current or Style())
    if run:
        out.append(run)
    return out


QR_BACKDROP = "#ffffff"


def qr_backdrop(
    rows: list[list[tuple[str, Style]]], start: int, y: float
) -> list[str]:
    """One rect covering the whole QR block, so only dark modules need drawing."""
    end = start
    while end < len(rows) and is_qr_row(rows[end]):
        end += 1
    width = max(len(rows[i]) for i in range(start, end)) * CHAR_W
    height = (end - start) * CHAR_W * 2
    return [
        f'<rect x="{PAD_X:.1f}" y="{y:.1f}" width="{width:.1f}" '
        f'height="{height:.1f}" fill="{QR_BACKDROP}"/>'
    ]


def module_runs(colours: list[str], y: float) -> list[str]:
    """Draw a half-row of QR modules as merged runs, skipping the backdrop."""
    out: list[str] = []
    col = 0
    while col < len(colours):
        colour = colours[col]
        run = 1
        while col + run < len(colours) and colours[col + run] == colour:
            run += 1
        if colour != QR_BACKDROP:
            out.append(
                f'<rect x="{PAD_X + col * CHAR_W:.1f}" y="{y:.1f}" '
                f'width="{run * CHAR_W:.1f}" height="{CHAR_W:.1f}" fill="{colour}"/>'
            )
        col += run
    return out


def render(rows: list[list[tuple[str, Style]]], title: str) -> str:
    width_cells = max((len(r) for r in rows), default=0)
    width_cells = max(width_cells, len(title) + 8, 46)

    # QR rows carry two modules per line, so they are shorter than text rows.
    heights = [CHAR_W * 2 if is_qr_row(r) else LINE_H for r in rows]

    w = width_cells * CHAR_W + PAD_X * 2
    h = sum(heights) + PAD_TOP + PAD_BOTTOM

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}" font-family="{FONT}" font-size="{FONT_SIZE}">',
        f'<rect width="{w:.0f}" height="{h:.0f}" rx="{RADIUS}" fill="{BG}" '
        f'stroke="{BORDER}"/>',
        f'<path d="M0 {RADIUS}a{RADIUS} {RADIUS} 0 0 1 {RADIUS} -{RADIUS}'
        f'h{w - 2 * RADIUS:.0f}a{RADIUS} {RADIUS} 0 0 1 {RADIUS} {RADIUS}v18H0z" '
        f'fill="{CHROME}"/>',
        f'<line x1="0" y1="{RADIUS + 18}" x2="{w:.0f}" y2="{RADIUS + 18}" '
        f'stroke="{BORDER}"/>',
    ]
    for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        parts.append(f'<circle cx="{20 + i * 16}" cy="14" r="5" fill="{colour}"/>')
    if title:
        parts.append(
            f'<text x="{w / 2:.1f}" y="19" fill="{DIM}" font-size="12.5" '
            f'text-anchor="middle">{esc(title)}</text>'
        )

    y = PAD_TOP
    for index, (row, row_h) in enumerate(zip(rows, heights)):
        if is_qr_row(row):
            if index == 0 or not is_qr_row(rows[index - 1]):
                parts.extend(qr_backdrop(rows, index, y))
            # `▀` means: upper half in the foreground colour, lower half in the
            # background colour. Only the modules that differ from the backdrop
            # are drawn, merged into horizontal runs.
            for half, colours in enumerate(
                ([st.colour() for _, st in row], [st.bg or BG for _, st in row])
            ):
                parts.extend(module_runs(colours, y + half * CHAR_W))
            y += row_h
            continue

        baseline = y + FONT_SIZE * 0.78
        for col, text, st in runs(row):
            weight = ' font-weight="bold"' if st.bold else ""
            parts.append(
                f'<text x="{PAD_X + col * CHAR_W:.1f}" y="{baseline:.1f}" '
                f'fill="{st.colour()}" textLength="{len(text) * CHAR_W:.1f}" '
                f'lengthAdjust="spacingAndGlyphs"{weight} '
                f'xml:space="preserve">{esc(text)}</text>'
            )
        y += row_h

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an ANSI capture to SVG.")
    ap.add_argument("capture", help="file of captured terminal output, or - for stdin")
    ap.add_argument("out", help="path to write the SVG to")
    ap.add_argument("--title", default="", help="text for the window title bar")
    args = ap.parse_args()

    text = sys.stdin.read() if args.capture == "-" else open(args.capture).read()
    with open(args.out, "w") as fh:
        fh.write(render(parse(text), args.title))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
