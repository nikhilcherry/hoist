"""QR encoder tests.

The golden matrix below was cross-checked against `qrencode` and decoded back
with `zbarimg`, so it pins real spec compliance rather than self-consistency.
"""

from __future__ import annotations

import pytest

from hoist import qr

# "HELLO", error-correction level M, version 1.
GOLDEN_HELLO_M = """\
#######.##.#..#######
#.....#..##.#.#.....#
#.###.#..####.#.###.#
#.###.#.#..#..#.###.#
#.###.#.#...#.#.###.#
#.....#.#.##..#.....#
#######.#.#.#.#######
........#####........
#...#.######.#####..#
...###..#.###..#.####
#.##..#.#.##..###..#.
###..#...#...##.#....
..#.###..#..###...##.
........###.###..#.##
#######.##..##...#.#.
#.....#....##..#...#.
#.###.#.#..#..###.#.#
#.###.#....##....#.##
#.###.#..###..####...
#.....#..#...##......
#######.#...#####.#.#"""

# Total codewords (data + error correction) per version, from the QR spec.
TOTAL_CODEWORDS = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
    6: 172, 7: 196, 8: 242, 9: 292, 10: 346,
}


def render_text(matrix: list[list[int]]) -> str:
    return "\n".join("".join("#" if v else "." for v in row) for row in matrix)


def test_matches_golden_matrix():
    assert render_text(qr.encode("HELLO", "M")) == GOLDEN_HELLO_M


def test_finder_patterns_present():
    matrix = qr.encode("https://example.com", "M")
    size = len(matrix)
    for row, col in [(0, 0), (0, size - 7), (size - 7, 0)]:
        block = [r[col : col + 7] for r in matrix[row : row + 7]]
        assert block[0] == [1] * 7
        assert block[6] == [1] * 7
        assert block[3][3] == 1
        assert block[1][1] == 0


def test_timing_patterns_alternate():
    matrix = qr.encode("timing", "M")
    size = len(matrix)
    for i in range(8, size - 8):
        assert matrix[6][i] == (1 if i % 2 == 0 else 0)
        assert matrix[i][6] == (1 if i % 2 == 0 else 0)


def test_dark_module_is_set():
    for text in ("a", "https://example.com/somewhat/longer/path"):
        matrix = qr.encode(text, "M")
        assert matrix[len(matrix) - 8][8] == 1


@pytest.mark.parametrize("version", sorted(TOTAL_CODEWORDS))
def test_free_module_count_matches_spec(version):
    """Data capacity must equal the spec's codeword count, plus <8 remainder bits."""
    size = version * 4 + 17
    _, reserved = qr._place_function_patterns(size, version)
    free = sum(1 for r in range(size) for c in range(size) if not reserved[r][c])
    remainder = free - TOTAL_CODEWORDS[version] * 8
    assert 0 <= remainder < 8


@pytest.mark.parametrize("ecl", ["L", "M", "Q", "H"])
def test_version_grows_with_payload(ecl):
    assert len(qr.encode("x" * 60, ecl)) >= len(qr.encode("x", ecl))


def test_size_formula():
    matrix = qr.encode("x" * 30, "M")
    assert len(matrix) == len(matrix[0])
    assert (len(matrix) - 17) % 4 == 0
    assert len(qr.encode("x", "L")) == 21


def test_unicode_payload_encodes():
    assert len(qr.encode("café ☕", "M")) >= 21


def test_rejects_unknown_ecl():
    with pytest.raises(ValueError):
        qr.encode("hi", "Z")


def test_rejects_oversized_payload():
    with pytest.raises(ValueError):
        qr.encode("x" * 5000, "H")


def test_render_has_quiet_zone_and_colour():
    art = qr.render("https://example.com")
    lines = art.splitlines()
    assert lines, "render produced nothing"
    assert "\033[" in art, "expected ANSI colour codes"
    # The first rendered line is the all-light quiet zone.
    assert "40m" not in lines[0], "quiet zone should contain no dark modules"


def test_render_ascii_is_colourless():
    art = qr.render_ascii("https://example.com")
    assert "\033[" not in art
    assert set(art) <= {"█", " ", "\n"}


def test_choose_version_is_monotonic():
    previous = 0
    for length in range(1, 200, 7):
        version = qr._choose_version(length, "L")
        assert version >= previous
        previous = version
