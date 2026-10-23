# Linux VM on the Mac, not real Linux hardware: an arm64 container
# running natively in Docker Desktop's VM. Built locally, never pushed.
#   docker build --platform linux/arm64 -t perfproof-traces \
#     tests/calibration/traces
FROM python:3.12-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy
