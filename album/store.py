"""Chỉ mục khuôn mặt: một file SQLite trong data dir + thư mục thumbs/."""
import hashlib
import os
import sqlite3

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime_ns INTEGER,
  width INTEGER, height INTEGER, det_size INTEGER, n_faces INTEGER);
CREATE TABLE IF NOT EXISTS faces (
  photo_id TEXT, idx INTEGER, x1 REAL, y1 REAL, x2 REAL, y2 REAL, score REAL, emb BLOB,
  PRIMARY KEY (photo_id, idx));
"""


def photo_id(relpath):
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:16]


def db_path(data_dir):
    return os.path.join(data_dir, "index.db")


def thumb_dir(data_dir):
    return os.path.join(data_dir, "thumbs")


def connect(data_dir):
    os.makedirs(thumb_dir(data_dir), exist_ok=True)
    db = sqlite3.connect(db_path(data_dir))
    db.executescript(SCHEMA)
    return db


def load_matrix(data_dir):
    """Trả về (E [N,512] float32, rows [N] -> dict mô tả khuôn mặt, photos {id: dict})."""
    if not os.path.exists(db_path(data_dir)):
        return np.zeros((0, 512), np.float32), [], {}
    db = sqlite3.connect(f"file:{db_path(data_dir)}?mode=ro", uri=True)
    photos = {r[0]: dict(id=r[0], path=r[1], width=r[2], height=r[3], n_faces=r[4])
              for r in db.execute("SELECT id, path, width, height, n_faces FROM photos")}
    embs, rows = [], []
    for pid, idx, x1, y1, x2, y2, sc, blob in db.execute(
            "SELECT photo_id, idx, x1, y1, x2, y2, score, emb FROM faces"):
        if pid not in photos:
            continue
        if len(blob) != 1024:
            raise RuntimeError("Chỉ mục có định dạng embedding cũ — chạy lại index_photos.py --rebuild")
        embs.append(np.frombuffer(blob, np.float16).astype(np.float32))
        rows.append(dict(photo_id=pid, idx=idx, bbox=[x1, y1, x2, y2], det_score=sc))
    db.close()
    E = np.stack(embs) if embs else np.zeros((0, 512), np.float32)
    return E, rows, photos
