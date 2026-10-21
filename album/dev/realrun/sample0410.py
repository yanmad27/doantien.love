"""Chỉ dùng khi phát triển: lấy N ảnh 4/10 CÔNG KHAI (bản 2000 px, WebP) từ một album đang chạy, chỉ bằng GET đọc-thôi.
  python3 -I sample0410.py --url https://album.doantien.love --n 50 --out DIR
Đọc /api/gallery (hàng: [id, w, h, t, v, …]; chỉ lấy ảnh ngày 4/10 nếu có trường ngày), chọn N ảnh rải đều, tải /i/<id>-<v>-2000.webp.
Chỉ dùng cho mẫu diễn tập; ảnh tải về là dữ liệu không tin cậy và KHÔNG được commit."""
import argparse, json, os, sys, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True); ap.add_argument("--n", type=int, default=50); ap.add_argument("--out", required=True)
a = ap.parse_args()
if os.path.exists(a.out) and os.listdir(a.out):
    sys.exit(f"DỪNG: {a.out} không rỗng")
os.makedirs(a.out, exist_ok=True)
get = lambda p: urllib.request.urlopen(urllib.request.Request(a.url.rstrip("/") + p, headers={"User-Agent": "album-realrun/1"}), timeout=60)
rows = json.load(get("/api/gallery"))["photos"]
rows = [r for r in rows if len(r) < 9 or r[8] == "2026-10-04"]
n = min(a.n, len(rows))
for k in range(n):
    r = rows[k * len(rows) // n + len(rows) // (2 * n)]
    with get(f"/i/{r[0]}-{r[4]}-2000.webp") as f, open(os.path.join(a.out, f"{r[0]}.webp"), "xb") as o:
        o.write(f.read())
print(f"{n} ảnh → {a.out}")
