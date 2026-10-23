# A Linux Rust box for checking the Rust support on Linux: the official
# rust 1.94.1 image plus perf (Debian linux-perf), git and Python with venv
# and pip. Built locally, never pushed:
#   docker build -t perfproof-rust:1.94.1 eval/tasks/workloads/rust
FROM rust:1.94.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        linux-perf git python3 python3-venv python3-pip ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*
