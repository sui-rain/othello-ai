//! Static evaluation: pattern tables learned from self-play plus mobility
//! (integer only; see `examples/train_eval.rs` for how the weights are made).
//!
//! Units: roughly 100 = one disc. Terminal positions are scored by
//! [`terminal_value`], which always dominates any static evaluation.

use crate::board::Board;
use crate::eval_weights::{MOBILITY_WEIGHTS, PATTERN_WEIGHTS};

/// Largest magnitude [`evaluate`] may return.
pub const EVAL_LIMIT: i32 = 9_999;
/// Added to the magnitude of every decided terminal score so wins beat any evaluation.
pub const WIN_BONUS: i32 = 10_000;

/// Number of game stages with their own weights (chosen by empty squares).
pub const STAGES: usize = 3;
/// Pattern tables per stage: the four outer rows (edge, second, third, fourth
/// line) and the two long diagonals.
pub const TABLES: usize = 5;
/// Entries per table: every configuration of 8 squares (empty / ours / theirs).
pub const TABLE_LEN: usize = 6561;
/// Table lookups per position: 4 lines for each of the 4 row tables, 2 diagonals.
pub const LINES: usize = 18;

/// Base-3 value of a byte read as 8 squares (bit i is worth 3^i).
const fn ternary_table() -> [u16; 256] {
    let mut t = [0u16; 256];
    let mut b = 0;
    while b < 256 {
        let mut v = 0u16;
        let mut pow = 1u16;
        let mut i = 0;
        while i < 8 {
            if b & (1 << i) != 0 {
                v += pow;
            }
            pow *= 3;
            i += 1;
        }
        t[b] = v;
        b += 1;
    }
    t
}

static TERNARY: [u16; 256] = ternary_table();

/// Mirror the board across the A1-H8 diagonal (rows become columns).
#[inline(always)]
fn transpose(mut x: u64) -> u64 {
    let t = (x ^ (x >> 7)) & 0x00aa_00aa_00aa_00aa;
    x ^= t ^ (t << 7);
    let t = (x ^ (x >> 14)) & 0x0000_cccc_0000_cccc;
    x ^= t ^ (t << 14);
    let t = (x ^ (x >> 28)) & 0x0000_0000_f0f0_f0f0;
    x ^ t ^ (t << 28)
}

#[inline(always)]
fn config(player: u8, opponent: u8) -> usize {
    TERNARY[player as usize] as usize + 2 * TERNARY[opponent as usize] as usize
}

/// Game stage of a position: 0 = opening, 2 = late middle game.
#[inline]
pub fn stage(b: &Board) -> usize {
    let empties = b.empties().count_ones();
    if empties >= 40 {
        0
    } else if empties >= 24 {
        1
    } else {
        2
    }
}

/// Indices into one stage's block of [`PATTERN_WEIGHTS`] (`TABLES * TABLE_LEN`
/// entries) for every pattern instance on the board.
#[inline]
pub fn pattern_indices(b: &Board) -> [usize; LINES] {
    let (p, o) = (b.player, b.opponent);
    let (tp, to) = (transpose(p), transpose(o));
    let mut out = [0usize; LINES];
    for t in 0..4 {
        let near = 8 * t;
        let far = 56 - 8 * t;
        let n = 4 * t;
        out[n] = t * TABLE_LEN + config((p >> near) as u8, (o >> near) as u8);
        out[n + 1] = t * TABLE_LEN + config((p >> far) as u8, (o >> far) as u8);
        out[n + 2] = t * TABLE_LEN + config((tp >> near) as u8, (to >> near) as u8);
        out[n + 3] = t * TABLE_LEN + config((tp >> far) as u8, (to >> far) as u8);
    }
    const MAIN: u64 = 0x8040_2010_0804_0201;
    const ANTI: u64 = 0x0102_0408_1020_4080;
    const SPREAD: u64 = 0x0101_0101_0101_0101;
    let diag = |mask: u64| {
        let pb = ((p & mask).wrapping_mul(SPREAD) >> 56) as u8;
        let ob = ((o & mask).wrapping_mul(SPREAD) >> 56) as u8;
        4 * TABLE_LEN + config(pb, ob)
    };
    out[16] = diag(MAIN);
    out[17] = diag(ANTI);
    out
}

/// Static evaluation from the side to move's point of view.
pub fn evaluate(b: &Board) -> i32 {
    let block = stage(b) * TABLES * TABLE_LEN;
    let mut sum = 0i32;
    for i in pattern_indices(b) {
        sum += PATTERN_WEIGHTS[block + i] as i32;
    }
    let mobility = b.legal_moves().count_ones() as i32 - b.pass().legal_moves().count_ones() as i32;
    (sum + MOBILITY_WEIGHTS[stage(b)] * mobility).clamp(-EVAL_LIMIT, EVAL_LIMIT)
}

/// Value of a finished game from the side to move's point of view.
pub fn terminal_value(b: &Board) -> i32 {
    let s = b.final_score();
    s * 100 + s.signum() * WIN_BONUS
}
