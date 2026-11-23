#!/usr/bin/env bash
set -euo pipefail
cd "$1"
cargo test --locked
cargo build --release --locked
if [ -n "${GITHUB_ENV:-}" ]; then
  printf 'MATHESIS_CATALOG_BINARY=%s/target/release/mathesis-catalog\n' "$PWD" >> "$GITHUB_ENV"
fi
