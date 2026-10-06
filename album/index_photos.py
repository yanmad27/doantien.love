"""Lập chỉ mục một thư mục ảnh: phát hiện mọi khuôn mặt, lưu embedding + bbox + thumbnail.

Chạy lại được: ảnh không đổi (cùng đường dẫn, kích thước, mtime, det-size) sẽ bị bỏ qua.
"""
import argparse
import json
import os
import sys

from PIL import Image

import store
from faces import FaceEngine
from indexer import (DEFAULT_DET_SIZE, DEFAULT_MIN_FACE, compute_bursts, derive_pending, drop_stale_derivatives, find_photos, index_photos,
                     pending_photos, require_webp)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--photos", required=True, help="thư mục ảnh (quét đệ quy)")
    ap.add_argument("--data-dir", default=os.environ.get("ALBUM_DATA_DIR", store.DEFAULT_DATA_DIR))
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", store.DEFAULT_MODEL_DIR))
    ap.add_argument("--det-size", type=int, default=int(os.environ.get("ALBUM_DET_SIZE", DEFAULT_DET_SIZE)),
                    help="cạnh dài ảnh khi dò mặt (mặc định 1600; 640 nhanh hơn nhưng mất mặt nhỏ)")
    ap.add_argument("--min-face", type=int, default=DEFAULT_MIN_FACE,
                    help="bỏ mặt có cạnh ngắn hơn N pixel (ảnh gốc): ở ~29 px cosine so với bản full-res chỉ 0.05–0.78, ~44 px 0.70–0.95, ≥66 px 0.93–0.99")
    ap.add_argument("--providers", default=os.environ.get("ALBUM_PROVIDERS", "CPUExecutionProvider"),
                    help="onnxruntime providers, cách nhau dấu phẩy")
    ap.add_argument("--rebuild", action="store_true", help="xoá chỉ mục cũ, làm lại từ đầu")
    ap.add_argument("--prune", action="store_true", help="xoá khỏi chỉ mục ảnh không còn trong thư mục")
    ap.add_argument("--no-derive", action="store_true", help="không sinh WebP (240/480/2000) + photo_meta sau khi lập chỉ mục")
    ap.add_argument("--limit", type=int, default=0, help="chỉ xử lý N ảnh đầu (thử nghiệm)")
    a = ap.parse_args()
    Image.MAX_IMAGE_PIXELS = None  # ảnh cưới 45 MP+ là ảnh của chủ nhà; chỉ nới ở indexer, không ở server

    require_webp()
    root = os.path.abspath(a.photos)
    if not os.path.isdir(root):
        sys.exit(f"Không thấy thư mục ảnh: {root}")
    owner_state = ([], [])
    if a.rebuild:
        owner_state = store.keep_owner_state(store.db_path(a.data_dir))  # ảnh đã ẩn + cài đặt của chủ nhà sống sót qua --rebuild
        for f in (store.db_path(a.data_dir),):
            for suffix in ("", "-wal", "-shm"):  # -wal còn sót sẽ bị SQLite áp lên DB mới
                if os.path.exists(f + suffix):
                    os.remove(f + suffix)
    db = store.connect(a.data_dir)
    store.restore_owner_state(db, *owner_state)
    old_root = db.execute("SELECT value FROM meta WHERE key='photos_root'").fetchone()
    if old_root and old_root[0] != root:
        sys.exit(f"Chỉ mục này lập cho {old_root[0]}, không phải {root}. "
                 f"Dùng --data-dir khác hoặc --rebuild.")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('photos_root', ?)", (root,))
    db.commit()  # để rollback của một ảnh lỗi không cuốn theo dòng này

    rels = find_photos(root)
    if a.limit:
        rels = rels[:a.limit]
    known = {r[0] for r in db.execute("SELECT path FROM photos")}
    todo, skipped = pending_photos(db, root, a.det_size, rels)
    if a.prune:
        gone = known - set(find_photos(root))
        for rel in gone:
            pid = store.photo_id(rel)
            db.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
            db.execute("DELETE FROM photos WHERE id=?", (pid,))
            db.execute("DELETE FROM photo_meta WHERE id=?", (pid,))
            drop_stale_derivatives(a.data_dir, pid)
            for d in (store.thumb_dir(a.data_dir), store.preview_dir(a.data_dir)):
                tp = os.path.join(d, pid + ".jpg")
                if os.path.exists(tp):
                    os.remove(tp)
        db.commit()  # chốt phần xoá trước khi lập chỉ mục tiếp (một ảnh lỗi sau đó rollback sẽ không cuốn theo)
        print(f"Đã xoá {len(gone)} ảnh không còn trong thư mục")
    print(f"{len(rels)} ảnh trong thư mục, {skipped} đã có (bỏ qua), {len(todo)} cần xử lý "
          f"(det-size {a.det_size})")

    eng = FaceEngine(a.model_dir, providers=a.providers.split(","))

    def progress(i, n, rel, info):
        if info["err"] is not None:
            print(f"CẢNH BÁO: bỏ qua {rel}: {info['err']}", file=sys.stderr)
        else:
            print(f"[{i}/{n}] {rel} {info['w']}x{info['h']}: {info['faces']} mặt "
                  f"(giải mã {info['td']:.2f}s, dò {info['tdet']:.2f}s, nhúng {info['temb']:.2f}s)", flush=True)

    st = index_photos(db, eng, root, a.data_dir, todo, a.det_size, a.min_face, on_progress=progress)
    if not a.no_derive:
        dst = derive_pending(db, root, a.data_dir,
                             on_progress=lambda i, n, rel, err: print(f"[WebP {i}/{n}] {rel}" + (f" LỖI: {err}" if err else ""), flush=True))
        compute_bursts(db)
        print(f"WebP: {dst['ok']} ảnh ({dst['bad']} lỗi) trong {dst['wall']:.1f}s")
    n_ok, n_bad, n_faces, wall = st["ok"], st["bad"], st["faces"], st["wall"]
    db.commit()
    total_photos, total_faces = db.execute("SELECT COUNT(*), COALESCE(SUM(n_faces),0) FROM photos").fetchone()
    db.close()
    summary = dict(processed=n_ok, failed=n_bad, skipped=skipped, faces_new=n_faces,
                   photos_total=total_photos, faces_total=total_faces, det_size=a.det_size,
                   wall_s=wall, wall_s_per_photo=wall / n_ok if n_ok else None,
                   decode_s_per_photo=st["td"] / n_ok if n_ok else None,
                   thumb_s_per_photo=st["tt"] / n_ok if n_ok else None,
                   detect_s_per_photo=st["tdet"] / n_ok if n_ok else None,
                   embed_s_per_photo=st["temb"] / n_ok if n_ok else None)
    with open(os.path.join(a.data_dir, "last_index_run.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(f"Xong: {n_ok} ảnh mới ({n_bad} lỗi), {n_faces} mặt, {wall:.1f}s"
          + (f" = {wall / n_ok:.2f}s/ảnh" if n_ok else "")
          + f". Tổng chỉ mục: {total_photos} ảnh, {total_faces} mặt.")


if __name__ == "__main__":
    main()
