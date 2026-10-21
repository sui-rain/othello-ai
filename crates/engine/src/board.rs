//! Bitboard representation of an Othello position.
//!
//! Square index = row * 8 + col, with A1 = 0 and H8 = 63 (decision I1).
//! The board is stored relative to the side to move (decision I2):
//! `player` holds the discs of the side to move, `opponent` the other side.

/// Bits on the A file (col 0).
pub const FILE_A: u64 = 0x0101_0101_0101_0101;
/// Bits on the H file (col 7).
pub const FILE_H: u64 = 0x8080_8080_8080_8080;

/// Black (first to move) discs in the standard initial position: E4 and D5.
pub const INITIAL_BLACK: u64 = (1 << 28) | (1 << 35);
/// White discs in the standard initial position: D4 and E5.
pub const INITIAL_WHITE: u64 = (1 << 27) | (1 << 36);

/// Shift every disc one step in direction `dir` (0..8), dropping discs that
/// would wrap around the board edge.
#[inline(always)]
pub fn shift(b: u64, dir: usize) -> u64 {
    match dir {
        0 => b << 8,             // north (row + 1)
        1 => b >> 8,             // south (row - 1)
        2 => (b << 1) & !FILE_A, // east (col + 1)
        3 => (b >> 1) & !FILE_H, // west (col - 1)
        4 => (b << 9) & !FILE_A, // north-east
        5 => (b << 7) & !FILE_H, // north-west
        6 => (b >> 7) & !FILE_A, // south-east
        _ => (b >> 9) & !FILE_H, // south-west
    }
}

/// An Othello position from the point of view of the side to move.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Board {
    /// Discs of the side to move.
    pub player: u64,
    /// Discs of the side not to move.
    pub opponent: u64,
}

impl Default for Board {
    fn default() -> Self {
        Self::initial()
    }
}

impl Board {
    /// The standard initial position, black to move.
    pub const fn initial() -> Self {
        Board {
            player: INITIAL_BLACK,
            opponent: INITIAL_WHITE,
        }
    }

    /// Build a board; returns `None` if the two sides overlap.
    pub fn new(player: u64, opponent: u64) -> Option<Self> {
        if player & opponent != 0 {
            None
        } else {
            Some(Board { player, opponent })
        }
    }

    /// Empty squares.
    #[inline(always)]
    pub fn empties(&self) -> u64 {
        !(self.player | self.opponent)
    }

    /// Bitboard of legal moves for the side to move.
    #[inline]
    pub fn legal_moves(&self) -> u64 {
        let p = self.player;
        let o = self.opponent;
        let empty = self.empties();
        let mut moves = 0u64;
        for dir in 0..8 {
            // Run of opponent discs adjacent to our discs, up to 6 long.
            let mut x = shift(p, dir) & o;
            x |= shift(x, dir) & o;
            x |= shift(x, dir) & o;
            x |= shift(x, dir) & o;
            x |= shift(x, dir) & o;
            x |= shift(x, dir) & o;
            moves |= shift(x, dir) & empty;
        }
        moves
    }

    /// Discs flipped by the side to move playing on `sq`.
    /// Returns 0 if `sq` is occupied or flips nothing (i.e. is not legal).
    #[inline]
    pub fn flips(&self, sq: u8) -> u64 {
        let m = 1u64 << sq;
        if (self.player | self.opponent) & m != 0 {
            return 0;
        }
        let p = self.player;
        let o = self.opponent;
        let mut flipped = 0u64;
        for dir in 0..8 {
            let mut run = 0u64;
            let mut t = shift(m, dir);
            while t & o != 0 {
                run |= t;
                t = shift(t, dir);
            }
            if t & p != 0 {
                flipped |= run;
            }
        }
        flipped
    }

    /// Play a legal move on `sq` and return the position with the other side to move.
    ///
    /// # Panics
    /// In debug builds, panics if the move is not legal.
    #[inline]
    pub fn play(&self, sq: u8) -> Board {
        let f = self.flips(sq);
        debug_assert!(f != 0, "illegal move on square {sq}");
        let m = 1u64 << sq;
        Board {
            player: self.opponent ^ f,
            opponent: self.player | m | f,
        }
    }

    /// Play `sq` if it is legal; `None` otherwise.
    pub fn try_play(&self, sq: u8) -> Option<Board> {
        if sq >= 64 || self.legal_moves() & (1u64 << sq) == 0 {
            None
        } else {
            Some(self.play(sq))
        }
    }

    /// Pass: the other side to move, discs unchanged.
    #[inline(always)]
    pub fn pass(&self) -> Board {
        Board {
            player: self.opponent,
            opponent: self.player,
        }
    }

