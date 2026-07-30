"""Minimal pure-Python QR encoder (byte mode, versions 1-10).

Exists so hoist has zero runtime dependencies -- a hackathon laptop with no
internet can still print a scannable code for the judges' phones.
"""

from __future__ import annotations

# (ec_codewords_per_block, group1_blocks, group1_data, group2_blocks, group2_data)
_RS_BLOCKS = {
    (1, "L"): (7, 1, 19, 0, 0), (1, "M"): (10, 1, 16, 0, 0),
    (1, "Q"): (13, 1, 13, 0, 0), (1, "H"): (17, 1, 9, 0, 0),
    (2, "L"): (10, 1, 34, 0, 0), (2, "M"): (16, 1, 28, 0, 0),
    (2, "Q"): (22, 1, 22, 0, 0), (2, "H"): (28, 1, 16, 0, 0),
    (3, "L"): (15, 1, 55, 0, 0), (3, "M"): (26, 1, 44, 0, 0),
    (3, "Q"): (18, 2, 17, 0, 0), (3, "H"): (22, 2, 13, 0, 0),
    (4, "L"): (20, 1, 80, 0, 0), (4, "M"): (18, 2, 32, 0, 0),
    (4, "Q"): (26, 2, 24, 0, 0), (4, "H"): (16, 4, 9, 0, 0),
    (5, "L"): (26, 1, 108, 0, 0), (5, "M"): (24, 2, 43, 0, 0),
    (5, "Q"): (18, 2, 15, 2, 16), (5, "H"): (22, 2, 11, 2, 12),
    (6, "L"): (18, 2, 68, 0, 0), (6, "M"): (16, 4, 27, 0, 0),
    (6, "Q"): (24, 4, 19, 0, 0), (6, "H"): (28, 4, 15, 0, 0),
    (7, "L"): (20, 2, 78, 0, 0), (7, "M"): (18, 4, 31, 0, 0),
    (7, "Q"): (18, 2, 14, 4, 15), (7, "H"): (26, 4, 13, 1, 14),
    (8, "L"): (24, 2, 97, 0, 0), (8, "M"): (22, 2, 38, 2, 39),
    (8, "Q"): (22, 4, 18, 2, 19), (8, "H"): (26, 4, 14, 2, 15),
    (9, "L"): (30, 2, 116, 0, 0), (9, "M"): (22, 3, 36, 2, 37),
    (9, "Q"): (20, 4, 16, 4, 17), (9, "H"): (24, 4, 12, 4, 13),
    (10, "L"): (18, 2, 68, 2, 69), (10, "M"): (26, 4, 43, 1, 44),
    (10, "Q"): (24, 6, 19, 2, 20), (10, "H"): (28, 6, 15, 2, 16),
}

_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_ECL_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# --- GF(256) arithmetic, primitive polynomial 0x11d ---------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        poly = nxt
    return poly


def _rs_encode(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _gf_mul(g, factor)
    return rem


# --- bit stream ---------------------------------------------------------------


class _Bits:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)


def _choose_version(nbytes: int, ecl: str) -> int:
    for version in range(1, 11):
        _ec, g1b, g1d, g2b, g2d = _RS_BLOCKS[(version, ecl)]
        capacity_bits = (g1b * g1d + g2b * g2d) * 8
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + nbytes * 8 <= capacity_bits:
            return version
    raise ValueError(f"payload too large for QR v1-10 ({nbytes} bytes)")


def _build_codewords(data: bytes, version: int, ecl: str) -> list[int]:
    ec_len, g1b, g1d, g2b, g2d = _RS_BLOCKS[(version, ecl)]
    total_data = g1b * g1d + g2b * g2d

    bs = _Bits()
    bs.put(0b0100, 4)  # byte mode
    bs.put(len(data), 8 if version < 10 else 16)
    for byte in data:
        bs.put(byte, 8)

    # terminator + byte alignment
    bs.put(0, min(4, total_data * 8 - len(bs)))
    while len(bs) % 8:
        bs.bits.append(0)

    codewords = []
    for i in range(0, len(bs.bits), 8):
        byte = 0
        for bit in bs.bits[i:i + 8]:
            byte = (byte << 1) | bit
        codewords.append(byte)
    pad = [0xEC, 0x11]
    for i in range(total_data - len(codewords)):
        codewords.append(pad[i % 2])

    # split into blocks
    blocks: list[list[int]] = []
    pos = 0
    for _ in range(g1b):
        blocks.append(codewords[pos:pos + g1d])
        pos += g1d
    for _ in range(g2b):
        blocks.append(codewords[pos:pos + g2d])
        pos += g2d

    ec_blocks = [_rs_encode(b, ec_len) for b in blocks]

    # interleave data then EC codewords
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_len):
        for b in ec_blocks:
            out.append(b[i])
    return out


# --- matrix -------------------------------------------------------------------


def _bch_format(fmt: int) -> int:
    val = fmt << 10
    for i in range(4, -1, -1):
        if val & (1 << (i + 10)):
            val ^= 0b10100110111 << i
    return ((fmt << 10) | val) ^ 0b101010000010010


