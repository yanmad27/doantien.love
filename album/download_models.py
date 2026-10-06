"""Tải gói buffalo_l của InsightFace và chỉ giữ 2 file cần dùng (phát hiện + nhận diện).

Trọng số InsightFace CHỈ dùng phi thương mại (xem README).
Mỗi file được kiểm SHA-256; file thiếu/hỏng/dở dang sẽ được tải lại.
"""
import argparse
import hashlib
import os
import sys
import urllib.request
import zipfile

import store

URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
SHA256 = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_good(path, name):
    return os.path.exists(path) and sha256(path) == SHA256[name]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", store.DEFAULT_MODEL_DIR))
    a = ap.parse_args()
    os.makedirs(a.model_dir, exist_ok=True)
    path = lambda n: os.path.join(a.model_dir, n)
    bad = [n for n in SHA256 if not is_good(path(n), n)]
    if not bad:
        print("Model đã có và đúng SHA-256 ở", a.model_dir)
        return
    print("Cần tải lại:", ", ".join(bad))
    zpath = os.path.join(a.model_dir, "buffalo_l.zip.part")
    print("Đang tải", URL)
    urllib.request.urlretrieve(URL, zpath)
    try:
        with zipfile.ZipFile(zpath) as z:
            for n in bad:
                part = path(n) + ".part"
                with z.open(n) as src, open(part, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                if sha256(part) != SHA256[n]:
                    os.remove(part)
                    sys.exit(f"SHA-256 của {n} không khớp bản đã ghim — dừng, không dùng file này.")
                os.replace(part, path(n))  # chỉ đổi tên vào chỗ khi đã kiểm xong
    finally:
        os.remove(zpath)
    print("Xong:", ", ".join(bad), "->", a.model_dir)


if __name__ == "__main__":
    main()
