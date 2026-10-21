//! Playing whole games.

use crate::board::Board;
use crate::rng::XorShift64;

/// Result of one finished game.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct GameResult {
    /// Final position.
    pub board: Board,
    /// Black's final score (disc difference with empties to the winner).
    pub black_score: i32,
    /// Plies played, counting passes.
    pub plies: u32,
}

/// Play one game where both sides pick uniformly random legal moves.
pub fn random_game(rng: &mut XorShift64) -> GameResult {
    let mut b = Board::initial();
    let mut black_to_move = true;
    let mut plies = 0;
    loop {
        let moves = b.legal_moves();
        if moves == 0 {
            if b.pass().legal_moves() == 0 {
                break;
            }
            b = b.pass();
        } else {
            b = b.play(rng.pick_bit(moves));
        }
        black_to_move = !black_to_move;
        plies += 1;
        // At most 60 moves plus passes; a pass can never follow a pass in a live game.
        assert!(plies <= 120, "game did not terminate");
    }
    let score = b.final_score();
    GameResult {
        board: b,
        black_score: if black_to_move { score } else { -score },
        plies,
    }
}
