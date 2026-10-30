//! Pushes batches of records into a file-backed `SqliteStore` and prints
//! the elapsed milliseconds.

use std::time::Instant;

use atuin_client::record::{sqlite_store::SqliteStore, store::Store};
use atuin_common::record::{EncryptedData, Host, HostId, Record};

const ROUNDS: usize = 300;

fn make(host: HostId, tag: &str) -> Record<EncryptedData> {
    Record::builder()
        .host(Host::new(host))
        .version("v1".into())
        .tag(tag.into())
        .data(EncryptedData {
            data: "1234".into(),
            content_encryption_key: "1234".into(),
        })
        .idx(0)
        .build()
}

#[tokio::main]
async fn main() {
    let path = std::env::temp_dir().join(format!(
        "eval_push_{}.db",
        std::process::id()
    ));
    let store = SqliteStore::new(&path, 5.0).await.unwrap();
    let host = HostId(atuin_common::utils::uuid_v7());
    let start = Instant::now();
    for size in [1usize, 10, 100] {
        for _ in 0..ROUNDS {
            let tag = atuin_common::utils::uuid_v7().simple().to_string();
            let batch: Vec<_> = (0..size).map(|_| make(host, &tag)).collect();
            store.push_batch(batch.iter()).await.unwrap();
        }
    }
    println!("{} ms", start.elapsed().as_millis());
    drop(store);
    for suffix in ["", "-wal", "-shm"] {
        let _ = std::fs::remove_file(format!("{}{}", path.display(), suffix));
    }
}
