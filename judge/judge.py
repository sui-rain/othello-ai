#!/usr/bin/env python3
"""Decide whether a candidate engine may replace the current one.

Stages (CI decides which gate applies; the change's author does not):
  1. endgame   - exact scores and optimal moves on the fixed endgame set and on
                 seeded endgames solved by the current engine
  2. identical - same root values as the current engine (fixed-depth search on
                 fixed + seeded positions, and the play path `go ... depth`),
                 plus a time-limit smoke check
  3a. speed    - if identical: fewer instructions (callgrind, children traced)
  3b. strength - if not identical: SPRT match against the current engine

The candidate is untrusted: CI runs it as a separate user (--new-prefix) that
cannot write the workspace, and the judge verifies its own inputs did not change.
Writes a JSON report to --out and exits 0 only if the change is accepted.
Python stdlib only.
"""

import argparse
import hashlib
import json
import os
import queue
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import othello as O  # noqa: E402
import sprt  # noqa: E402

# Speed gate: some part at least 1% cheaper, no part more than 0.1% dearer.
SPEED_GAIN = 0.99
SPEED_TOLERANCE = 1.001
EXACT_EMPTIES = 10        # must match the engine's play-path exact region
WIN_BONUS = 10_000        # must match eval::terminal_value
PLAY_DEPTH = 4


class Engine:
    """How to run one engine binary (prefix = e.g. sudo -u sandbox --)."""

    def __init__(self, path, prefix=(), cleanup=()):
        self.path, self.prefix, self.cleanup_cmd = path, list(prefix), list(cleanup)

    def cmd(self, *args):
        return self.prefix + [self.path] + list(args)

    def cleanup(self):
        if self.cleanup_cmd:
            subprocess.run(self.cleanup_cmd, capture_output=True)


def read_boards(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.split())
    return out


def run_lines(engine, args, boards, timeout):
    """Run a batch command with boards on stdin; return parsed JSON lines."""
    try:
        res = subprocess.run(engine.cmd(*args), input="\n".join(boards) + "\n",
                             capture_output=True, text=True, timeout=timeout)
    finally:
        engine.cleanup()
    if res.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {res.stderr.strip()[:300]}")
    lines = []
    for l in res.stdout.splitlines():
        if l.strip():
            v = json.loads(l)
            if not isinstance(v, dict):
                raise RuntimeError("engine output is not a JSON object")
            lines.append(v)
    if len(lines) != len(boards):
        raise RuntimeError(f"expected {len(boards)} results, got {len(lines)}")
    return lines


def engine_session(engine, commands, timeout):
    """Send `go` lines to `engine` mode; return reply lines."""
    try:
        res = subprocess.run(engine.cmd("engine"), input="\n".join(commands) + "\nquit\n",
                             capture_output=True, text=True, timeout=timeout)
    finally:
        engine.cleanup()
    lines = [l for l in res.stdout.splitlines() if l.strip()]
    if len(lines) != len(commands):
        raise RuntimeError(f"engine mode: expected {len(commands)} replies, got {len(lines)}")
    return lines


def parse_move(board, move):
    """Square, None (pass) or raise if `move` is not legal on `board`."""
    p, o = O.parse(board)
    legal = O.legal_moves(p, o)
    if not isinstance(move, str):
        raise ValueError("missing move")
    sq = None if move == "pass" else O.parse_square(move)
    if (sq is None and legal) or (sq is not None and sq not in legal):
        raise ValueError(f"illegal move {move}")
    return sq


def child_board(board, sq):
    p, o = O.parse(board)
    return O.to_compact(*O.play(p, o, sq))


