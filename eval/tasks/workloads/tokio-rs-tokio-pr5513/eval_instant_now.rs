// Calls tokio's Instant::now() ten million times with the test-util
// feature on and the clock never paused, the case the change speeds
// up. Copied into tokio/tests/ and run as an integration test.
#![cfg(all(feature = "full", feature = "test-util"))]

use tokio::time::Instant;

#[test]
fn eval_instant_now() {
    let start = Instant::now();
    let mut last = start;
    for _ in 0..10_000_000 {
        last = Instant::now();
    }
    assert!(last >= start);
}
