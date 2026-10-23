// One hot loop for each slowness class in skills/perfproof/rust.md, so that
// the playbook's "what the profile shows" column can be checked against a
// real profile of each. Build and run one mode:
//
//     rustc -C opt-level=3 -C force-frame-pointers=yes -o pathologies pathologies.rs
//     ./pathologies <mode> <seconds>
//
// Modes: grow, clone, contains, format, hash, mutex, unbuffered, bounds.
// Each loops until the time is up and prints a checksum, so the work cannot
// be removed.

use std::collections::HashMap;
use std::hint::black_box;
use std::io::Write as _;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn run_until(seconds: f64, mut body: impl FnMut() -> u64) -> u64 {
    let end = Instant::now() + Duration::from_secs_f64(seconds);
    let mut sum = 0u64;
    while Instant::now() < end {
        sum = sum.wrapping_add(body());
    }
    sum
}

#[inline(never)]
fn grow() -> u64 {
    // A vector that grows by doubling although its length is known.
    let mut v = Vec::new();
    for i in 0..4096u64 {
        v.push(black_box(i));
    }
    v.len() as u64
}

#[inline(never)]
fn takes_ownership(words: Vec<String>) -> u64 {
    words.iter().map(|w| w.len() as u64).sum()
}

#[inline(never)]
fn contains(haystack: &[u64]) -> u64 {
    // A membership test on a vector, once per key.
    let mut hits = 0;
    for key in 0..512u64 {
        if haystack.contains(&black_box(key * 9)) {
            hits += 1;
        }
    }
    hits
}

#[inline(never)]
fn format_numbers() -> u64 {
    let mut total = 0;
    for i in 0..2048u64 {
        total += black_box(i).to_string().len() as u64;
    }
    total
}

#[inline(never)]
fn hash(map: &mut HashMap<u64, u64>) -> u64 {
    let mut total = 0;
    for i in 0..4096u64 {
        *map.entry(black_box(i) % 1024).or_insert(0) += i;
        total += map.get(&black_box(i % 997)).copied().unwrap_or(0);
    }
    total
}

#[inline(never)]
fn index_sum(values: &[u64], positions: &[usize]) -> u64 {
    // Indexing with positions the compiler cannot prove are in range.
    let mut total = 0u64;
    for &p in positions {
        total = total.wrapping_add(values[p]);
    }
    total
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mode = args.next().unwrap_or_default();
    let seconds: f64 = args.next().and_then(|a| a.parse().ok()).unwrap_or(1.0);
    let sum = match mode.as_str() {
        "grow" => run_until(seconds, grow),
        "clone" => {
            let base: Vec<String> = (0..256).map(|i| format!("item-{i}")).collect();
            run_until(seconds, || takes_ownership(black_box(&base).clone()))
        }
        "contains" => {
            let haystack: Vec<u64> = (0..4096).collect();
            run_until(seconds, || contains(black_box(&haystack)))
        }
        "format" => run_until(seconds, format_numbers),
        "hash" => {
            let mut map = HashMap::new();
            run_until(seconds, || hash(&mut map))
        }
        "mutex" => {
            let counter = Arc::new(Mutex::new(0u64));
            let workers: Vec<_> = (0..4)
                .map(|_| {
                    let counter = Arc::clone(&counter);
                    std::thread::spawn(move || {
                        run_until(seconds, || {
                            let mut guard = counter.lock().unwrap();
                            *guard += 1;
                            1
                        })
                    })
                })
                .collect();
            workers.into_iter().map(|w| w.join().unwrap()).sum()
        }
        "unbuffered" => {
            // A File with no BufWriter: every piece of the formatted line
            // is its own write call.
            let mut out = std::fs::OpenOptions::new()
                .write(true)
                .open("/dev/null")
                .unwrap();
            run_until(seconds, || {
                writeln!(out, "line {}", black_box(7)).unwrap();
                1
            })
        }
        "bounds" => {
            let values: Vec<u64> = (0..4096).collect();
            let positions: Vec<usize> = (0..4096).map(|i| (i * 7) % 4096).collect();
            run_until(seconds, || index_sum(black_box(&values), black_box(&positions)))
        }
        other => {
            eprintln!("unknown mode {other:?}");
            std::process::exit(2);
        }
    };
    eprintln!("checksum {sum}");
}
