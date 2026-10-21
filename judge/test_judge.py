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


if __name__ == "__main__":
    unittest.main()
