//! Static evaluation (hand-written, integer only).
//!
//! Units: roughly 100 = one disc. Terminal positions are scored by
//! [`terminal_value`], which always dominates any static evaluation.

use crate::board::Board;

/// Largest magnitude [`evaluate`] may return.
pub const EVAL_LIMIT: i32 = 9_999;
/// Added to the magnitude of every decided terminal score so wins beat any evaluation.
pub const WIN_BONUS: i32 = 10_000;

/// Classic square weights, row by row from A1.
#[rustfmt::skip]
const WEIGHTS: [i32; 64] = [
    100, -20,  10,   5,   5,  10, -20, 100,
    -20, -50,  -2,  -2,  -2,  -2, -50, -20,
     10,  -2,  -1,  -1,  -1,  -1,  -2,  10,
      5,  -2,  -1,  -1,  -1,  -1,  -2,   5,
      5,  -2,  -1,  -1,  -1,  -1,  -2,   5,
     10,  -2,  -1,  -1,  -1,  -1,  -2,  10,
    -20, -50,  -2,  -2,  -2,  -2, -50, -20,
    100, -20,  10,   5,   5,  10, -20, 100,
];

fn weighted(bits: u64) -> i32 {
    let mut b = bits;
    let mut s = 0;
    while b != 0 {
        s += WEIGHTS[b.trailing_zeros() as usize];
        b &= b - 1;
    }
    s
}

/// Static evaluation from the side to move's point of view.
pub fn evaluate(b: &Board) -> i32 {
    let positional = weighted(b.player) - weighted(b.opponent);
    let mobility = b.legal_moves().count_ones() as i32 - b.pass().legal_moves().count_ones() as i32;
    (positional + 15 * mobility).clamp(-EVAL_LIMIT, EVAL_LIMIT)
}

/// Value of a finished game from the side to move's point of view.
pub fn terminal_value(b: &Board) -> i32 {
    let s = b.final_score();
    s * 100 + s.signum() * WIN_BONUS
}
