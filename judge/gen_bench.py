#!/usr/bin/env python3
"""One-off generator for the fixed judge data in bench/ (run by the owner, not by CI).

Uses an independent engine (Edax, run as a separate program; no code copied)
to evaluate openings and to provide exact endgame scores, and cross-checks the
endgame scores with this repository's solver.

usage: gen_bench.py --edax <edax-binary> --engine <othello-binary> --out bench/ [--seed N]
"""

import argparse
import os
import random
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import othello as O  # noqa: E402


def edax_scores(edax, boards, level=None):
    """Scores (side to move) for relative boards, via Edax's problem solver."""
    with tempfile.NamedTemporaryFile("w", suffix=".obf", delete=False) as f:
        for b in boards:
            f.write(f"{b} X;\n")
        path = f.name
    cmd = [edax, "-solve", path, "-n", "1", "-book-usage", "off", "-verbose", "1"]
    if level is not None:
        cmd += ["-l", str(level)]
    out = subprocess.run(
        cmd, cwd=os.path.dirname(edax), capture_output=True, text=True, check=True
    ).stdout
    os.unlink(path)
    scores = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\|\s*\S+\s+([+-]\d+)", line)
        if m:
            scores[int(m.group(1)) - 1] = int(m.group(2))
    if len(scores) != len(boards):
        raise RuntimeError(f"edax returned {len(scores)} scores for {len(boards)} boards")
    return [scores[i] for i in range(len(boards))]


class Engine:
    def __init__(self, path):
        self.p = subprocess.Popen(
            [path, "engine"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )

    def best(self, player, opponent, depth):
        self.p.stdin.write(f"go {O.to_compact(player, opponent)} depth {depth}\n")
        self.p.stdin.flush()
        parts = self.p.stdout.readline().split()
        return O.parse_square(parts[1])

    def close(self):
        self.p.stdin.write("quit\n")
        self.p.stdin.flush()
        self.p.wait()


def random_opening(rng, plies=8):
    p, o = O.INITIAL
    for _ in range(plies):
        moves = O.legal_moves(p, o)
        if not moves:
            return None
        p, o = O.play(p, o, rng.choice(moves))
    return p, o


def game_positions(rng, engine, opening, epsilon=0.25, depth=2):
    """Positions along one game: engine moves with some random moves mixed in."""
    p, o = opening
    seen = []
    while not O.is_game_over(p, o):
        moves = O.legal_moves(p, o)
        if not moves:
            p, o = o, p
            seen.append((p, o))
            continue
        if rng.random() < epsilon:
            sq = rng.choice(moves)
        else:
            sq = engine.best(p, o, depth)
        p, o = O.play(p, o, sq)
        seen.append((p, o))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edax", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    edax = os.path.abspath(args.edax)

    # 1. Openings: unique (up to symmetry) 8-ply positions, roughly balanced.
    uniq = {}
    while len(uniq) < 3000:
        b = random_opening(rng)
        if b and O.legal_moves(*b):
            uniq.setdefault(O.canonical(*b), b)
    cands = list(uniq.values())
    rng.shuffle(cands)
    scores = edax_scores(edax, [O.to_compact(*b) for b in cands], level=12)
    balanced = [b for b, s in zip(cands, scores) if abs(s) <= 4][:1000]
    print(f"openings: {len(balanced)} balanced of {len(cands)}", file=sys.stderr)

    # 2. Positions from games (engine moves mixed with random moves).
    eng = Engine(args.engine)
    mid, end = [], []
    pass_positions = []
    for opening in balanced[:400]:
        for p, o in game_positions(rng, eng, opening):
            e = 64 - bin(p | o).count("1")
            if O.is_game_over(p, o):
                continue
            if not O.legal_moves(p, o):
                pass_positions.append((p, o))
            if 14 <= e <= 50 and rng.random() < 0.03:
                mid.append((p, o))
            if 12 <= e <= 18 and O.legal_moves(p, o) and rng.random() < 0.05:
                end.append((p, o))
    eng.close()
    mid = mid[:190] + pass_positions[:10]
    end = end[:100]
    print(f"midgame: {len(mid)}, endgame: {len(end)}", file=sys.stderr)

    # 3. Exact endgame scores from Edax, cross-checked with our solver.
    end_boards = [O.to_compact(*b) for b in end]
    edax_exact = edax_scores(edax, end_boards)
    ours = subprocess.run(
        [args.engine, "solve"], input="\n".join(end_boards) + "\n",
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for b, s, line in zip(end_boards, edax_exact, ours):
        mine = int(re.search(r'"score":(-?\d+)', line).group(1))
        if mine != s:
            raise SystemExit(f"solver disagreement on {b}: edax {s}, ours {mine}")

    os.makedirs(os.path.join(args.out, "positions"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "endgame"), exist_ok=True)
    with open(os.path.join(args.out, "openings.txt"), "w") as f:
        f.write("# 8-ply openings, unique up to symmetry, |Edax level-12 score| <= 4.\n")
        f.write("# Board relative to side to move (X = to move).\n")
        for b in balanced:
            f.write(O.to_compact(*b) + "\n")
    with open(os.path.join(args.out, "positions", "midgame.txt"), "w") as f:
        f.write("# Midgame positions for the identical-answer and speed checks.\n")
        f.write("# Board relative to side to move; includes positions where X must pass.\n")
        for b in mid:
            f.write(O.to_compact(*b) + "\n")
    with open(os.path.join(args.out, "endgame", "exact.txt"), "w") as f:
        f.write("# Endgame positions (12-18 empties) with exact scores.\n")
        f.write("# Scores from Edax, cross-checked with this repository's solver.\n")
        f.write("# columns: board exact_score empties\n")
        for b, s in zip(end_boards, edax_exact):
            f.write(f"{b} {s} {b.count('-')}\n")


if __name__ == "__main__":
    main()
