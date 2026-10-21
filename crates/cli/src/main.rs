//! Command-line entry point. The judge harness depends on these commands and
//! output formats; keep them stable.
//!
//! Boards are 64 characters, A1..H8 row by row, relative to the side to move:
//! `X` = side to move, `O` = opponent, `-` = empty. Anything after the first
//! whitespace on an input line is ignored.
//!
//! Commands:
//!   othello perft <depth>
//!   othello random-games <count> [seed]
//!   othello search --depth <d> [--move <sq>]   boards on stdin, one JSON line each
//!   othello solve                             boards on stdin, one JSON line each
//!   othello engine                            line protocol for matches:
//!       go <board> depth <d> | movetime <ms> | nodes <n>
//!       -> move <sq|pass> value <v> depth <d> nodes <n>
//!       quit

use othello_engine::board::{parse_square, square_name};
use othello_engine::game::random_game;
use othello_engine::perft::perft;
use othello_engine::rng::XorShift64;
use othello_engine::search::{Limits, Searcher};
use othello_engine::solve::Solver;
use othello_engine::Board;
use std::io::{self, BufRead, BufWriter, Write};
use std::process::ExitCode;
use std::time::{Duration, Instant};

const USAGE: &str = "usage:
  othello perft <depth>
  othello random-games <count> [seed]
  othello search --depth <d> [--move <sq>]   (boards on stdin)
  othello solve                             (boards on stdin)
  othello engine                            (line protocol on stdin)";

fn usage() -> ExitCode {
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

fn move_name(m: Option<u8>) -> String {
    m.map(square_name).unwrap_or_else(|| "pass".to_string())
}

fn parse_board_line(line: &str) -> Option<Board> {
    Board::parse(line.split_whitespace().next()?)
}

fn flag<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .map(String::as_str)
}

/// Run `f` on every board read from stdin; stop with an error on a bad line.
fn for_each_board(mut f: impl FnMut(&Board, &mut dyn Write) -> io::Result<()>) -> ExitCode {
    let stdin = io::stdin();
    let mut out = BufWriter::new(io::stdout().lock());
    for (i, line) in stdin.lock().lines().enumerate() {
        let Ok(line) = line else {
            return ExitCode::FAILURE;
        };
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(b) = parse_board_line(&line) else {
            eprintln!("line {}: invalid board", i + 1);
            return ExitCode::FAILURE;
        };
        if f(&b, &mut out).is_err() {
            return ExitCode::FAILURE;
        }
    }
    if out.flush().is_err() {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

fn cmd_search(args: &[String]) -> ExitCode {
    let Some(depth) = flag(args, "--depth").and_then(|s| s.parse::<u32>().ok()) else {
        return usage();
    };
    let only_move = match flag(args, "--move") {
        Some(s) => match parse_square(s) {
            Some(sq) => Some(sq),
            None => return usage(),
        },
        None => None,
    };
    for_each_board(|b, out| {
        let mut s = Searcher::new();
        match only_move {
            Some(sq) => match s.move_value(b, sq, depth) {
                Some(v) => writeln!(
                    out,
                    "{{\"value\":{v},\"move\":\"{}\",\"nodes\":{}}}",
                    square_name(sq),
                    s.nodes
                ),
                None => writeln!(out, "{{\"error\":\"illegal move\"}}"),
            },
            None => {
                let r = s.search_depth(b, depth);
                writeln!(
                    out,
                    "{{\"value\":{},\"move\":\"{}\",\"nodes\":{}}}",
                    r.value,
                    move_name(r.best),
                    r.nodes
                )
            }
        }
    })
}

fn cmd_solve() -> ExitCode {
    for_each_board(|b, out| {
        let mut s = Solver::new();
        let r = s.solve(b);
        writeln!(
            out,
            "{{\"score\":{},\"move\":\"{}\",\"nodes\":{}}}",
            r.score,
            move_name(r.best),
            r.nodes
        )
    })
}

fn cmd_engine() -> ExitCode {
    let stdin = io::stdin();
    let mut out = io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else {
            return ExitCode::FAILURE;
        };
        let parts: Vec<&str> = line.split_whitespace().collect();
        let reply = match parts.as_slice() {
            ["quit"] => break,
            ["go", board, kind, amount] => {
                let start = Instant::now();
                match (Board::parse(board), amount.parse::<u64>()) {
                    (Some(b), Ok(n)) => {
                        let limits = match *kind {
                            "depth" => Some(Limits {
                                max_depth: Some(n.clamp(1, 64) as u32),
                                ..Limits::default()
                            }),
                            "movetime" => Some(Limits {
                                deadline: Some(start + Duration::from_millis(n)),
                                ..Limits::default()
                            }),
                            "nodes" => Some(Limits {
                                max_nodes: Some(n),
                                ..Limits::default()
                            }),
                            _ => None,
                        };
                        match limits {
                            Some(l) => {
                                let r = Searcher::new().search(&b, l);
                                format!(
                                    "move {} value {} depth {} nodes {}",
                                    move_name(r.best),
                                    r.value,
                                    r.depth,
                                    r.nodes
                                )
                            }
                            None => "error unknown limit".to_string(),
                        }
                    }
                    _ => "error bad board or amount".to_string(),
                }
            }
            _ => "error unknown command".to_string(),
        };
        if writeln!(out, "{reply}").and_then(|_| out.flush()).is_err() {
            return ExitCode::FAILURE;
        }
    }
    ExitCode::SUCCESS
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
        Some("search") => cmd_search(&args[1..]),
        Some("solve") => cmd_solve(),
        Some("engine") => cmd_engine(),
        _ => usage(),
    }
}
