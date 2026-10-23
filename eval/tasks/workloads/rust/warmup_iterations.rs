// One process running the same work again and again, to see whether the
// time of an iteration settles. Compiled code has no JIT to wait for; what
// can change over the first iterations is the allocator's heap growing and
// the CPU's caches filling.
//
//     rustc -O -o warmup_iterations warmup_iterations.rs
//     ./warmup_iterations 200
//
// Prints one line per iteration, `iteration <n> <microseconds>`, and a
// checksum of the last one so that the work cannot be removed.

use std::hint::black_box;
use std::time::Instant;

fn work() -> u64 {
    // 20,000 small heap strings, sorted and hashed: allocation, memory
    // traffic and branching, as a collection-heavy program does.
    let mut words: Vec<String> = (0..20_000u64)
        .map(|i| format!("w{}", i.wrapping_mul(2654435761) % 100_003))
        .collect();
    words.sort();
    let mut h: u64 = 1469598103934665603;
    for w in &words {
        for b in w.bytes() {
            h = (h ^ u64::from(b)).wrapping_mul(1099511628211);
        }
    }
    h
}

fn main() {
    let iterations: usize = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(200);
    let mut last = 0;
    for n in 1..=iterations {
        let start = Instant::now();
        last = black_box(work());
        let micros = start.elapsed().as_secs_f64() * 1e6;
        println!("iteration {n} {micros:.1}");
    }
    println!("checksum {last}");
}
