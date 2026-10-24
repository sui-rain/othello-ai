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

/// At most this many empties are handled by the dedicated [`Solver::solve_few`] routine.
const FEW_EMPTIES: u32 = 5;

/// Final score from disc counts (empty squares go to the winner).
#[inline(always)]
fn final_diff(p: i32, o: i32) -> i32 {
    let e = 64 - p - o;
    match p.cmp(&o) {
        std::cmp::Ordering::Greater => p - o + e,
        std::cmp::Ordering::Less => p - o - e,
        std::cmp::Ordering::Equal => 0,
    }
}

/// True if the side holding `player` can play on any of `empties`.
#[inline]
fn has_move(b: &Board, mut empties: u64) -> bool {
    while empties != 0 {
        let sq = empties.trailing_zeros() as u8;
        empties &= empties - 1;
        if b.flips(sq) != 0 {
            return true;
        }
    }
    false
}

impl Solver {
    pub fn new() -> Self {
        Self::default()
    }

    /// Last few empties: no ordering and no separate move-generation pass; each
    /// empty square is tried directly by its flips. Same fail-soft value as
    /// the general path.
    fn solve_few(
        &mut self,
        player: u64,
        opponent: u64,
        empties: u64,
        mut alpha: i32,
        beta: i32,
    ) -> i32 {
        self.nodes += 1;
        let b = Board { player, opponent };
        let p_count = player.count_ones() as i32;
        let o_count = opponent.count_ones() as i32;
        if empties.count_ones() == 1 {
            // One square left: whoever can play it does, and nothing follows.
            let sq = empties.trailing_zeros() as u8;
            let f = b.flips(sq);
            if f != 0 {
                let n = f.count_ones() as i32;
                return final_diff(p_count + 1 + n, o_count - n);
            }
            let f = b.pass().flips(sq);
            if f != 0 {
                let n = f.count_ones() as i32;
                return final_diff(p_count - n, o_count + 1 + n);
            }
            return final_diff(p_count, o_count);
        }
        let mut best = -65;
        let mut any = false;
        let mut m = empties;
        while m != 0 {
            let sq = m.trailing_zeros() as u8;
            m &= m - 1;
            let f = b.flips(sq);
            if f == 0 {
                continue;
            }
            any = true;
            let v = -self.solve_few(
                opponent ^ f,
                player | (1u64 << sq) | f,
                empties & !(1u64 << sq),
                -beta,
                -alpha,
            );
            if v > best {
                best = v;
                if v > alpha {
                    alpha = v;
                    if alpha >= beta {
                        return best;
                    }
                }
            }
        }
        if any {
            return best;
        }
        // No move: the opponent plays, or the game is over.
        if has_move(&b.pass(), empties) {
            -self.solve_few(opponent, player, empties, -beta, -alpha)
        } else {
            final_diff(p_count, o_count)
        }
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
        if empties <= FEW_EMPTIES {
            // `moves != 0` here, so this never starts from a finished game.
            return self.solve_few(b.player, b.opponent, b.empties(), alpha, beta);
        }
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
