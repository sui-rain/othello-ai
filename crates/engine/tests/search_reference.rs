//! Search and solver reference tests (protected).
//!
//! The engine's search is checked against slow, obviously-correct minimax written
//! here, using only the public evaluation functions. This pins the search
//! semantics: a pass does not consume depth; depth 0 = evaluate; finished game =
//! terminal_value.

use othello_engine::eval::{evaluate, terminal_value, EVAL_LIMIT};
use othello_engine::rng::XorShift64;
use othello_engine::search::{Limits, Searcher, INF};
use othello_engine::solve::Solver;
use othello_engine::Board;
use std::time::{Duration, Instant};

fn minimax(b: &Board, depth: u32) -> i32 {
    let moves = b.legal_moves();
    if moves == 0 {
        if b.pass().legal_moves() == 0 {
            return terminal_value(b);
        }
        return -minimax(&b.pass(), depth);
    }
    if depth == 0 {
        return evaluate(b);
    }
    (0..64u8)
        .filter(|&s| moves & (1 << s) != 0)
        .map(|s| -minimax(&b.play(s), depth - 1))
        .max()
        .unwrap()
}

fn exact(b: &Board) -> i32 {
    let moves = b.legal_moves();
    if moves == 0 {
        if b.pass().legal_moves() == 0 {
            return b.final_score();
        }
        return -exact(&b.pass());
    }
    (0..64u8)
        .filter(|&s| moves & (1 << s) != 0)
        .map(|s| -exact(&b.play(s)))
        .max()
        .unwrap()
}

/// Random position reached after `plies` random plies (passes included).
fn random_position(rng: &mut XorShift64, plies: u32) -> Board {
    let mut b = Board::initial();
    for _ in 0..plies {
        let moves = b.legal_moves();
        if moves == 0 {
            if b.pass().legal_moves() == 0 {
                break;
            }
            b = b.pass();
            continue;
        }
        let list: Vec<u8> = (0..64u8).filter(|&s| moves & (1 << s) != 0).collect();
        b = b.play(list[(rng.next_u64() % list.len() as u64) as usize]);
    }
    b
}

#[test]
fn fixed_depth_value_equals_minimax() {
    let mut rng = XorShift64::new(777);
    for i in 0..300 {
        let b = random_position(&mut rng, 4 + i % 50);
        for depth in 1..=3 {
            let expected = minimax(&b, depth);
            let mut s = Searcher::new();
            let r = s.search_depth(&b, depth);
            assert_eq!(r.value, expected, "{} depth {depth}", b.to_compact());
            if let Some(m) = r.best {
                assert_eq!(-minimax(&b.play(m), depth - 1), expected);
            }
            // Full-window alphabeta from the root agrees too.
            assert_eq!(s.alphabeta(&b, depth, -INF, INF), expected);
        }
    }
}

#[test]
fn move_value_equals_minimax_of_child() {
    let mut rng = XorShift64::new(4242);
    for i in 0..200 {
        let b = random_position(&mut rng, 10 + i % 40);
        let moves = b.legal_moves();
        for sq in (0..64u8).filter(|&s| moves & (1 << s) != 0) {
            let v = Searcher::new().move_value(&b, sq, 3);
            assert_eq!(v, Some(-minimax(&b.play(sq), 2)));
        }
        let illegal = (0..64u8).find(|&s| moves & (1 << s) == 0).unwrap();
        assert_eq!(Searcher::new().move_value(&b, illegal, 3), None);
    }
}

#[test]
fn solver_equals_exhaustive_search() {
    let mut rng = XorShift64::new(99);
    for _ in 0..200 {
        // 9-10 empties: small enough for exhaustive search.
        let plies = 50 + (rng.next_u64() % 2) as u32;
        let b = random_position(&mut rng, plies);
        if b.is_game_over() {
            continue;
        }
        let expected = exact(&b);
        let r = Solver::new().solve(&b);
        assert_eq!(r.score, expected, "{}", b.to_compact());
        if let Some(m) = r.best {
            assert_eq!(-exact(&b.play(m)), expected);
        }
    }
}

