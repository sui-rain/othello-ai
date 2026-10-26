//! Learns the evaluation correction tables from self-play and writes `src/eval_weights.rs`.
//!
//!   cargo run --release -p othello-engine --example train_eval -- <games> <seed> <out-file>
//!
//! Self-play uses the engine as compiled, so the result depends on the tables
//! already in `src/eval_weights.rs` as well as on the arguments (rerunning on
//! the new tables is a further round of the same training).
//!
//! Games start with random moves, then the engine plays itself at a shallow
//! depth (with a few random moves mixed in). Every position with 8..=58 empties
//! is labelled with the final disc difference from the side to move's point of
//! view. The tables are fitted by least squares (Adagrad); the engine only ever
//! sees the rounded integer weights.

use othello_engine::board::Board;
use othello_engine::eval::{
    base_score, pattern_indices, stage, BASE_SCALE, STAGES, TABLES, TABLE_LEN,
};
use othello_engine::rng::XorShift64;
use othello_engine::search::{Limits, Searcher};
use std::fmt::Write as _;

const OPENING_RANDOM_PLIES: u32 = 8;
const RANDOM_MOVE_PERCENT: u64 = 15;
const SEARCH_DEPTH: u32 = 3;
const EPOCHS: usize = 10;
const LEARNING_RATE: f32 = 0.05;
const BLOCK: usize = TABLES * TABLE_LEN;

struct Sample {
    board: Board,
    /// Final disc difference for the side to move.
    y: f32,
}

fn play_game(rng: &mut XorShift64, out: &mut Vec<Sample>) {
    let mut b = Board::initial();
    let mut history: Vec<(Board, bool)> = Vec::new(); // (position, black to move)
    let mut black = true;
    let mut ply = 0;
    let mut searcher = Searcher::new();
    loop {
        let moves = b.legal_moves();
        if moves == 0 {
            if b.pass().legal_moves() == 0 {
                break;
            }
            b = b.pass();
            black = !black;
            continue;
        }
        history.push((b, black));
        let random = ply < OPENING_RANDOM_PLIES || rng.below(100) < RANDOM_MOVE_PERCENT;
        let sq = if random {
            rng.pick_bit(moves)
        } else {
            let limits = Limits {
                max_depth: Some(SEARCH_DEPTH),
                ..Limits::default()
            };
            searcher.search(&b, limits).best.expect("a move exists")
        };
        b = b.play(sq);
        black = !black;
        ply += 1;
    }
    // `b` is final; `black` says who would be to move in it.
    let score = b.final_score() as f32;
    for (pos, was_black) in history {
        let empties = pos.empties().count_ones();
        if !(8..=58).contains(&empties) {
            continue;
        }
        let y = if was_black == black { score } else { -score };
        // Fit what the hand-written base misses, in discs.
        out.push(Sample {
            board: pos,
            y: y - (BASE_SCALE * base_score(&pos)) as f32 / 100.0,
        });
    }
}

struct Model {
    w: Vec<f32>,
    mob: [f32; STAGES],
}

impl Model {
    fn predict(&self, b: &Board) -> f32 {
        let st = stage(b);
        let block = st * BLOCK;
        let mut s = 0.0;
        for i in pattern_indices(b) {
            s += self.w[block + i];
        }
        s + self.mob[st] * mobility(b)
    }
}

fn mobility(b: &Board) -> f32 {
    b.legal_moves().count_ones() as f32 - b.pass().legal_moves().count_ones() as f32
}

fn mse(model: &Model, data: &[Sample]) -> f32 {
    let mut sum = 0.0;
    for s in data {
        let e = model.predict(&s.board) - s.y;
        sum += e * e;
    }
    sum / data.len() as f32
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let games: u64 = args.get(1).and_then(|s| s.parse().ok()).expect("games");
    let seed: u64 = args.get(2).and_then(|s| s.parse().ok()).expect("seed");
    let out_path = args.get(3).expect("out-file");

    let threads = 4u64;
    let handles: Vec<_> = (0..threads)
        .map(|t| {
            std::thread::spawn(move || {
                let mut rng = XorShift64::new(seed * 1000 + t + 1);
                let mut v = Vec::new();
                for _ in 0..games / threads {
                    play_game(&mut rng, &mut v);
                }
                v
            })
        })
        .collect();
    let mut data: Vec<Sample> = Vec::new();
    for h in handles {
        data.extend(h.join().expect("worker panicked"));
    }
    eprintln!("{} samples from {} games", data.len(), games);

    // Hold out every 20th position's game neighbours loosely: shuffle, then split.
    let mut rng = XorShift64::new(seed + 99);
    for i in (1..data.len()).rev() {
        data.swap(i, rng.below(i as u64 + 1) as usize);
    }
    let split = data.len() / 20;
    let (valid, train) = data.split_at(split);

    let mut model = Model {
        w: vec![0.0; STAGES * BLOCK],
        mob: [0.15; STAGES],
    };
    let mut g2 = vec![1e-6f32; STAGES * BLOCK];
    let mut g2_mob = [1e-6f32; STAGES];
    eprintln!(
        "start: train mse {:.2} valid mse {:.2}",
        mse(&model, train),
        mse(&model, valid)
    );
    let mut best = (f32::MAX, model.w.clone(), model.mob);
    for epoch in 0..EPOCHS {
        for s in train {
            let st = stage(&s.board);
            let block = st * BLOCK;
            let m = mobility(&s.board);
            let err = model.predict(&s.board) - s.y;
            for i in pattern_indices(&s.board) {
                let k = block + i;
                g2[k] += err * err;
                model.w[k] -= LEARNING_RATE * err / g2[k].sqrt();
            }
            g2_mob[st] += (err * m) * (err * m);
            model.mob[st] -= LEARNING_RATE * err * m / g2_mob[st].sqrt();
        }
        let v = mse(&model, valid);
        eprintln!(
            "epoch {epoch}: train mse {:.2} valid mse {v:.2}",
            mse(&model, train)
        );
        if v < best.0 {
            best = (v, model.w.clone(), model.mob);
        }
    }
    let (v, w, mob) = best;
    eprintln!("best valid mse {v:.2}");

    let mut text = String::new();
    text.push_str("//! Generated by `examples/train_eval.rs`. Do not edit by hand.\n\n");
    text.push_str("use crate::eval::{STAGES, TABLES, TABLE_LEN};\n\n");
    let mobs: Vec<String> = mob
        .iter()
        .map(|m| format!("{}", (m * 100.0).round() as i32))
        .collect();
    writeln!(
        text,
        "pub static MOBILITY_WEIGHTS: [i32; STAGES] = [{}];\n",
        mobs.join(", ")
    )
    .unwrap();
    text.push_str(
        "#[rustfmt::skip]\npub static PATTERN_WEIGHTS: [i16; STAGES * TABLES * TABLE_LEN] = [\n",
    );
    for chunk in w.chunks(24) {
        let line: Vec<String> = chunk
            .iter()
            .map(|x| format!("{}", (x * 100.0).round().clamp(-3000.0, 3000.0) as i16))
            .collect();
        writeln!(text, "    {},", line.join(", ")).unwrap();
    }
    text.push_str("];\n");
    std::fs::write(out_path, text).expect("write output");
}
