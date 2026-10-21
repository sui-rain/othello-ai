//! Perft: count the leaf nodes of the game tree to a fixed depth.
//!
//! Counting rule (requirements M1, matches OEIS A124004):
//! a pass is one ply, and a line where the game ends before `depth` contributes 0.

use crate::board::Board;

pub fn perft(b: &Board, depth: u32) -> u64 {
    if depth == 0 {
        return 1;
    }
    let moves = b.legal_moves();
    if moves == 0 {
        let passed = b.pass();
        if passed.legal_moves() == 0 {
            return 0; // game over before reaching the requested depth
        }
        return perft(&passed, depth - 1);
    }
    if depth == 1 {
        return moves.count_ones() as u64;
    }
    let mut total = 0;
    let mut m = moves;
    while m != 0 {
        let sq = m.trailing_zeros() as u8;
        m &= m - 1;
        total += perft(&b.play(sq), depth - 1);
    }
    total
}

/// Known perft values from the initial position, index = depth.
pub const INITIAL_PERFT: [u64; 12] = [
    1, 4, 12, 56, 244, 1396, 8200, 55092, 390216, 3005288, 24571056, 212258216,
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initial_position_to_depth_9() {
        let b = Board::initial();
        for (depth, &expected) in INITIAL_PERFT.iter().enumerate().take(10) {
            assert_eq!(perft(&b, depth as u32), expected, "depth {depth}");
        }
    }

    /// Depths 10 and 11 are the first that detect pass-handling mistakes.
    /// Slow in debug builds; CI runs it with `--release -- --ignored`.
    #[test]
    #[ignore]
    fn initial_position_depth_10_and_11() {
        let b = Board::initial();
        assert_eq!(perft(&b, 10), INITIAL_PERFT[10]);
        assert_eq!(perft(&b, 11), INITIAL_PERFT[11]);
    }
}
