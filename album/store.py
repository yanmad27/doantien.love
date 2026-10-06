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
CREATE TABLE IF NOT EXISTS failed (  -- ảnh không xử lý được: không thử lại mỗi lượt (trừ khi file đổi)
  path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS originals (  -- ảnh gốc tải lên qua web: tên đã chuẩn hoá + SHA-256 để chống trùng
  name TEXT PRIMARY KEY, sha256 TEXT UNIQUE, size INTEGER);
CREATE TABLE IF NOT EXISTS derive_failed (  -- WebP không sinh được: không thử lại mỗi lượt Xử lý (trừ khi file gốc đổi); KHÔNG liên quan failed/rejected
  id TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT, deriv_ver INTEGER);
CREATE TABLE IF NOT EXISTS photo_meta (  -- bảng riêng (không thêm cột vào photos: INSERT positional của indexer + rollback về bản cũ)
  id TEXT PRIMARY KEY, taken REAL, v TEXT, color TEXT, deriv_ver INTEGER, w INTEGER, h INTEGER,
  size INTEGER, mtime_ns INTEGER,  -- size/mtime_ns của file gốc lúc tạo: đổi file thì tạo lại
  cam TEXT, sig BLOB, burst TEXT);  -- máy ảnh (Make|Model|Serial), chữ ký 16x16 xám từ thumb 240, nhóm chụp liên tiếp (id khung đầu; NULL = đứng một mình)
"""
META_COLUMNS = (("cam", "TEXT"), ("sig", "BLOB"), ("burst", "TEXT"))
EXTRA_COLUMNS = {"photo_meta": META_COLUMNS, "derive_failed": (("deriv_ver", "INTEGER"),)}  # thêm vào bảng photo_meta đã có từ bản trước (ALTER, an toàn)



HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(HERE, "data")
DEFAULT_MODEL_DIR = os.path.join(HERE, "models")


def photo_id(relpath):
    return hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:16]


def db_path(data_dir):
    return os.path.join(data_dir, "index.db")


def thumb_dir(data_dir):
    return os.path.join(data_dir, "thumbs")


def preview_dir(data_dir):
    return os.path.join(data_dir, "previews")


def deriv_dir(data_dir):
    """WebP đặt tên theo nội dung: {id}-{v}-{size}.webp"""
    return os.path.join(data_dir, "i")


def rejected_dir(data_dir):
    return os.path.join(data_dir, "rejected")


def originals_dir(data_dir):
    return os.path.join(data_dir, "originals")


def connect(data_dir, init=True):
    """init=False: bỏ qua tạo bảng (mở kết nối mỗi request không nên chạy executescript — chồng chéo với
    giao dịch của kết nối khác dễ gây 'disk I/O error' của SQLite)."""
    os.makedirs(thumb_dir(data_dir), exist_ok=True)
    db = sqlite3.connect(db_path(data_dir), timeout=30)
    if init:
        try:  # WAL: người đọc (manifest, thống kê, tìm kiếm) không bao giờ chờ người ghi (Xử lý/tải lên) commit + fsync; bản cũ vẫn đọc được DB này
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # đang có kết nối khác giữ khoá: để lần khởi động sau
        db.executescript(SCHEMA)
        for table, cols in EXTRA_COLUMNS.items():
            have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols:
                if col not in have:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        db.commit()
    return db


def connect_ro(data_dir, timeout=30):
    """Kết nối chỉ đọc cho request. KHÔNG dùng URI mode=ro: mở/đóng kết nối mode=ro trong cùng process làm mọi lần ghi của
    kết nối khác báo 'disk I/O error' (tái hiện được với SQLite 3.51/macOS); kết nối thường + query_only thì an toàn."""
    db = sqlite3.connect(db_path(data_dir), timeout=timeout)
    db.execute("PRAGMA query_only=ON")
    return db


def load_matrix(data_dir):
    """Trả về (E [N,512] float32, rows [N] -> dict mô tả khuôn mặt, photos {id: dict})."""
    if not os.path.exists(db_path(data_dir)):
        return np.zeros((0, 512), np.float32), [], {}
    db = connect_ro(data_dir)
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
