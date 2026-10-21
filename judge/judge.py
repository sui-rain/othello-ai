#!/usr/bin/env python3
"""Decide whether a candidate engine may replace the current one.

Stages (CI decides which gate applies; the change's author does not):
  1. endgame  - the new solver reproduces every exact score in bench/endgame/exact.txt
  2. identical - root values at a fixed depth match the current engine on the fixed
                 midgame positions plus positions generated from --seed
  3a. speed    - if identical: fewer instructions (callgrind), see SPEED_* below
  3b. strength - if not identical: SPRT match against the current engine

Writes a JSON report to --out and exits 0 only if the change is accepted.
Python stdlib only. Both engines are run as separate programs.
"""

import argparse
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import othello as O  # noqa: E402
import sprt  # noqa: E402

# Speed gate: accept if some part gets at least 1% cheaper and no part gets
# more than 0.5% more expensive.
SPEED_GAIN = 0.99
SPEED_TOLERANCE = 1.005


def read_boards(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.split())
    return out


def run_lines(cmd, boards, timeout):
    """Run a batch command with boards on stdin; return parsed JSON lines."""
    res = subprocess.run(
        cmd, input="\n".join(boards) + "\n", capture_output=True, text=True, timeout=timeout
    )
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {res.stderr.strip()[:500]}")
    lines = [json.loads(l) for l in res.stdout.splitlines() if l.strip()]
    if len(lines) != len(boards):
        raise RuntimeError(f"expected {len(boards)} results, got {len(lines)}")
    return lines


# ---------------------------------------------------------------- stage 1

def check_endgame(new, bench, timeout):
    rows = read_boards(os.path.join(bench, "endgame", "exact.txt"))
    boards = [r[0] for r in rows]
    t0 = time.time()
    got = run_lines([new, "solve"], boards, timeout)
    secs = time.time() - t0
    failures = []
    for (board, score, _empties), g in zip(rows, got):
        p, o = O.parse(board)
        legal = O.legal_moves(p, o)
        move = g.get("move")
        sq = None if move == "pass" else O.parse_square(move) if move else -1
        if g.get("score") != int(score):
            failures.append({"board": board, "expected": int(score), "got": g.get("score")})
        elif (sq is None) != (not legal) or (sq is not None and sq not in legal):
            failures.append({"board": board, "illegal_move": move})
    return {"positions": len(rows), "failures": failures[:10],
            "failed": len(failures), "seconds": round(secs, 2)}


# ---------------------------------------------------------------- stage 2

def generated_positions(bench, seed, count=100):
    """Positions reached by random play from random openings (seeded per run)."""
    rng = random.Random(seed)
    openings = [O.parse(r[0]) for r in read_boards(os.path.join(bench, "openings.txt"))]
    out = []
    while len(out) < count:
        p, o = rng.choice(openings)
        for _ in range(rng.randint(0, 44)):
            moves = O.legal_moves(p, o)
            if not moves:
                if not O.legal_moves(o, p):
                    break
                p, o = o, p
                continue
            p, o = O.play(p, o, rng.choice(moves))
        if not O.is_game_over(p, o):
            out.append(O.to_compact(p, o))
    return out


def check_identical(base, new, boards, depth, timeout):
    ref = run_lines([base, "search", "--depth", str(depth)], boards, timeout)
    cand = run_lines([new, "search", "--depth", str(depth)], boards, timeout)
    mismatches, illegal, verified = [], [], 0
    for board, r, c in zip(boards, ref, cand):
        p, o = O.parse(board)
        legal = O.legal_moves(p, o)
        move = c.get("move")
        sq = None if move == "pass" else O.parse_square(move)
        if (sq is None and legal) or (sq is not None and sq not in legal):
            illegal.append({"board": board, "move": move})
            continue
        if c.get("value") != r.get("value"):
            mismatches.append({"board": board, "base": r.get("value"), "new": c.get("value")})
            continue
        if move != r.get("move"):
            # Same value, different move: the current engine must agree that the
            # new move is worth exactly the reference value.
            v = run_lines([base, "search", "--depth", str(depth), "--move", move],
                          [board], timeout)[0]
            if v.get("value") != r.get("value"):
                mismatches.append({"board": board, "base": r.get("value"),
                                   "new_move": move, "new_move_value": v.get("value")})
            else:
                verified += 1
    nodes_base = sum(r.get("nodes", 0) for r in ref)
    nodes_new = sum(c.get("nodes", 0) for c in cand)
    return {"positions": len(boards), "depth": depth, "identical": not mismatches and not illegal,
            "mismatches": len(mismatches), "illegal": len(illegal),
            "examples": (illegal + mismatches)[:5], "different_moves_verified": verified,
            "nodes_base": nodes_base, "nodes_new": nodes_new}