def file_digest(paths):
    h = hashlib.sha256()
    for path in paths:
        for root, _dirs, files in sorted(os.walk(path)) if os.path.isdir(path) else [("", [], [path])]:
            for name in sorted(files):
                fp = os.path.join(root, name)
                if "__pycache__" in fp:
                    continue
                h.update(fp.encode())
                with open(fp, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()


# ---------------------------------------------------------------- positions

def seeded_positions(bench, seed, count, min_empties=0, max_empties=60, tag=""):
    """Positions reached by random play from random openings (seeded per run)."""
    rng = random.Random(f"{seed}:{tag}")
    openings = [O.parse(r[0]) for r in read_boards(os.path.join(bench, "openings.txt"))]
    out = []
    tries = 0
    while len(out) < count and tries < count * 200:
        tries += 1
        p, o = rng.choice(openings)
        target = rng.randint(min_empties, max_empties)
        while 64 - bin(p | o).count("1") > target:
            moves = O.legal_moves(p, o)
            if not moves:
                if not O.legal_moves(o, p):
                    break
                p, o = o, p
                continue
            p, o = O.play(p, o, rng.choice(moves))
        e = 64 - bin(p | o).count("1")
        if min_empties <= e <= max_empties and not O.is_game_over(p, o) and O.legal_moves(p, o):
            out.append(O.to_compact(p, o))
    return out


# ---------------------------------------------------------------- stage 1

def verify_solve_moves(base, boards, results, timeout):
    """Each reported move must reach the reported score (checked by base's solver)."""
    children, idx = [], []
    for i, (b, r) in enumerate(zip(boards, results)):
        sq = parse_move(b, r.get("move"))
        if sq is not None:
            children.append(child_board(b, sq))
            idx.append(i)
    bad = []
    if children:
        ref = run_lines(base, ["solve"], children, timeout)
        for i, c in zip(idx, ref):
            if -c.get("score", 999) != results[i].get("score"):
                bad.append({"board": boards[i], "move": results[i].get("move"),
                            "move_is_worth": -c.get("score", 999)})
    return bad


def check_endgame(base, new, bench, seed, timeout):
    rows = read_boards(os.path.join(bench, "endgame", "exact.txt"))
    fixed = [r[0] for r in rows]
    expected = [int(r[1]) for r in rows]
    seeded = seeded_positions(bench, seed, 20, 12, 15, "endgame")
    t0 = time.time()
    got = run_lines(new, ["solve"], fixed + seeded, timeout)
    secs = time.time() - t0
    expected += [r.get("score") for r in run_lines(base, ["solve"], seeded, timeout)]
    boards = fixed + seeded
    failures = []
    for b, e, g in zip(boards, expected, got):
        try:
            parse_move(b, g.get("move"))
        except ValueError as ex:
            failures.append({"board": b, "error": str(ex)})
            continue
        if g.get("score") != e:
            failures.append({"board": b, "expected": e, "got": g.get("score")})
    if not failures:
        failures = verify_solve_moves(base, boards, got, timeout)
    return {"positions": len(boards), "failed": len(failures), "failures": failures[:5],
            "seconds": round(secs, 2)}


# ---------------------------------------------------------------- stage 2

def compare_depth_search(base, new, boards, depth, timeout):
    ref = run_lines(base, ["search", "--depth", str(depth)], boards, timeout)
    cand = run_lines(new, ["search", "--depth", str(depth)], boards, timeout)
    mismatches, illegal, verified = [], [], 0
    for board, r, c in zip(boards, ref, cand):
        try:
            sq = parse_move(board, c.get("move"))
        except ValueError as ex:
            illegal.append({"board": board, "error": str(ex)})
            continue
        if c.get("value") != r.get("value"):
            mismatches.append({"board": board, "base": r.get("value"), "new": c.get("value")})
        elif c.get("move") != r.get("move") and sq is not None:
            v = run_lines(base, ["search", "--depth", str(depth), "--move", c["move"]],
                          [board], timeout)[0]
            if v.get("value") != r.get("value"):
                mismatches.append({"board": board, "new_move": c["move"],
                                   "worth": v.get("value"), "base": r.get("value")})
            else:
                verified += 1
    return {"positions": len(boards), "mismatches": mismatches, "illegal": illegal,
            "verified_alternatives": verified,
            "nodes_base": sum(r.get("nodes", 0) for r in ref if isinstance(r.get("nodes"), int)),
            "nodes_new": sum(c.get("nodes", 0) for c in cand if isinstance(c.get("nodes"), int))}


def parse_reply(line):
    parts = line.split()
    if len(parts) < 4 or parts[0] != "move" or parts[2] != "value":
        raise ValueError(f"bad reply: {line[:80]}")
    return parts[1], int(parts[3])


def compare_play_path(base, new, boards, timeout):
    """`go <board> depth PLAY_DEPTH` (iterative deepening, exact near the end)."""
    cmds = [f"go {b} depth {PLAY_DEPTH}" for b in boards]
    ref = engine_session(base, cmds, timeout)
    cand = engine_session(new, cmds, timeout)
    mismatches, illegal = [], []
    to_verify = []
    for b, r, c in zip(boards, ref, cand):
        rm, rv = parse_reply(r)
        try:
            cm, cv = parse_reply(c)
            sq = parse_move(b, cm)
        except ValueError as ex:
            illegal.append({"board": b, "error": str(ex)})
            continue
        if cv != rv:
            mismatches.append({"board": b, "base": rv, "new": cv})
        elif cm != rm and sq is not None:
            to_verify.append((b, sq, cm, rv))
    for b, sq, cm, rv in to_verify:
        if b.count("-") <= EXACT_EMPTIES:
            s = -run_lines(base, ["solve"], [child_board(b, sq)], timeout)[0].get("score", 999)
            worth = s * 100 + (WIN_BONUS if s > 0 else -WIN_BONUS if s < 0 else 0)
        else:
            worth = run_lines(base, ["search", "--depth", str(PLAY_DEPTH), "--move", cm],
                              [b], timeout)[0].get("value")
        if worth != rv:
            mismatches.append({"board": b, "new_move": cm, "worth": worth, "base": rv})
    return {"positions": len(boards), "mismatches": mismatches, "illegal": illegal}


def check_timing(new, boards, ms=50):
    """The candidate must answer `movetime` searches on time."""
    slow = []
    for b in boards:
        t0 = time.time()
        line = engine_session(new, [f"go {b} movetime {ms}"], 30)[0]
        took = (time.time() - t0) * 1000
        parse_move(b, parse_reply(line)[0])
        if took > 2 * ms + 250:  # generous: includes process start-up
            slow.append({"board": b, "ms": round(took)})
    return {"positions": len(boards), "slow": slow}


# ---------------------------------------------------------------- stage 3a

def reclaim(engine, d):
    """Files written by a sandboxed engine belong to the sandbox user and are
    private to it; take them back so the judge can read and remove them."""
    if engine.prefix and engine.prefix[0] == "sudo":
        subprocess.run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", d],
                       capture_output=True)


