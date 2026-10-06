"""Đo RAM + dung lượng/ảnh qua đúng đường chạy thật: khởi động server như container (DATA_DIR, PORT, 0.0.0.0),
tải ảnh lên qua HTTP, bấm Xử lý, lấy mẫu RSS, truy vấn selfie.

  python measure.py --photos <thư mục/file ảnh...> --queries <selfie> [--env ALBUM_LOW_MEM=1] [--threads N]

Dùng data dir tạm (xoá khi xong). Trong --photos có thể đưa cả ảnh 20–45 MP để đo đỉnh RAM.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) / 1024 if out else 0.0


def dir_bytes(d):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)


def call(url, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--photos", nargs="+", required=True)
    ap.add_argument("--queries", nargs="+", required=True)
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE thêm cho server")
    ap.add_argument("--port", type=int, default=8791)
    a = ap.parse_args()
    files = []
    for p in a.photos:
        files += [p] if os.path.isfile(p) else sorted(os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs if f.lower().endswith(EXTS))
    work = tempfile.mkdtemp(prefix="album-measure-")
    data = os.path.join(work, "data")
    env = dict(os.environ, DATA_DIR=data, PORT=str(a.port), ALBUM_HOST="0.0.0.0", ALBUM_MIN_FREE_MB="1", TMPDIR=work)
    env.update(kv.split("=", 1) for kv in a.env)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    peak, stop = [0.0], threading.Event()
    try:
        url = f"http://127.0.0.1:{a.port}"
        for _ in range(240):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).read()
                break
            except Exception:
                if srv.poll() is not None:
                    sys.exit("Server thoát sớm")
                time.sleep(0.5)
        idle = rss_mb(srv.pid)
        up_bytes = 0
        for f in files:
            call(url + "/api/upload?name=" + urllib.parse.quote(os.path.basename(f)), open(f, "rb").read())
            up_bytes += os.path.getsize(f)

        def sample():
            while not stop.is_set():
                peak[0] = max(peak[0], rss_mb(srv.pid))
                time.sleep(0.1)
        threading.Thread(target=sample, daemon=True).start()
        t0 = time.time()
        call(url + "/api/process", b"")
        while call(url + "/api/process")["state"] == "running":
            time.sleep(0.5)
        wall = time.time() - t0
        stop.set()
        s = call(url + "/api/process")
        after_proc = rss_mb(srv.pid)
        ms = []
        for q in a.queries:
            req = urllib.request.Request(url + "/api/search", data=open(q, "rb").read(), method="POST")
            t = time.perf_counter()
            urllib.request.urlopen(req, timeout=60).read()
            ms.append((time.perf_counter() - t) * 1e3)
        after_q = rss_mb(srv.pid)
        n = s["done"] - s["failed"]
        orig, th, pv = (dir_bytes(os.path.join(data, d)) for d in ("originals", "thumbs", "previews"))
        db = os.path.getsize(os.path.join(data, "index.db"))
        print(f"Ảnh: {len(files)} ({n} xử lý được, {s['failed']} lỗi), {s['faces']} khuôn mặt, env thêm: {a.env}")
        print(f"Xử lý: {wall:.1f}s tổng = {wall / max(n, 1):.2f} s/ảnh")
        print(f"RSS: nhàn rỗi {idle:.0f} MB | ĐỈNH khi xử lý {peak[0]:.0f} MB | sau xử lý {after_proc:.0f} MB | sau {len(ms)} truy vấn {after_q:.0f} MB")
        print(f"Truy vấn selfie: {sorted(ms)[len(ms) // 2]:.0f} ms (trung vị, đầu-cuối HTTP)")
        k = 1024
        print(f"Đĩa/ảnh (trung bình {n} ảnh): gốc {orig / n / k:.0f} KB + bản xem {pv / n / k:.0f} KB + thumbnail {th / n / k:.0f} KB "
              f"+ index.db {db / n / k:.1f} KB = {(orig + pv + th + db) / n / k:.0f} KB. Ngoài ảnh gốc: {(pv + th + db) / n / k:.0f} KB/ảnh")
        print(f"  (tổng gốc {orig / 2**20:.1f} MB, bản xem {pv / 2**20:.1f} MB, thumb {th / 2**20:.2f} MB, db {db / k:.0f} KB)")
        print(f"Selfie ghi ra TMPDIR: {'KHÔNG' if not [x for x in os.listdir(work) if x != 'data'] else 'CÓ'}")
    finally:
        srv.terminate()
        srv.wait(timeout=15)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
