//! Midgame search: negamax alpha-beta with iterative deepening and time control.
//!
//! Semantics (fixed by the protected reference tests):
//! - depth counts moves; a pass does not consume depth;
//! - depth 0 returns [`evaluate`]; a finished game returns [`terminal_value`];
//! - the root value at a fixed depth equals plain minimax at that depth.

use crate::board::Board;
use crate::eval::{evaluate, terminal_value, WIN_BONUS};
use crate::solve::Solver;
use std::time::Instant;

/// Larger than any reachable score.
pub const INF: i32 = 1_000_000;

/// In [`Searcher::search`], positions with at most this many empties are solved
/// exactly with the endgame [`Solver`], whatever the depth limit.
pub const EXACT_EMPTIES: u32 = 10;

/// Result of a root search.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SearchResult {
    /// Value from the side to move's point of view.
    pub value: i32,
    /// Best move, or `None` when the side to move must pass or the game is over.
    pub best: Option<u8>,
    /// Depth of the last completed iteration.
    pub depth: u32,
    /// Nodes visited (all iterations).
    pub nodes: u64,
}

/// Limits for one search. Unset limits mean "no limit".
#[derive(Clone, Copy, Debug, Default)]
pub struct Limits {
    pub max_depth: Option<u32>,
    pub deadline: Option<Instant>,
    pub max_nodes: Option<u64>,
}

/// Search state. Create a fresh one per root position in judge mode.
#[derive(Debug, Default)]
pub struct Searcher {
    pub nodes: u64,
    limits: Limits,
    stopped: bool,
}

impl Searcher {
    pub fn new() -> Self {
        Self::default()
    }

    #[inline]
    fn should_stop(&mut self) -> bool {
        if self.stopped {
            return true;
        }
        if let Some(n) = self.limits.max_nodes {
            if self.nodes >= n {
                self.stopped = true;
                return true;
            }
        }
        if self.nodes & 1023 == 0 {
            if let Some(d) = self.limits.deadline {
                if Instant::now() >= d {
                    self.stopped = true;
                }
            }
        }
        self.stopped
    }

    /// Negamax alpha-beta (fail-soft). Returns a value whose meaning follows the
    /// usual bound rules: `<= alpha` is an upper bound, `>= beta` a lower bound.
    pub fn alphabeta(&mut self, b: &Board, depth: u32, mut alpha: i32, beta: i32) -> i32 {
        self.nodes += 1;
        if self.should_stop() {
            return 0;
        }
        let moves = b.legal_moves();
        if moves == 0 {
            let passed = b.pass();
            if passed.legal_moves() == 0 {
                return terminal_value(b);
            }
            return -self.alphabeta(&passed, depth, -beta, -alpha);
        }
        if depth == 0 {
            return evaluate(b);
        }
        let mut best = -INF;
        let mut m = moves;
        while m != 0 {
            let sq = m.trailing_zeros() as u8;
            m &= m - 1;
            let v = -self.alphabeta(&b.play(sq), depth - 1, -beta, -alpha);
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
        best
    }

    /// One fixed-depth root search with a full window.
    /// Ties keep the first move in move order. Returns `None` if stopped.
    fn root(&mut self, b: &Board, depth: u32) -> Option<(i32, Option<u8>)> {
        let moves = b.legal_moves();
        if moves == 0 {
            let v = self.alphabeta(b, depth, -INF, INF);
            return (!self.stopped).then_some((v, None));
        }
        let mut alpha = -INF;
        let mut best_move = None;
        let mut m = moves;
        while m != 0 {
            let sq = m.trailing_zeros() as u8;
            m &= m - 1;
            let v = -self.alphabeta(&b.play(sq), depth - 1, -INF, -alpha);
            if self.stopped {
                return None;
            }
            if v > alpha || best_move.is_none() {
                alpha = v;
                best_move = Some(sq);
            }
        }
        Some((alpha, best_move))
    }

    /// Iterative deepening under `limits` (the way the engine plays). Always
    /// completes depth 1 so a move is returned even with a tiny budget. Near the
    /// end of the game it searches to the end (see [`EXACT_EMPTIES`]).
    pub fn search(&mut self, b: &Board, limits: Limits) -> SearchResult {
        self.nodes = 0;
        self.stopped = false;
        let empties = b.empties().count_ones();
        if empties <= EXACT_EMPTIES {
            let mut solver = Solver::new();
            let r = solver.solve(b);
            self.nodes = solver.nodes;
            return SearchResult {
                value: r.score * 100 + r.score.signum() * WIN_BONUS,
                best: r.best,
                depth: empties,
                nodes: solver.nodes,
            };
        }
        let max_depth = limits.max_depth.unwrap_or(empties).max(1);

        self.limits = Limits::default();
        let (v, m) = self.root(b, 1).expect("unlimited search cannot stop");
        let mut result = SearchResult {
            value: v,
            best: m,
            depth: 1,
            nodes: self.nodes,
        };
        self.limits = limits;
        // Past the number of empties the search is already exact.
        for depth in 2..=max_depth.min(empties) {
            match self.root(b, depth) {
                Some((v, m)) => {
                    result = SearchResult {
                        value: v,
                        best: m,
                        depth,
                        nodes: self.nodes,
                    };
                }
                None => break,
            }
        }
        result.nodes = self.nodes;
        result
    }

    /// Fixed-depth search with no other limits (judge mode).
    pub fn search_depth(&mut self, b: &Board, depth: u32) -> SearchResult {
        self.nodes = 0;
        self.stopped = false;
        self.limits = Limits::default();
        let (value, best) = self
            .root(b, depth.max(1))
            .expect("unlimited search cannot stop");
        SearchResult {
            value,
            best,
            depth: depth.max(1),
            nodes: self.nodes,
        }
    }

    /// Value of playing `sq` at the root, searched to `depth` (judge mode).
    /// Returns `None` if the move is illegal.
    pub fn move_value(&mut self, b: &Board, sq: u8, depth: u32) -> Option<i32> {
        let next = b.try_play(sq)?;
        self.nodes = 0;
        self.stopped = false;
        self.limits = Limits::default();
        Some(-self.alphabeta(&next, depth.max(1) - 1, -INF, INF))
    }
}