def instructions(valgrind, engine, args, boards, timeout):
    d = tempfile.mkdtemp()
    os.chmod(d, 0o777)  # the sandbox user writes the profile here
    out = os.path.join(d, "cg.out")
    cmd = engine.prefix + [valgrind, "--tool=callgrind", "--trace-children=yes",
                           f"--callgrind-out-file={out}.%p", engine.path] + list(args)
    try:
        res = subprocess.run(cmd, input="\n".join(boards) + "\n", capture_output=True,
                             text=True, timeout=timeout)
        if res.returncode != 0:
            raise RuntimeError(f"valgrind failed: {res.stderr.strip()[-300:]}")
        reclaim(engine, d)
        total, found = 0, False
        for name in os.listdir(d):
            with open(os.path.join(d, name)) as f:
                for line in f:
                    m = re.match(r"(summary|totals):\s+(\d+)", line)
                    if m:
                        total += int(m.group(2))
                        found = True
                        break
        if not found:
            raise RuntimeError("no instruction total in callgrind output")
        return total
    finally:
        engine.cleanup()
        reclaim(engine, d)
        shutil.rmtree(d, ignore_errors=True)


def check_speed(base, new, bench, seed, depth, valgrind, timeout):
    mid = [r[0] for r in read_boards(os.path.join(bench, "positions", "midgame.txt"))][:40]
    mid += seeded_positions(bench, seed, 20, 14, 50, "speed-mid")
    end = [r[0] for r in read_boards(os.path.join(bench, "endgame", "exact.txt"))]
    end = sorted(end, key=lambda b: b.count("-"))[:30]
    end += seeded_positions(bench, seed, 10, 12, 14, "speed-end")
    parts = {"midgame_search": (["search", "--depth", str(depth)], mid),
             "endgame_solve": (["solve"], end)}
    result = {}
    for name, (args, boards) in parts.items():
        b = instructions(valgrind, base, args, boards, timeout)
        n = instructions(valgrind, new, args, boards, timeout)
        result[name] = {"base": b, "new": n, "ratio": round(n / b, 5)}
    ratios = [v["ratio"] for v in result.values()]
    return {"parts": result,
            "accepted": min(ratios) <= SPEED_GAIN and max(ratios) <= SPEED_TOLERANCE}


# ---------------------------------------------------------------- stage 3b

