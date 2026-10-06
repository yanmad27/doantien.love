"""Web app album.doantien.love (không mật khẩu): tải ảnh lên → nút "Xử lý" lập chỉ mục
nền → khách tải selfie lên và tìm ảnh có mặt mình.

Mọi thứ bền vững nằm trong DATA_DIR (ảnh gốc, thumbnail, bản xem, index.db). Selfie chỉ nằm
trong RAM: gửi dạng body thô (không multipart nên không có file tạm), xử lý xong là bỏ.
"""
import argparse
import hashlib
import logging
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

import store
from faces import QUERY_MAX_PIXELS, FaceEngine, ImageTooLarge, ImageUnreadable, load_image
from indexer import (DEFAULT_DET_SIZE, DEFAULT_MIN_FACE, find_photos, index_photos, mark_failed, pending_photos,
                     recover_crashed)

log = logging.getLogger("album")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 25 * 1024 * 1024
SCORE_FLOOR = 0.15  # dưới mức này không bao giờ là cùng người, khỏi gửi về
MAX_RESULTS = 300
QUERY_MIN_FACE = 40  # cạnh ngắn (px) tối thiểu của mặt trong selfie, khớp --min-face của indexer
UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
UPLOAD_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}  # MPO: JPEG máy ảnh/điện thoại có kèm ảnh xem trước (MPF)
UPLOAD_MAX_PIXELS = 64_000_000  # ảnh bom PNG nhỏ khai báo hàng trăm MP ngốn ~18 B/điểm ảnh khi giải mã; 64 MP đủ cho máy ảnh 61 MP
MAX_UPLOAD_SLOTS = 4      # số upload ghi đĩa cùng lúc (toàn cục)
MAX_SEARCH_SLOTS = 2      # số truy vấn selfie tính toán cùng lúc (mỗi cái có thể ~0.7 GB RAM)
MAX_BODY_SLOTS = 8        # số body selfie đang nhận cùng lúc (mỗi cái ≤ 25 MB)
DISK_CHECK_EVERY = 8 * 1024 * 1024
PHOTO_ID = re.compile(r"^[0-9a-f]{16}$")
INDEX_WINDOW = int(os.environ.get("ALBUM_INDEX_WINDOW", 1))  # số ảnh giải mã cùng lúc khi xử lý: 45 MP giải mã xong ~135 MB/ảnh, giữ RAM đỉnh thấp


