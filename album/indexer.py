"""Lõi lập chỉ mục dùng chung cho CLI (index_photos.py) và nút "Xử lý" của server.

Với mỗi ảnh: giải mã, dò mọi khuôn mặt, nhúng, ghi vào SQLite, làm thumbnail 640 px và
bản xem 2000 px. Mỗi ảnh một giao dịch, ảnh hỏng chỉ bị báo lỗi chứ không dừng cả lượt.
"""
import calendar
import hashlib
import io
import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
from PIL import Image, ImageCms, features

import store
from faces import ImageTooLarge, load_image

log = logging.getLogger("album")
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
THUMB_SIDE = 640
PREVIEW_SIDE = 2000
DEFAULT_MIN_FACE = 40
DEFAULT_DET_SIZE = 1600

# --- bộ WebP cho trang công khai (xem derive_one) ---
DERIV_VERSION = 1       # đổi khi đổi cách sinh WebP (cỡ, chất lượng, màu): v đổi theo → URL mới, CDN không giữ bản cũ
THUMB_WIDTHS = (240, 480)  # bề rộng điểm ảnh của thumb (srcset)
WEBP_PREVIEW_SIDE = 2000   # cạnh dài của bản xem
WEBP_SIZES = THUMB_WIDTHS + (WEBP_PREVIEW_SIDE,)
WEBP_QUALITY = 78
WEBP_METHOD = 4            # 0..6; 4 là mặc định, 6 chậm gấp ~2 mà chỉ nhỏ hơn vài %
DERIVE_MAX_PIXELS = 200_000_000


def find_photos(root):
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in EXTS and not f.startswith("."):
                out.append(os.path.relpath(os.path.join(d, f), root))
    return sorted(out)


def pending_photos(db, root, det_size, rels=None):
    """Ảnh chưa có trong chỉ mục (hoặc đổi kích thước/mtime/det-size), bỏ ảnh đã từng lỗi mà file không đổi.
    Trả (todo, số đã có)."""
    rels = find_photos(root) if rels is None else rels
    known = {r[0]: r[1:] for r in db.execute("SELECT path, size, mtime_ns, det_size FROM photos")}
    failed = {r[0]: r[1:] for r in db.execute("SELECT path, size, mtime_ns FROM failed")}
    todo = []
    for rel in rels:
        st = os.stat(os.path.join(root, rel))
        if known.get(rel) != (st.st_size, st.st_mtime_ns, det_size) and failed.get(rel) != (st.st_size, st.st_mtime_ns):
            todo.append(rel)
    return todo, len(rels) - len(todo)


