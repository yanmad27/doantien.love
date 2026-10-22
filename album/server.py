"""Web app album.doantien.love (không mật khẩu): tải ảnh lên → nút "Xử lý" lập chỉ mục
nền → khách tải selfie lên và tìm ảnh có mặt mình.

Mọi thứ bền vững nằm trong DATA_DIR (ảnh gốc, thumbnail, bản xem, WebP, index.db). Selfie chỉ nằm
trong RAM: gửi dạng body thô (không multipart nên không có file tạm), xử lý xong là bỏ.

Trang công khai: `GET /api/gallery` (manifest gọn, không có mặt/embedding) + `/i/{id}-{v}-{size}.webp` đặt tên theo nội dung
(CDN cache vô hạn). Host ngoài ALBUM_APP_HOSTS (vd. tên miền CDN) chỉ lấy được file ảnh/tĩnh — xem HostGate.
"""
import argparse
import gzip
import hashlib
import hmac
import html as htmllib
import json
import logging
import io
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.staticfiles import StaticFiles

import mp4box
import store
from faces import QUERY_MAX_PIXELS, FaceEngine, ImageTooLarge, ImageUnreadable, load_image
from indexer import (DEFAULT_DET_SIZE, DEFAULT_MIN_FACE, DERIV_VERSION, WEBP_SIZES, CRASH_REASON, compute_bursts, derive_pending,
                     find_photos, index_photos, known_derive_failures, mark_failed, pending_derive, pending_photos, recover_crashed,
                     require_webp, restore_rejected)

# Container (python:slim) không có /etc/mime.types: .webp/.woff2 sẽ thành text/plain, cùng nosniff → trình duyệt chặn ảnh chéo origin (ORB).
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")

log = logging.getLogger("album")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 25 * 1024 * 1024
SEARCH_GZIP_MIN = 1024  # nhỏ hơn thì nén không đáng
SEARCH_GZIP_LEVEL = 5


def accepted_codings(header):
    """Accept-Encoding (RFC 9110 §12.5.3) → (gzip_ok, identity_ok). Thiếu/rỗng = chỉ identity (không nén). gzip/x-gzip có q>0 thì nén được; `*` chỉ áp cho
    coding không được nêu. identity luôn chấp nhận được trừ khi bị loại tường minh (`identity;q=0`) hoặc bởi `*;q=0` khi identity không được nêu."""
    q = {}
    for part in (header or "").split(","):
        name, _, params = part.partition(";")
        name, val = name.strip().lower(), 1.0
        for prm in params.split(";"):
            k, _, v = prm.partition("=")
            if k.strip().lower() == "q":
                try:
                    val = float(v.strip())
                except ValueError:
                    val = 0.0
        if name:
            q[name] = val
    gz = q["gzip"] if "gzip" in q else q["x-gzip"] if "x-gzip" in q else q.get("*", 0.0)
    ident = q["identity"] if "identity" in q else (q["*"] if "*" in q else 1.0)
    return gz > 0, ident > 0


def search_encoding(header, size):
    """"gzip" hay "identity". Nén khi gzip chấp nhận được và (thân đủ lớn HOẶC identity bị loại). Không cái nào chấp nhận được: vẫn gửi identity (như đa số máy chủ), không 406."""
    gz, ident = accepted_codings(header)
    return "gzip" if gz and (size >= SEARCH_GZIP_MIN or not ident) else "identity"


SCORE_FLOOR = 0.15  # dưới mức này không bao giờ là cùng người, khỏi gửi về
MAX_RESULTS = int(os.environ.get("ALBUM_MAX_RESULTS", 5000))  # phủ cả album (giao diện ảo hoá, hiện được hết); đặt nhỏ lại nếu cần giới hạn
QUERY_MIN_FACE = 40  # cạnh ngắn (px) tối thiểu của mặt trong selfie, khớp --min-face của indexer
UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
UPLOAD_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}  # MPO: JPEG máy ảnh/điện thoại có kèm ảnh xem trước (MPF)
UPLOAD_MAX_PIXELS = 64_000_000  # ảnh bom PNG nhỏ khai báo hàng trăm MP ngốn ~18 B/điểm ảnh khi giải mã; 64 MP đủ cho máy ảnh 61 MP
MAX_UPLOAD_SLOTS = 4      # số upload ghi đĩa cùng lúc (toàn cục)
MAX_SEARCH_SLOTS = 2      # số truy vấn selfie tính toán cùng lúc (mỗi cái có thể ~0.7 GB RAM)
MAX_BODY_SLOTS = 8        # số body selfie đang nhận cùng lúc (mỗi cái ≤ 25 MB)
DISK_CHECK_EVERY = 8 * 1024 * 1024
PHOTO_ID = re.compile(r"^[0-9a-f]{16}$")
DERIVE_WORKERS = int(os.environ.get("ALBUM_DERIVE_WORKERS", 2))  # số ảnh sinh WebP cùng lúc (JPEG giải mã thu nhỏ nên nhẹ RAM)
GALLERY_REFRESH = 3.0   # giây: giãn cách dựng lại manifest khi đang Xử lý
STATS_TTL = 10.0        # giây: /api/stats dùng giá trị đã nhớ khi đang Xử lý
DISK_TTL = 5.0         # giây: làm mới số liệu đĩa của /healthz
LOGIN_MAX_FAILS = 10  # lượt thử mỗi phút mỗi IP
LOGIN_GLOBAL_MAX = 30  # lượt thử mỗi phút trên mọi IP; vượt → 429 cho tất cả trong 60 s
LOGIN_FALLBACK = ("<!doctype html><meta charset=utf-8><title>Đăng nhập</title><p>Cần mã chủ nhà. "
                  "Gửi POST /api/admin-login {\"token\": …} để nhận cookie.</p>")  # khi static/manage-login.html chưa có
DEFAULT_APP_HOSTS = "album.doantien.love,localhost,127.0.0.1"
PUBLIC_PREFIXES = ("/i/", "/thumbs/", "/previews/", "/static/")  # host ngoài (CDN) chỉ được GET/HEAD/OPTIONS các đường này
PUBLIC_EXACT = ("/healthz", "/robots.txt")
WEBP_NAME = re.compile(r"^([0-9a-f]{16})-([0-9a-f]{8})-(\d+)\.webp$")
IMAGE_TOKEN_TTL = 24 * 3600  # giây: token ?t= cho ảnh đã ẩn trong kết quả tìm kiếm
ADMIN_BODY_MAX = 256 * 1024
ADMIN_MAX_IDS = 5000
IMMUTABLE = "public, max-age=31536000, immutable"
VIDEO_MAX_BYTES = 2 * 1024 ** 3   # mỗi video
VIDEO_CHUNK_MAX = 16 * 1024 ** 2  # mỗi request PUT chunk
POSTER_MAX_BYTES = 2 * 1024 ** 2
POSTER_MAX_SIDE = 4096
MAX_VIDEO_SESSIONS = 2            # phiên tải video chưa xong cùng lúc
VIDEO_SESSION_TTL = 24 * 3600
VIDEO_INIT_BODY_MAX = 4096
VIDEO_TITLE_MAX = 120
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UID_RE = re.compile(r"^[0-9a-f]{32}$")
VIDEO_FILE = re.compile(r"^([0-9a-f]{16})-([0-9a-f]{10})\.(mp4|jpg)$")
LEGACY_CACHE = "public, max-age=300"  # /thumbs, /previews không đặt tên theo nội dung → cache ngắn (ảnh vừa ẩn còn ở CDN tối đa ~5 phút)
INDEX_WINDOW = int(os.environ.get("ALBUM_INDEX_WINDOW", 1))  # số ảnh giải mã cùng lúc khi xử lý: 45 MP giải mã xong ~135 MB/ảnh, giữ RAM đỉnh thấp


class CachedStatic(StaticFiles):
    """File tĩnh của giao diện, cache dài: HTML gắn ?v=<mã nội dung> (xem static_version) nên URL đổi khi nội dung đổi;
    font đặt tên theo kiểu chữ + subset (đổi font thì đổi tên file)."""

    async def get_response(self, path, scope):
        if path.lower().endswith((".html", ".htm")):  # HTML chỉ phát qua các route có templating (static_page), không qua /static ở bất kỳ host nào
            raise HTTPException(404)
        return await super().get_response(path, scope)

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        resp.headers["Cache-Control"] = "public, max-age=2592000"
        return resp


_ver = {"sig": None, "v": ""}


def static_version():
    """Mã nội dung của cả thư mục static/: gắn vào URL (?v=) trong HTML để deploy mới không bao giờ ghép HTML mới với JS/CSS/ảnh cũ
    (HTML no-store; file tĩnh cache dài vì URL đổi theo nội dung). Font đặt tên theo kiểu chữ + subset, đổi font thì đổi tên file."""
    root = os.path.join(HERE, "static")
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs)
    sig = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in files)
    if sig != _ver["sig"]:
        h = hashlib.sha1()
        for p in files:
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
        _ver.update(sig=sig, v=h.hexdigest()[:10])
    return _ver["v"]


def static_page(name, cdn="", status=200, fallback=None):
    try:
        with open(os.path.join(HERE, "static", name), encoding="utf-8") as fh:
            html = fh.read().replace("__V__", static_version())
    except FileNotFoundError:
        if fallback is None:
            raise
        html = fallback
    if cdn:
        html = html.replace("<!--__CDN_PRECONNECT__-->", f'<link rel="preconnect" href="{htmllib.escape(cdn, quote=True)}">')
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


