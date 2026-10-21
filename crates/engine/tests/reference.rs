//! Reference correctness tests (protected: unattended `auto/` changes may not edit this file).
//!
//! Expected values live here, not in the engine source, so a change to the engine
//! cannot also change what it is checked against. Only the public API is used.

use othello_engine::board::square_name;
use othello_engine::game::random_game;
use othello_engine::perft::perft;
use othello_engine::rng::XorShift64;
use othello_engine::Board;

/// Perft from the initial position, index = depth (OEIS A124004).
/// Counting rule: a pass is one ply; a line that ends before the depth counts 0.
const INITIAL_PERFT: [u64; 12] = [
    1, 4, 12, 56, 244, 1396, 8200, 55092, 390216, 3005288, 24571056, 212258216,
];

#[test]
fn perft_initial_to_depth_9() {
    let b = Board::initial();
    for (depth, &expected) in INITIAL_PERFT.iter().enumerate().take(10) {
        assert_eq!(perft(&b, depth as u32), expected, "depth {depth}");
    }
}

/// Depths 10 and 11 are the first that detect pass-handling mistakes.
/// CI runs this with `cargo test --release -- --ignored`.
#[test]
#[ignore]
fn perft_initial_depth_10_and_11() {
    let b = Board::initial();
    assert_eq!(perft(&b, 10), INITIAL_PERFT[10]);
    assert_eq!(perft(&b, 11), INITIAL_PERFT[11]);
}

#[test]
fn initial_legal_moves() {
    let b = Board::initial();
    let names: Vec<String> = (0..64u8)
        .filter(|&s| b.legal_moves() & (1 << s) != 0)
        .map(square_name)
        .collect();
    assert_eq!(names, ["d3", "c4", "f5", "e6"]);
}

/// Slow, obviously-correct flip computation on an 8x8 grid.
fn naive_flips(b: &Board, sq: u8) -> u64 {
    let m = 1u64 << sq;
    if (b.player | b.opponent) & m != 0 {
        return 0;
    }
    let (r0, c0) = ((sq / 8) as i32, (sq % 8) as i32);
    let mut flipped = 0;
    for dr in -1..=1 {
        for dc in -1..=1 {
            if dr == 0 && dc == 0 {
                continue;
            }
            let (mut r, mut c) = (r0 + dr, c0 + dc);
            let mut run = 0u64;
            while (0..8).contains(&r) && (0..8).contains(&c) {
                let bit = 1u64 << (r * 8 + c);
                if b.opponent & bit != 0 {
                    run |= bit;
                } else {
                    if b.player & bit != 0 {
                        flipped |= run;
                    }
                    break;
                }
                r += dr;
                c += dc;
            }
        }
    }
    flipped
}

#[test]
fn bitboard_matches_naive_generator() {
    let mut rng = XorShift64::new(12345);
    for _ in 0..3000 {
        let mut b = Board::initial();
        loop {
            let naive_moves = (0..64u8)
                .filter(|&sq| naive_flips(&b, sq) != 0)
                .fold(0u64, |acc, sq| acc | (1 << sq));
            assert_eq!(b.legal_moves(), naive_moves);
            for sq in 0..64u8 {
                assert_eq!(b.flips(sq), naive_flips(&b, sq));
            }
            let moves = b.legal_moves();
            if moves == 0 {
                if b.pass().legal_moves() == 0 {
                    break;
                }
                b = b.pass();
                continue;
            }
            // Pick a random legal move without relying on engine helpers.
            let list: Vec<u8> = (0..64u8).filter(|&s| moves & (1 << s) != 0).collect();
            let sq = list[(rng.next_u64() % list.len() as u64) as usize];
            let next = b.play(sq);
            let f = naive_flips(&b, sq);
            assert_eq!(next.opponent, b.player | (1 << sq) | f);
            assert_eq!(next.player, b.opponent & !f);
            b = next;
        }
    }
}

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