class Inflight:
    """Ghi bền (JSON {tên: số lần}) các file đang giải mã/suy luận. Một lần process bị dừng khi deploy (SIGTERM/SIGKILL)
    không được quy cho file: chỉ file nằm trong marker ở hai lần chết liền nhau mới bị loại (xem recover_crashed)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.counts = load_marker(path)

    def _write(self):
        if self.counts:
            with open(self.path + ".part", "w") as f:
                json.dump(self.counts, f)
            os.replace(self.path + ".part", self.path)
        elif os.path.exists(self.path):
            os.remove(self.path)

    def add(self, rel):
        with self.lock:
            self.counts[rel] = self.counts.get(rel, 0) + 1
            self._write()

    def remove(self, rel):
        with self.lock:
            self.counts.pop(rel, None)
            self._write()


def marker_path(data_dir):
    return os.path.join(data_dir, "decoding.json")


def mark_failed(db, root, rel, reason):
    try:
        st = os.stat(os.path.join(root, rel))
        db.execute("INSERT OR REPLACE INTO failed VALUES (?,?,?,?)", (rel, st.st_size, st.st_mtime_ns, reason[:300]))
        db.commit()
    except OSError:
        pass


CRASH_REASON = "làm sập khi giải mã (2 lần)"
OLD_CRASH_REASON = "làm sập khi giải mã"  # chính sách cũ: loại ngay lần chết đầu tiên → khôi phục một lần lúc khởi động
MAX_STRIKES = 2


def load_marker(mp):
    try:
        d = json.load(open(mp))
    except (OSError, ValueError):
        return {}
    return {k: 1 for k in d} if isinstance(d, list) else dict(d)


def recover_crashed(db, root, data_dir):
    """File nằm trong marker = đang xử lý lúc process chết. Mới một lần → giữ nguyên để thử lại (deploy/SIGTERM không
    phải lỗi của file); đủ MAX_STRIKES lần → chuyển sang rejected/. Trả danh sách tên bị loại."""
    mp = marker_path(data_dir)
    counts = load_marker(mp)
    moved = []
    for rel in list(counts):
        src = os.path.join(root, rel)
        if not os.path.exists(src):
            del counts[rel]
        elif counts[rel] >= MAX_STRIKES:
            os.makedirs(store.rejected_dir(data_dir), exist_ok=True)
            os.replace(src, os.path.join(store.rejected_dir(data_dir), os.path.basename(rel)))
            moved.append(rel)
            del counts[rel]
    if counts:
        with open(mp + ".part", "w") as f:
            json.dump(counts, f)
        os.replace(mp + ".part", mp)
    elif os.path.exists(mp):
        os.remove(mp)
    return moved


def restore_rejected(db, root, data_dir):
    """Một lần: ảnh bị loại oan bởi chính sách cũ (reason == OLD_CRASH_REASON; không đụng ảnh hỏng khi giải mã) → về originals/."""
    n = 0
    for (name,) in db.execute("SELECT path FROM failed WHERE reason=?", (OLD_CRASH_REASON,)).fetchall():
        src, dst = os.path.join(store.rejected_dir(data_dir), name), os.path.join(root, name)
        if os.path.exists(src) and not os.path.exists(dst) and \
                db.execute("SELECT 1 FROM originals WHERE name=?", (name,)).fetchone():
            os.replace(src, dst)
            db.execute("DELETE FROM failed WHERE path=?", (name,))
            n += 1
    db.commit()
    return n


def make_derivatives(rgb, data_dir, pid):
    """Từ mảng đã giải mã (đã xoay EXIF) → thumbnail 640 px + bản xem 2000 px. Không giải mã lần hai, không sao chép mảng."""
    h, w = rgb.shape[:2]
    im = Image.frombuffer("RGB", (w, h), np.ascontiguousarray(rgb), "raw", "RGB", 0, 1)
    im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
    pv = os.path.join(store.preview_dir(data_dir), pid + ".jpg")
    im.save(pv + ".part", "JPEG", quality=85)
    os.replace(pv + ".part", pv)
    im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    th = os.path.join(store.thumb_dir(data_dir), pid + ".jpg")
    im.save(th + ".part", "JPEG", quality=80)
    os.replace(th + ".part", th)


def _load_job(root, rel, data_dir, max_pixels, inflight):
    """Chạy ở thread phụ: giải mã (kiểm số điểm ảnh trong header TRƯỚC khi giải mã) + làm thumbnail/preview."""
    path = os.path.join(root, rel)
    t0 = time.perf_counter()
    try:
        if inflight:
            inflight.add(rel)
        rgb = load_image(path, max_pixels)
        t1 = time.perf_counter()
        make_derivatives(rgb, data_dir, store.photo_id(rel))
        return rel, rgb, t1 - t0, time.perf_counter() - t1, None
    except Exception as e:  # file hỏng: bỏ qua, báo lỗi
        return rel, None, 0, 0, e


def index_photos(db, engine, root, data_dir, todo, det_size=DEFAULT_DET_SIZE, min_face=DEFAULT_MIN_FACE,
                 window=3, workers=2, max_pixels=None, on_progress=None, should_stop=None, track_inflight=True):
    """Xử lý danh sách `todo` (đường dẫn tương đối trong `root`). `db` phải tạo ở luồng gọi hàm này.

    window = số ảnh giải mã cùng lúc kể cả ảnh đang suy luận; 1 = tuần tự (RAM thấp nhất), >1 = giải mã trước.
    on_progress(i, n, rel, info) với info = dict(ok, faces, w, h, err, td, tt, tdet, temb).
    Trả về dict thống kê.
    """
    st = dict(ok=0, bad=0, faces=0, td=0.0, tt=0.0, tdet=0.0, temb=0.0)
    os.makedirs(store.thumb_dir(data_dir), exist_ok=True)
    os.makedirs(store.preview_dir(data_dir), exist_ok=True)
    wall0 = time.perf_counter()
    inflight = Inflight(marker_path(data_dir)) if track_inflight else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submit = lambda rel: pool.submit(_load_job, root, rel, data_dir, max_pixels, inflight)
        win = deque(submit(rel) for rel in todo[:window])
        nxt = len(win)
        for i in range(1, len(todo) + 1):
            rel, rgb, td, tt, err = win.popleft().result()
            if should_stop and should_stop():
                break
            if window > 1 and nxt < len(todo):  # window=1: không giải mã trước, RAM đỉnh thấp nhất
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
                mark_failed(db, root, rel, f"{type(err).__name__}: {err}")
            if inflight:
                inflight.remove(rel)
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
            if window <= 1 and nxt < len(todo):
                win.append(submit(todo[nxt]))
                nxt += 1
    st["wall"] = time.perf_counter() - wall0
    return st


# ---------------------------------------------------------------------------------------------
# WebP cho trang công khai + metadata (photo_meta). Không dò mặt, không chạm decoding.json/failed.
# ---------------------------------------------------------------------------------------------
_TRANSPOSE = {2: Image.FLIP_LEFT_RIGHT, 3: Image.ROTATE_180, 4: Image.FLIP_TOP_BOTTOM, 5: Image.TRANSPOSE,
              6: Image.ROTATE_270, 7: Image.TRANSVERSE, 8: Image.ROTATE_90}


def require_webp():
    """Pillow phải có libwebp + littlecms: thiếu thì dừng ngay lúc khởi động, không để mọi ảnh lỗi âm thầm."""
    for f in ("webp", "littlecms2"):
        if not features.check(f):
            raise RuntimeError(f"Pillow thiếu hỗ trợ {f} — cần bản wheel chính thức (pillow=={Image.__version__})")


def content_version(sha256_hex):
    """v (8 hex) = 6 hex đầu SHA-256 của file gốc + 2 hex của DERIV_VERSION: đổi file hoặc đổi cách sinh đều đổi v."""
    return sha256_hex[:6] + format(DERIV_VERSION & 0xFF, "02x")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def exif_camera(im):
    """Make|Model|BodySerial từ EXIF ('' nếu không có): để không ghép khung của hai máy ảnh vào một nhóm chụp liên tiếp."""
    try:
        ex = im.getexif()
        sub = ex.get_ifd(0x8769)
        parts = [ex.get(0x010F), ex.get(0x0110), sub.get(0xA431)]
        return "|".join(str(x).strip("\x00 ") for x in parts if x)
    except Exception:
        return ""


SIG_SIDE = 16
BURST_MAX_GAP = 1.0   # giây giữa hai khung liền nhau
BURST_MAX_DIFF = 6.0  # sai khác điểm ảnh trung bình (0..255) trên chữ ký xám 16x16, so với khung liền trước
BURST_FIRST_DIFF = 12.0  # ... và so với khung ĐẦU của nhóm (chống trôi dần: cảnh đổi chậm không thành một nhóm khổng lồ)
BURST_MAX_SPAN = 5.0     # tổng thời gian tối đa của một nhóm (giây)


def exif_taken(im):
    """DateTimeOriginal + SubSecTimeOriginal → giây epoch (giờ trên ảnh coi như UTC, chỉ để sắp xếp); thiếu/hỏng → None."""
    try:
        sub = im.getexif().get_ifd(0x8769)
        raw = sub.get(0x9003)
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "ignore")
        t = calendar.timegm(datetime.strptime(raw.strip("\x00 "), "%Y:%m:%d %H:%M:%S").timetuple())
        frac = sub.get(0x9291)
        if isinstance(frac, bytes):
            frac = frac.decode("ascii", "ignore")
        frac = str(frac or "").strip("\x00 ")
        return t + (float("0." + frac) if frac.isdigit() else 0.0)
    except Exception:
        return None


_SRGB = {}


def to_srgb(im):
    """Về RGB sRGB, theo profile ICC nhúng (Display-P3, Adobe RGB, CMYK…) để ảnh không bị nhạt màu; không profile = coi như sRGB."""
    icc = im.info.get("icc_profile")
    if icc and im.mode in ("RGB", "CMYK"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "sRGB" not in (ImageCms.getProfileDescription(src) or ""):
                dst = _SRGB.get("p") or _SRGB.setdefault("p", ImageCms.createProfile("sRGB"))
                return ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
        except Exception:
            pass  # profile hỏng: coi như sRGB
    return im if im.mode == "RGB" else im.convert("RGB")


def derive_one(path, data_dir, pid, sha, max_pixels=DERIVE_MAX_PIXELS):
    """Giải mã MỘT lần (JPEG/MPO dùng draft: giải mã thu nhỏ 1/2,1/4,1/8 rất nhanh), xoay EXIF, sang sRGB, ghi
    {pid}-{v}-{240,480,2000}.webp vào deriv_dir. Trả dict(v, taken, color, w, h) (w,h = kích thước hiển thị đầy đủ, sau xoay).
    Ghi từng file qua .part + rename; chưa chạm DB."""
    v = content_version(sha)
    with Image.open(path) as im:
        w0, h0 = im.size
        if max_pixels and w0 * h0 > max_pixels:
            raise ImageTooLarge(f"{w0}x{h0}")
        taken = exif_taken(im)
        cam = exif_camera(im)
        if im.mode.startswith(("I", "F")):  # xám 16-bit…: dùng đường chung của faces.load_image (chuẩn hoá về 8-bit, đã xoay)
            prev = Image.fromarray(load_image(path, max_pixels))
            dims = prev.size
            sc = min(1.0, WEBP_PREVIEW_SIDE / max(dims))
            if sc < 1:
                prev = prev.resize((max(1, round(dims[0] * sc)), max(1, round(dims[1] * sc))), Image.LANCZOS)
            return _write_set(prev, data_dir, pid, v, taken, dims, cam)
        orient = im.getexif().get(0x0112, 1)
        sc = min(1.0, WEBP_PREVIEW_SIDE / max(w0, h0))
        pw, ph = max(1, round(w0 * sc)), max(1, round(h0 * sc))
        if im.format in ("JPEG", "MPO") and sc < 1 and im.mode in ("RGB", "L"):
            im.draft(im.mode, (pw, ph))  # giữ ≥ cỡ yêu cầu theo cả hai chiều
        im.load()
        small = im if im.size == (pw, ph) else im.resize((pw, ph), Image.LANCZOS, reducing_gap=3.0)
        prev = to_srgb(small)
        if orient in _TRANSPOSE:
            prev = prev.transpose(_TRANSPOSE[orient])
    dims = (h0, w0) if orient in (5, 6, 7, 8) else (w0, h0)
    return _write_set(prev, data_dir, pid, v, taken, dims, cam)


def _write_set(prev, data_dir, pid, v, taken, dims, cam=""):
    d = store.deriv_dir(data_dir)
    os.makedirs(d, exist_ok=True)

    def save(img, size):
        out = os.path.join(d, f"{pid}-{v}-{size}.webp")
        img.save(out + ".part", "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
        os.replace(out + ".part", out)

    save(prev, WEBP_PREVIEW_SIDE)
    pw, ph = prev.size
    src, thumbs = prev, {}
    for tw in sorted(THUMB_WIDTHS, reverse=True):  # 480 từ bản xem, 240 từ 480
        if tw < src.size[0]:
            src = src.resize((tw, max(1, round(ph * tw / pw))), Image.LANCZOS)
        thumbs[tw] = src  # ảnh gốc hẹp hơn tw: giữ nguyên, không phóng to
        save(src, tw)
    r, g, b = thumbs[min(THUMB_WIDTHS)].resize((1, 1), Image.BOX).getpixel((0, 0))
    sig = thumbs[min(THUMB_WIDTHS)].convert("L").resize((SIG_SIDE, SIG_SIDE), Image.BOX).tobytes()
    return dict(v=v, taken=taken, color=f"#{r:02x}{g:02x}{b:02x}", w=dims[0], h=dims[1], cam=cam, sig=sig)


def pending_derive(db):
    """Ảnh đã lập chỉ mục mà chưa có photo_meta hợp lệ: thiếu, khác DERIV_VERSION, hoặc file gốc đã đổi từ lúc sinh."""
    return db.execute(
        "SELECT p.id, p.path, p.size, p.mtime_ns FROM photos p LEFT JOIN photo_meta m ON m.id=p.id "
        "WHERE (m.id IS NULL OR m.deriv_ver!=? OR m.size!=p.size OR m.mtime_ns!=p.mtime_ns OR m.sig IS NULL) "
        "AND NOT EXISTS (SELECT 1 FROM derive_failed f WHERE f.id=p.id AND f.size=p.size AND f.mtime_ns=p.mtime_ns) ORDER BY p.path",
        (DERIV_VERSION,)).fetchall()


def known_derive_failures(db):
    """Số ảnh đang có bản ghi lỗi sinh WebP còn hiệu lực (file gốc chưa đổi từ lúc lỗi)."""
    return db.execute("SELECT count(*) FROM derive_failed f JOIN photos p ON p.id=f.id "
                      "WHERE f.size=p.size AND f.mtime_ns=p.mtime_ns").fetchone()[0]


def drop_stale_derivatives(data_dir, pid, keep_v=None):
    d = store.deriv_dir(data_dir)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        if n.startswith(pid + "-") and (keep_v is None or not n.startswith(f"{pid}-{keep_v}-")):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


def derive_pending(db, root, data_dir, workers=2, max_pixels=DERIVE_MAX_PIXELS, on_progress=None, should_stop=None):
    """Sinh WebP + photo_meta cho ảnh còn thiếu (ảnh mới và ảnh đã lập chỉ mục từ bản cũ). KHÔNG dò mặt, KHÔNG đánh dấu
    failed/decoding.json: lỗi chỉ được ghi log + đếm, ảnh giữ nguyên (v=null → giao diện dùng thumb cũ).
    Bộ nhớ chặn: tối đa `workers` ảnh cùng lúc. `db` tạo ở luồng gọi. on_progress(i, n, rel, err). Trả dict(ok, bad, wall)."""
    todo = pending_derive(db)
    known = {r[0]: (r[1], r[2]) for r in db.execute("SELECT name, sha256, size FROM originals")}
    st = dict(ok=0, bad=0, total=len(todo), wall=0.0)
    t0 = time.perf_counter()

    def job(row):
        pid, rel, size, _ = row
        try:
            path = os.path.join(root, rel)
            k = known.get(rel)
            sha = k[0] if k and k[1] == size and k[0] else file_sha256(path)  # upload web đã có SHA-256 trong DB
            return row, derive_one(path, data_dir, pid, sha, max_pixels), None
        except Exception as e:
            return row, None, e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        win, nxt = deque(), 0
        for i in range(1, len(todo) + 1):
            while nxt < len(todo) and len(win) < workers:
                win.append(pool.submit(job, todo[nxt]))
                nxt += 1
            if should_stop and should_stop():
                break
            (pid, rel, size, mtime_ns), m, err = win.popleft().result()
            if err is None:
                db.execute("INSERT OR REPLACE INTO photo_meta (id, taken, v, color, deriv_ver, w, h, size, mtime_ns, cam, sig) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (pid, m["taken"], m["v"], m["color"], DERIV_VERSION, m["w"], m["h"], size, mtime_ns, m["cam"], m["sig"]))
                db.commit()
                db.execute("DELETE FROM derive_failed WHERE id=?", (pid,))
                db.commit()
                drop_stale_derivatives(data_dir, pid, m["v"])
                st["ok"] += 1
            else:
                log.warning("WebP bỏ qua %s: %s: %s", rel, type(err).__name__, err)
                db.execute("INSERT OR REPLACE INTO derive_failed VALUES (?,?,?,?)",
                           (pid, size, mtime_ns, f"{type(err).__name__}: {err}"[:300]))
                db.commit()
                st["bad"] += 1
            if on_progress:
                on_progress(i, len(todo), rel, err)
    st["wall"] = time.perf_counter() - t0
    return st


def compute_bursts(db):
    """Gán photo_meta.burst = id khung ĐẦU của nhóm chụp liên tiếp (≥ 2 khung), còn lại NULL.

    Giới hạn chống trôi: khung cũng phải khác khung ĐẦU nhóm < BURST_FIRST_DIFF và cả nhóm kéo dài ≤ BURST_MAX_SPAN giây.
    Xác định (không phụ thuộc thứ tự xử lý): trong từng (máy ảnh, kích thước) — máy ảnh = Make|Model (+ số seri thân máy nếu
    EXIF có, để hai thân máy cùng model chụp xen kẽ không cắt chuỗi của nhau) — duyệt theo (giờ chụp, tên file); khung nối vào
    khung liền trước khi cách 0..BURST_MAX_GAP giây VÀ sai khác điểm ảnh trung bình < BURST_MAX_DIFF trên chữ ký xám 16x16
    của thumb 240 (chuỗi nối tiếp: so với khung liền trước, không phải khung đầu). Ảnh không có giờ chụp không thuộc nhóm nào.
    Tính lại toàn bộ mỗi lần (rẻ: chỉ đọc DB) nên khung mới thêm vào sau tự nhập vào nhóm cũ; chỉ UPDATE những dòng đổi.
    Chỉ là DỮ LIỆU, không đổi cách liệt kê/đếm. Trả số nhóm."""
    rows = db.execute("SELECT m.id, m.taken, m.cam, m.w, m.h, m.sig FROM photo_meta m JOIN photos p ON p.id=m.id "
                      "WHERE m.taken IS NOT NULL AND m.sig IS NOT NULL ORDER BY m.cam, m.w, m.h, m.taken, p.path").fetchall()
    groups, cur, prev, first = [], [], None, None
    for pid, taken, cam, w, h, sig in rows:
        arr = np.frombuffer(sig, np.uint8).astype(np.int16)
        if prev and prev[0] == (cam, w, h) and 0 <= taken - prev[1] <= BURST_MAX_GAP \
                and np.abs(arr - prev[2]).mean() < BURST_MAX_DIFF \
                and taken - first[0] <= BURST_MAX_SPAN and np.abs(arr - first[1]).mean() < BURST_FIRST_DIFF:
            cur.append(pid)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur, first = [pid], (taken, arr)
        prev = ((cam, w, h), taken, arr)
    if len(cur) > 1:
        groups.append(cur)
    want = {pid: g[0] for g in groups for pid in g}
    have = dict(db.execute("SELECT id, burst FROM photo_meta WHERE burst IS NOT NULL").fetchall())
    for pid in have.keys() - want.keys():
        db.execute("UPDATE photo_meta SET burst=NULL WHERE id=?", (pid,))
    for pid, b in want.items():
        if have.get(pid) != b:
            db.execute("UPDATE photo_meta SET burst=? WHERE id=?", (b, pid))
    db.commit()
    return len(groups)


def burst_stats(db):
    """Thống kê nhóm chụp liên tiếp: số nhóm, số khung trong nhóm, histogram kích thước, nhóm lớn nhất (tên file các khung)."""
    groups = {}
    for b, path in db.execute("SELECT m.burst, p.path FROM photo_meta m JOIN photos p ON p.id=m.id "
                              "WHERE m.burst IS NOT NULL ORDER BY m.taken, p.path"):
        groups.setdefault(b, []).append(path)
    hist = {}
    for g in groups.values():
        hist[len(g)] = hist.get(len(g), 0) + 1
    total = db.execute("SELECT count(*) FROM photos").fetchone()[0]
    big = max(groups.values(), key=len) if groups else []
    return dict(photos=total, groups=len(groups), frames_in_groups=sum(len(g) for g in groups.values()),
                tiles=total - sum(len(g) - 1 for g in groups.values()), histogram=dict(sorted(hist.items())),
                largest=len(big), largest_frames=big)
