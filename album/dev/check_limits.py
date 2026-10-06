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
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--photo", required=True)
ap.add_argument("--selfie", required=True)
ap.add_argument("--poison", help="thư mục chứa bomb150_rgb.png, a60_rgb.png, b60_rgba.png, c60_gray16.png, selfie50.png, selfie40.png")
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


def start(work, extra_env):
    env = dict(os.environ, DATA_DIR=os.path.join(work, "data"), PORT=str(a.port), ALBUM_HOST="0.0.0.0", **extra_env)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            urllib.request.urlopen(URL + "/healthz", timeout=1).read()
            break
        except Exception:
            time.sleep(0.5)
    return srv


def run(extra_env, fn):
    work = tempfile.mkdtemp(prefix="album-limits-")
    srv = start(work, extra_env)
    try:
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


def wait_done():
    for _ in range(1200):
        st = json.loads(req("/api/process")[2])
        if st["state"] != "running":
            return st
        time.sleep(0.25)


def slow_upload(name, body, hold=2.0):
    """Gửi header + 1 KB đầu, giữ kết nối `hold` giây rồi gửi nốt: mô phỏng upload chậm để các request chồng lên nhau."""
    import socket
    sk = socket.create_connection(("127.0.0.1", a.port), timeout=60)
    sk.sendall((f"POST /api/upload?name={name} HTTP/1.1\r\nHost: x\r\nContent-Type: application/octet-stream\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body[:1024])
    time.sleep(hold)
    try:
        sk.sendall(body[1024:])
    except OSError:
        pass
    data = b""
    try:
        while True:
            c = sk.recv(65536)
            if not c:
                break
            data += c
    except OSError:
        pass
    sk.close()
    return int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else 0


def poison_checks(work):
    P = a.poison
    rd = lambda n: open(os.path.join(P, n), "rb").read()
    s, h, b = req("/api/upload?name=bomb150.png", rd("bomb150_rgb.png"), method="POST")
    check("PNG khai báo 150 MP bị từ chối khi upload", s == 413, (s, json.loads(b)["detail"]))
    s, h, b = req("/api/search", rd("selfie50.png"), method="POST")
    check("selfie khai báo 50 MP bị từ chối", s == 413, s)
    # 3 truy vấn đồng thời: tối đa 2 chạy, cái thứ 3 nhận 503
    import threading
    body, codes, bar = rd("selfie40.png"), [], threading.Barrier(3)

    def one():
        bar.wait()
        codes.append(req("/api/search", body, method="POST")[0])
    ts = [threading.Thread(target=one) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("3 truy vấn selfie đồng thời → có 503 (giới hạn 2)", 503 in codes and len([c for c in codes if c != 503]) >= 2, sorted(codes))
    # ảnh hỏng (header đúng, thân cụt) → lỗi 1 lần rồi không thử lại
    good = rd("a60_rgb.png")
    s, h, b = req("/api/upload?name=truncated.png", good[:len(good) // 2], method="POST")
    check("upload PNG cụt (header đúng) được nhận", s == 200, s)
    req("/api/upload?name=ok.jpg", photo, method="POST")
    s, _, _ = req("/api/process", b"", method="POST")
    st1 = wait_done()
    check("lượt 1: ảnh cụt bị báo lỗi, ảnh tốt được xử lý", st1["failed"] == 1 and st1["state"] == "done" and st1["total"] == 2, (st1["total"], st1["failed"], st1["errors"]))
    req("/api/process", b"", method="POST")
    st2 = wait_done()
    check("lượt 2: ảnh lỗi không bị thử lại (total=0)", st2["total"] == 0, st2["total"])
    # ảnh nặng 60 MP: giết server ngay khi đang giải mã → lần khởi động sau chuyển file sang rejected/
    for n in ("a60_rgb.png", "b60_rgba.png", "c60_gray16.png"):
        s, h, b = req(f"/api/upload?name=0-{n}", rd(n), method="POST")
        assert s == 200, (n, s, b)
    return "kill"


def crash_check():
    work = tempfile.mkdtemp(prefix="album-crash-")
    marker = os.path.join(work, "data", "decoding.json")
    srv = start(work, {})
    try:
        for n in ("a60_rgb.png", "b60_rgba.png", "c60_gray16.png"):
            req(f"/api/upload?name=0-{n}", open(os.path.join(a.poison, n), "rb").read(), method="POST")
        req("/api/process", b"", method="POST")
        for _ in range(2000):
            if os.path.exists(marker):
                break
            time.sleep(0.01)
        marked = json.load(open(marker)) if os.path.exists(marker) else None
        srv.kill()  # như OOM-kill: process chết khi file còn "đang giải mã"
        srv.wait()
        check("marker 'đang giải mã' được ghi bền trước khi giải mã", bool(marked), marked)
        srv = start(work, {})
        st = json.loads(req("/api/process")[2])
        rej = sorted(os.listdir(os.path.join(work, "data", "rejected")))
        check("sau khi khởi động lại, file đang giải mã nằm trong rejected/ và báo trong trạng thái",
              bool(rej) and sorted(st["rejected"]) == sorted(marked), (rej, st["rejected"]))
        check("marker đã dọn", not os.path.exists(marker), os.path.exists(marker))
        req("/api/process", b"", method="POST")
        st = wait_done()
        left = os.listdir(os.path.join(work, "data", "originals"))
        check("Process kế tiếp chạy xong, không lặp lại file đã loại", st["state"] == "done" and not set(marked) & set(left),
              (st["state"], st["total"], st["failed"], left))
    finally:
        srv.kill()
        srv.wait()
        shutil.rmtree(work, ignore_errors=True)


def burst_checks(work):
    """Quota tổng 150 MB, cap mỗi ảnh 60 MB → đặt chỗ được 2 upload cùng lúc; slot upload toàn cục = 4."""
    import threading
    codes = []

    def one(i):
        codes.append(slow_upload(f"b{i}.jpg", photo + bytes([i]) * 8, hold=2.0))
    ts = [threading.Thread(target=one, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("burst 12 upload chậm song song: có 200, 503 (hết slot) và 507 (quota)", {200, 503, 507} <= set(codes), sorted(codes))
    s, h, b = req("/api/upload?name=after.jpg", photo + b"zz", method="POST")
    d = json.loads(b)
    check("sau burst đặt chỗ đã được trả lại, upload bình thường vẫn nhận", s == 200 and d["status"] == "ok", (s, d))
    s, h, b = req("/api/stats")
    st = json.loads(b)
    check("stats có disk_free/disk_total/quota", st["disk_free"] > 0 and st["disk_total"] >= st["disk_free"] and st["stored_bytes"] > 0, st)


def name_checks(work):
    long_name = "Ảnh cưới đẹp nhất của chúng tôi " * 8 + ".jpg"  # > 255 byte khi mã hoá UTF-8
    s, h, b = req("/api/upload?name=" + urllib.parse.quote(long_name), photo, method="POST")
    d = json.loads(b)
    check("tên tiếng Việt dài được rút theo byte, không 500", s == 200 and len(d["name"].encode()) <= 200, (s, len(d["name"].encode())))


run({"ALBUM_MAX_UPLOAD_MB": "2", "ALBUM_MIN_FREE_MB": "1"}, main_checks)
run({"ALBUM_MAX_UPLOAD_MB": "2", "ALBUM_MIN_FREE_MB": "1"}, name_checks)
run({"ALBUM_MIN_FREE_MB": "99999999"}, disk_checks)
run({"ALBUM_MAX_UPLOAD_MB": "60", "ALBUM_MAX_TOTAL_GB": "0.15", "ALBUM_MIN_FREE_MB": "1"}, burst_checks)
if a.poison:
    run({"ALBUM_MIN_FREE_MB": "1"}, poison_checks)
    crash_check()
print("\nTẤT CẢ ĐẠT" if not fails else f"\nTHẤT BẠI: {fails}")
sys.exit(1 if fails else 0)
