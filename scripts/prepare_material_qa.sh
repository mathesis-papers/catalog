#!/usr/bin/env bash
# Mathesis consumer dependencies, inside the disposable rendering container.
set -euo pipefail

missing=()
for tool in pdftotext pdffonts pdftoppm; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
if ((${#missing[@]})); then
  echo "Installing PDF inspection tools: ${missing[*]}"
  apt-get update -qq
  apt-get install -y --no-install-recommends poppler-utils
fi

# Refuse an incomplete inspection environment before rendering any films.
for tool in pdftotext pdffonts pdftoppm ffmpeg ffprobe node python java; do
  command -v "$tool" >/dev/null || {
    echo "Missing required material inspection tool: $tool" >&2
    exit 1
  }
done
python -c 'import pikepdf; from PIL import Image'
pdftotext -v
