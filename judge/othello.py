"""Minimal, independent Othello rules for the judge (Python stdlib only).

Square index = row * 8 + col, A1 = 0 (same as the engine). Boards are kept as
(player, opponent) bitboards relative to the side to move, like the engine.
"""

FULL = (1 << 64) - 1
_DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def flips(player, opponent, sq):
    """Discs flipped by playing sq (0 if illegal)."""
    if (player | opponent) >> sq & 1:
        return 0
    r0, c0 = divmod(sq, 8)
    total = 0
    for dr, dc in _DIRS:
        r, c, run = r0 + dr, c0 + dc, 0
        while 0 <= r < 8 and 0 <= c < 8:
            bit = 1 << (r * 8 + c)
            if opponent & bit:
                run |= bit
            else:
                if player & bit:
                    total |= run
                break
            r, c = r + dr, c + dc
    return total


def legal_moves(player, opponent):
    return [sq for sq in range(64) if flips(player, opponent, sq)]


def play(player, opponent, sq):
    """Return (player, opponent) for the side now to move."""
    f = flips(player, opponent, sq)
    if not f:
        raise ValueError(f"illegal move {square_name(sq)}")
    return opponent & ~f, player | f | (1 << sq)


def is_game_over(player, opponent):
    return not legal_moves(player, opponent) and not legal_moves(opponent, player)


def final_score(player, opponent):
    p, o = bin(player).count("1"), bin(opponent).count("1")
    e = 64 - p - o
    if p > o:
        return p - o + e
    if p < o:
        return p - o - e
    return 0


def to_compact(player, opponent):
    return "".join(
        "X" if player >> i & 1 else "O" if opponent >> i & 1 else "-" for i in range(64)
    )


def parse(s):
    s = s.strip()
    if len(s) != 64 or set(s) - set("XO-."):
        raise ValueError("bad board")
    p = sum(1 << i for i, ch in enumerate(s) if ch == "X")
    o = sum(1 << i for i, ch in enumerate(s) if ch == "O")
    return p, o


def square_name(sq):
    return "abcdefgh"[sq % 8] + "12345678"[sq // 8]


def parse_square(name):
    if name == "pass":
        return None
    col = "abcdefgh".index(name[0].lower())
    row = int(name[1]) - 1
    if not 0 <= row < 8:
        raise ValueError(name)
    return row * 8 + col


INITIAL = (1 << 28 | 1 << 35, 1 << 27 | 1 << 36)  # black (to move), white


def _transform(bits, f):
    out = 0
    for sq in range(64):
        if bits >> sq & 1:
            r, c = divmod(sq, 8)
            r2, c2 = f(r, c)
            out |= 1 << (r2 * 8 + c2)
    return out


SYMMETRIES = [
    lambda r, c: (r, c),
    lambda r, c: (c, r),
    lambda r, c: (7 - r, 7 - c),
    lambda r, c: (7 - c, 7 - r),
    lambda r, c: (r, 7 - c),
    lambda r, c: (7 - r, c),
    lambda r, c: (c, 7 - r),
    lambda r, c: (7 - c, r),
]


def canonical(player, opponent):
    """Smallest (player, opponent) over the 8 board symmetries."""
    return min((_transform(player, f), _transform(opponent, f)) for f in SYMMETRIES)