def _bch_version(version: int) -> int:
    val = version << 12
    for i in range(5, -1, -1):
        if val & (1 << (i + 12)):
            val ^= 0b1111100100101 << i
    return (version << 12) | val


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _place_function_patterns(size: int, version: int):
    """Return (matrix, reserved) with finders/timing/alignment drawn."""
    mat = [[0] * size for _ in range(size)]
    res = [[False] * size for _ in range(size)]

    def finder(row: int, col: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                res[rr][cc] = True
                inside = 0 <= r < 7 and 0 <= c < 7
                if inside and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)):
                    mat[rr][cc] = 1
                else:
                    mat[rr][cc] = 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    # timing patterns
    for i in range(size):
        if not res[6][i]:
            mat[6][i] = 1 if i % 2 == 0 else 0
            res[6][i] = True
        if not res[i][6]:
            mat[i][6] = 1 if i % 2 == 0 else 0
            res[i][6] = True

    # alignment patterns, skipping the three that collide with finders.
    # Note the others legitimately sit on the timing row/column, so a
    # "already reserved" test would wrongly drop them from v7 up.
    centers = _ALIGN[version]
    last = len(centers) - 1
    skip = {(0, 0), (0, last), (last, 0)}
    for ri, r in enumerate(centers):
        for ci, c in enumerate(centers):
            if (ri, ci) in skip:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr, cc = r + dr, c + dc
                    res[rr][cc] = True
                    mat[rr][cc] = 1 if max(abs(dr), abs(dc)) != 1 else 0

    # dark module
    mat[size - 8][8] = 1
    res[size - 8][8] = True

    # reserve format info areas
    for i in range(9):
        for rr, cc in ((8, i), (i, 8)):
            if 0 <= rr < size and 0 <= cc < size:
                res[rr][cc] = True
    for i in range(8):
        res[8][size - 1 - i] = True
        res[size - 1 - i][8] = True

    # reserve version info areas
    if version >= 7:
        for i in range(6):
            for j in range(3):
                res[size - 11 + j][i] = True
                res[i][size - 11 + j] = True

    return mat, res


def _penalty(mat: list[list[int]]) -> int:
    size = len(mat)
    score = 0
    lines = [list(row) for row in mat] + [list(col) for col in zip(*mat)]

    # rule 1: runs of 5 or more same-coloured modules
    for line in lines:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)

    # rule 2: 2x2 same-coloured blocks
    for r in range(size - 1):
        for c in range(size - 1):
            if mat[r][c] == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                score += 3

    # rule 3: finder-like 1:1:3:1:1 patterns
    pat_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat_b = list(reversed(pat_a))
    for line in lines:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == pat_a or window == pat_b:
                score += 40

    # rule 4: dark/light balance
    dark = sum(sum(row) for row in mat)
    pct = dark * 100 / (size * size)
    score += 10 * int(abs(pct - 50) // 5)
    return score


def encode(text: str, ecl: str = "M") -> list[list[int]]:
    """Encode `text` and return the QR matrix as rows of 0/1 ints."""
    if ecl not in _ECL_BITS:
        raise ValueError(f"unknown error-correction level {ecl!r}")
    data = text.encode("utf-8")
    version = _choose_version(len(data), ecl)
    codewords = _build_codewords(data, version, ecl)
    size = version * 4 + 17

    base, res = _place_function_patterns(size, version)

    bits: list[int] = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)

    # place data bits in zigzag, skipping the vertical timing column
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for c in (col, col - 1):
                if res[row][c]:
                    continue
                base[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2

    best: tuple[int, list[list[int]]] | None = None
    for mask_id, mask in enumerate(_MASKS):
        mat = [row[:] for row in base]
        for r in range(size):
            for c in range(size):
                if not res[r][c] and mask(r, c):
                    mat[r][c] ^= 1

        # Format info, written twice. Bit 0 lands at the top of column 8 and
        # walks down/left; getting the row/column order backwards here still
        # produces a plausible-looking symbol that no decoder can read.
        fmt = _bch_format((_ECL_BITS[ecl] << 3) | mask_id)
        for i in range(15):
            bit = (fmt >> i) & 1
            if i < 6:
                mat[i][8] = bit
            elif i == 6:
                mat[7][8] = bit
            elif i == 7:
                mat[8][8] = bit
            elif i == 8:
                mat[8][7] = bit
            else:
                mat[8][14 - i] = bit
            if i < 8:
                mat[8][size - 1 - i] = bit
            else:
                mat[size - 15 + i][8] = bit
        mat[size - 8][8] = 1  # dark module, restated after format info

        # version info for v7+
        if version >= 7:
            ver = _bch_version(version)
            for i in range(18):
                bit = (ver >> i) & 1
                mat[i // 3][size - 11 + i % 3] = bit
                mat[size - 11 + i % 3][i // 3] = bit

        score = _penalty(mat)
        if best is None or score < best[0]:
            best = (score, mat)

    assert best is not None
    return best[1]


def _padded(text: str, ecl: str, quiet: int) -> list[list[int]]:
    mat = encode(text, ecl)
    width = len(mat) + quiet * 2
    rows = [[0] * width for _ in range(quiet)]
    for row in mat:
        rows.append([0] * quiet + list(row) + [0] * quiet)
    rows.extend([0] * width for _ in range(quiet))
    return rows


def render(text: str, ecl: str = "M", quiet: int = 2) -> str:
    """Render with explicit ANSI colours, two module rows per terminal line.

    Colours are set rather than inherited so the code keeps the right polarity
    (dark modules dark) on light and dark terminal themes alike -- most phone
    scanners will not read an inverted symbol.
    """
    rows = _padded(text, ecl, quiet)
    if len(rows) % 2:
        rows.append([0] * len(rows[0]))
    fg = {0: "97", 1: "30"}  # bright white / black
    bg = {0: "107", 1: "40"}
    out = []
    for i in range(0, len(rows), 2):
        line = []
        for top, bottom in zip(rows[i], rows[i + 1]):
            line.append(f"\033[{fg[top]};{bg[bottom]}m▀")
        out.append("".join(line) + "\033[0m")
    return "\n".join(out)


def render_ascii(text: str, ecl: str = "M", quiet: int = 2) -> str:
    """Colourless renderer: two block chars per dark module, one row per line."""
    rows = _padded(text, ecl, quiet)
    return "\n".join("".join("██" if v else "  " for v in row) for row in rows)
