#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 x86_64 VM to run the labeller.
#
# Run as root on the VM (Hetzner's default login), once, before
# copying the repository over:
#
#   scp eval/label/bootstrap-vm.sh root@<vm>:
#   ssh root@<vm> bash bootstrap-vm.sh
#
# Installs git, build tools, Docker (for the tasks whose recipes run
# Go, Ruby or PostgreSQL in containers), perf (instruction counts),
# uv, CMake, Ninja, Node and Rust at pinned versions, the tools the recipes in
# eval/mine/checks.json call. It changes no kernel or security setting:
# as root, perf counts without lowering kernel.perf_event_paranoid.
# See eval/label/README.md, "Running on the Linux VM".

set -euo pipefail

UV_VERSION="0.12.20"
# The versions the recipes in eval/mine/checks.json were built with on
# the Mac (cmake and ninja from uv tools, node from nvm, cargo from
# rustup), so both machines build with the same tools.
CMAKE_VERSION="4.4.4"
NINJA_VERSION="1.13.2"
NODE_VERSION="24.13.0"
RUST_VERSION="1.94.1"
# A pinned rustup-init, checked against the sha256 that
# static.rust-lang.org publishes for this release (rustup 1.28.2,
# x86_64-unknown-linux-gnu), rather than running an unpinned script.
RUSTUP_VERSION="1.28.2"
RUSTUP_INIT_SHA256="20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c"
# The uv release tarball, checked against the sha256 GitHub publishes
# beside it (uv 0.12.20, x86_64-unknown-linux-gnu).
UV_SHA256="6590717592ace991ff83a63fef799e3ad9d33ecc8f96c5d6bdd732496e79337f"
CURL_MAX_TIME=300

if [[ "$(id -u)" -ne 0 ]]; then
    echo "run as root" >&2
    exit 1
fi
# shellcheck source=/dev/null
. /etc/os-release
if [[ "${ID}" != "ubuntu" || "${VERSION_ID}" != "24.04" ]]; then
    echo "expected Ubuntu 24.04, found ${PRETTY_NAME}" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
    build-essential ca-certificates clang curl docker.io git \
    linux-tools-common linux-tools-generic pkg-config rsync tmux
# perf must match the running kernel. Hetzner's images run Ubuntu's
# generic kernel, which has this package; a custom kernel won't.
kernel_tools="linux-tools-$(uname -r)"
if ! apt-get install -y -q --no-install-recommends "${kernel_tools}"; then
    echo "warning: ${kernel_tools} not available; perf may not run" >&2
fi
systemctl enable --now docker

# uv, from the pinned release tarball with a sha256 check.
uv_dir="$(mktemp -d)"
curl --max-time "${CURL_MAX_TIME}" --proto '=https' --tlsv1.2 -sSfL \
    -o "${uv_dir}/uv.tar.gz" \
    "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz"
echo "${UV_SHA256}  ${uv_dir}/uv.tar.gz" | sha256sum -c -
tar -xzf "${uv_dir}/uv.tar.gz" -C "${uv_dir}"
install -m 0755 "${uv_dir}/uv-x86_64-unknown-linux-gnu/uv" \
    "${uv_dir}/uv-x86_64-unknown-linux-gnu/uvx" /usr/local/bin/
# CMake and Ninja from PyPI, as on the Mac (eval/README.md), linked
# where a non-interactive SSH command finds them.
UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "cmake==${CMAKE_VERSION}"
UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "ninja==${NINJA_VERSION}"
# Node (node, npm, npx) for the tasks built with npm, from PyPI so
# no third-party apt repository is needed.
UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "nodejs-wheel-binaries==${NODE_VERSION}"

# Rust, pinned: rustup-init is downloaded, checked and run by hand.
if ! command -v cargo >/dev/null 2>&1; then
    init="$(mktemp -d)/rustup-init"
    curl --max-time "${CURL_MAX_TIME}" --proto '=https' --tlsv1.2 -sSf \
        -o "${init}" \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init"
    echo "${RUSTUP_INIT_SHA256}  ${init}" | sha256sum -c -
    chmod +x "${init}"
    "${init}" -y --profile minimal --default-toolchain "${RUST_VERSION}"
fi
# rustup's proxies pick the toolchain by name, so links work.
for tool in cargo rustc rustup; do
    ln -sf "${HOME}/.cargo/bin/${tool}" "/usr/local/bin/${tool}"
done

# Fail if a tool is not the pinned version (a preinstalled cargo, say).
expect() {
    if [[ "$2" != *"$3"* ]]; then
        echo "$1 is '$2', expected $3" >&2
        exit 1
    fi
}
expect cargo "$(cargo --version)" "cargo ${RUST_VERSION} "
expect cmake "$(cmake --version | head -n 1)" "${CMAKE_VERSION}"
expect ninja "$(ninja --version)" "${NINJA_VERSION}"
expect node "$(node --version)" "v${NODE_VERSION}"

echo
echo "installed:"
git --version
docker --version
/usr/local/bin/uv --version
cargo --version
clang --version | head -n 1
node --version
if perf --version; then
    echo "perf_event_paranoid: $(cat /proc/sys/kernel/perf_event_paranoid)"
    perf stat -x, -e instructions:u -- true 2>&1 | tail -n 1 || true
else
    echo "perf did not run" >&2
fi
echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-)"
echo "kernel: $(uname -r)"
echo "load: $(cut -d' ' -f1-3 /proc/loadavg)"
