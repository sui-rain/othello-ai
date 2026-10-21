//! Exact endgame solver: final disc difference with perfect play.

use crate::board::Board;

/// Result of an exact solve.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SolveResult {
    /// Final score (empties to the winner) from the side to move's point of view.
    pub score: i32,
    /// A best move, or `None` if the side to move must pass or the game is over.
    pub best: Option<u8>,
    pub nodes: u64,
}

/// Exact solver with fastest-first move ordering.
#[derive(Debug, Default)]
pub struct Solver {
    pub nodes: u64,
}

/// Below this many empties, moves are tried in square order (ordering costs more than it saves).
const ORDER_MIN_EMPTIES: u32 = 7;

impl Solver {
    pub fn new() -> Self {
        Self::default()
    }

    /// Fail-soft negamax on the final score.
    pub fn negamax(&mut self, b: &Board, mut alpha: i32, beta: i32) -> i32 {
        self.nodes += 1;
        let moves = b.legal_moves();
        if moves == 0 {
            let passed = b.pass();
            if passed.legal_moves() == 0 {
                return b.final_score();
            }
            return -self.negamax(&passed, -beta, -alpha);
        }
        let empties = b.empties().count_ones();
        let mut best = -65;
        if empties >= ORDER_MIN_EMPTIES {
            // Fastest first: try moves that leave the opponent the fewest replies.
            // At most 33 legal moves are possible in any reachable position.
            let mut list = [(0u32, 0u8, Board::initial()); 34];
            let mut n = 0;
            let mut m = moves;
            while m != 0 {
                let sq = m.trailing_zeros() as u8;
                m &= m - 1;
                let next = b.play(sq);
                list[n] = (next.legal_moves().count_ones(), sq, next);
                n += 1;
            }
            let list = &mut list[..n];
            list.sort_unstable_by_key(|&(k, sq, _)| (k, sq));
            for &(_, _, next) in list.iter() {
                let v = -self.negamax(&next, -beta, -alpha);
                if v > best {
                    best = v;
                    if v > alpha {
                        alpha = v;
                        if alpha >= beta {
                            break;
                        }
                    }
                }
            }
        } else {
            let mut m = moves;
            while m != 0 {
                let sq = m.trailing_zeros() as u8;
                m &= m - 1;
                let v = -self.negamax(&b.play(sq), -beta, -alpha);
                if v > best {
                    best = v;
                    if v > alpha {
                        alpha = v;
                        if alpha >= beta {
                            break;
                        }
                    }
                }
            }
        }
        best
    }

    /// Solve the position exactly.
    pub fn solve(&mut self, b: &Board) -> SolveResult {
        self.nodes = 0;
        let moves = b.legal_moves();
        if moves == 0 {
            let score = self.negamax(b, -65, 65);
            return SolveResult {
                score,
                best: None,
                nodes: self.nodes,
            };
        }
        let mut alpha = -65;
        let mut best = None;
        let mut m = moves;
        while m != 0 {
            let sq = m.trailing_zeros() as u8;
            m &= m - 1;
            let v = -self.negamax(&b.play(sq), -65, -alpha);
            if v > alpha || best.is_none() {
                alpha = v;
                best = Some(sq);
            }
        }
        SolveResult {
            score: alpha,
            best,
            nodes: self.nodes,
        }
    }
}