class NoIndexMiddleware:
    """Mọi response mang X-Robots-Tag: noindex + vài header an toàn cơ bản."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send2(msg):
            if msg["type"] == "http.response.start":
                msg.setdefault("headers", []).extend([
                    (b"x-robots-tag", b"noindex, nofollow"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer")])
            await send(msg)
        await self.app(scope, receive, send2 if scope["type"] == "http" else send)


def safe_name(raw):
    """Tên file an toàn: chỉ lấy phần cuối, bỏ ký tự lạ, đuôi chữ thường. Trả None nếu đuôi không cho phép."""
    base = unicodedata.normalize("NFC", raw.replace("\\", "/").split("/")[-1]).strip()
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in UPLOAD_EXTS:
        return None
    stem = re.sub(r"[^\w.\- ()]", "_", stem).strip(" .")[:100] or "photo"
    while len(stem.encode("utf-8")) > 150:  # tên file tối đa 255 byte; chừa chỗ cho hậu tố -<sha8> và đuôi
        stem = stem[:-1]
    return stem.rstrip(" .") + ext


class ProcessJob:
    """Một lượt "Xử lý" chạy nền trong process server, dùng lại model đã nạp. Chỉ một lượt một lúc."""

    def __init__(self, data_dir, engine, det_size, min_face):
        self.data_dir, self.engine, self.det_size, self.min_face = data_dir, engine, det_size, min_face
        self.lock = threading.Lock()
        self.s = dict(state="idle", total=0, done=0, failed=0, faces=0, current=None, errors=[], rejected=[],
                      started_at=None, finished_at=None, elapsed=0.0)

    def status(self):
        with self.lock:
            s = dict(self.s, errors=list(self.s["errors"]), rejected=list(self.s["rejected"]))
        if s["state"] == "running":
            s["elapsed"] = time.time() - s["started_at"]
        return s

    def start(self):
        """True nếu bắt đầu được; False nếu đang có lượt chạy."""
        with self.lock:
            if self.s["state"] == "running":
                return False
            self.s = dict(state="running", total=0, done=0, failed=0, faces=0, current=None, errors=[],
                          rejected=self.s["rejected"], started_at=time.time(), finished_at=None, elapsed=0.0)
        threading.Thread(target=self._run, daemon=True, name="process").start()
        return True

    def _clean(self, e):
        """Lỗi gửi ra ngoài: chỉ loại lỗi + thông điệp đã bỏ mọi đường dẫn tuyệt đối."""
        return f"{type(e).__name__}: " + re.sub(r"(/[^\s:'\"]+)+", lambda m: os.path.basename(m.group(0)), str(e))[:200]

    def recover(self, db):
        """File làm process chết ở lần trước (OOM khi giải mã…) → chuyển sang DATA_DIR/rejected/ và báo trong trạng thái."""
        moved = recover_crashed(db, store.originals_dir(self.data_dir), self.data_dir)
        for rel in moved:
            mark_failed(db, store.rejected_dir(self.data_dir), os.path.basename(rel), "làm sập khi giải mã")
        if moved:
            with self.lock:
                self.s["rejected"] = self.s["rejected"] + moved
        return moved

    def _upd(self, **kw):
        with self.lock:
            self.s.update(kw)

    def _run(self):
        try:
            db = store.connect(self.data_dir, init=False)  # kết nối riêng của luồng này (bảng đã tạo lúc khởi động)
            root = store.originals_dir(self.data_dir)
            self.recover(db)
            todo, _ = pending_photos(db, root, self.det_size)
            self._upd(total=len(todo))

            def progress(i, n, rel, info):
                with self.lock:
                    self.s["done"] = i
                    self.s["current"] = rel
                    if info["err"] is not None:
                        self.s["failed"] += 1
                        self.s["errors"] = (self.s["errors"] + [f"{rel}: {self._clean(info['err'])}"])[-20:]
                    else:
                        self.s["faces"] += info["faces"]

            index_photos(db, self.engine, root, self.data_dir, todo, self.det_size, self.min_face,
                         window=INDEX_WINDOW, workers=1, max_pixels=UPLOAD_MAX_PIXELS, on_progress=progress)
            db.close()
            now = time.time()
            self._upd(state="done", current=None, finished_at=now, elapsed=now - self.s["started_at"])
        except Exception as e:
            log.exception("xử lý lỗi")
            self._upd(state="error", current=None, finished_at=time.time(), elapsed=time.time() - self.s["started_at"],
                      errors=self.s["errors"] + [f"Lỗi chung: {self._clean(e)}"])


def create_app(data_dir, model_dir, providers, query_det_size=640, threads=0,
               max_upload=60 * 1024 * 1024, det_size=DEFAULT_DET_SIZE, min_free=5120 * 1024 * 1024,
               max_total=20 * 1024 ** 3):
    app = FastAPI(title="Tìm ảnh cưới", docs_url=None, redoc_url=None, openapi_url=None)  # docs mặc định nạp JS từ CDN
    for d in (store.originals_dir(data_dir), store.thumb_dir(data_dir), store.preview_dir(data_dir),
              os.path.join(data_dir, "tmp")):
        os.makedirs(d, exist_ok=True)
    engine = FaceEngine(model_dir, providers=providers, threads=threads)
    job = ProcessJob(data_dir, engine, det_size, DEFAULT_MIN_FACE)
    cache = {"mtime": None, "E": None, "rows": None, "photos": None}
    lock = threading.Lock()
    upload_lock = threading.Lock()
    tmp_dir = os.path.join(data_dir, "tmp")
    for f in os.listdir(tmp_dir):  # upload dở dang của lần chạy trước
        os.remove(os.path.join(tmp_dir, f))
    upload_slots = threading.BoundedSemaphore(MAX_UPLOAD_SLOTS)
    search_slots = threading.BoundedSemaphore(MAX_SEARCH_SLOTS)
    body_slots = threading.BoundedSemaphore(MAX_BODY_SLOTS)
    quota = {"reserved": 0}
    quota_lock = threading.Lock()
    _db = store.connect(data_dir)  # tạo bảng một lần ở đây; lần chạy trước chết giữa lúc xử lý một ảnh → loại ảnh đó ngay
    job.recover(_db)
    stored = {"bytes": _db.execute("SELECT COALESCE(SUM(size),0) FROM originals").fetchone()[0]}  # tổng ảnh gốc, giữ trong RAM
    _db.close()

    def stored_bytes():
        return stored["bytes"]

    class Busy(Exception):
        pass

    def search_gated(body):
        if not search_slots.acquire(blocking=False):
            raise Busy
        try:
            return search(body)
        finally:
            search_slots.release()

    def index():
        p = store.db_path(data_dir)
        m = os.stat(p).st_mtime_ns if os.path.exists(p) else None
        with lock:
            if cache["mtime"] != m or cache["E"] is None:
                cache["E"], cache["rows"], cache["photos"] = store.load_matrix(data_dir)
                cache["mtime"] = m
            return cache["E"], cache["rows"], cache["photos"]

    def search(body):
        t0 = time.perf_counter()
        rgb = load_image(body, QUERY_MAX_PIXELS)
        t1 = time.perf_counter()
        faces = engine.detect(rgb, query_det_size)
        t2 = time.perf_counter()
        if not faces:
            return None
        usable = [f for f in faces if f.size >= QUERY_MIN_FACE]
        if not usable:
            return "face_too_small"
        # mặt to và chắc chắn nhất = người chụp selfie (diện tích x điểm dò; mặt tí hon bị bỏ)
        face = max(usable, key=lambda f: f.area * f.score)
        engine.embed(rgb, [face])
        t3 = time.perf_counter()
        E, rows, photos = index()
        sims = E @ face.emb if len(E) else np.zeros(0, np.float32)
        best = {}
        for i in np.flatnonzero(sims >= SCORE_FLOOR):
            pid = rows[i]["photo_id"]
            if pid not in best or sims[i] > sims[best[pid]]:
                best[pid] = i
        res = []
        ranked = sorted(best.items(), key=lambda kv: -sims[kv[1]])
        for pid, i in ranked[:MAX_RESULTS]:
            ph = photos[pid]
            res.append(dict(id=pid, name=ph["path"], score=round(float(sims[i]), 4),
                            thumb=f"thumbs/{pid}.jpg", preview=f"previews/{pid}.jpg", width=ph["width"], height=ph["height"],
                            n_faces=ph["n_faces"], bbox=[round(v, 1) for v in rows[i]["bbox"]]))
        t4 = time.perf_counter()
        return dict(
            query=dict(n_faces=len(usable), n_ignored=len(faces) - len(usable), bbox=[round(float(v), 1) for v in face.bbox],
                       width=rgb.shape[1], height=rgb.shape[0], det_score=round(face.score, 3)),
            results=res, total_matches=len(ranked), truncated=len(ranked) > MAX_RESULTS,
            indexed_photos=len(photos), indexed_faces=len(rows),
            timing_ms=dict(decode=(t1 - t0) * 1e3, detect=(t2 - t1) * 1e3, embed=(t3 - t2) * 1e3,
                           compare=(t4 - t3) * 1e3, total=(t4 - t0) * 1e3))

    @app.post("/api/search")
    async def api_search(request: Request):
        if not body_slots.acquire(blocking=False):
            raise HTTPException(503, "Máy chủ đang bận, hãy thử lại sau ít giây.")
        try:
            chunks, n = [], 0
            async for c in request.stream():
                n += len(c)
                if n > MAX_BODY:
                    raise HTTPException(413, "Ảnh quá lớn (tối đa 25 MB)")
                chunks.append(c)
            body = b"".join(chunks)
        finally:
            body_slots.release()
        if not body:
            raise HTTPException(400, "Không có dữ liệu ảnh")
        try:
            out = await run_in_threadpool(search_gated, body)
        except Busy:
            raise HTTPException(503, "Máy chủ đang bận, hãy thử lại sau ít giây.")
        except ImageTooLarge:
            raise HTTPException(413, "Ảnh quá lớn (số điểm ảnh vượt giới hạn). Hãy chọn ảnh nhỏ hơn.")
        except ImageUnreadable:
            raise HTTPException(400, "Không đọc được ảnh này. Hãy thử file JPG hoặc PNG khác.")
        except Exception:
            log.exception("search lỗi")
            raise HTTPException(500, "Máy chủ gặp lỗi khi xử lý. Vui lòng thử lại sau.")
        if out is None or out == "face_too_small":
            return JSONResponse({"error": out or "no_face"}, status_code=422)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @app.get("/api/stats")
    def stats():
        _, rows, photos = index()
        db = store.connect(data_dir, init=False)
        try:
            todo, _ = pending_photos(db, store.originals_dir(data_dir), det_size)
            n_orig = len(find_photos(store.originals_dir(data_dir)))
        finally:
            db.close()
        du = shutil.disk_usage(data_dir)
        return dict(photos=len(photos), faces=len(rows), originals=n_orig, pending=len(todo),
                    max_upload_mb=max_upload // 2**20, disk_free=du.free, disk_total=du.total,
                    stored_bytes=stored_bytes(), max_total_bytes=max_total)

    async def reject(request, status, msg):
        n = 0
        async for c in request.stream():  # đọc bỏ (có giới hạn) phần body để trình duyệt nhận được thông báo thay vì lỗi mạng
            n += len(c)
            if n > max_upload:
                break
        raise HTTPException(status, msg)

    # ---------- tải ảnh lên: body thô theo từng file, ghi thẳng ra đĩa theo từng mảnh ----------
    @app.post("/api/upload")
    async def api_upload(request: Request, name: str = ""):
        fname = safe_name(name)
        if fname is None:
            raise HTTPException(400, "Chỉ nhận ảnh .jpg, .jpeg, .png, .webp")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_upload:
            raise HTTPException(413, f"Ảnh quá lớn (tối đa {max_upload // 2**20} MB)")
        if not upload_slots.acquire(blocking=False):
            await reject(request, 503, "Máy chủ đang nhận nhiều ảnh cùng lúc, hãy thử lại sau ít giây.")
        reserved = False
        part = os.path.join(tmp_dir, uuid.uuid4().hex + ".part")
        try:
            if shutil.disk_usage(data_dir).free < min_free:
                await reject(request, 507, f"Ổ đĩa sắp đầy (còn dưới {min_free // 2**20} MB trống), không nhận thêm ảnh. Hãy xoá bớt hoặc mở rộng dung lượng.")
            with quota_lock:  # đặt chỗ trước dung lượng tối đa của file này; trả lại trong finally
                if stored_bytes() + quota["reserved"] + max_upload <= max_total:
                    quota["reserved"] += max_upload
                    reserved = True
            if not reserved:
                await reject(request, 507, f"Album đã đạt giới hạn dung lượng ({max_total // 1024 ** 3} GB), không nhận thêm ảnh.")
            h, n, checked = hashlib.sha256(), 0, 0
            with open(part, "wb") as fh:
                async for c in request.stream():
                    n += len(c)
                    if n > max_upload:
                        raise HTTPException(413, f"Ảnh quá lớn (tối đa {max_upload // 2**20} MB)")
                    if n - checked >= DISK_CHECK_EVERY:  # đĩa có thể đầy dần trong lúc nhiều upload chạy song song
                        checked = n
                        if shutil.disk_usage(data_dir).free < min_free:
                            raise HTTPException(507, "Ổ đĩa sắp đầy, dừng nhận ảnh.")
                    h.update(c)
                    fh.write(c)
            if n == 0:
                raise HTTPException(400, "File rỗng")
            return await run_in_threadpool(finalize_upload, part, fname, h.hexdigest(), n)
        finally:
            if os.path.exists(part):
                os.remove(part)
            if reserved:
                with quota_lock:
                    quota["reserved"] -= max_upload
            upload_slots.release()

    def finalize_upload(part, fname, digest, size):
        try:
            with Image.open(part) as im:  # chỉ đọc header: kiểm định dạng thật + chặn ảnh bom
                fmt, (w, hgt) = im.format, im.size
        except Exception:
            raise HTTPException(400, "Không đọc được ảnh này")
        if fmt not in UPLOAD_FORMATS:
            raise HTTPException(400, "Định dạng ảnh không được hỗ trợ")
        if w * hgt > UPLOAD_MAX_PIXELS:
            raise HTTPException(413, "Ảnh có quá nhiều điểm ảnh")
        with upload_lock:
            db = store.connect(data_dir, init=False)
            try:
                dup = db.execute("SELECT name FROM originals WHERE sha256=?", (digest,)).fetchone()
                if dup:
                    if not os.path.exists(os.path.join(store.originals_dir(data_dir), dup[0])):
                        raise HTTPException(400, "Ảnh này đã bị loại trước đó vì không xử lý được.")
                    return dict(status="duplicate", name=dup[0])
                final = fname
                root = store.originals_dir(data_dir)
                if db.execute("SELECT 1 FROM originals WHERE name=?", (final,)).fetchone() or \
                        os.path.exists(os.path.join(root, final)):
                    stem, ext = os.path.splitext(fname)
                    final = f"{stem}-{digest[:8]}{ext}"  # cùng tên, khác nội dung: không ghi đè
                os.replace(part, os.path.join(root, final))
                db.execute("INSERT INTO originals VALUES (?,?,?)", (final, digest, size))
                db.commit()
                stored["bytes"] += size
            finally:
                db.close()
        return dict(status="ok", name=final, renamed=final != fname, width=w, height=hgt)

    # ---------- xử lý nền ----------
    @app.post("/api/process")
    def api_process():
        if not job.start():
            return JSONResponse(dict(started=False, message="Đang xử lý rồi — hãy đợi lượt hiện tại xong.",
                                     **job.status()), status_code=409)
        return dict(started=True, **job.status())

    @app.get("/api/process")
    def api_process_status():
        return JSONResponse(job.status(), headers={"Cache-Control": "no-store"})

    # ---------- ảnh: thumbnail, bản xem, bản gốc (đều sau đăng nhập, do middleware chặn) ----------
    def photo_file(folder, pid, ext=".jpg"):
        if not PHOTO_ID.match(pid):
            raise HTTPException(404)
        p = os.path.join(folder, pid + ext)
        if not os.path.exists(p):
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/thumbs/{pid}.jpg")
    def thumb(pid: str):
        return photo_file(store.thumb_dir(data_dir), pid)

    @app.get("/previews/{pid}.jpg")
    def preview(pid: str):
        return photo_file(store.preview_dir(data_dir), pid)

    @app.get("/originals/{pid}")
    def original(pid: str):
        _, _, photos = index()
        if pid not in photos:
            raise HTTPException(404)
        path = os.path.join(store.originals_dir(data_dir), photos[pid]["path"])
        if not os.path.exists(path):
            raise HTTPException(404)
        return FileResponse(path, filename=photos[pid]["path"], headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/healthz")
    def healthz():
        du = shutil.disk_usage(data_dir)  # không chứa dữ liệu ảnh; deploy dùng để kiểm đĩa trước khi tải ảnh
        return dict(ok=True, disk_free=du.free, disk_total=du.total)

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/")
    def home():
        return FileResponse(os.path.join(HERE, "static", "index.html"), headers={"Cache-Control": "no-store"})

    @app.get("/manage")
    def manage():
        return FileResponse(os.path.join(HERE, "static", "manage.html"), headers={"Cache-Control": "no-store"})

    app.add_middleware(NoIndexMiddleware)
    return app


def env(*names, default=None):
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=env("DATA_DIR", "ALBUM_DATA_DIR", default=store.DEFAULT_DATA_DIR))
    ap.add_argument("--model-dir", default=env("ALBUM_MODEL_DIR", default=store.DEFAULT_MODEL_DIR))
    ap.add_argument("--host", default=env("ALBUM_HOST", default="127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(env("PORT", "ALBUM_PORT", default=8000)))
    ap.add_argument("--providers", default=env("ALBUM_PROVIDERS", default="CPUExecutionProvider"))
    ap.add_argument("--threads", type=int, default=int(env("ALBUM_THREADS", default=0)),
                    help="số luồng onnxruntime (0 = theo số lõi)")
    ap.add_argument("--max-upload-mb", type=int, default=int(env("ALBUM_MAX_UPLOAD_MB", default=60)))
    ap.add_argument("--min-free-mb", type=int, default=int(env("ALBUM_MIN_FREE_MB", default=5120)),
                    help="từ chối tải lên khi ổ đĩa của DATA_DIR còn trống ít hơn mức này")
    ap.add_argument("--max-total-gb", type=float, default=float(env("ALBUM_MAX_TOTAL_GB", default=20)),
                    help="tổng dung lượng ảnh gốc tối đa trong album")
    a = ap.parse_args()
    data_dir = os.path.abspath(a.data_dir)
    try:
        os.makedirs(data_dir, exist_ok=True)
        probe = os.path.join(data_dir, f".write-test-{os.getpid()}")
        open(probe, "w").close()
        os.remove(probe)
    except OSError as e:
        sys.exit(f"LỖI: không ghi được vào DATA_DIR={data_dir} ({e}). Volume phải cho phép user của container ghi (xem DEPLOY.md).")
    app = create_app(data_dir, a.model_dir, a.providers.split(","), threads=a.threads,
                     max_upload=a.max_upload_mb * 1024 * 1024, min_free=a.min_free_mb * 1024 * 1024,
                     max_total=int(a.max_total_gb * 1024 ** 3))
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", workers=1)


if __name__ == "__main__":
    main()