# ---------------------------------------------------------------- stage 3a

def instructions(valgrind, cmd, boards, timeout):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "cg.out")
        res = subprocess.run(
            [valgrind, "--tool=callgrind", f"--callgrind-out-file={out}"] + cmd,
            input="\n".join(boards) + "\n", capture_output=True, text=True, timeout=timeout,
        )
        if res.returncode != 0:
            raise RuntimeError(f"valgrind failed: {res.stderr.strip()[-500:]}")
        with open(out) as f:
            for line in f:
                m = re.match(r"(summary|totals):\s+(\d+)", line)
                if m:
                    return int(m.group(2))
    raise RuntimeError("no instruction total in callgrind output")


def check_speed(base, new, bench, depth, valgrind, timeout):
    mid = [r[0] for r in read_boards(os.path.join(bench, "positions", "midgame.txt"))][:60]
    end = [r[0] for r in read_boards(os.path.join(bench, "endgame", "exact.txt"))]
    end = sorted(end, key=lambda b: b.count("-"))[:40]
    parts = {
        "midgame_search": (["search", "--depth", str(depth)], mid),
        "endgame_solve": (["solve"], end),
    }
    result = {}
    for name, (args, boards) in parts.items():
        b = instructions(valgrind, [base] + args, boards, timeout)
        n = instructions(valgrind, [new] + args, boards, timeout)
        result[name] = {"base": b, "new": n, "ratio": round(n / b, 5)}
    ratios = [v["ratio"] for v in result.values()]
    accepted = min(ratios) <= SPEED_GAIN and max(ratios) <= SPEED_TOLERANCE
    return {"parts": result, "accepted": accepted}


# ---------------------------------------------------------------- stage 3b

