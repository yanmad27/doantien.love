"""Lõi lập chỉ mục dùng chung cho CLI (index_photos.py) và nút "Xử lý" của server.

Với mỗi ảnh: giải mã, dò mọi khuôn mặt, nhúng, ghi vào SQLite, làm thumbnail 640 px và
bản xem 2000 px. Mỗi ảnh một giao dịch, ảnh hỏng chỉ bị báo lỗi chứ không dừng cả lượt.
"""
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageOps

import store
from faces import load_image

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
THUMB_SIDE = 640
PREVIEW_SIDE = 2000
DEFAULT_MIN_FACE = 40
DEFAULT_DET_SIZE = 1600


def find_photos(root):
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in EXTS and not f.startswith("."):
                out.append(os.path.relpath(os.path.join(d, f), root))
    return sorted(out)


def pending_photos(db, root, det_size, rels=None):
    """Ảnh chưa có trong chỉ mục (hoặc đổi kích thước/mtime/det-size). Trả (todo, số đã có)."""
    rels = find_photos(root) if rels is None else rels
    known = {r[0]: r[1:] for r in db.execute("SELECT path, size, mtime_ns, det_size FROM photos")}
    todo = []
    for rel in rels:
        st = os.stat(os.path.join(root, rel))
        if known.get(rel) != (st.st_size, st.st_mtime_ns, det_size):
            todo.append(rel)
    return todo, len(rels) - len(todo)


def make_derivatives(path, data_dir, pid):
    """Một lần mở ảnh → thumbnail 640 px + bản xem 2000 px (JPEG, đã xoay theo EXIF)."""
    with Image.open(path) as im:
        im.draft("RGB", (PREVIEW_SIDE, PREVIEW_SIDE))  # JPEG: giải mã nhanh ở độ phân giải thấp
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
        pv = os.path.join(store.preview_dir(data_dir), pid + ".jpg")
        im.save(pv + ".part", "JPEG", quality=85)
        os.replace(pv + ".part", pv)
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        th = os.path.join(store.thumb_dir(data_dir), pid + ".jpg")
        im.save(th + ".part", "JPEG", quality=80)
        os.replace(th + ".part", th)


def _load_job(root, rel, data_dir, max_pixels):
    """Chạy ở thread phụ: giải mã + làm thumbnail/preview trong lúc luồng chính đang suy luận."""
    path = os.path.join(root, rel)
    t0 = time.perf_counter()
    try:
        rgb = load_image(path, max_pixels)
        t1 = time.perf_counter()
        make_derivatives(path, data_dir, store.photo_id(rel))
        return rel, rgb, t1 - t0, time.perf_counter() - t1, None
    except Exception as e:  # file hỏng: bỏ qua, báo lỗi
        return rel, None, 0, 0, e


def index_photos(db, engine, root, data_dir, todo, det_size=DEFAULT_DET_SIZE, min_face=DEFAULT_MIN_FACE,
                 window=3, workers=2, max_pixels=None, on_progress=None, should_stop=None):
    """Xử lý danh sách `todo` (đường dẫn tương đối trong `root`). `db` phải tạo ở luồng gọi hàm này.

    window = số ảnh giải mã trước (ảnh 45 MP giải mã xong nặng ~135 MB mỗi ảnh).
    on_progress(i, n, rel, info) với info = dict(ok, faces, w, h, err, td, tt, tdet, temb).
    Trả về dict thống kê.
    """
    st = dict(ok=0, bad=0, faces=0, td=0.0, tt=0.0, tdet=0.0, temb=0.0)
    os.makedirs(store.thumb_dir(data_dir), exist_ok=True)
    os.makedirs(store.preview_dir(data_dir), exist_ok=True)
    wall0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submit = lambda rel: pool.submit(_load_job, root, rel, data_dir, max_pixels)
        win = deque(submit(rel) for rel in todo[:window])
        nxt = len(win)
        for i in range(1, len(todo) + 1):
            rel, rgb, td, tt, err = win.popleft().result()
            if should_stop and should_stop():
                break
            if nxt < len(todo):
                win.append(submit(todo[nxt]))
                nxt += 1
            info = dict(ok=False, faces=0, w=0, h=0, err=None, td=td, tt=tt, tdet=0.0, temb=0.0)
            if err is None:
                try:
                    t0 = time.perf_counter()
                    dets = [f for f in engine.detect(rgb, det_size) if f.size >= min_face]
                    t1 = time.perf_counter()
                    engine.embed(rgb, dets)
                    t2 = time.perf_counter()
                    h, w = rgb.shape[:2]
                    fst = os.stat(os.path.join(root, rel))
                    pid = store.photo_id(rel)
                    db.execute("DELETE FROM faces WHERE photo_id=?", (pid,))
                    db.execute("INSERT OR REPLACE INTO photos VALUES (?,?,?,?,?,?,?,?)",
                               (pid, rel, fst.st_size, fst.st_mtime_ns, w, h, det_size, len(dets)))
                    for k, f in enumerate(dets):
                        db.execute("INSERT INTO faces VALUES (?,?,?,?,?,?,?,?)",
                                   (pid, k, *map(float, f.bbox), f.score, f.emb.astype(np.float16).tobytes()))
                    db.commit()
                    info.update(ok=True, faces=len(dets), w=w, h=h, tdet=t1 - t0, temb=t2 - t1)
                except Exception as e:  # một ảnh hỏng không được làm hỏng cả lượt chạy
                    db.rollback()
                    err = e
            rgb = None
            if err is not None:
                info["err"] = err
                st["bad"] += 1
            else:
                st["ok"] += 1
                st["faces"] += info["faces"]
                for k in ("td", "tt", "tdet", "temb"):
                    st[k] += info[k]
            if on_progress:
                on_progress(i, len(todo), rel, info)
    st["wall"] = time.perf_counter() - wall0
    return st
