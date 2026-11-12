#!/usr/bin/env bash
set -euo pipefail
cd "$1"
cargo test --locked
cargo build --release --locked