class EngineProc:
    def __init__(self, engine):
        self.engine = engine
        self.p = None
        self.start()

    def start(self):
        self.p = subprocess.Popen(self.engine.cmd("engine"), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def restart(self):
        self.close()
        self.start()

    def ask(self, board, tc, timeout=10.0):
        """(square | None for pass | 'error', milliseconds)."""
        kind, amount = tc.split(":")
        if self.p.poll() is not None:
            self.start()
        t0 = time.time()
        try:
            self.p.stdin.write(f"go {board} {kind} {amount}\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            self.restart()
            return "error", 0
        timer = threading.Timer(timeout, self.p.terminate)
        timer.start()
        line = self.p.stdout.readline()
        timer.cancel()
        ms = (time.time() - t0) * 1000
        try:
            return O.parse_square(parse_reply(line)[0]), ms
        except (ValueError, IndexError):
            self.restart()
            return "error", ms

    def close(self):
        try:
            if self.p.poll() is None:
                self.p.stdin.write("quit\n")
                self.p.stdin.flush()
                self.p.wait(timeout=2)
        except Exception:
            self.p.kill()


def time_allowed(tc):
    kind, amount = tc.split(":")
    return 2 * int(amount) + 50 if kind == "movetime" else None


def play_game(black, white, opening, tc_black, tc_white):
    """(score for black, faulty side or None). A faulty side loses the game."""
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
        sq, ms = eng.ask(O.to_compact(p, o), tc)
        limit = time_allowed(tc)
        if sq == "error" or sq not in moves or (limit is not None and ms > limit):
            return (0.0 if black_to_move else 1.0), ("black" if black_to_move else "white")
        p, o = O.play(p, o, sq)
        black_to_move = not black_to_move
    s = O.final_score(p, o)
    if not black_to_move:
        s = -s
    return (1.0 if s > 0 else 0.0 if s < 0 else 0.5), None


def run_sprt(base, new, bench, seed, tc, tc_new, elo0, elo1, alpha, beta,
             max_pairs, workers, max_seconds):
    rng = random.Random(f"{seed}:sprt")
    openings = [r[0] for r in read_boards(os.path.join(bench, "openings.txt"))]
    rng.shuffle(openings)
    counts = [0, 0, 0, 0, 0]
    faults = {"new": 0, "base": 0}
    lock = threading.Lock()
    stop = threading.Event()
    jobs = queue.Queue()
    for i in range(max_pairs):
        jobs.put(openings[i % len(openings)])
    state = {"decision": None, "reason": None}
    t0 = time.time()

    def worker():
        e_new, e_base = EngineProc(new), EngineProc(base)
        try:
            while not stop.is_set():
                try:
                    opening = jobs.get_nowait()
                except queue.Empty:
                    return
                g1, f1 = play_game(e_new, e_base, opening, tc_new, tc)   # new is black
                g2, f2 = play_game(e_base, e_new, opening, tc, tc_new)   # new is white
                with lock:
                    if stop.is_set():
                        return
                    for fault, new_side in ((f1, "black"), (f2, "white")):
                        if fault:
                            faults["new" if fault == new_side else "base"] += 1
                    counts[int(round((g1 + (1.0 - g2)) * 2))] += 1
                    if faults["base"]:
                        state.update(decision="error", reason="current engine misbehaved")
                    elif faults["new"]:
                        state.update(decision="reject",
                                     reason="new engine crashed, timed out or played illegally")
                    else:
                        d = sprt.decide(counts, elo0, elo1, alpha, beta)
                        if d:
                            state.update(decision=d)
                        elif time.time() - t0 > max_seconds:
                            state.update(decision="hold", reason="time limit")
                    if state["decision"]:
                        stop.set()
        finally:
            e_new.close()
            e_base.close()
            new.cleanup()

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
            "decision": state["decision"] or "hold", "reason": state["reason"],
            "faults": faults, "seconds": round(time.time() - t0, 1),
            "settings": {"elo0": elo0, "elo1": elo1, "alpha": alpha, "beta": beta,
                         "tc": tc, "tc_new": tc_new, "max_pairs": max_pairs}}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="current engine binary (trusted)")
    ap.add_argument("--new", required=True, help="candidate engine binary (untrusted)")
    ap.add_argument("--new-prefix", default="", help='e.g. "sudo -n -u othello-sandbox --"')
    ap.add_argument("--new-cleanup", default="", help='e.g. "sudo -n pkill -KILL -u othello-sandbox"')
    ap.add_argument("--bench", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--tc", default="movetime:10")
    ap.add_argument("--tc-new", default=None, help="(self-test only) different tc for new")
    ap.add_argument("--elo0", type=float, default=0.0)
    ap.add_argument("--elo1", type=float, default=10.0)
    ap.add_argument("--alpha", type=float, default=0.02)
    ap.add_argument("--beta", type=float, default=0.05)
    ap.add_argument("--max-pairs", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--max-seconds", type=float, default=4 * 3600)
    ap.add_argument("--valgrind", default=shutil.which("valgrind"))
    ap.add_argument("--force-sprt", action="store_true", help="(self-test only)")
    ap.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()

    base = Engine(os.path.abspath(args.base))
    new = Engine(os.path.abspath(args.new), shlex.split(args.new_prefix),
                 shlex.split(args.new_cleanup))
    judge_dir = os.path.dirname(os.path.abspath(__file__))
    watched = [os.path.abspath(args.base), os.path.abspath(args.bench), judge_dir]
    digest = file_digest(watched)
    report = {"seed": args.seed, "result": None, "gate": None}

    def finish(result, gate, reason):
        if file_digest(watched) != digest:
            result, reason = "error", "judge inputs changed during the run"
        report.update(result=result, gate=gate, reason=reason)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(json.dumps(report, indent=2))
        sys.exit(0 if result == "accepted" else 1)

    try:
        report["gate"] = "endgame"
        eg = check_endgame(base, new, args.bench, args.seed, args.timeout)
        report["endgame"] = eg
        if eg["failed"]:
            finish("rejected", "endgame", f"{eg['failed']} endgame answers wrong")

        report["gate"] = "identical"
        fixed = [r[0] for r in read_boards(os.path.join(args.bench, "positions", "midgame.txt"))]
        seeded = seeded_positions(args.bench, args.seed, 100, 10, 52, "identical")
        ds = compare_depth_search(base, new, fixed + seeded, args.depth, args.timeout)
        late = seeded_positions(args.bench, args.seed, 20, 6, EXACT_EMPTIES, "late")
        pp = compare_play_path(base, new, fixed[:60] + seeded[:40] + late, args.timeout)
        tm = check_timing(new, fixed[:5] + late[:5])
        report["identical"] = {"depth_search": {k: v for k, v in ds.items()
                                                if k not in ("mismatches", "illegal")},
                               "depth_mismatches": len(ds["mismatches"]),
                               "play_mismatches": len(pp["mismatches"]),
                               "examples": (ds["illegal"] + pp["illegal"] + ds["mismatches"]
                                            + pp["mismatches"])[:5],
                               "timing": tm}
        if ds["illegal"] or pp["illegal"]:
            finish("rejected", "identical", "new engine returned illegal moves")
        if tm["slow"]:
            finish("rejected", "identical", "new engine ignored the time limit")
        identical = not ds["mismatches"] and not pp["mismatches"]
        report["identical"]["identical"] = identical

        if identical and not args.force_sprt:
            report["gate"] = "speed"
            if not args.valgrind:
                finish("error", "speed", "valgrind not found")
            sp = check_speed(base, new, args.bench, args.seed, args.depth, args.valgrind,
                             args.timeout)
            report["speed"] = sp
            if sp["accepted"]:
                finish("accepted", "speed", "same answers, fewer instructions")
            finish("rejected", "speed", "same answers but not cheaper")

        report["gate"] = "strength"
        st = run_sprt(base, new, args.bench, args.seed, args.tc, args.tc_new or args.tc,
                      args.elo0, args.elo1, args.alpha, args.beta, args.max_pairs,
                      args.workers, args.max_seconds)
        report["strength"] = st
        d = st["decision"]
        if d == "accept":
            finish("accepted", "strength", f"stronger: {st['elo']:+} Elo")
        if d == "reject":
            finish("rejected", "strength", st["reason"] or f"not stronger: {st['elo']:+} Elo")
        if d == "error":
            finish("error", "strength", st["reason"])
        finish("hold", "strength", st["reason"] or "SPRT did not decide within the limits")
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError, KeyError,
            TypeError) as e:
        finish("error" if report.get("gate") != "endgame" else "rejected",
               report.get("gate"), f"{type(e).__name__}: {str(e)[:300]}")


if __name__ == "__main__":
    main()
