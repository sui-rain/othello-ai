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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ten_thousand_random_games_terminate_consistently() {
        let mut rng = XorShift64::new(2026);
        let mut black_wins = 0;
        for _ in 0..10_000 {
            let r = random_game(&mut rng);
            let b = r.board;
            assert_eq!(b.player & b.opponent, 0);
            assert!(b.is_game_over());
            let discs = (b.player | b.opponent).count_ones();
            assert!((5..=64).contains(&discs));
            // Each move adds exactly one disc; passes add none.
            assert!(r.plies >= discs - 4);
            assert!(r.black_score.abs() <= 64);
            if r.black_score > 0 {
                black_wins += 1;
            }
        }
        // Random play is roughly balanced; this only guards against gross bugs.
        assert!(
            (3000..7000).contains(&black_wins),
            "black wins: {black_wins}"
        );
    }
}
