# A Linux JDK box for the Java tasks and for checking the Java support on
# Linux: the official Temurin 21 image plus Maven, git and Python.
# Built locally, never pushed:
#   docker build -t perfproof-java:21 eval/tasks/workloads/java
FROM eclipse-temurin:21-jdk

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        maven git python3 python3-venv python3-pip ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
