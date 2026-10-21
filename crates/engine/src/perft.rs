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
