"""Chỉ dùng khi phát triển: kiểm kê một thư mục ảnh đã giải nén so với danh sách tên file (mỗi dòng một tên).
  python3 -I census.py --dir DIR --names names-1710.txt --out OUT_PREFIX [--expect-day 2026-10-17]
Báo: có / thiếu / thừa; trùng sha256; ngày chụp (EXIF DateTimeOriginal) chia theo ngày; máy ảnh (Make Model). Ghi OUT_PREFIX.json + OUT_PREFIX.md.
Ảnh là dữ liệu không tin cậy: chỉ đọc byte + đọc EXIF bằng Pillow (mở lười, không giải mã điểm ảnh), không chạy gì từ ảnh. Chạy bằng -I.
Thoát 0 nếu: không thiếu/thừa/trùng, mọi ảnh đều có ngày và đều là --expect-day (nếu cho); ngược lại 1 (DỪNG, xem báo cáo)."""
import argparse, collections, hashlib, json, os
from PIL import Image

EXTS = (".jpg", ".jpeg", ".png", ".webp")
ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True); ap.add_argument("--names", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--expect-day", default="", help="YYYY-MM-DD: mọi ảnh phải chụp ngày này")
a = ap.parse_args()

listed = {}
for line in open(a.names, encoding="utf-8"):
    if line.strip():
        listed[line.rstrip("\n")] = 1
files = sorted(f for f in os.listdir(a.dir) if not f.startswith(".") and f.lower().endswith(EXTS))
others = sorted(f for f in os.listdir(a.dir) if f not in files and not f.startswith("."))  # vd. .part, .zip sót lại
have, want = set(files), set(listed)
low = collections.Counter(n.lower() for n in files)
sha, by_sha, days, cams, no_exif, size_total = {}, collections.defaultdict(list), collections.Counter(), collections.Counter(), [], 0
for f in files:
    p = os.path.join(a.dir, f)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    sha[f] = h.hexdigest(); by_sha[sha[f]].append(f); size_total += os.path.getsize(p)
    try:
        with Image.open(p) as im:
            ex = im.getexif()
            sub = ex.get_ifd(0x8769)  # Exif IFD
            dt = sub.get(0x9003) or ex.get(0x0132)  # DateTimeOriginal, rơi về DateTime
            cam = " ".join(str(x).strip() for x in (ex.get(0x010F), ex.get(0x0110)) if x) or "(không có)"
    except Exception as e:
        dt, cam = None, f"(lỗi đọc: {type(e).__name__})"
    day = str(dt)[:10].replace(":", "-") if dt else None
    if day is None:
        no_exif.append(f)
    days[day or "(không có ngày)"] += 1; cams[cam] += 1
dups = {h: v for h, v in by_sha.items() if len(v) > 1}
rep = dict(dir=os.path.basename(os.path.abspath(a.dir)), listed=len(want), found=len(have), size_bytes=size_total,
           missing=sorted(want - have), extra=sorted(have - want), non_photo_files=others,
           case_collisions=sorted(k for k, n in low.items() if n > 1), duplicate_sha256=[sorted(v) for v in dups.values()],
           days=dict(sorted(days.items())), cameras=dict(cams.most_common()), no_exif_date=no_exif)
bad_days = {d: n for d, n in days.items() if a.expect_day and d != a.expect_day}
rep["unexpected_days"] = bad_days
rep["clean"] = not (rep["missing"] or rep["extra"] or dups or no_exif or bad_days or others or rep["case_collisions"])
json.dump(rep, open(a.out + ".json", "w"), indent=1, ensure_ascii=False)
L = [f"# Kiểm kê {rep['dir']}", f"- Danh sách: {rep['listed']} · tìm thấy: {rep['found']} · {size_total / 1e9:.2f} GB",
     f"- Thiếu: {len(rep['missing'])} · Thừa: {len(rep['extra'])} · Trùng sha256: {len(dups)} nhóm · File lạ (không phải ảnh/.part): {len(others)}",
     f"- Ngày chụp: " + ", ".join(f"{d}: {n}" for d, n in sorted(days.items())), f"- Máy ảnh: " + ", ".join(f"{c}: {n}" for c, n in cams.most_common()),
     f"- **Kết luận: {'SẠCH' if rep['clean'] else 'DỪNG — xem chi tiết (thiếu/thừa/trùng/ngày lạ)'}**"]
for k, t in (("missing", "Thiếu"), ("extra", "Thừa"), ("non_photo_files", "File lạ"), ("no_exif_date", "Không có ngày EXIF")):
    if rep[k]:
        L += ["", f"## {t} ({len(rep[k])})"] + [f"- {x}" for x in rep[k][:50]] + ([f"- … (+{len(rep[k]) - 50})"] if len(rep[k]) > 50 else [])
if dups:
    L += ["", "## Trùng sha256"] + [f"- {', '.join(v)}" for v in rep["duplicate_sha256"][:30]]
open(a.out + ".md", "w", encoding="utf-8").write("\n".join(L) + "\n")
print("\n".join(L[:6]))
raise SystemExit(0 if rep["clean"] else 1)
