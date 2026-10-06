"""Tải gói buffalo_l của InsightFace và chỉ giữ 2 file cần dùng (phát hiện + nhận diện).

Trọng số InsightFace CHỈ dùng phi thương mại (xem README).
"""
import argparse
import os
import urllib.request
import zipfile

URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
NEED = ("det_10g.onnx", "w600k_r50.onnx")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", "models"))
    a = ap.parse_args()
    os.makedirs(a.model_dir, exist_ok=True)
    if all(os.path.exists(os.path.join(a.model_dir, n)) for n in NEED):
        print("Model đã có ở", a.model_dir)
        return
    zpath = os.path.join(a.model_dir, "buffalo_l.zip.part")
    print("Đang tải", URL)
    urllib.request.urlretrieve(URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        for n in NEED:
            z.extract(n, a.model_dir)
    os.remove(zpath)
    print("Xong:", ", ".join(NEED), "->", a.model_dir)


if __name__ == "__main__":
    main()
