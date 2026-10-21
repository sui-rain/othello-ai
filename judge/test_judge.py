"""Tests for the judge's own logic (python3 -m unittest discover judge)."""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import othello as O  # noqa: E402
import sprt  # noqa: E402


class RulesTest(unittest.TestCase):
    def test_initial_moves(self):
        moves = [O.square_name(s) for s in O.legal_moves(*O.INITIAL)]
        self.assertEqual(moves, ["d3", "c4", "f5", "e6"])

    def test_perft_depth_5(self):
        def perft(p, o, d):
            if d == 0:
                return 1
            moves = O.legal_moves(p, o)
            if not moves:
                if not O.legal_moves(o, p):
                    return 0
                return perft(o, p, d - 1)
            return sum(perft(*O.play(p, o, m), d - 1) for m in moves)

        self.assertEqual(perft(*O.INITIAL, 5), 1396)

    def test_compact_roundtrip_and_symmetry(self):
        b = O.INITIAL
        self.assertEqual(O.parse(O.to_compact(*b)), b)
        # The four first moves are symmetric to each other.
        children = {O.canonical(*O.play(*b, m)) for m in O.legal_moves(*b)}
        self.assertEqual(len(children), 1)


class SprtTest(unittest.TestCase):
    def simulate(self, p_win, pairs, seed):
        """Pentanomial counts for independent games won with probability p_win."""
        rng = random.Random(seed)
        counts = [0] * 5
        for _ in range(pairs):
            s = (rng.random() < p_win) + (rng.random() < p_win)
            counts[s * 2] += 1
        return counts

    def run_until_decision(self, p_win, seed, max_pairs=20000):
        rng = random.Random(seed)
        counts = [0] * 5
        for _ in range(max_pairs):
            s = (rng.random() < p_win) + (rng.random() < p_win)
            counts[s * 2] += 1
            d = sprt.decide(counts, 0, 10, 0.05, 0.05)
            if d:
                return d
        return None

    def test_clearly_stronger_is_accepted(self):
        p = sprt.elo_to_score(50)
        decisions = [self.run_until_decision(p, s) for s in range(20)]
        self.assertGreaterEqual(decisions.count("accept"), 19)

    def test_equal_is_rejected(self):
        decisions = [self.run_until_decision(0.5, s) for s in range(20)]
        self.assertGreaterEqual(decisions.count("reject"), 17)

    def test_weaker_is_rejected(self):
        p = sprt.elo_to_score(-30)
        decisions = [self.run_until_decision(p, s) for s in range(20)]
        self.assertEqual(decisions.count("reject"), 20)

    def test_needs_minimum_pairs(self):
        self.assertIsNone(sprt.decide([0, 0, 0, 0, 5], 0, 10, 0.05, 0.05))

    def test_elo_roundtrip(self):
        for e in (-200, -10, 0, 10, 200):
            self.assertAlmostEqual(sprt.score_to_elo(sprt.elo_to_score(e)), e, places=6)



import judge as J  # noqa: E402
import report as R  # noqa: E402

BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench")


class FakeProc:
    """Stands in for EngineProc: plays the first legal move, or misbehaves."""

    def __init__(self, mode="ok", ms=1.0):
        self.mode, self.ms = mode, ms

    def ask(self, board, tc):
        p, o = O.parse(board)
        moves = O.legal_moves(p, o)
        if self.mode == "illegal":
            return next(s for s in range(64) if s not in moves), self.ms
        if self.mode == "error":
            return "error", self.ms
        return moves[0], self.ms


class JudgeLogicTest(unittest.TestCase):
    opening = O.to_compact(*O.INITIAL)

    def test_faulty_side_loses(self):
        for mode in ("illegal", "error"):
            score, fault = J.play_game(FakeProc(mode), FakeProc(), self.opening,
                                       "movetime:10", "movetime:10")
            self.assertEqual((score, fault), (0.0, "black"))
            score, fault = J.play_game(FakeProc(), FakeProc(mode), self.opening,
                                       "movetime:10", "movetime:10")
            self.assertEqual(fault, "white")
            self.assertEqual(score, 1.0)

    def test_overrunning_the_clock_loses(self):
        score, fault = J.play_game(FakeProc(ms=500), FakeProc(), self.opening,
                                   "movetime:10", "movetime:10")
        self.assertEqual((score, fault), (0.0, "black"))
        # Depth limits have no clock.
        _score, fault = J.play_game(FakeProc(ms=500), FakeProc(), self.opening,
                                    "depth:3", "depth:3")
        self.assertIsNone(fault)

    def test_parse_move(self):
        b = self.opening
        self.assertEqual(J.parse_move(b, "d3"), O.parse_square("d3"))
        for bad in ("a1", "pass", None, 5):
            with self.assertRaises(ValueError):
                J.parse_move(b, bad)

    def test_seeded_positions_are_reproducible_and_in_range(self):
        a = J.seeded_positions(BENCH, 7, 15, 12, 15, "x")
        self.assertEqual(a, J.seeded_positions(BENCH, 7, 15, 12, 15, "x"))
        self.assertNotEqual(a, J.seeded_positions(BENCH, 8, 15, 12, 15, "x"))
        for b in a:
            self.assertTrue(12 <= b.count("-") <= 15)
            self.assertTrue(O.legal_moves(*O.parse(b)))


class ReportSanitizeTest(unittest.TestCase):
    def random_value(self, rng, depth=0):
        kinds = ["int", "float", "str", "none", "bool"] + (["list", "dict"] if depth < 4 else [])
        k = rng.choice(kinds)
        if k == "int":
            return rng.randint(-10**12, 10**12)
        if k == "float":
            return rng.uniform(-1e6, 1e6)
        if k == "str":
            return rng.choice(["accepted", "speed", "=cmd()", "<script>", "", "x" * 500])
        if k == "bool":
            return rng.random() < 0.5
        if k == "list":
            return [self.random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
        if k == "dict":
            keys = ["result", "gate", "reason", "speed", "parts", "midgame_search", "ratio",
                    "strength", "elo", "elo_95", "pairs", "endgame_solve"]
            return {rng.choice(keys): self.random_value(rng, depth + 1)
                    for _ in range(rng.randint(0, 5))}
        return None

    def test_untrusted_reports_never_crash(self):
        rng = random.Random(1)
        for _ in range(3000):
            row = {k: "" for k in R.HISTORY_FIELDS}
            row.update(R.sanitize(self.random_value(rng)))
            row["merged"] = rng.choice(["yes", "no"])
            for v in row.values():
                self.assertFalse(isinstance(v, str) and ("<" in v or v[:1] == "="))
            R.summary([row], {"ffo_solved": 1, "ffo_seconds": 9.5}, None)

    def test_formula_and_markup_are_neutralised(self):
        self.assertEqual(R.clean_text("=HYPERLINK(1)")[:1], "'")
        self.assertNotIn("<", R.clean_text("<b>x</b>"))


if __name__ == "__main__":
    unittest.main()