class NoIndexMiddleware:
    """Mọi response mang X-Robots-Tag: noindex + vài header an toàn cơ bản; lỗi (>=400) không bao giờ cacheable."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send2(msg):
            if msg["type"] == "http.response.start":
                hs = msg.setdefault("headers", [])
                hs.extend([(b"x-robots-tag", b"noindex, nofollow"), (b"x-content-type-options", b"nosniff"),
                           (b"referrer-policy", b"no-referrer")])
                if msg["status"] >= 400:
                    msg["headers"] = [h for h in hs if h[0].lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(msg)
        await self.app(scope, receive, send2 if scope["type"] == "http" else send)


def host_of(scope):
    """Tên host (không cổng, chữ thường) từ header Host; không có → ''."""
    for k, v in scope.get("headers", ()):
        if k == b"host":
            h = v.decode("latin-1").strip().lower()
            if h.startswith("["):  # IPv6 [::1]:8000
                return h[:h.find("]") + 1] if "]" in h else h
            return h.rsplit(":", 1)[0] if ":" in h else h
    return ""


class HostGate:
    """Mặc định chặn: chỉ host trong ALBUM_APP_HOSTS thấy toàn bộ app. Host khác (tên miền CDN trỏ vào cùng container)
    chỉ GET/HEAD/OPTIONS ảnh + file tĩnh + /healthz + /robots.txt; mọi thứ còn lại 404 no-store — chạy trước khi đọc body,
    nên selfie/upload không bao giờ đi qua CDN. Đường công khai thêm Access-Control-Allow-Origin: * (ảnh/font cho trang ở host khác)."""

    def __init__(self, app, app_hosts):
        self.app, self.app_hosts = app, frozenset(app_hosts)

    def allowed(self, scope):
        if host_of(scope) in self.app_hosts:
            return True
        path = scope["path"]
        return scope["method"] in ("GET", "HEAD", "OPTIONS") and (path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":  # websocket… app không dùng
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if not self.allowed(scope):
            await JSONResponse({"detail": "Not Found"}, status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        if scope["path"].startswith(PUBLIC_PREFIXES):
            async def send2(msg):
                if msg["type"] == "http.response.start":
                    msg.setdefault("headers", []).append((b"access-control-allow-origin", b"*"))
                await send(msg)
            return await self.app(scope, receive, send2)
        await self.app(scope, receive, send)


def safe_name(raw, exts=None):
    """Tên file an toàn: chỉ lấy phần cuối, bỏ ký tự lạ, đuôi chữ thường. Trả None nếu đuôi không cho phép."""
    base = unicodedata.normalize("NFC", raw.replace("\\", "/").split("/")[-1]).strip()
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in (exts or UPLOAD_EXTS):
        return None
    stem = re.sub(r"[^\w.\- ()]", "_", stem).strip(" .")[:100] or "photo"
    while len(stem.encode("utf-8")) > 150:  # tên file tối đa 255 byte; chừa chỗ cho hậu tố -<sha8> và đuôi
        stem = stem[:-1]
    return stem.rstrip(" .") + ext


def load_session_secret(data_dir, name=".session_secret"):
    """32 byte ngẫu nhiên, lưu ở DATA_DIR/.session_secret (0600; name=".image_secret" cho token ảnh). Tạo nguyên tử (ghi file tạm rồi link) nên hai process/khởi động
    chồng nhau cùng thấy một giá trị; file hỏng/sai độ dài thì tạo lại (mọi phiên cũ hết hiệu lực). Không bao giờ log hay phát ra."""
    path = os.path.join(data_dir, name)
    for _ in range(3):
        try:
            with open(path, "rb") as fh:
                v = fh.read()
            if len(v) == 32:
                return v
            os.remove(path)
        except FileNotFoundError:
            pass
        tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(os.urandom(32))
            try:
                os.link(tmp, path)  # chỉ một bên thắng; bên thua đọc lại file của bên thắng
            except FileExistsError:
                pass
        finally:
            os.remove(tmp)
    raise RuntimeError(f"không tạo được {name}")


def cpu_budget():
    """Số lõi THỰC được dùng: min(affinity, hạn mức cgroup CPU). os.cpu_count() trong container trả số lõi của máy chủ, nên
    đặt số luồng theo nó sẽ vượt hạn mức CPU và bị throttle (cả process đứng từng đợt)."""
    n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    try:
        q, per = open("/sys/fs/cgroup/cpu.max").read().split()[:2]  # cgroup v2: "600000 100000" | "max 100000"
        if q != "max":
            n = min(n, max(1, -(-int(q) // int(per))))
    except (OSError, ValueError):
        try:  # cgroup v1
            q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
            per = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
            if q > 0:
                n = min(n, max(1, -(-q // per)))
        except (OSError, ValueError):
            pass
    return max(1, n)


class ProcessJob:
    """Một lượt "Xử lý" chạy nền trong process server. Chỉ một lượt một lúc. Có engine (model) RIÊNG với ngân sách luồng riêng
    (ALBUM_PROCESS_THREADS) để tìm kiếm của khách luôn còn phần CPU; engine_factory=None → dùng chung engine của tìm kiếm.

    status() không bao giờ chờ khoá: trả bản chụp (snapshot) được job tự cập nhật, nên GET /api/process và /healthz O(1)."""

    def __init__(self, data_dir, engine_factory, det_size, min_face):
        self.data_dir, self.engine_factory, self.det_size, self.min_face = data_dir, engine_factory, det_size, min_face
        self.lock = threading.Lock()  # chỉ để tuần tự hoá các lần GHI trạng thái; người đọc không dùng
        self.thread = None
        self.s = dict(state="idle", phase=None, total=0, done=0, failed=0, derive_failed=0, faces=0, current=None,
                      errors=[], rejected=[], started_at=None, finished_at=None, elapsed=0.0)
        self._publish()

    def _publish(self):
        self._snap = dict(self.s, errors=list(self.s["errors"]), rejected=list(self.s["rejected"]))  # gán tham chiếu = nguyên tử

    @property
    def running(self):
        return self._snap["state"] == "running"

    def status(self):
        s = dict(self._snap)
        if s["state"] == "running":
            s["elapsed"] = time.time() - s["started_at"]
        return s

    def start(self):
        """True nếu bắt đầu được; False nếu đang có lượt chạy."""
        with self.lock:
            if self.s["state"] == "running":
                return False
            self.s = dict(state="running", phase="index", total=0, done=0, failed=0, derive_failed=0, faces=0, current=None,
                          errors=[], rejected=self.s["rejected"], started_at=time.time(), finished_at=None, elapsed=0.0)
            self._publish()
        self.thread = threading.Thread(target=self._run, daemon=True, name="process")
        self.thread.start()
        return True

    def _clean(self, e):
        """Lỗi gửi ra ngoài: chỉ loại lỗi + thông điệp đã bỏ mọi đường dẫn tuyệt đối."""
        return f"{type(e).__name__}: " + re.sub(r"(/[^\s:'\"]+)+", lambda m: os.path.basename(m.group(0)), str(e))[:200]

    def recover(self, db):
        """File làm process chết ở lần trước (OOM khi giải mã…) → chuyển sang DATA_DIR/rejected/ và báo trong trạng thái."""
        moved = recover_crashed(db, store.originals_dir(self.data_dir), self.data_dir)
        for rel in moved:
            mark_failed(db, store.rejected_dir(self.data_dir), os.path.basename(rel), CRASH_REASON)
        if moved:
            with self.lock:
                self.s["rejected"] = self.s["rejected"] + moved
                self._publish()
        return moved

    def _upd(self, **kw):
        with self.lock:
            self.s.update(kw)
            self._publish()

    def _run(self):
        engine = None
        try:
            engine = self.engine_factory()
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
                    self._publish()

            index_photos(db, engine, root, self.data_dir, todo, self.det_size, self.min_face,
                         window=INDEX_WINDOW, workers=1, max_pixels=UPLOAD_MAX_PIXELS, on_progress=progress)
            # Giai đoạn 2: WebP + metadata cho ảnh mới và ảnh đã lập chỉ mục từ bản cũ. Không dò mặt, không động tới decoding.json.
            self._upd(phase="derive", total=len(pending_derive(db)), done=0, current=None, derive_failed=known_derive_failures(db))

            def dprogress(i, n, rel, err):
                with self.lock:
                    self.s["done"] = i
                    self.s["current"] = rel
                    if err is not None:
                        self.s["derive_failed"] += 1
                        self.s["errors"] = (self.s["errors"] + [f"WebP {rel}: {self._clean(err)}"])[-20:]
                    self._publish()

            derive_pending(db, root, self.data_dir, workers=DERIVE_WORKERS, max_pixels=UPLOAD_MAX_PIXELS, on_progress=dprogress)
            compute_bursts(db)
            db.close()
            now = time.time()
            self._upd(state="done", phase=None, current=None, finished_at=now, elapsed=now - self.s["started_at"])
        except Exception as e:
            log.exception("xử lý lỗi")
            self._upd(state="error", phase=None, current=None, finished_at=time.time(), elapsed=time.time() - self.s["started_at"],
                      errors=self.s["errors"] + [f"Lỗi chung: {self._clean(e)}"])
        finally:
            engine = None  # engine riêng của lượt này được giải phóng (trả RAM của model thứ hai)


def create_app(data_dir, model_dir, providers, query_det_size=640, threads=0,
               max_upload=60 * 1024 * 1024, det_size=DEFAULT_DET_SIZE, min_free=5120 * 1024 * 1024,
               max_total=20 * 1024 ** 3, app_hosts=None, cdn_origin=None, admin_token=None, process_threads=None):
    require_webp()
    max_total = max_total or None  # 0/None = không giới hạn tổng dung lượng (vẫn giữ ngưỡng đĩa trống)
    if app_hosts is None:
        app_hosts = env("ALBUM_APP_HOSTS", default=DEFAULT_APP_HOSTS)
    app_hosts = [h.strip().lower() for h in (app_hosts.split(",") if isinstance(app_hosts, str) else app_hosts) if h.strip()]
    cdn = (env("ALBUM_CDN_ORIGIN", default="") if cdn_origin is None else cdn_origin).strip().rstrip("/")
    if cdn and not re.match(r"^https?://[^/\s]+$", cdn):
        log.error("ALBUM_CDN_ORIGIN=%r không phải origin hợp lệ (cần http(s)://host) — bỏ qua, dùng cùng origin", cdn)
        cdn = ""
    token = env("ALBUM_ADMIN_TOKEN", default="") if admin_token is None else admin_token
    app = FastAPI(title="Tìm ảnh cưới", docs_url=None, redoc_url=None, openapi_url=None)  # docs mặc định nạp JS từ CDN
    for d in (store.originals_dir(data_dir), store.thumb_dir(data_dir), store.preview_dir(data_dir),
              os.path.join(data_dir, "tmp")):
        os.makedirs(d, exist_ok=True)
    engine_cls = FaceEngine
    engine = engine_cls(model_dir, providers=providers, threads=threads)
    if process_threads is None:
        process_threads = int(env("ALBUM_PROCESS_THREADS", default=max(1, cpu_budget() - 2)))
    # Xử lý có model + ngân sách luồng riêng (tạo khi bắt đầu lượt, bỏ khi xong): tìm kiếm của khách (ALBUM_THREADS) không bị job chiếm hết CPU.
    # ALBUM_PROCESS_THREADS=0 → dùng chung engine với tìm kiếm (tiết kiệm RAM, nhưng hai bên tranh nhau cùng nhóm luồng).
    job = ProcessJob(data_dir, (lambda: engine_cls(model_dir, providers=providers, threads=process_threads)) if process_threads > 0
                     else (lambda: engine), det_size, DEFAULT_MIN_FACE)
    log.info("CPU khả dụng %d; luồng tìm kiếm=%s, luồng Xử lý=%s", cpu_budget(), threads or "tự động", process_threads or "dùng chung")
    app.state.job = job  # cho test: job.thread.join()
    cache = {"mtime": None, "sig": None, "t": None}  # t = (E, rows, photos) công bố bằng MỘT phép gán
    # gal["g"] là MỘT bản chụp bất biến (manifest + meta + ẩn + cài đặt), thay nguyên cả bản bằng một phép gán; người đọc lấy g = gallery_state() rồi chỉ dùng g
    gal = {"g": dict(mtime=None, ts=0.0, meta={}, hidden=frozenset(), include_hidden=True, hats={}, epoch=0, pub_b={}, pos={}, admin=b"", raw=b"", gz=b"", mv="", vids={}),
           "fresh_at": 0.0}
    glock = threading.Lock()
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
    restore_rejected(_db, store.originals_dir(data_dir), data_dir)
    stored = {"bytes": _db.execute("SELECT COALESCE(SUM(size),0) FROM originals").fetchone()[0]}  # tổng ảnh gốc, giữ trong RAM
    _db.close()

    def stored_bytes():
        return stored["bytes"]

    disk = {"free": 0, "total": 0, "ts": 0.0, "busy": False}

    def refresh_disk():
        try:
            du = shutil.disk_usage(data_dir)
            disk.update(free=du.free, total=du.total, ts=time.time())
        finally:
            disk["busy"] = False

    refresh_disk()

    def disk_state():
        if time.time() - disk["ts"] > DISK_TTL and not disk["busy"]:
            disk["busy"] = True
            threading.Thread(target=refresh_disk, daemon=True).start()
        return disk

    class Busy(Exception):
        pass

    def search_gated(body, scope="all"):
        if not search_slots.acquire(blocking=False):
            raise Busy
        try:
            return search(body, scope)
        finally:
            search_slots.release()

    def db_mtime():
        """Dấu hiệu DB đã đổi. WAL: commit chỉ ghi vào -wal (mtime/kích thước của nó đổi), file chính đổi khi checkpoint."""
        p = store.db_path(data_dir)
        try:
            sig = [os.stat(p).st_mtime_ns]
        except OSError:
            return None
        try:
            w = os.stat(p + "-wal")
            sig += [w.st_mtime_ns, w.st_size]
        except OSError:
            pass
        return tuple(sig)

    def faces_sig():
        """Chữ ký phần ảnh/khuôn mặt của chỉ mục: ghi photo_meta (sinh WebP) làm đổi DB nhưng không đổi chữ ký
        này, nên tìm kiếm không phải nạp lại ma trận embedding sau mỗi ảnh khi backfill."""
        db = store.connect_ro(data_dir)
        try:
            return (db.execute("SELECT count(*), total(mtime_ns), total(det_size), total(n_faces), total(width) FROM photos").fetchone(),
                    db.execute("SELECT count(*) FROM faces").fetchone())
        finally:
            db.close()

    def index():
        """(E, rows, photos) trong RAM — luôn là MỘT bộ nhất quán (công bố bằng một phép gán, mỗi request đọc một lần). Có một bản
        rồi mà đang có luồng khác nạp lại → trả bản cũ ngay, không chờ khoá (request không bao giờ xếp hàng sau một lượt nạp)."""
        m = db_mtime()
        t = cache["t"]
        if t is not None and cache["mtime"] == m:
            return t
        if not lock.acquire(blocking=t is None):
            return t
        try:
            if cache["mtime"] != m or cache["t"] is None:
                sig = faces_sig() if m is not None else None
                if cache["t"] is None or sig is None or sig != cache["sig"]:
                    cache["t"] = store.load_matrix(data_dir)
                    cache["sig"] = sig
                cache["mtime"] = m
            return cache["t"]
        finally:
            lock.release()

    def public_bursts(order):
        """order: [(id, burst gốc, sự kiện)] của các khung HIỂN THỊ theo thứ tự manifest → {id: burst công khai}. Nhóm theo burst gốc;
        burst công khai = id khung hiển thị đầu tiên; nhóm còn < 2 khung hiển thị → không có (None). Mọi id ra khỏi đây đều là ảnh hiện."""
        groups = {}
        for pid, b, ev in order:
            if b:
                groups.setdefault((b, ev), []).append(pid)  # không bao giờ gộp khung của hai sự kiện khác nhau
        return {pid: ids[0] for ids in groups.values() if len(ids) >= 2 for pid in ids}

    def build_gallery(m):
        """MỘT SELECT (LEFT JOIN photo_hidden) + cài đặt trong cùng một giao dịch đọc → ảnh chụp nhất quán."""
        meta, rows, arows, order, hidden, include, hats, epoch = {}, [], [], [], set(), True, {}, 0
        vrows = []
        if m is not None:
            db = store.connect_ro(data_dir)
            try:
                db.execute("BEGIN")
                q = ("SELECT p.id, COALESCE(m.w,p.width), COALESCE(m.h,p.height), m.taken, m.v, m.color, p.path, m.burst, h.id IS NOT NULL, h.hidden_at, e.event "
                     "FROM photos p LEFT JOIN photo_meta m ON m.id=p.id LEFT JOIN photo_hidden h ON h.id=p.id "
                     "LEFT JOIN photo_event e ON e.photo_id=p.id "
                     "ORDER BY m.taken IS NULL, m.taken, p.path")
                for pid, w, h, t, v, c, name, b, hid, hat, ev in db.execute(q):
                    if pid in meta:  # mỗi id đúng một lần (photos.id là khoá chính, photo_meta.id cũng; phòng thủ thêm)
                        continue
                    t = None if t is None else round(t, 3)
                    ev = ev if ev in store.EVENT_IDS else store.DEFAULT_EVENT  # vắng dòng (hoặc giá trị lạ) = sự kiện mặc định
                    meta[pid] = (v, c, b, bool(hid), w, h, ev)
                    arows.append([pid, w, h, t, v, c, name, b, 1 if hid else 0, ev])
                    if hid:
                        hidden.add(pid)
                        hats[pid] = "n" if hat is None else repr(float(hat))
                    else:
                        rows.append([pid, w, h, t, v, c, name, None, ev])
                        order.append((pid, b, ev))
                r = db.execute("SELECT value FROM settings WHERE key=?", (store.SEARCH_HIDDEN_KEY,)).fetchone()
                include = r is None or r[0] != "0"
                r = db.execute("SELECT value FROM settings WHERE key=?", (store.EPOCH_KEY,)).fetchone()
                epoch = int(r[0]) if r and r[0].isdigit() else 0
                vrows = [[i, v, pv, w, h, d, t, ev, so] for i, v, pv, w, h, d, t, ev, so in db.execute(
                    "SELECT id, v, pv, w, h, duration, title, ev, sort FROM videos ORDER BY ev IS NULL, ev, sort, created")]
                db.rollback()
            finally:
                db.close()
        pub = public_bursts(order)
        for r in rows:
            r[7] = pub.get(r[0])
        photos_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        videos_json = json.dumps(vrows, ensure_ascii=False, separators=(",", ":"))
        mv = hashlib.sha1(f"{DERIV_VERSION}|{cdn}|{photos_json}{'|' + videos_json if vrows else ''}".encode()).hexdigest()[:12]  # không có video → mv y hệt bản trước
        n_ev = {e: 0 for e in store.EVENT_IDS}  # số ảnh HIỆN mỗi sự kiện (ảnh ẩn không tính)
        for r in rows:
            n_ev[r[8]] += 1
        events_json = json.dumps([dict(id=e, label=lb, n=n_ev[e]) for e, lb in store.EVENTS], ensure_ascii=False, separators=(",", ":"))
        raw = (f'{{"mv":"{mv}","cdn":{json.dumps(cdn)},"dv":{DERIV_VERSION},"events":{events_json},"photos":{photos_json},"videos":{videos_json}}}').encode()
        n_hidden = len(hidden)
        by_event = {e: dict(all=0, shown=0, hidden=0) for e in store.EVENT_IDS}
        for a in arows:
            by_event[a[9]]["all"] += 1
            by_event[a[9]]["hidden" if a[8] else "shown"] += 1
        admin = json.dumps(dict(photos=arows, counts=dict(all=len(arows), shown=len(arows) - n_hidden, hidden=n_hidden), by_event=by_event,
                                events=[dict(id=e, label=lb) for e, lb in store.EVENTS],
                                settings={store.SEARCH_HIDDEN_KEY: include}), ensure_ascii=False, separators=(",", ":")).encode()
        # thứ tự manifest của MỌI khung (kể cả ẩn): search dùng để xếp khung đầu của nhóm burst ẩn
        pos = {a[0]: i for i, a in enumerate(arows)}
        return dict(mtime=m, ts=time.time(), meta=meta, hidden=hidden, include_hidden=include, hats=hats, epoch=epoch, pub_b=pub, pos=pos, admin=admin,
                    raw=raw, gz=gzip.compress(raw, 9, mtime=0), mv=mv, vids={r[0]: (r[1], r[2]) for r in vrows})

    def gallery_state(fresh=False, force=False):
        """Manifest công khai (chỉ ảnh hiện) + `meta` của MỌI ảnh trong RAM, dựng lại khi DB đổi (đọc dấu hiệu TRƯỚC khi truy vấn: ghi chen
        vào thì lần sau dựng lại). Trong lúc Xử lý chạy: dựng lại tối đa mỗi GALLERY_REFRESH giây và luôn trả bản cũ thay vì chờ
        (fresh=True bỏ qua giãn cách; force=True — sau khi chủ nhà ẩn/hiện — dựng lại NGAY, chờ khoá, bất kể Xử lý hay giãn cách)."""
        m = db_mtime()
        g = gal["g"]
        have = bool(g["raw"])
        if have and g["mtime"] == m and not force:
            return g
        if fresh and not force:  # dựng lại "tươi" do /i/ gọi tối đa mỗi giây một lần (URL sai v không được khuếch đại thành truy vấn DB)
            if time.time() - gal["fresh_at"] < 1.0:
                fresh = False
            else:
                gal["fresh_at"] = time.time()
        if have and not fresh and not force and job.running and time.time() - g["ts"] < GALLERY_REFRESH:
            return g
        if not glock.acquire(blocking=force or not have):
            return g
        try:
            if force or gal["g"]["mtime"] != m or not gal["g"]["raw"]:
                gal["g"] = build_gallery(m)
            return gal["g"]
        finally:
            glock.release()

    def search(body, scope="all"):
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
        g = gallery_state()  # ẩn + cài đặt đọc từ bản chụp manifest (dựng lại khi DB đổi), KHÔNG từ cache ma trận theo faces_sig
        meta, hidden, include = g["meta"], g["hidden"], g["include_hidden"]
        sims = E @ face.emb if len(E) else np.zeros(0, np.float32)
        best = {}
        for i in np.flatnonzero(sims >= SCORE_FLOOR):
            pid = rows[i]["photo_id"]
            if pid not in meta or (pid in hidden and not include) or (scope != "all" and meta[pid][6] != scope):  # phạm vi sự kiện + ảnh ẩn bị loại TRƯỚC best/xếp hạng/cắt/đếm; ảnh chưa vào manifest chờ lượt dựng sau
                continue
            if pid not in best or sims[i] > sims[best[pid]]:
                best[pid] = i
        ranked = sorted(best.items(), key=lambda kv: -sims[kv[1]])
        top = ranked[:MAX_RESULTS]
        hid_hits = {}  # burst gốc -> các ảnh ẩn TRÚNG trong phản hồi này
        for pid, _ in top:
            if pid in hidden and meta[pid][2]:
                hid_hits.setdefault((meta[pid][2], meta[pid][6]), []).append(pid)
        hb = {pid: min(ids, key=g["pos"].__getitem__) for ids in hid_hits.values() if len(ids) >= 2 for pid in ids}
        exp = int(time.time()) + IMAGE_TOKEN_TTL
        res = []
        for pid, i in top:
            ph = photos[pid]
            v, c, _, hid, w, h, ev = meta[pid]
            res.append(dict(id=pid, name=ph["path"], score=round(float(sims[i]), 4),
                            thumb=f"thumbs/{pid}.jpg", preview=f"previews/{pid}.jpg", width=ph["width"], height=ph["height"],
                            n_faces=ph["n_faces"], bbox=[round(x, 1) for x in rows[i]["bbox"]],
                            v=v, w=w, h=h, c=c, ev=ev, b=hb.get(pid) if hid else g["pub_b"].get(pid), t=image_token(pid, exp, g)))
        t4 = time.perf_counter()
        return dict(
            query=dict(n_faces=len(usable), n_ignored=len(faces) - len(usable), bbox=[round(float(v), 1) for v in face.bbox],
                       width=rgb.shape[1], height=rgb.shape[0], det_score=round(face.score, 3)),
            results=res, total_matches=len(ranked), truncated=len(ranked) > MAX_RESULTS,
            timing_ms=dict(decode=(t1 - t0) * 1e3, detect=(t2 - t1) * 1e3, embed=(t3 - t2) * 1e3,
                           compare=(t4 - t3) * 1e3, total=(t4 - t0) * 1e3))

    # Cookie phiên KHÔNG chứa mã: HMAC-SHA256(khoá = bí mật ngẫu nhiên 32 byte của server + mã chủ nhà, nhãn cố định). Bí mật nằm ở
    # DATA_DIR/.session_secret (0600, tạo nguyên tử một lần, không route nào phát ra DATA_DIR) nên có cookie cũng không dò ngược ra mã
    # offline; ổn định qua restart (cùng file), đổi mã hoặc xoá file → cookie cũ vô hiệu.
    session_value = hmac.new(load_session_secret(data_dir) + b"\0" + token.encode(), b"album-session-v1", hashlib.sha256).hexdigest() if token else ""

    image_secret = load_session_secret(data_dir, ".image_secret")  # luôn tạo (kể cả khi chưa đặt ALBUM_ADMIN_TOKEN)

    def image_token(pid, exp, g):
        """Token "<hết hạn>.<HMAC-SHA256 128 bit>": cho phép lấy bản gốc/xem/thumb của ảnh ĐÃ ẩn trong kết quả tìm kiếm của chính khách. Thông điệp ký =
        id + hết hạn + hidden_at hiện tại (0 khi hiện) + với ảnh ẩn: epoch cài đặt. Nên ẩn một ảnh (hoặc ẩn lại sau khi hiện) làm token cấp trước đó vô hiệu,
        đổi cài đặt tìm kiếm làm vô hiệu mọi token của ảnh ẩn, còn token của ảnh hiện vẫn sống khi ẩn ảnh KHÁC."""
        hat = g["hats"].get(pid)
        msg = f"{pid}|{exp}|0|0" if hat is None else f"{pid}|{exp}|{hat}|{g['epoch']}"
        return f"{exp}.{hmac.new(image_secret, msg.encode(), hashlib.sha256).hexdigest()[:32]}"

    def token_ok(pid, t, g):
        if not t or not re.fullmatch(r"\d{1,12}\.[0-9a-f]{32}", t):
            return False
        exp = int(t.split(".")[0])
        return exp >= time.time() and hmac.compare_digest(t.encode(), image_token(pid, exp, g).encode())

    def admin_state(request: Request, header=True):
        """'ok' | 'none' (không đưa thông tin xác thực → KHÔNG tính lượt thử) | 'bad' (đưa sai, đã tính) | 'limited' (quá ngưỡng).
        Mọi thông tin xác thực được đưa ra (header X-Album-Token, cookie album_session) đều qua cùng bộ giới hạn với đăng nhập:
        đặt chỗ lượt thử trước khi so, đúng thì hoàn lại. Cookie album_token (mã thô, bản cũ) không còn được chấp nhận."""
        if not token:
            return "ok"
        cookie = request.cookies.get("album_session")
        if cookie and hmac.compare_digest(cookie.encode(), session_value.encode()):
            return "ok"  # cookie phiên đúng = HMAC 256-bit, người giữ hợp lệ chứ không phải đoán mò: miễn mọi ngưỡng, không đặt chỗ
        got = []
        if header and request.headers.get("x-album-token"):
            got.append((request.headers["x-album-token"], token))
        if cookie:
            got.append((cookie, session_value))  # cookie sai vẫn tính lượt thử
        if not got:
            return "none"
        ip = client_ip(request)
        stamp = login_reserve(ip)
        if stamp is None:
            return "limited"
        if any(hmac.compare_digest(a.encode(), b.encode()) for a, b in got):
            login_refund(ip, stamp)
            return "ok"
        return "bad"

    def require_admin(request: Request):
        st = admin_state(request)
        if st == "limited":
            raise HTTPException(429, "Nhập sai quá nhiều lần, hãy thử lại sau ít phút.")
        if st != "ok":
            raise HTTPException(401, "Cần đăng nhập chủ nhà (cookie phiên hoặc header X-Album-Token) để thực hiện thao tác này.")

    fails = {}  # ip -> [dấu thời gian các lần thử trong 60 s]; throttle đăng nhập trong RAM
    glob = {"ts": [], "locked_until": 0.0}
    fails_lock = threading.Lock()

    def client_ip(request: Request):
        xff = request.headers.get("x-forwarded-for", "")
        return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")  # mục ngoài cùng bên phải do proxy tin cậy thêm

    def login_reserve(ip):
        """Đặt chỗ MỘT lượt thử TRƯỚC khi so mã (đúng thì hoàn lại): nhiều kết nối đồng thời không lọt qua ngưỡng. Mỗi IP tối đa
        LOGIN_MAX_FAILS lượt/phút; toàn cục tối đa LOGIN_GLOBAL_MAX lượt/phút, vượt thì khoá cả hệ thống 60 s. None = bị chặn."""
        now = time.time()
        with fails_lock:
            if now < glob["locked_until"]:
                return None
            for k in [k for k, v in fails.items() if not v or now - v[-1] > 60]:
                del fails[k]
            ts = [t for t in fails.get(ip, []) if now - t < 60]
            glob["ts"] = [t for t in glob["ts"] if now - t < 60]
            if len(ts) >= LOGIN_MAX_FAILS:
                fails[ip] = ts
                return None
            if len(glob["ts"]) >= LOGIN_GLOBAL_MAX:
                glob["locked_until"] = now + 60
                return None
            stamp = now
            while stamp in glob["ts"]:  # dấu duy nhất để hoàn lại đúng lượt
                stamp += 1e-6
            ts.append(stamp)
            fails[ip] = ts
            glob["ts"].append(stamp)
            return stamp

    def login_refund(ip, stamp):
        with fails_lock:
            for lst in (fails.get(ip, []), glob["ts"]):
                if stamp in lst:
                    lst.remove(stamp)

    @app.post("/api/admin-login")
    async def api_admin_login(request: Request):
        if not token:
            raise HTTPException(404)
        ip = client_ip(request)
        stamp = login_reserve(ip)
        if stamp is None:
            raise HTTPException(429, "Nhập sai quá nhiều lần, hãy thử lại sau ít phút.")
        body = b""
        async for c in request.stream():
            body += c
            if len(body) > 4096:
                raise HTTPException(413)
        try:
            given = json.loads(body).get("token")
        except Exception:
            given = None
        if not isinstance(given, str) or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "Mã không đúng.")  # lượt thử đã tính, không hoàn
        login_refund(ip, stamp)
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
        resp = Response(status_code=204, headers={"Cache-Control": "no-store"})
        resp.set_cookie("album_session", session_value, max_age=30 * 86400, path="/", httponly=True, samesite="strict", secure=https)
        return resp

    @app.get("/api/gallery")
    def api_gallery(request: Request):
        g = gallery_state()
        gz = "gzip" in request.headers.get("accept-encoding", "").lower()
        etag = f'"{g["mv"]}-gz"' if gz else f'"{g["mv"]}"'
        hdr = {"ETag": etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}  # luôn hỏi lại (304 rẻ): ẩn/hiện có hiệu lực ngay
        inm = [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]
        if etag in inm or "*" in inm:
            return Response(status_code=304, headers=hdr)
        if gz:
            hdr["Content-Encoding"] = "gzip"
        return Response(g["gz"] if gz else g["raw"], media_type="application/json", headers=hdr)

    def encode_search(out, accept):
        raw = json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        hdr = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
        if search_encoding(accept, len(raw)) == "gzip":
            raw = gzip.compress(raw, SEARCH_GZIP_LEVEL, mtime=0)
            hdr["Content-Encoding"] = "gzip"
        return raw, hdr

    @app.post("/api/search")
    async def api_search(request: Request):
        scope = request.query_params.get("scope", "all")
        if scope != "all" and scope not in store.EVENT_IDS:
            raise HTTPException(400, "Phạm vi tìm kiếm không hợp lệ.")
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
            out = await run_in_threadpool(search_gated, body, scope)
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
        raw, hdr = await run_in_threadpool(encode_search, out, request.headers.get("accept-encoding"))
        return Response(raw, media_type="application/json", headers=hdr)

    def compute_stats():
        _, rows, photos = index()
        db = store.connect_ro(data_dir)
        try:
            todo, _ = pending_photos(db, store.originals_dir(data_dir), det_size)
            n_derive = len(pending_derive(db))
            n_orig = len(find_photos(store.originals_dir(data_dir)))
        finally:
            db.close()
        du = disk_state()
        return dict(photos=len(photos), faces=len(rows), originals=n_orig, pending=len(todo), pending_derive=n_derive,
                    max_upload_mb=max_upload // 2**20, disk_free=du["free"], disk_total=du["total"],
                    stored_bytes=stored_bytes(), max_total_bytes=max_total)

    sc = {"v": None, "ts": 0.0, "busy": False}

    def refresh_stats():
        try:
            sc.update(v=compute_stats(), ts=time.time())
        except Exception:
            log.exception("stats lỗi")
        finally:
            sc["busy"] = False

    @app.get("/api/stats")
    async def stats(request: Request):
        """Lúc rảnh: tính mới mỗi lần (quét thư mục + so sánh chỉ mục). Lúc đang Xử lý: trả giá trị đã nhớ (tối đa STATS_TTL
        giây tuổi), việc quét chạy nền — không request nào chờ quét thư mục/khoá DB trong khi job đang ghi. Chỉ chủ nhà."""
        require_admin(request)
        if sc["v"] is not None and job.running:
            if time.time() - sc["ts"] > STATS_TTL and not sc["busy"]:
                sc["busy"] = True
                threading.Thread(target=refresh_stats, daemon=True).start()
            return JSONResponse(sc["v"], headers={"Cache-Control": "no-store"})
        v = await run_in_threadpool(compute_stats)
        sc.update(v=v, ts=time.time())
        return JSONResponse(v, headers={"Cache-Control": "no-store"})

    # ---------- chủ nhà: ẩn/hiện ảnh + cài đặt tìm kiếm (mọi response no-store; require_admin chạy TRƯỚC khi đọc body) ----------
    NOSTORE = {"Cache-Control": "no-store"}

    def admin_enabled():
        """Không có ALBUM_ADMIN_TOKEN → route ẩn/hiện KHÔNG bao giờ dùng được (đóng khi thiếu cấu hình; stats/process GET vẫn mở như trước)."""
        if not token:
            raise HTTPException(403, "Chưa đặt ALBUM_ADMIN_TOKEN: tính năng chủ nhà bị tắt.")

    def same_origin_or_403(request: Request):
        """Trình duyệt gửi Origin/Sec-Fetch-Site trên POST: phải cùng origin (chống CSRF dù cookie SameSite=Strict). Không có cả hai
        (curl, script của chủ nhà) → cho qua, vẫn cần cookie/header xác thực."""
        sfs = request.headers.get("sec-fetch-site")
        if sfs is not None and sfs != "same-origin":
            raise HTTPException(403, "Yêu cầu khác origin.")
        origin = request.headers.get("origin")
        if origin is not None:
            host = request.headers.get("host", "").strip().lower()
            m = re.fullmatch(r"https?://([^/\s]+)", origin.strip().lower())
            if not m or not host or m.group(1) != host:
                raise HTTPException(403, "Yêu cầu khác origin.")

    async def admin_json(request: Request, limit=ADMIN_BODY_MAX):
        admin_enabled()
        require_admin(request)
        same_origin_or_403(request)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(400, "Cần Content-Type: application/json.")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "Nội dung quá lớn.")
        body = b""
        async for c in request.stream():
            body += c
            if len(body) > limit:
                raise HTTPException(413, "Nội dung quá lớn.")
        try:
            data = json.loads(body)
        except Exception:
            raise HTTPException(400, "JSON không hợp lệ.")
        if not isinstance(data, dict):
            raise HTTPException(400, "Cần một đối tượng JSON.")
        return data

    @app.get("/api/admin/photos")
    async def api_admin_photos(request: Request):
        admin_enabled()
        require_admin(request)
        return Response((await run_in_threadpool(gallery_state))["admin"], media_type="application/json", headers=NOSTORE)

    def set_visibility(ids, hide):
        """MỘT giao dịch, idempotent; đếm bằng JOIN photos trong cùng giao dịch. id không có trong photos → "unknown", không ghi."""
        db = store.connect(data_dir, init=False)
        try:
            db.execute("BEGIN IMMEDIATE")
            known = [i for i in ids if db.execute("SELECT 1 FROM photos WHERE id=?", (i,)).fetchone()]
            ks = set(known)
            changed = 0
            if hide:
                now = time.time()
                for i in known:
                    changed += db.execute("INSERT OR IGNORE INTO photo_hidden VALUES (?,?)", (i, now)).rowcount
            else:
                for i in ids:
                    n = db.execute("DELETE FROM photo_hidden WHERE id=?", (i,)).rowcount
                    changed += n if i in ks else 0  # dòng mồ côi (ảnh đã bị xoá) cũng được dọn nhưng không tính
            total = db.execute("SELECT count(*) FROM photos").fetchone()[0]
            n_hidden = db.execute("SELECT count(*) FROM photos p JOIN photo_hidden h ON h.id=p.id").fetchone()[0]
            db.commit()
            return dict(changed=changed, unknown=len(ids) - len(known), counts=dict(all=total, shown=total - n_hidden, hidden=n_hidden))
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @app.post("/api/admin/visibility")
    async def api_admin_visibility(request: Request):
        data = await admin_json(request)
        ids, hide = data.get("ids"), data.get("hidden")
        if set(data) != {"ids", "hidden"} or not isinstance(hide, bool) or not isinstance(ids, list) \
                or not 1 <= len(ids) <= ADMIN_MAX_IDS or not all(isinstance(i, str) and PHOTO_ID.fullmatch(i) for i in ids):
            raise HTTPException(400, f'Cần {{"ids": [1..{ADMIN_MAX_IDS} mã 16 hex], "hidden": true|false}}.')
        out = await run_in_threadpool(set_visibility, list(dict.fromkeys(ids)), hide)
        await run_in_threadpool(gallery_state, force=True)  # CHẶN: dựng lại manifest công khai trước khi trả lời, kể cả khi đang Xử lý
        return JSONResponse(out, headers=NOSTORE)

    def set_event(ids, event):
        """MỘT giao dịch, idempotent. Chỉ ảnh có trong photos; mặc định → xoá dòng (vắng = mặc định), còn lại → ghi đè. Ảnh ẩn cũng đổi được."""
        db = store.connect(data_dir, init=False)
        try:
            db.execute("BEGIN IMMEDIATE")
            known = [i for i in ids if db.execute("SELECT 1 FROM photos WHERE id=?", (i,)).fetchone()]
            changed = 0
            for i in known:
                r = db.execute("SELECT event FROM photo_event WHERE photo_id=?", (i,)).fetchone()
                if (r[0] if r and r[0] in store.EVENT_IDS else store.DEFAULT_EVENT) != event:
                    changed += 1
                put_event(db, i, event)
            db.commit()
            return dict(changed=changed, unknown=len(ids) - len(known), event=event)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put_event(db, pid, event):
        if event == store.DEFAULT_EVENT:
            db.execute("DELETE FROM photo_event WHERE photo_id=?", (pid,))
        else:
            db.execute("INSERT OR REPLACE INTO photo_event VALUES (?,?)", (pid, event))

    @app.post("/api/admin/event")
    async def api_admin_event(request: Request):
        data = await admin_json(request)
        ids, event = data.get("ids"), data.get("event")
        if set(data) != {"ids", "event"} or event not in store.EVENT_IDS or not isinstance(ids, list) \
                or not 1 <= len(ids) <= ADMIN_MAX_IDS or not all(isinstance(i, str) and PHOTO_ID.fullmatch(i) for i in ids):
            raise HTTPException(400, f'Cần {{"ids": [1..{ADMIN_MAX_IDS} mã 16 hex], "event": {" | ".join(store.EVENT_IDS)}}}.')
        out = await run_in_threadpool(set_event, list(dict.fromkeys(ids)), event)
        await run_in_threadpool(gallery_state, force=True)  # CHẶN: dựng lại manifest trước khi trả lời
        return JSONResponse(out, headers=NOSTORE)

    def set_include_hidden(on):
        db = store.connect(data_dir, init=False)
        try:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT value FROM settings WHERE key=?", (store.SEARCH_HIDDEN_KEY,)).fetchone()
            if (r is None or r[0] != "0") != on:  # đổi giá trị thật sự → tăng epoch (vô hiệu token ảnh ẩn đã cấp)
                e = db.execute("SELECT value FROM settings WHERE key=?", (store.EPOCH_KEY,)).fetchone()
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (store.EPOCH_KEY, str((int(e[0]) if e and e[0].isdigit() else 0) + 1)))
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (store.SEARCH_HIDDEN_KEY, "1" if on else "0"))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @app.post("/api/admin/settings")
    async def api_admin_settings(request: Request):
        data = await admin_json(request)
        on = data.get(store.SEARCH_HIDDEN_KEY)
        if set(data) != {store.SEARCH_HIDDEN_KEY} or not isinstance(on, bool):
            raise HTTPException(400, f'Cần {{"{store.SEARCH_HIDDEN_KEY}": true|false}}.')
        await run_in_threadpool(set_include_hidden, on)
        await run_in_threadpool(gallery_state, force=True)
        return JSONResponse({"settings": {store.SEARCH_HIDDEN_KEY: on}}, headers=NOSTORE)

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
        require_admin(request)
        fname = safe_name(name)
        if fname is None:
            raise HTTPException(400, "Chỉ nhận ảnh .jpg, .jpeg, .png, .webp")
        event = request.query_params.get("event", store.DEFAULT_EVENT)
        if event not in store.EVENT_IDS:
            raise HTTPException(400, "Sự kiện không hợp lệ.")
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
                if max_total is None or stored_bytes() + quota["reserved"] + max_upload <= max_total:
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
            return await run_in_threadpool(finalize_upload, part, fname, h.hexdigest(), n, event)
        finally:
            if os.path.exists(part):
                os.remove(part)
            if reserved:
                with quota_lock:
                    quota["reserved"] -= max_upload
            upload_slots.release()

    def finalize_upload(part, fname, digest, size, event=store.DEFAULT_EVENT):
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
                freed = 0
                dup = db.execute("SELECT name FROM originals WHERE sha256=?", (digest,)).fetchone()
                if dup and os.path.exists(os.path.join(store.originals_dir(data_dir), dup[0])):
                    r = db.execute("SELECT event FROM photo_event WHERE photo_id=?", (store.photo_id(dup[0]),)).fetchone()
                    return dict(status="duplicate", name=dup[0], event=r[0] if r and r[0] in store.EVENT_IDS else store.DEFAULT_EVENT)  # giữ nguyên sự kiện đang có
                if dup:  # bản gốc đã bị loại (rejected/) hoặc mất: cho tải lại, xoá dấu vết để xử lý lại
                    old = db.execute("SELECT size FROM originals WHERE sha256=?", (digest,)).fetchone()
                    db.execute("DELETE FROM originals WHERE sha256=?", (digest,))
                    db.execute("DELETE FROM failed WHERE path=?", (dup[0],))
                    freed = old[0] if old else 0
                final = fname
                root = store.originals_dir(data_dir)
                # trùng tên KHÔNG phân biệt hoa/thường (X.jpg ~ x.jpg: ổ đĩa không phân biệt thì sẽ ghi đè nhau) mà khác nội dung
                # → đây là ảnh khác, giữ cả hai bằng hậu tố -<sha8>; cùng nội dung thì đã bị chặn ở trên theo SHA-256
                if db.execute("SELECT 1 FROM originals WHERE lower(name)=lower(?)", (final,)).fetchone() or \
                        os.path.exists(os.path.join(root, final)):
                    stem, ext = os.path.splitext(fname)
                    final = f"{stem}-{digest[:8]}{ext}"  # cùng tên, khác nội dung: không ghi đè
                os.replace(part, os.path.join(root, final))
                try:
                    db.execute("INSERT INTO originals VALUES (?,?,?)", (final, digest, size))
                    db.execute("DELETE FROM failed WHERE path=?", (final,))
                    put_event(db, store.photo_id(final), event)  # cùng giao dịch với originals: sự kiện có hiệu lực ngay khi ảnh xuất hiện
                    db.commit()
                except Exception:
                    db.rollback()
                    os.remove(os.path.join(root, final))  # không để file mồ côi không có dòng trong DB
                    raise
                stored["bytes"] += size - freed  # chỉ cộng/trừ khi commit đã thành công
            finally:
                db.close()
        return dict(status="ok", name=final, renamed=final != fname, width=w, height=hgt, event=event)

    # ---------- xử lý nền ----------
    @app.post("/api/process")
    def api_process(request: Request):
        require_admin(request)
        if not job.start():
            return JSONResponse(dict(started=False, message="Đang xử lý rồi — hãy đợi lượt hiện tại xong.",
                                     **job.status()), status_code=409)
        return dict(started=True, **job.status())

    @app.get("/api/process")
    async def api_process_status(request: Request):  # async + snapshot: chạy thẳng trên event loop, không qua threadpool, không khoá, không đĩa
        require_admin(request)
        return JSONResponse(job.status(), headers={"Cache-Control": "no-store"})

    # ---------- video: tải lên theo mảnh có thể nối lại (chỉ chủ nhà), phục vụ có Range ----------
    vstage = store.video_staging_dir(data_dir)
    vdir, vposters = store.videos_dir(data_dir), store.video_poster_dir(data_dir)
    for d in (vstage, vdir, vposters):
        os.makedirs(d, exist_ok=True)
    vsessions = {}   # uid -> dict phiên (bản sao trên đĩa: {uid}.json cạnh {uid}.part / {uid}.poster)
    vlocks = {}      # uid -> Lock ghi/finish của đúng phiên đó
    app.state.video_locks = vlocks  # cho test
    vglobal = threading.Lock()
    vclean = {"t": 0.0}

    def vpath(uid, ext):
        return os.path.join(vstage, uid + ext)

    def vsave(s):
        tmp = vpath(s["uid"], ".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(s, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, vpath(s["uid"], ".json"))

    def vdrop(uid):
        vsessions.pop(uid, None)
        for ext in (".json", ".json.tmp", ".part", ".poster", ".poster.tmp"):
            try:
                os.remove(vpath(uid, ext))
            except OSError:
                pass

    def vcleanup(force=False):
        """Xoá phiên hết hạn + file mồ côi trong video-staging (khi khởi động và tối đa mỗi 60 s khi có request video)."""
        now = time.time()
        with vglobal:
            if not force and now - vclean["t"] < 60:
                return
            vclean["t"] = now
            for uid in [u for u, s in vsessions.items() if s["expires"] <= now]:
                vdrop(uid)
            try:
                names = os.listdir(vstage)
            except OSError:
                return
            for f in names:
                uid = f.split(".", 1)[0]
                if uid not in vsessions and UID_RE.fullmatch(uid):
                    try:
                        os.remove(os.path.join(vstage, f))
                    except OSError:
                        pass

    def vload():
        for f in sorted(os.listdir(vstage)):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(vstage, f)) as fh:
                    s = json.load(fh)
                uid = s["uid"]
                if not (UID_RE.fullmatch(uid) and f == uid + ".json" and s["expires"] > time.time()):
                    continue
                if s["state"] == "uploading":
                    sz = os.path.getsize(vpath(uid, ".part"))
                    if sz < s["offset"]:
                        continue
                    if sz > s["offset"]:  # chunk dở dang lúc chết: bỏ phần chưa được ghi nhận
                        with open(vpath(uid, ".part"), "r+b") as fh2:
                            fh2.truncate(s["offset"])
                    if s["poster"] and not os.path.exists(vpath(uid, ".poster")):
                        s["poster"] = False
                vsessions[uid] = s
            except (OSError, ValueError, KeyError, TypeError):
                continue
        vcleanup(force=True)

    vload()

    def vguard(request, mutate=True):
        admin_enabled()
        require_admin(request)
        if mutate:
            same_origin_or_403(request)

    def vsession(uid):
        s = vsessions.get(uid) if UID_RE.fullmatch(uid) else None
        if s is None or s["expires"] <= time.time():
            raise HTTPException(404, "Không có phiên tải video này.")
        return s

    def vstate(s):
        if s["state"] == "published":
            return "published"
        return "ready" if s["offset"] >= s["size"] and s["poster"] else "uploading"

    def vview(s):
        out = dict(uid=s["uid"], offset=s["offset"], size=s["size"], poster=bool(s["poster"]), state=vstate(s), expires=int(s["expires"]))
        if s["state"] == "published":
            out["id"] = s["result"]["id"]
        return out

    def vjson(obj, status=200):
        return JSONResponse(obj, status_code=status, headers=NOSTORE)

    @app.post("/api/admin/video/init")
    async def api_video_init(request: Request):
        data = await admin_json(request, VIDEO_INIT_BODY_MAX)
        name, size, sha, title = data.get("name"), data.get("size"), data.get("sha256"), data.get("title", "")
        ev = data.get("ev")
        fname = safe_name(name, (".mp4",)) if isinstance(name, str) else None
        if fname is None:
            raise HTTPException(400, "Tên file phải kết thúc bằng .mp4")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= VIDEO_MAX_BYTES:
            raise HTTPException(400, f"Kích thước video không hợp lệ (tối đa {VIDEO_MAX_BYTES // 2**30} GB).")
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha.lower()):
            raise HTTPException(400, "sha256 phải gồm 64 ký tự hex.")
        sha = sha.lower()
        if ev is not None and (not isinstance(ev, str) or ev not in store.EVENT_IDS):
            raise HTTPException(400, "Sự kiện không hợp lệ.")
        if not isinstance(title, str) or len(title) > VIDEO_TITLE_MAX or any(ord(c) < 32 for c in title):
            raise HTTPException(400, f"Tiêu đề tối đa {VIDEO_TITLE_MAX} ký tự.")
        title = title.strip()
        return await run_in_threadpool(video_init, fname, size, sha, ev, title)

    def video_init(fname, size, sha, ev, title):
        vcleanup()
        db = store.connect_ro(data_dir)
        try:
            dup = db.execute("SELECT id FROM videos WHERE sha256=?", (sha,)).fetchone()
        finally:
            db.close()
        if dup:
            return vjson(dict(error="duplicate", id=dup[0]), 409)
        with vglobal:
            active = [s for s in vsessions.values() if s["state"] == "uploading" and s["expires"] > time.time()]
            for s in active:
                if s["sha256"] == sha:
                    if s["size"] != size:
                        return vjson(dict(error="mismatch", uid=s["uid"]), 409)
                    return vjson(dict(uid=s["uid"], offset=s["offset"], size=s["size"], chunk_max=VIDEO_CHUNK_MAX, expires=int(s["expires"])))
            if len(active) >= MAX_VIDEO_SESSIONS:
                return vjson(dict(error="too_many_sessions"), 429)
            owed = sum(s["size"] - s["offset"] for s in active)
            if shutil.disk_usage(data_dir).free - owed - size < min_free:
                return vjson(dict(error="disk_full", message=f"Ổ đĩa không đủ chỗ (cần chừa {min_free // 2**20} MB trống)."), 507)
            uid = uuid.uuid4().hex
            now = time.time()
            s = dict(uid=uid, name=fname, size=size, sha256=sha, ev=ev, title=title, offset=0, poster=False, psha="", created=now,
                     expires=now + VIDEO_SESSION_TTL, state="uploading", result=None)
            open(vpath(uid, ".part"), "wb").close()
            vlocks[uid] = threading.Lock()
            vsave(s)
            vsessions[uid] = s
        return vjson(dict(uid=uid, offset=0, size=size, chunk_max=VIDEO_CHUNK_MAX, expires=int(s["expires"])))

    @app.get("/api/admin/video/{uid}")
    async def api_video_status(uid: str, request: Request):
        vguard(request, mutate=False)
        return vjson(vview(vsession(uid)))

    async def read_limited(request, limit):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"Nội dung quá lớn (tối đa {limit // 2**20} MB).")
        buf = bytearray()
        try:
            async for c in request.stream():
                buf += c
                if len(buf) > limit:
                    raise HTTPException(413, f"Nội dung quá lớn (tối đa {limit // 2**20} MB).")
        except ClientDisconnect:
            raise HTTPException(400, "Kết nối bị ngắt giữa chừng.")
        if declared and declared.isdigit() and int(declared) != len(buf):
            raise HTTPException(400, "Thân yêu cầu ngắn hơn Content-Length.")
        return bytes(buf)

    @app.put("/api/admin/video/{uid}/chunk")
    async def api_video_chunk(uid: str, request: Request):
        vguard(request)
        s = vsession(uid)
        try:
            off = int(request.query_params.get("off", ""))
        except ValueError:
            raise HTTPException(400, "Thiếu hoặc sai off.")
        if off < 0:
            raise HTTPException(400, "Thiếu hoặc sai off.")
        body = await read_limited(request, VIDEO_CHUNK_MAX)  # nhận đủ thân TRƯỚC khi chạm file: ngắt giữa chừng không để lại byte nào
        if not body:
            raise HTTPException(400, "Mảnh rỗng.")
        return await run_in_threadpool(video_chunk, s, off, body)

    def video_chunk(s, off, body):
        lk = vlocks.setdefault(s["uid"], threading.Lock())
        if not lk.acquire(blocking=False):
            return vjson(dict(error="busy", offset=s["offset"]), 409)
        try:
            if s["state"] != "uploading" or off != s["offset"] or off + len(body) > s["size"]:
                return vjson(dict(error="offset", offset=s["offset"]), 409)
            if shutil.disk_usage(data_dir).free < min_free:
                return vjson(dict(error="disk_full"), 507)
            committed = s["offset"]
            try:
                with open(vpath(s["uid"], ".part"), "r+b") as fh:
                    fh.seek(committed)
                    try:
                        fh.write(body)
                        fh.flush()
                        os.fsync(fh.fileno())
                    except BaseException:
                        fh.truncate(committed)
                        raise
                s["offset"] = committed + len(body)
                try:
                    vsave(s)
                except BaseException:
                    s["offset"] = committed
                    with open(vpath(s["uid"], ".part"), "r+b") as fh:
                        fh.truncate(committed)
                    raise
            except OSError as e:
                log.error("ghi chunk video lỗi: %s", e)
                return vjson(dict(error="write_failed", offset=s["offset"]), 500)
            return vjson(dict(offset=s["offset"]))
        finally:
            lk.release()

    @app.put("/api/admin/video/{uid}/poster")
    async def api_video_poster(uid: str, request: Request):
        vguard(request)
        s = vsession(uid)
        body = await read_limited(request, POSTER_MAX_BYTES)
        return await run_in_threadpool(video_poster, s, body)

    def check_poster(body):
        try:
            with Image.open(io.BytesIO(body)) as im:
                fmt, (w, h) = im.format, im.size
                if fmt != "JPEG":
                    return "Poster phải là JPEG."
                if not (0 < w <= POSTER_MAX_SIDE and 0 < h <= POSTER_MAX_SIDE):  # kiểm TRƯỚC khi giải mã: khai báo kích thước khổng lồ không bao giờ được giải
                    return f"Poster tối đa {POSTER_MAX_SIDE}px mỗi cạnh."
                im.load()
        except Exception:
            return "Không đọc được poster."
        return None

    def video_poster(s, body):
        if not body:
            raise HTTPException(400, "Poster rỗng.")
        err = check_poster(body)
        if err:
            raise HTTPException(400, err)
        lk = vlocks.setdefault(s["uid"], threading.Lock())
        with lk:
            if s["state"] != "uploading":
                return vjson(dict(error="published"), 409)
            tmp = vpath(s["uid"], ".poster.tmp")
            with open(tmp, "wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, vpath(s["uid"], ".poster"))
            s["poster"], s["psha"] = True, hashlib.sha256(body).hexdigest()
            vsave(s)
        return vjson(dict(poster=True))

    @app.post("/api/admin/video/{uid}/finish")
    async def api_video_finish(uid: str, request: Request):
        vguard(request)
        s = vsession(uid)
        return await run_in_threadpool(video_finish, s)

    def fsync_dir(d):
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def video_finish(s):
        uid = s["uid"]
        with vlocks.setdefault(uid, threading.Lock()):
            if s["state"] == "published":
                return vjson(s["result"])
            if s["offset"] != s["size"]:
                return vjson(dict(error="incomplete", offset=s["offset"]), 409)
            if not s["poster"]:
                return vjson(dict(error="no_poster"), 409)
            part, poster = vpath(uid, ".part"), vpath(uid, ".poster")

            def reject(reason):
                vdrop(uid)  # nội dung đã cố định theo sha256: giữ phiên cũng không sửa được → giải phóng chỗ
                return vjson(dict(error="invalid", reason=reason), 422)
            h = hashlib.sha256()
            with open(part, "rb") as fh:
                for c in iter(lambda: fh.read(1 << 20), b""):
                    h.update(c)
            if h.hexdigest() != s["sha256"]:
                return reject("sha256 không khớp với file đã tải lên.")
            try:
                info = mp4box.probe(part)
            except mp4box.Mp4Error as e:
                return reject(str(e))
            except OSError as e:
                log.error("đọc video lỗi: %s", e)
                return vjson(dict(error="read_failed"), 500)
            vid = hashlib.sha1(s["sha256"].encode()).hexdigest()[:16]
            v, pv = s["sha256"][:10], s["psha"][:10]
            fmp4, fjpg = os.path.join(vdir, f"{vid}-{v}.mp4"), os.path.join(vposters, f"{vid}-{pv}.jpg")
            for p in (part, poster):
                fd = os.open(p, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            moved = []
            try:
                os.replace(part, fmp4)
                moved.append((fmp4, part))
                os.replace(poster, fjpg)
                moved.append((fjpg, poster))
                fsync_dir(vdir)
                fsync_dir(vposters)
                db = store.connect(data_dir, init=False)
                try:
                    db.execute("BEGIN IMMEDIATE")
                    so = db.execute("SELECT COALESCE(MAX(sort),0)+1 FROM videos WHERE ev IS ?", (s["ev"],)).fetchone()[0]
                    db.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (vid, s["name"], s["sha256"], s["size"], v, pv, info["w"], info["h"], info["duration"], info["codec"],
                                s["title"], s["ev"], so, time.time()))
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    dup = db.execute("SELECT id FROM videos WHERE sha256=?", (s["sha256"],)).fetchone()
                    for a, b in reversed(moved):
                        os.replace(a, b)
                    moved = []
                    if dup:
                        vdrop(uid)
                        return vjson(dict(error="duplicate", id=dup[0]), 409)
                    raise
                finally:
                    db.close()
            except BaseException:
                for a, b in reversed(moved):  # công bố hỏng giữa chừng: trả file về staging, KHÔNG có dòng DB → phiên còn nguyên để thử lại
                    try:
                        os.replace(a, b)
                    except OSError:
                        pass
                raise
            res = dict(id=vid, v=v, pv=pv, w=info["w"], h=info["h"], duration=info["duration"], title=s["title"], ev=s["ev"])
            s["state"], s["result"] = "published", res
            try:
                vsave(s)
            except OSError:
                log.error("không ghi được trạng thái published của %s", uid)
            gallery_state(force=True)
            return vjson(res)

    @app.api_route("/v/{fname}", methods=["GET", "HEAD"])
    def video_file(fname: str, request: Request):
        """/v/{id}-{v}.mp4 và /v/{id}-{pv}.jpg: chỉ 200 khi id tồn tại và phiên bản đúng; mọi URL khác (kể cả có query) 404 no-store."""
        m = VIDEO_FILE.match(fname)
        if not m or request.url.query:
            raise HTTPException(404)
        vid, ver, ext = m.groups()
        idx = 0 if ext == "mp4" else 1
        cur = gallery_state()["vids"].get(vid)
        if cur is None or cur[idx] != ver:
            cur = gallery_state(fresh=True)["vids"].get(vid)
            if cur is None or cur[idx] != ver:
                raise HTTPException(404)
        p = os.path.join(vdir if ext == "mp4" else vposters, fname)
        if not os.path.isfile(p):
            raise HTTPException(404)
        return FileResponse(p, media_type="video/mp4" if ext == "mp4" else "image/jpeg", headers={"Cache-Control": IMMUTABLE})

    # ---------- ảnh: WebP đặt tên theo nội dung, thumbnail/bản xem cũ, bản gốc (host ngoài chỉ lấy được ba nhóm đầu: HostGate) ----------
    @app.api_route("/i/{fname}", methods=["GET", "HEAD"])
    def deriv(fname: str, request: Request):
        """/i/{id}-{v}-{size}.webp: chỉ 200 khi id tồn tại, v đúng bản hiện tại và size hợp lệ — mọi URL khác 404 (no-store
        do middleware), nên CDN không bao giờ cache URL đoán mò hay bản cũ."""
        m = WEBP_NAME.match(fname)
        if not m or int(m.group(3)) not in WEBP_SIZES or request.url.query:  # query lạ = khoá cache khác trên CDN: không phục vụ
            raise HTTPException(404)
        v = gallery_state()["meta"].get(m.group(1), (None,))[0]
        if v is None or v != m.group(2):
            v = gallery_state(fresh=True)["meta"].get(m.group(1), (None,))[0]  # bản nhớ có thể cũ hơn manifest khách đã nhận
            if v is None or v != m.group(2):
                raise HTTPException(404)
        p = os.path.join(store.deriv_dir(data_dir), fname)
        if not os.path.exists(p):
            raise HTTPException(404)
        return FileResponse(p, media_type="image/webp", headers={"Cache-Control": IMMUTABLE})

    def gate_hidden(pid, request):
        """Ảnh ẩn: 404 y hệt id không tồn tại (cùng HTTPException) trừ khi có ?t= hợp lệ cho đúng id này. True = vào bằng token."""
        g = gallery_state()
        if pid in g["hidden"]:
            if not token_ok(pid, request.query_params.get("t"), g):
                raise HTTPException(404)
            return True
        return False

    def photo_file(folder, pid, request, ext=".jpg"):
        if not PHOTO_ID.fullmatch(pid):
            raise HTTPException(404)
        by_token = gate_hidden(pid, request)
        p = os.path.join(folder, pid + ext)
        if not os.path.exists(p):
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, no-store" if by_token else LEGACY_CACHE})

    @app.api_route("/thumbs/{pid}.jpg", methods=["GET", "HEAD"])
    def thumb(pid: str, request: Request):
        return photo_file(store.thumb_dir(data_dir), pid, request)

    @app.api_route("/previews/{pid}.jpg", methods=["GET", "HEAD"])
    def preview(pid: str, request: Request):
        return photo_file(store.preview_dir(data_dir), pid, request)

    @app.get("/originals/{pid}")  # cùng origin, tải về dạng attachment; không qua CDN
    def original(pid: str, request: Request):
        _, _, photos = index()
        if pid not in photos:
            raise HTTPException(404)
        by_token = gate_hidden(pid, request)
        path = os.path.join(store.originals_dir(data_dir), photos[pid]["path"])
        if not os.path.exists(path):
            raise HTTPException(404)
        return FileResponse(path, filename=photos[pid]["path"], headers={"Cache-Control": "private, no-store" if by_token else "private, max-age=86400"})

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def healthz():
        """O(1): đọc số liệu đĩa đã nhớ (làm mới nền mỗi DISK_TTL giây), không threadpool, không khoá, không đụng DB/thư mục ảnh."""
        du = disk_state()
        return JSONResponse(dict(ok=True, disk_free=du["free"], disk_total=du["total"]), headers={"Cache-Control": "no-store"})

    @app.api_route("/robots.txt", methods=["GET", "HEAD"])
    def robots():
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/")
    def home():
        return static_page("index.html", cdn)

    @app.get("/manage")
    def manage(request: Request):
        st = admin_state(request, header=False)  # trình duyệt điều hướng không gửi được header → chỉ nhận cookie phiên
        if st != "ok":
            return static_page("manage-login.html", cdn, 429 if st == "limited" else 401, fallback=LOGIN_FALLBACK)
        return static_page("manage.html", cdn)

    # CSS/JS/font/hoạ tiết của giao diện: chỉ file tĩnh cục bộ, không nạp gì từ ngoài
    app.mount("/static", CachedStatic(directory=os.path.join(HERE, "static")), name="static")

    app.add_middleware(HostGate, app_hosts=app_hosts)  # trong cùng NoIndex: 404 của cổng cũng có header an toàn
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
    ap.add_argument("--process-threads", type=int, default=int(env("ALBUM_PROCESS_THREADS", default=-1)),
                    help="số luồng onnxruntime của Xử lý (model riêng); mặc định = lõi CPU khả dụng − 2; 0 = dùng chung với tìm kiếm")
    ap.add_argument("--max-upload-mb", type=int, default=int(env("ALBUM_MAX_UPLOAD_MB", default=60)))
    ap.add_argument("--min-free-mb", type=int, default=int(env("ALBUM_MIN_FREE_MB", default=5120)),
                    help="từ chối tải lên khi ổ đĩa của DATA_DIR còn trống ít hơn mức này")
    ap.add_argument("--max-total-gb", type=float, default=(float(os.environ["ALBUM_MAX_TOTAL_GB"] or 0) if "ALBUM_MAX_TOTAL_GB" in os.environ else 20.0),
                    help="tổng dung lượng ảnh gốc tối đa trong album; 0 (hoặc ALBUM_MAX_TOTAL_GB rỗng) = không giới hạn")
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
                     max_total=int(a.max_total_gb * 1024 ** 3) if a.max_total_gb > 0 else None,
                     process_threads=None if a.process_threads < 0 else a.process_threads)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", workers=1)


if __name__ == "__main__":
    main()
