#!/usr/bin/env bash
# Rasterise SVG brand assets -> PNG/ICO.
# Dùng qlmanage (có sẵn trên macOS) nên không phụ thuộc Chrome.
# Chạy sau tools/build-brand.py:   bash tools/render-icons.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/assets"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

render() { # <svg> <size> <dest.png>
  qlmanage -t -s "$2" -o "$TMP" "$1" >/dev/null 2>&1
  mv "$TMP/$(basename "$1").png" "$3"
}

render "$OUT/favicon.svg"        16 "$OUT/favicon-16.png"
render "$OUT/favicon.svg"        32 "$OUT/favicon-32.png"
render "$OUT/favicon.svg"        48 "$OUT/favicon-48.png"
render "$OUT/icon-large.svg"    180 "$OUT/apple-touch-icon.png"
render "$OUT/icon-large.svg"    192 "$OUT/icon-192.png"
render "$OUT/icon-large.svg"    512 "$OUT/icon-512.png"
render "$OUT/icon-maskable.svg" 512 "$OUT/icon-maskable-512.png"

# favicon.ico = container gói 3 ảnh PNG (Windows Vista trở lên đọc được).
python3 - "$OUT" <<'PY'
import struct, sys, pathlib
out = pathlib.Path(sys.argv[1])
imgs = [(s, (out / f"favicon-{s}.png").read_bytes()) for s in (16, 32, 48)]
header = struct.pack("<HHH", 0, 1, len(imgs))
offset = 6 + 16 * len(imgs)
entries = blobs = b""
for size, data in imgs:
    entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset)
    blobs += data
    offset += len(data)
(out / "favicon.ico").write_bytes(header + entries + blobs)
print("favicon.ico:", len(header + entries + blobs), "bytes")
PY

echo "done -> $OUT"
