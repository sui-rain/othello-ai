//! Othello engine core.

pub mod board;
pub mod eval;
mod eval_weights;
pub mod game;
pub mod perft;
pub mod rng;
pub mod search;
pub mod solve;

pub use board::Board;
