"""Phát hiện + nhúng khuôn mặt bằng buffalo_l (SCRFD det_10g + ArcFace w600k_r50)
chạy thẳng bằng onnxruntime — không cần gói `insightface` (cần build C-extension).

Mọi ảnh ở đây là numpy RGB uint8 (H, W, 3), đã xoay đúng theo EXIF.
"""
import os
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps, UnidentifiedImageError

# Không nới MAX_IMAGE_PIXELS ở đây: file này cũng chạy trên đường tải selfie (không tin cậy).
# Chỉ index_photos.py (ảnh của chủ nhà) mới bỏ giới hạn đó.
QUERY_MAX_PIXELS = 100_000_000


class ImageTooLarge(Exception):
    pass


class ImageUnreadable(Exception):
    pass

COARSE_SIZES = (640, 320)
DET_FILE = "det_10g.onnx"
REC_FILE = "w600k_r50.onnx"

ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


@dataclass
class Face:
    bbox: np.ndarray  # x1,y1,x2,y2 theo pixel ảnh gốc (đã xoay)
    score: float
    kps: np.ndarray   # 5x2
    emb: np.ndarray = None  # 512 float32, đã chuẩn hoá L2

    @property
    def size(self):
        return float(min(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1]))

    @property
    def area(self):
        return float((self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1]))


def load_image(src, max_pixels=None):
    """src: đường dẫn hoặc bytes. Trả về numpy RGB đã áp EXIF orientation.

    max_pixels: kiểm tra kích thước khai báo trong header TRƯỚC khi giải mã, để ảnh "bom"
    (PNG nhỏ khai báo hàng tỉ pixel) không ngốn RAM. Lỗi: ImageTooLarge / ImageUnreadable.
    """
    import io
    fp = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src
    try:
        with Image.open(fp) as im:
            if max_pixels and im.size[0] * im.size[1] > max_pixels:
                raise ImageTooLarge(f"{im.size[0]}x{im.size[1]}")
            im = ImageOps.exif_transpose(im)
            if im.mode.startswith("I"):  # I;16 / I (xám 16-bit): convert("RGB") sẽ ra toàn trắng
                a = np.asarray(im).astype(np.float32)
                if a.max() > 255:
                    a = a / 256.0 if a.max() <= 65535 else (a - a.min()) / max(1.0, a.max() - a.min()) * 255
                a = np.clip(a, 0, 255).astype(np.uint8)
                return np.repeat(a[..., None], 3, axis=2)
            return np.asarray(im.convert("RGB"))
    except ImageTooLarge:
        raise
    except Image.DecompressionBombError as e:
        raise ImageTooLarge(str(e)) from e
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as e:
        raise ImageUnreadable(str(e)) from e


def _umeyama(src, dst):
    n = src.shape[0]
    sm, dm = src.mean(0), dst.mean(0)
    sd, dd = src - sm, dst - dm
    cov = dd.T @ sd / n
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[-1] = -1
    u, s, vt = np.linalg.svd(cov)
    r = u @ np.diag(d) @ vt
    var = sd.var(0).sum()
    scale = (s * d).sum() / var
    t = dm - scale * r @ sm
    m = np.zeros((2, 3), dtype=np.float32)
    m[:, :2] = scale * r
    m[:, 2] = t
    return m


def _nms(dets, thresh):
    x1, y1, x2, y2, sc = dets.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = sc.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1 + 1) * np.maximum(0, yy2 - yy1 + 1)
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= thresh]
    return keep


