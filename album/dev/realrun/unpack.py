"""Chỉ dùng khi phát triển: giải nén AN TOÀN các file zip ảnh (dữ liệu không tin cậy) rồi dồn ảnh vào MỘT thư mục phẳng.
  python3 -I unpack.py --zips Z1.zip Z2.zip … --work WORKDIR --flat FLATDIR
Mỗi zip giải vào WORKDIR/unz-N (thư mục MỚI, phải chưa tồn tại); từ chối cả zip nếu có đường dẫn tuyệt đối, '..', liên kết tượng trưng hoặc tên thiết bị;
chỉ lấy .jpg/.jpeg/.png/.webp; FLATDIR phải rỗng/chưa có; tên trùng (KHÔNG phân biệt hoa/thường, vì APFS không phân biệt) → dừng, không bao giờ ghi đè.
Trước khi giải nén: cộng dung lượng các file sẽ lấy, dừng nếu ổ còn trống < tổng + 5 GB. Không chạy gì từ file trong zip.
Bị dừng giữa chừng: xoá các thư mục WORKDIR/unz-* và FLATDIR mà lần chạy này đã tạo rồi chạy lại từ đầu (KHÔNG đụng tới các file zip)."""
import argparse, os, shutil, stat, sys, zipfile

EXTS = (".jpg", ".jpeg", ".png", ".webp")
HEADROOM = 5 * 1024 ** 3
ap = argparse.ArgumentParser()
ap.add_argument("--zips", nargs="+", required=True); ap.add_argument("--work", required=True); ap.add_argument("--flat", required=True)
a = ap.parse_args()
if os.path.exists(a.flat) and os.listdir(a.flat):
    sys.exit(f"DỪNG: {a.flat} không rỗng")
for n in range(1, len(a.zips) + 1):
    if os.path.exists(os.path.join(a.work, f"unz-{n}")):
        sys.exit(f"DỪNG: {os.path.join(a.work, f'unz-{n}')} đã tồn tại (mỗi zip phải vào thư mục mới; xoá thư mục cũ của lần chạy hỏng)")

def wanted(i):
    base = os.path.basename(i.filename.replace("\\", "/"))
    return None if i.is_dir() or not i.filename.lower().endswith(EXTS) or not base or base.startswith(".") else base

# ---- lượt 1: kiểm đường dẫn, tên trùng, dung lượng — chưa ghi gì ----
seen, total = {}, 0
for zp in a.zips:
    with zipfile.ZipFile(zp) as z:
        for i in z.infolist():
            nm = i.filename
            parts = nm.replace("\\", "/").split("/")
            if nm.startswith(("/", "\\")) or (len(nm) > 1 and nm[1] == ":") or ".." in parts or "\x00" in nm:
                sys.exit(f"DỪNG: {zp}: đường dẫn không an toàn {nm!r}")
            if stat.S_ISLNK(i.external_attr >> 16):
                sys.exit(f"DỪNG: {zp}: liên kết tượng trưng {nm!r}")
            base = wanted(i)
            if base is None:
                continue
            if base.lower() in seen:
                sys.exit(f"DỪNG: tên trùng (không phân biệt hoa/thường): {base} ({seen[base.lower()]} và {zp})")
            seen[base.lower()] = zp; total += i.file_size
os.makedirs(a.flat, exist_ok=True); os.makedirs(a.work, exist_ok=True)
for d in {os.stat(a.flat).st_dev: a.flat, os.stat(a.work).st_dev: a.work}.values():
    free = shutil.disk_usage(d).free
    if free < total + HEADROOM:
        sys.exit(f"DỪNG: ổ của {d} còn {free / 1e9:.1f} GB, cần ≥ {(total + HEADROOM) / 1e9:.1f} GB (ảnh {total / 1e9:.1f} GB + 5 GB dự phòng)")
print(f"{len(seen)} ảnh, {total / 1e9:.1f} GB; ổ đủ chỗ", flush=True)

# ---- lượt 2: giải nén ----
for n, zp in enumerate(a.zips, 1):
    dest = os.path.join(a.work, f"unz-{n}"); os.makedirs(dest); k = 0
    with zipfile.ZipFile(zp) as z:
        for i in z.infolist():
            base = wanted(i)
            if base is None:
                continue
            tgt = os.path.join(a.flat, base)
            if os.path.lexists(tgt):  # APFS không phân biệt hoa/thường: lexists bắt cả tên chỉ khác hoa/thường
                sys.exit(f"DỪNG: {tgt} đã có, không ghi đè")
            with z.open(i) as src, open(os.path.join(dest, base), "xb") as dst:  # chỉ ghi byte, không giải mã
                shutil.copyfileobj(src, dst, 1 << 20)
            os.rename(os.path.join(dest, base), tgt); k += 1  # rename (không replace): lỗi chứ không ghi đè
    print(f"{zp}: {k} ảnh → {dest} → {a.flat}", flush=True)
print(f"Xong: {len(seen)} ảnh trong {a.flat}")
