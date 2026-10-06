"""Lập chỉ mục một thư mục ảnh: phát hiện mọi khuôn mặt, lưu embedding + bbox + thumbnail.

Chạy lại được: ảnh không đổi (cùng đường dẫn, kích thước, mtime, det-size) sẽ bị bỏ qua.
"""
import argparse
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageOps

import store
from faces import FaceEngine, load_image

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
THUMB_SIDE = 640


def find_photos(root):
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in EXTS and not f.startswith("."):
                out.append(os.path.relpath(os.path.join(d, f), root))
    return sorted(out)


def make_thumb(path, dest):
    with Image.open(path) as im:
        im.draft("RGB", (THUMB_SIDE * 2, THUMB_SIDE * 2))  # JPEG: giải mã nhanh ở độ phân giải thấp
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        im.save(dest, "JPEG", quality=80)
    return os.path.getsize(dest)


def load_job(root, rel, data_dir):
    """Chạy ở thread phụ: giải mã ảnh + làm thumbnail trong lúc luồng chính đang suy luận."""
    path = os.path.join(root, rel)
    t0 = time.perf_counter()
    try:
        rgb = load_image(path)
        t1 = time.perf_counter()
        make_thumb(path, os.path.join(store.thumb_dir(data_dir), store.photo_id(rel) + ".jpg"))
        return rel, rgb, t1 - t0, time.perf_counter() - t1, None
    except Exception as e:  # file hỏng: bỏ qua, cảnh báo
        return rel, None, 0, 0, e


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--photos", required=True, help="thư mục ảnh (quét đệ quy)")
    ap.add_argument("--data-dir", default=os.environ.get("ALBUM_DATA_DIR", "data"))
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", "models"))
    ap.add_argument("--det-size", type=int, default=int(os.environ.get("ALBUM_DET_SIZE", 1600)),
                    help="cạnh dài ảnh khi dò mặt (mặc định 1600; 640 nhanh hơn nhưng mất mặt nhỏ)")
    ap.add_argument("--min-face", type=int, default=24,
                    help="bỏ mặt có cạnh ngắn hơn N pixel (ảnh gốc) vì embedding kém tin cậy")
    ap.add_argument("--providers", default=os.environ.get("ALBUM_PROVIDERS", "CPUExecutionProvider"),
                    help="onnxruntime providers, cách nhau dấu phẩy")
    ap.add_argument("--rebuild", action="store_true", help="xoá chỉ mục cũ, làm lại từ đầu")
    ap.add_argument("--prune", action="store_true", help="xoá khỏi chỉ mục ảnh không còn trong thư mục")
    ap.add_argument("--limit", type=int, default=0, help="chỉ xử lý N ảnh đầu (thử nghiệm)")
    a = ap.parse_args()

    root = os.path.abspath(a.photos)
    if not os.path.isdir(root):
        sys.exit(f"Không thấy thư mục ảnh: {root}")
    if a.rebuild:
        for f in (store.db_path(a.data_dir),):
            if os.path.exists(f):
                os.remove(f)
    db = store.connect(a.data_dir)
    old_root = db.execute("SELECT value FROM meta WHERE key='photos_root'").fetchone()
    if old_root and old_root[0] != root:
        sys.exit(f"Chỉ mục này lập cho {old_root[0]}, không phải {root}. "
                 f"Dùng --data-dir khác hoặc --rebuild.")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('photos_root', ?)", (root,))

    rels = find_photos(root)
    if a.limit:
        rels = rels[:a.limit]
    known = {r[0]: r[1:] for r in db.execute("SELECT path, size, mtime_ns, det_size FROM photos")}
    todo, skipped = [], 0
    for rel in rels:
        st = os.stat(os.path.join(root, rel))
        if known.get(rel) == (st.st_size, st.st_mtime_ns, a.det_size):
            skipped += 1
        else:
            todo.append(rel)
    if a.prune:
        gone = set(known) - set(find_photos(root))
        for rel in gone:
            pid = store.photo_id(rel)
            db.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
            db.execute("DELETE FROM photos WHERE id=?", (pid,))
            tp = os.path.join(store.thumb_dir(a.data_dir), pid + ".jpg")
            if os.path.exists(tp):
                os.remove(tp)
        print(f"Đã xoá {len(gone)} ảnh không còn trong thư mục")
    print(f"{len(rels)} ảnh trong thư mục, {skipped} đã có (bỏ qua), {len(todo)} cần xử lý "
          f"(det-size {a.det_size})")

    eng = FaceEngine(a.model_dir, providers=a.providers.split(","))
    n_ok = n_bad = n_faces = 0
    t_dec = t_thumb = t_det = t_emb = 0.0
    wall0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        # chỉ giải mã trước vài ảnh: ảnh 45 MP đã giải mã nặng ~135 MB
        window = deque(pool.submit(load_job, root, rel, a.data_dir) for rel in todo[:3])
        nxt = len(window)
        for i in range(1, len(todo) + 1):
            rel, rgb, td, tt, err = window.popleft().result()
            if nxt < len(todo):
                window.append(pool.submit(load_job, root, todo[nxt], a.data_dir))
                nxt += 1
            if err is not None:
                n_bad += 1
                print(f"CẢNH BÁO: bỏ qua {rel}: {err}", file=sys.stderr)
                continue
            t0 = time.perf_counter()
            dets = [f for f in eng.detect(rgb, a.det_size) if f.size >= a.min_face]
            t1 = time.perf_counter()
            eng.embed(rgb, dets)
            t2 = time.perf_counter()
            h, w = rgb.shape[:2]
            st = os.stat(os.path.join(root, rel))
            pid = store.photo_id(rel)
            db.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
            db.execute("INSERT OR REPLACE INTO photos VALUES (?,?,?,?,?,?,?,?)",
                       (pid, rel, st.st_size, st.st_mtime_ns, w, h, a.det_size, len(dets)))
            for k, f in enumerate(dets):
                db.execute("INSERT INTO faces VALUES (?,?,?,?,?,?,?,?)",
                           (pid, k, *map(float, f.bbox), f.score, f.emb.astype(np.float16).tobytes()))
            db.commit()
            n_ok += 1
            n_faces += len(dets)
            t_dec += td
            t_thumb += tt
            t_det += t1 - t0
            t_emb += t2 - t1
            print(f"[{i}/{len(todo)}] {rel} {w}x{h}: {len(dets)} mặt "
                  f"(giải mã {td:.2f}s, dò {t1 - t0:.2f}s, nhúng {t2 - t1:.2f}s)", flush=True)
    wall = time.perf_counter() - wall0
    db.commit()
    total_photos, total_faces = db.execute("SELECT COUNT(*), COALESCE(SUM(n_faces),0) FROM photos").fetchone()
    db.close()
    summary = dict(processed=n_ok, failed=n_bad, skipped=skipped, faces_new=n_faces,
                   photos_total=total_photos, faces_total=total_faces, det_size=a.det_size,
                   wall_s=wall, wall_s_per_photo=wall / n_ok if n_ok else None,
                   decode_s_per_photo=t_dec / n_ok if n_ok else None,
                   thumb_s_per_photo=t_thumb / n_ok if n_ok else None,
                   detect_s_per_photo=t_det / n_ok if n_ok else None,
                   embed_s_per_photo=t_emb / n_ok if n_ok else None)
    with open(os.path.join(a.data_dir, "last_index_run.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(f"Xong: {n_ok} ảnh mới ({n_bad} lỗi), {n_faces} mặt, {wall:.1f}s"
          + (f" = {wall / n_ok:.2f}s/ảnh" if n_ok else "")
          + f". Tổng chỉ mục: {total_photos} ảnh, {total_faces} mặt.")


if __name__ == "__main__":
    main()