class FaceEngine:
    def __init__(self, model_dir, providers=None, det_thresh=0.5, nms_thresh=0.4, threads=0):
        det_path = os.path.join(model_dir, DET_FILE)
        rec_path = os.path.join(model_dir, REC_FILE)
        for p in (det_path, rec_path):
            if not os.path.exists(p):
                raise FileNotFoundError(f"Thiếu model {p} — chạy: python download_models.py")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        if threads:
            so.intra_op_num_threads = threads
        providers = providers or ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(det_path, so, providers=providers)
        self.rec = ort.InferenceSession(rec_path, so, providers=providers)
        self.det_in = self.det.get_inputs()[0].name
        self.rec_in = self.rec.get_inputs()[0].name
        self.det_thresh, self.nms_thresh = det_thresh, nms_thresh
        self._anchors = {}

    def _centers(self, h, w, stride):
        key = (h, w, stride)
        if key not in self._anchors:
            g = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            c = (g * stride).reshape(-1, 2)
            self._anchors[key] = np.repeat(c, 2, axis=0)  # 2 anchor mỗi ô
        return self._anchors[key]

    def detect(self, rgb, det_size=1280, coarse_pass=True):
        """Dò ở cạnh dài `det_size` (bắt mặt nhỏ trong ảnh nhóm). SCRFD lại bỏ sót mặt quá
        to so với khung (chân dung/selfie cận cảnh: mặt chiếm ~80% khung vẫn trượt ở 640,
        nhưng thấy ở 320–384), nên luôn thêm các lượt thô 640 và 320 rẻ tiền rồi gộp bằng NMS."""
        sizes = [det_size] + ([s for s in COARSE_SIZES if s < det_size] if coarse_pass else [])
        faces = [f for s in sizes for f in self._detect_one(rgb, s)]
        if len(sizes) > 1:
            if len(faces) > 1:
                dets = np.array([[*f.bbox, f.score] for f in faces])
                faces = [faces[i] for i in _nms(dets, self.nms_thresh)]
        return faces

    def _detect_one(self, rgb, det_size):
        """det_size = cạnh dài của ảnh sau khi thu nhỏ (giữ tỉ lệ, đệm tới bội số 32)."""
        h, w = rgb.shape[:2]
        scale = det_size / max(h, w)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        small = cv2.resize(rgb, (nw, nh), interpolation=interp)
        ph, pw = -(-nh // 32) * 32, -(-nw // 32) * 32
        canvas = np.zeros((ph, pw, 3), dtype=np.uint8)
        canvas[:nh, :nw] = small
        blob = ((canvas.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        outs = self.det.run(None, {self.det_in: blob})
        scores_l, boxes_l, kps_l = [], [], []
        for i, stride in enumerate((8, 16, 32)):
            sc = outs[i].reshape(-1)
            bb = outs[i + 3].reshape(-1, 4) * stride
            kp = outs[i + 6].reshape(-1, 10) * stride
            ctr = self._centers(ph // stride, pw // stride, stride)
            m = sc >= self.det_thresh
            if not m.any():
                continue
            c, bb, kp = ctr[m], bb[m], kp[m]
            boxes_l.append(np.stack([c[:, 0] - bb[:, 0], c[:, 1] - bb[:, 1],
                                     c[:, 0] + bb[:, 2], c[:, 1] + bb[:, 3]], 1))
            kps_l.append((kp.reshape(-1, 5, 2) + c[:, None, :]))
            scores_l.append(sc[m])
        if not scores_l:
            return []
        sc = np.concatenate(scores_l)
        bx = np.concatenate(boxes_l) / scale
        kp = np.concatenate(kps_l) / scale
        keep = _nms(np.hstack([bx, sc[:, None]]), self.nms_thresh)
        faces = []
        for i in keep:
            b = bx[i].copy()
            b[[0, 2]] = b[[0, 2]].clip(0, w)
            b[[1, 3]] = b[[1, 3]].clip(0, h)
            faces.append(Face(b.astype(np.float32), float(sc[i]), kp[i].astype(np.float32)))
        return faces

    @staticmethod
    def _align(rgb, kps, bbox):
        """Căn 5 điểm về 112x112. Mặt to (co lại >3 lần) mà warp song tuyến thẳng từ ảnh gốc sẽ
        bị răng cưa (cosine ~0.97 so với bản thu nhỏ theo diện tích) → cắt vùng quanh mặt, thu
        nhỏ INTER_AREA còn ~2x rồi mới warp."""
        m = _umeyama(kps, ARCFACE_DST)
        s = float(np.sqrt(abs(np.linalg.det(m[:, :2]))))  # tỉ lệ ảnh gốc → 112
        if s < 1 / 3:
            k = 2 * s  # sau thu nhỏ, còn co ~2x
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x0 = int(max(0, bbox[0] - w)); y0 = int(max(0, bbox[1] - h))
            x1 = int(min(rgb.shape[1], bbox[2] + w)); y1 = int(min(rgb.shape[0], bbox[3] + h))
            roi = rgb[y0:y1, x0:x1]
            roi = cv2.resize(roi, (max(1, round(roi.shape[1] * k)), max(1, round(roi.shape[0] * k))),
                             interpolation=cv2.INTER_AREA)
            kps = (kps - np.array([x0, y0], np.float32)) * k
            m = _umeyama(kps, ARCFACE_DST)
            rgb = roi
        return cv2.warpAffine(rgb, m, (112, 112), borderValue=0.0)

    def embed(self, rgb, faces):
        if not faces:
            return
        crops = []
        for f in faces:
            crops.append(self._align(rgb, f.kps, f.bbox))
        blob = ((np.stack(crops).astype(np.float32) - 127.5) / 127.5).transpose(0, 3, 1, 2)
        e = self.rec.run(None, {self.rec_in: blob})[0] if self._rec_batch_ok() else \
            np.concatenate([self.rec.run(None, {self.rec_in: b[None]})[0] for b in blob])
        e = e / np.linalg.norm(e, axis=1, keepdims=True)
        for f, v in zip(faces, e):
            f.emb = v.astype(np.float32)

    def _rec_batch_ok(self):
        d = self.rec.get_inputs()[0].shape[0]
        return not isinstance(d, int)

    def analyze(self, rgb, det_size=1280, min_face=0):
        faces = [f for f in self.detect(rgb, det_size) if f.size >= min_face]
        self.embed(rgb, faces)
        return faces