#[test]
fn search_is_deterministic() {
    let mut rng = XorShift64::new(5);
    for _ in 0..50 {
        let b = random_position(&mut rng, 20);
        let a = Searcher::new().search_depth(&b, 5);
        let c = Searcher::new().search_depth(&b, 5);
        assert_eq!(a, c);
    }
}

#[test]
fn time_limit_is_respected() {
    let b = Board::initial();
    let start = Instant::now();
    let r = Searcher::new().search(
        &b,
        Limits {
            deadline: Some(start + Duration::from_millis(50)),
            ..Limits::default()
        },
    );
    assert!(start.elapsed() < Duration::from_millis(500));
    assert!(r.best.is_some());
    assert!(r.depth >= 1);
}

#[test]
fn node_limit_still_returns_a_legal_move() {
    let mut rng = XorShift64::new(8);
    for _ in 0..50 {
        let b = random_position(&mut rng, 30);
        let r = Searcher::new().search(
            &b,
            Limits {
                max_nodes: Some(1),
                ..Limits::default()
            },
        );
        match r.best {
            Some(m) => assert!(b.legal_moves() & (1 << m) != 0),
            None => assert_eq!(b.legal_moves(), 0),
        }
    }
}

/// Engine play (depth 3, exact in the last empties) must beat uniformly random
/// play in at least 99% of 1000 games. Release-only (run with --ignored).
#[test]
#[ignore]
fn depth3_beats_random_at_least_99_percent() {
    let mut rng = XorShift64::new(31337);
    let mut wins = 0;
    for game in 0..1000 {
        let engine_is_black = game % 2 == 0;
        let mut b = Board::initial();
        let mut black_to_move = true;
        while !b.is_game_over() {
            let moves = b.legal_moves();
            if moves == 0 {
                b = b.pass();
            } else if black_to_move == engine_is_black {
                let r = Searcher::new().search(
                    &b,
                    Limits {
                        max_depth: Some(3),
                        ..Limits::default()
                    },
                );
                let m = r.best.expect("a legal move exists");
                assert!(moves & (1 << m) != 0, "illegal move");
                b = b.play(m);
            } else {
                let list: Vec<u8> = (0..64u8).filter(|&s| moves & (1 << s) != 0).collect();
                b = b.play(list[(rng.next_u64() % list.len() as u64) as usize]);
            }
            black_to_move = !black_to_move;
        }
        let black_score = if black_to_move {
            b.final_score()
        } else {
            -b.final_score()
        };
        let engine_score = if engine_is_black {
            black_score
        } else {
            -black_score
        };
        if engine_score > 0 {
            wins += 1;
        }
    }
    assert!(wins >= 990, "only {wins}/1000 wins against random play");
}

#[test]
fn terminal_values_dominate_evaluation_and_have_the_right_sign() {
    let mut rng = XorShift64::new(11);
    for _ in 0..2000 {
        let b = random_position(&mut rng, 70);
        assert!(evaluate(&b).abs() <= EVAL_LIMIT);
        if b.is_game_over() {
            let v = terminal_value(&b);
            let s = b.final_score();
            assert_eq!(v.signum(), s.signum());
            if s != 0 {
                assert!(v.abs() > EVAL_LIMIT);
            }
        }
    }
}

#[test]
fn play_search_is_exact_near_the_end() {
    let mut rng = XorShift64::new(123);
    for _ in 0..100 {
        let b = random_position(&mut rng, 51);
        if b.is_game_over() || b.empties().count_ones() > 10 {
            continue;
        }
        let r = Searcher::new().search(
            &b,
            Limits {
                max_depth: Some(1),
                ..Limits::default()
            },
        );
        let s = exact(&b);
        assert_eq!(r.value.signum(), s.signum());
        if let Some(m) = r.best {
            assert_eq!(-exact(&b.play(m)), s);
        }
    }
}