    /// True when neither side has a legal move.
    pub fn is_game_over(&self) -> bool {
        self.legal_moves() == 0 && self.pass().legal_moves() == 0
    }

    /// Disc difference from the side to move's point of view.
    pub fn disc_diff(&self) -> i32 {
        self.player.count_ones() as i32 - self.opponent.count_ones() as i32
    }

    /// Final score from the side to move's point of view, with empty squares
    /// going to the winner (the standard scoring rule).
    pub fn final_score(&self) -> i32 {
        let p = self.player.count_ones() as i32;
        let o = self.opponent.count_ones() as i32;
        let e = 64 - p - o;
        match p.cmp(&o) {
            std::cmp::Ordering::Greater => p - o + e,
            std::cmp::Ordering::Less => p - o - e,
            std::cmp::Ordering::Equal => 0,
        }
    }

    /// Parse 64 characters (A1..H8, row by row): `X` = side to move, `O` = opponent,
    /// `-` or `.` = empty. Whitespace is ignored.
    pub fn parse(s: &str) -> Option<Board> {
        let mut player = 0u64;
        let mut opponent = 0u64;
        let mut i = 0u32;
        for c in s.chars().filter(|c| !c.is_whitespace()) {
            if i >= 64 {
                return None;
            }
            match c {
                'X' | 'x' => player |= 1 << i,
                'O' | 'o' => opponent |= 1 << i,
                '-' | '.' => {}
                _ => return None,
            }
            i += 1;
        }
        if i != 64 {
            return None;
        }
        Board::new(player, opponent)
    }

    /// Inverse of [`Board::parse`].
    pub fn to_compact(&self) -> String {
        (0..64)
            .map(|i| {
                let m = 1u64 << i;
                if self.player & m != 0 {
                    'X'
                } else if self.opponent & m != 0 {
                    'O'
                } else {
                    '-'
                }
            })
            .collect()
    }
}

/// Square name such as "d3" for an index.
pub fn square_name(sq: u8) -> String {
    let col = (b'a' + sq % 8) as char;
    let row = (b'1' + sq / 8) as char;
    format!("{col}{row}")
}

/// Index for a square name such as "d3".
pub fn parse_square(s: &str) -> Option<u8> {
    let b = s.as_bytes();
    if b.len() != 2 {
        return None;
    }
    let col = b[0].to_ascii_lowercase().checked_sub(b'a')?;
    let row = b[1].checked_sub(b'1')?;
    if col < 8 && row < 8 {
        Some(row * 8 + col)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rng::XorShift64;

    /// Slow, obviously-correct move generator used to cross-check the bitboard one.
    fn naive_flips(b: &Board, sq: u8) -> u64 {
        let m = 1u64 << sq;
        if (b.player | b.opponent) & m != 0 {
            return 0;
        }
        let (r0, c0) = ((sq / 8) as i32, (sq % 8) as i32);
        let mut flipped = 0;
        for (dr, dc) in [
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        ] {
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
        flipped
    }

    fn naive_moves(b: &Board) -> u64 {
        (0..64u8)
            .filter(|&sq| naive_flips(b, sq) != 0)
            .fold(0, |acc, sq| acc | (1 << sq))
    }

    #[test]
    fn initial_moves() {
        let b = Board::initial();
        let names: Vec<String> = (0..64u8)
            .filter(|&s| b.legal_moves() & (1 << s) != 0)
            .map(square_name)
            .collect();
        assert_eq!(names, ["d3", "c4", "f5", "e6"]);
    }

    #[test]
    fn matches_naive_on_random_positions() {
        let mut rng = XorShift64::new(12345);
        for _ in 0..3000 {
            let mut b = Board::initial();
            loop {
                assert_eq!(b.legal_moves(), naive_moves(&b));
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
                b = b.play(rng.pick_bit(moves));
            }
        }
    }

    #[test]
    fn parse_roundtrip() {
        let b = Board::initial();
        assert_eq!(Board::parse(&b.to_compact()), Some(b));
        assert_eq!(parse_square("d3"), Some(19));
        assert_eq!(square_name(19), "d3");
        assert_eq!(Board::parse("X"), None);
    }

    #[test]
    fn final_score_gives_empties_to_winner() {
        let b = Board::new(0b111, 0b1).unwrap_or_default();
        assert_eq!(b, Board::default()); // overlap rejected
        let b = Board::new(0b110, 0b1).unwrap();
        assert_eq!(b.final_score(), 2 - 1 + 61);
        assert_eq!(b.pass().final_score(), -(2 - 1 + 61));
    }
}
