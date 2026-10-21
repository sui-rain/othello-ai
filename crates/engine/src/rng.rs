//! Small deterministic PRNG (xorshift64*), so tests and tools need no dependencies.

#[derive(Clone, Debug)]
pub struct XorShift64 {
    state: u64,
}

impl XorShift64 {
    /// A seed of 0 is replaced with a fixed non-zero constant.
    pub fn new(seed: u64) -> Self {
        XorShift64 {
            state: if seed == 0 {
                0x9E37_79B9_7F4A_7C15
            } else {
                seed
            },
        }
    }

    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// Uniform value in `0..n` (n > 0).
    pub fn below(&mut self, n: u64) -> u64 {
        debug_assert!(n > 0);
        ((self.next_u64() as u128 * n as u128) >> 64) as u64
    }

    /// Index of a uniformly chosen set bit of `bits` (must be non-zero).
    pub fn pick_bit(&mut self, mut bits: u64) -> u8 {
        debug_assert!(bits != 0);
        let k = self.below(bits.count_ones() as u64);
        for _ in 0..k {
            bits &= bits - 1;
        }
        bits.trailing_zeros() as u8
    }
}