class EngineProc:
    def __init__(self, path):
        self.path = path
        self.start()

    def start(self):
        self.p = subprocess.Popen([self.path, "engine"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def ask(self, board, tc, timeout=10.0):
        """Return a square, None for pass, or 'error'."""
        kind, amount = tc.split(":")
        try:
            self.p.stdin.write(f"go {board} {kind} {amount}\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            return "error"
        timer = threading.Timer(timeout, self.p.kill)
        timer.start()
        line = self.p.stdout.readline()
        timer.cancel()
        parts = line.split()
        if len(parts) < 2 or parts[0] != "move":
            self.close()
            self.start()
            return "error"
        try:
            return O.parse_square(parts[1])
        except (ValueError, IndexError):
            return "error"

    def close(self):
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
            self.p.wait(timeout=2)
        except Exception:
            self.p.kill()


def play_game(black, white, opening, tc_black, tc_white):
    """Score for black (1, 0.5, 0) and whether an engine misbehaved."""
    p, o = O.parse(opening)
    black_to_move = True
    while True:
        moves = O.legal_moves(p, o)
        if not moves:
            if not O.legal_moves(o, p):
                break
            p, o, black_to_move = o, p, not black_to_move
            continue
        eng, tc = (black, tc_black) if black_to_move else (white, tc_white)
        sq = eng.ask(O.to_compact(p, o), tc)
        if sq == "error" or sq not in moves:
            return (0.0 if black_to_move else 1.0), True
        p, o = O.play(p, o, sq)
        black_to_move = not black_to_move
    s = O.final_score(p, o)
    if not black_to_move:
        s = -s
    return (1.0 if s > 0 else 0.0 if s < 0 else 0.5), False


def run_sprt(base, new, bench, seed, tc, tc_new, elo0, elo1, alpha, beta,
             max_pairs, workers, max_seconds):
    rng = random.Random(seed)
    openings = [r[0] for r in read_boards(os.path.join(bench, "openings.txt"))]
    rng.shuffle(openings)
    counts = [0, 0, 0, 0, 0]
    errors = {"new": 0, "base": 0}
    lock = threading.Lock()
    stop = threading.Event()
    jobs = queue.Queue()
    for i in range(max_pairs):
        jobs.put(openings[i % len(openings)])
    decision = {"value": None}
    t0 = time.time()

    def worker():
        e_new, e_base = EngineProc(new), EngineProc(base)
        try:
            while not stop.is_set():
                try:
                    opening = jobs.get_nowait()
                except queue.Empty:
                    return
                g1, err1 = play_game(e_new, e_base, opening, tc_new, tc)      # new is black
                g2, err2 = play_game(e_base, e_new, opening, tc, tc_new)      # new is white
                score = g1 + (1.0 - g2)
                with lock:
                    if err1:
                        errors["new" if g1 == 0.0 else "base"] += 1
                    if err2:
                        errors["base" if g2 == 0.0 else "new"] += 1
                    counts[int(round(score * 2))] += 1
                    d = sprt.decide(counts, elo0, elo1, alpha, beta)
                    if d or time.time() - t0 > max_seconds:
                        decision["value"] = d
                        stop.set()
        finally:
            e_new.close()
            e_base.close()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elo, (lo, hi) = sprt.elo_estimate(counts)
    return {"pentanomial": counts, "pairs": sum(counts),
            "llr": round(sprt.llr(counts, elo0, elo1), 3),
            "bounds": [round(x, 3) for x in sprt.bounds(alpha, beta)],
            "elo": round(elo, 1), "elo_95": [round(lo, 1), round(hi, 1)],
            "decision": decision["value"] or "hold", "errors": errors,
            "seconds": round(time.time() - t0, 1),
            "settings": {"elo0": elo0, "elo1": elo1, "alpha": alpha, "beta": beta,
                         "tc": tc, "tc_new": tc_new, "max_pairs": max_pairs}}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="current engine binary")
    ap.add_argument("--new", required=True, help="candidate engine binary")
    ap.add_argument("--bench", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--tc", default="movetime:10")
    ap.add_argument("--tc-new", default=None, help="(self-test only) different tc for new")
    ap.add_argument("--elo0", type=float, default=0.0)
    ap.add_argument("--elo1", type=float, default=10.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--beta", type=float, default=0.05)
    ap.add_argument("--max-pairs", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--max-seconds", type=float, default=4 * 3600)
    ap.add_argument("--valgrind", default=shutil.which("valgrind"))
    ap.add_argument("--force-sprt", action="store_true", help="(self-test only)")
    ap.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()

    report = {"seed": args.seed, "result": None, "gate": None}

    def finish(result, gate, reason):
        report.update(result=result, gate=gate, reason=reason)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report, indent=2))
        sys.exit(0 if result == "accepted" else 1)

    try:
        eg = check_endgame(args.new, args.bench, args.timeout)
        report["endgame"] = eg
        if eg["failed"]:
            finish("rejected", "endgame", f"{eg['failed']} exact endgame scores wrong")

        fixed = [r[0] for r in read_boards(os.path.join(args.bench, "positions", "midgame.txt"))]
        boards = fixed + generated_positions(args.bench, args.seed)
        ident = check_identical(args.base, args.new, boards, args.depth, args.timeout)
        report["identical"] = ident
        if ident["illegal"]:
            finish("rejected", "identical", "new engine returned illegal moves")

        if ident["identical"] and not args.force_sprt:
            if not args.valgrind:
                finish("error", "speed", "valgrind not found")
            sp = check_speed(args.base, args.new, args.bench, args.depth,
                             args.valgrind, args.timeout)
            report["speed"] = sp
            if sp["accepted"]:
                finish("accepted", "speed", "same answers, fewer instructions")
            finish("rejected", "speed", "same answers but not cheaper")

        st = run_sprt(args.base, args.new, args.bench, args.seed, args.tc,
                      args.tc_new or args.tc, args.elo0, args.elo1, args.alpha, args.beta,
                      args.max_pairs, args.workers, args.max_seconds)
        report["strength"] = st
        if st["decision"] == "accept":
            finish("accepted", "strength", f"stronger: {st['elo']} Elo")
        if st["decision"] == "reject":
            finish("rejected", "strength", f"not stronger: {st['elo']} Elo")
        finish("hold", "strength", "SPRT did not decide within the limits")
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as e:
        finish("error", report.get("gate"), str(e)[:500])


if __name__ == "__main__":
    main()
