#!/usr/bin/env bash
# Fetch the Kokoro neural TTS model assets (one-time setup, ~340 MB total).
#
# These are NOT bundled in git (the ONNX model is too large and Git LFS proved
# unreliable — clones used to receive a 134-byte pointer file and silently lose
# neural TTS). Run this once from the repo root; files land where the server
# expects them.
set -euo pipefail

BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files"
DEST_DIR="$(cd "$(dirname "$0")/.." && pwd)"

fetch() {
    local name="$1" min_bytes="$2"
    local dest="$DEST_DIR/$name"
    if [ -f "$dest" ] && [ "$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest")" -ge "$min_bytes" ]; then
        echo "✓ $name already present ($(du -h "$dest" | cut -f1))"
        return
    fi
    echo "Downloading $name ..."
    curl -fL --retry 3 -o "$dest.part" "$BASE_URL/$name"
    local size
    size=$(stat -c%s "$dest.part" 2>/dev/null || stat -f%z "$dest.part")
    if [ "$size" -lt "$min_bytes" ]; then
        echo "ERROR: $name downloaded only $size bytes (expected >= $min_bytes) — aborting." >&2
        rm -f "$dest.part"
        exit 1
    fi
    mv "$dest.part" "$dest"
    echo "✓ $name downloaded ($(du -h "$dest" | cut -f1))"
}

# Sizes are sanity floors, not exact: model ~325 MB, voices ~27 MB.
fetch "kokoro-v0_19.onnx" 300000000
fetch "voices.bin"        20000000

echo "Done. Neural TTS assets are in place."
