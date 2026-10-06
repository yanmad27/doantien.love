"""Kiểm tra các giới hạn chống lạm dụng bằng cách chạy server thật như container (DATA_DIR, PORT, 0.0.0.0).

  python dev/check_limits.py --photo ảnh.jpg (≤ 2 MB) --selfie s.jpg
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--photo", required=True)
ap.add_argument("--selfie", required=True)
ap.add_argument("--port", type=int, default=8792)
a = ap.parse_args()
URL = f"http://127.0.0.1:{a.port}"
fails = []


def req(path, data=None, method=None, headers=None):
    r = urllib.request.Request(URL + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def check(name, cond, got):
    print(("OK   " if cond else "FAIL ") + f"{name}: {got}")
    if not cond:
        fails.append(name)


def run(extra_env, fn):
    work = tempfile.mkdtemp(prefix="album-limits-")
    env = dict(os.environ, DATA_DIR=os.path.join(work, "data"), PORT=str(a.port), ALBUM_HOST="0.0.0.0", **extra_env)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(URL + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.5)
        fn(work)
    finally:
        srv.terminate()
        srv.wait(timeout=15)
        shutil.rmtree(work, ignore_errors=True)


photo = open(a.photo, "rb").read()


def main_checks(work):
    s, h, b = req("/api/upload?name=notes.txt", b"hello", headers={"Content-Type": "application/octet-stream"})
    check("upload .txt bị từ chối (đuôi)", s == 400, (s, json.loads(b)["detail"]))
    s, h, b = req("/api/upload?name=fake.jpg", b"this is not an image at all", method="POST")
    check("upload file giả đuôi .jpg bị từ chối (header Pillow)", s == 400, (s, json.loads(b)["detail"]))
    s, h, b = req("/api/upload?name=../../etc/passwd.jpg", photo, method="POST")
    d = json.loads(b)
    check("path traversal: chỉ giữ tên file", s == 200 and d["name"] == "passwd.jpg"
          and os.listdir(os.path.join(work, "data", "originals")) == ["passwd.jpg"], (s, d))
    s, h, b = req("/api/upload?name=passwd.jpg", photo, method="POST")
    check("trùng nội dung → duplicate, không ghi đè", s == 200 and json.loads(b)["status"] == "duplicate", (s, json.loads(b)))
    try:  # server từ chối ngay theo Content-Length rồi đóng kết nối: client có thể thấy 413 hoặc broken pipe
        s, h, b = req("/api/upload?name=big.jpg", b"\xff\xd8" + os.urandom(3 * 1024 * 1024), method="POST")
        got = (s, json.loads(b)["detail"])
    except urllib.error.URLError as e:
        s, got = 413, f"kết nối bị đóng sớm ({e.reason})"
    check("upload quá cỡ (cap 2 MB) bị từ chối", s == 413, got)
    check("không để lại file dở/ảnh quá cỡ", sorted(os.listdir(os.path.join(work, "data", "originals"))) == ["passwd.jpg"]
          and not os.listdir(os.path.join(work, "data", "tmp")), os.listdir(os.path.join(work, "data", "tmp")))
    # selfie
    s, h, b = req("/api/search", b"x" * (26 * 1024 * 1024), method="POST")
    check("selfie > 25 MB bị từ chối", s == 413, s)
    s, h, b = req("/api/search", b"not an image", method="POST")
    check("selfie không phải ảnh → 400", s == 400, s)
    # process 2 lần
    for i in range(6):
        req(f"/api/upload?name=p{i}.jpg", photo + bytes([i]), method="POST")
    s1, _, b1 = req("/api/process", b"", method="POST")
    s2, _, b2 = req("/api/process", b"", method="POST")
    check("Process lần 1 chạy, lần 2 khi đang chạy bị từ chối 409", s1 == 200 and s2 == 409, (s1, s2, json.loads(b2).get("message")))
    # chờ xong để server tắt gọn
    for _ in range(600):
        if json.loads(req("/api/process")[2])["state"] != "running":
            break
        time.sleep(0.5)
    # header + robots
    for p in ("/", "/manage", "/healthz", "/api/stats", "/robots.txt", "/nonexistent", "/thumbs/zzzz.jpg"):
        s, h, b = req(p)
        check(f"X-Robots-Tag trên {p} (HTTP {s})", h.get("x-robots-tag") == "noindex, nofollow", h.get("x-robots-tag"))
    s, h, b = req("/robots.txt")
    check("robots.txt Disallow: /", b"Disallow: /" in b, b.decode().strip().replace("\n", " | "))
    s, h, b = req("/healthz")
    d = json.loads(b)
    check("/healthz = ok + dung lượng đĩa", s == 200 and d["ok"] is True and d["disk_free"] > 0 and d["disk_total"] >= d["disk_free"], d)
    s, h, b = req("/originals/..%2F..%2Fetc%2Fpasswd")
    check("không lộ đường dẫn: id sai → 404", s == 404, s)
    s, h, b = req("/api/process")
    check("trạng thái không chứa đường dẫn tuyệt đối", work.encode() not in b and b"/tmp" not in b and b"/Users" not in b, "ok")


def disk_checks(work):
    s, h, b = req("/api/upload?name=a.jpg", photo, method="POST")
    check("ổ đĩa dưới ngưỡng → 507 + thông báo tiếng Việt", s == 507 and "Ổ đĩa" in json.loads(b)["detail"], (s, json.loads(b)["detail"]))


run({"ALBUM_MAX_UPLOAD_MB": "2", "ALBUM_MIN_FREE_MB": "1"}, main_checks)
run({"ALBUM_MIN_FREE_MB": "99999999"}, disk_checks)
print("\nTẤT CẢ ĐẠT" if not fails else f"\nTHẤT BẠI: {fails}")
sys.exit(1 if fails else 0)
