//! Command-line entry point.
//!
//! Usage:
//!   othello perft <depth>
//!   othello random-games <count> [seed]

use othello_engine::game::random_game;
use othello_engine::perft::perft;
use othello_engine::rng::XorShift64;
use othello_engine::Board;
use std::process::ExitCode;
use std::time::Instant;

fn usage() -> ExitCode {
    eprintln!("usage:\n  othello perft <depth>\n  othello random-games <count> [seed]");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("perft") => {
            let Some(depth) = args.get(1).and_then(|s| s.parse::<u32>().ok()) else {
                return usage();
            };
            let start = Instant::now();
            let n = perft(&Board::initial(), depth);
            let secs = start.elapsed().as_secs_f64();
            println!("depth {depth}: {n} ({secs:.3}s)");
            ExitCode::SUCCESS
        }
        Some("random-games") => {
            let Some(count) = args.get(1).and_then(|s| s.parse::<u64>().ok()) else {
                return usage();
            };
            let seed = args.get(2).and_then(|s| s.parse::<u64>().ok()).unwrap_or(1);
            let mut rng = XorShift64::new(seed);
            let (mut black, mut white, mut draw) = (0u64, 0u64, 0u64);
            for _ in 0..count {
                let r = random_game(&mut rng);
                match r.black_score.signum() {
                    1 => black += 1,
                    -1 => white += 1,
                    _ => draw += 1,
                }
            }
            println!("black {black} / white {white} / draw {draw}");
            ExitCode::SUCCESS
        }
        _ => usage(),
    }
}
