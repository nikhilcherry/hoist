"""Terminal output helpers."""

from __future__ import annotations

import os
import sys

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(text: str) -> str:
    return _c("2", text)


def bold(text: str) -> str:
    return _c("1", text)


def green(text: str) -> str:
    return _c("32", text)


def yellow(text: str) -> str:
    return _c("33", text)


def red(text: str) -> str:
    return _c("31", text)


def cyan(text: str) -> str:
    return _c("36", text)


def ok(message: str) -> None:
    print(f"{green('✓')} {message}")


def info(message: str) -> None:
    print(f"{cyan('·')} {message}")


def warn(message: str) -> None:
    print(f"{yellow('!')} {message}")


def fail(message: str) -> None:
    print(f"{red('✗')} {message}", file=sys.stderr)


def step(message: str) -> None:
    print(f"{dim('  →')} {dim(message)}")


def banner(url: str, subtitle: str = "") -> None:
    print()
    print(f"  {bold(green(url))}")
    if subtitle:
        print(f"  {dim(subtitle)}")
    print()


def visible_width(text: str) -> int:
    """Length of `text` ignoring ANSI escape sequences."""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            while i < len(text) and text[i] != "m":
                i += 1
            i += 1
            continue
        out += 1
        i += 1
    return out


def table(rows: list[list[str]], headers: list[str]) -> None:
    """Print a left-aligned table, ignoring ANSI codes when measuring width."""
    if not rows:
        return
    cols = len(headers)
    widths = [visible_width(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], visible_width(row[i]))

    def render(cells: list[str]) -> str:
        parts = [
            cell + " " * (widths[i] - visible_width(cell))
            for i, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    print(render([bold(h) for h in headers]))
    print(dim("  ".join("─" * w for w in widths)))
    for row in rows:
        print(render(row))
