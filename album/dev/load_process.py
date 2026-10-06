"""Đo độ trễ các endpoint NHẸ trong lúc "Xử lý" chạy (chạy một lệnh, không cần thao tác tay).

  .venv/bin/python dev/load_process.py --photos DIR --selfie ẢNH [--threads 8] [--process-threads N] [--port 8811] [--album DIR_code]

Khởi động server (có thể là bản code khác qua --album, vd. bản cũ giải nén từ git archive) trên data dir tạm chứa bản sao ảnh,
bấm Xử lý rồi mỗi giây gọi /healthz, GET /api/process, /api/stats, /api/gallery và /api/search (mỗi lời gọi một luồng
riêng, timeout 30 s) cho tới khi job xong. In p50/p95/max (ms) + số lần quá 5 s/timeout."""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def req(base, path, data=None, method=None, timeout=30):
    t = time.perf_counter()
    try:
        r = urllib.request.Request(base + path, data=data, method=method)
        with urllib.request.urlopen(r, timeout=timeout) as x:
            body = x.read()
        return time.perf_counter() - t, body
    except Exception as e:  # timeout / lỗi: ghi nhận là timeout
        return None if "timed out" in str(e) else time.perf_counter() - t, None


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", help="thư mục ảnh (sao chép vào data dir tạm)")
    ap.add_argument("--data", help="dùng thẳng data dir có sẵn (đã có originals/ + index.db), không sao chép")
    ap.add_argument("--selfie", required=True)
    ap.add_argument("--album", default=HERE, help="thư mục code album cần đo (có server.py)")
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    ap.add_argument("--process-threads", type=int, default=None)
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--idle", type=int, default=5, help="giây đo lúc rảnh trước khi bấm Xử lý")
    ap.add_argument("--max-seconds", type=int, default=480)
    ap.add_argument("--nice", default=None)
    a = ap.parse_args()
    if a.data:
        data = a.data
    else:
        data = tempfile.mkdtemp(prefix="load-")
        shutil.copytree(a.photos, os.path.join(data, "originals"))
    env = dict(os.environ, DATA_DIR=data, PORT=str(a.port), ALBUM_THREADS=str(a.threads), ALBUM_MIN_FREE_MB="1",
               ALBUM_MODEL_DIR=os.path.join(HERE, "models"))
    if a.process_threads is not None:
        env["ALBUM_PROCESS_THREADS"] = str(a.process_threads)
    log = open(os.path.join(tempfile.gettempdir(), f"load-server-{a.port}.log"), "w")
    srv = subprocess.Popen([sys.executable, os.path.join(a.album, "server.py")], env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{a.port}"
    selfie = open(a.selfie, "rb").read()
    try:
        subprocess.run(["curl", "-s", "--retry", "60", "--retry-connrefused", "--retry-delay", "1", "-o", "/dev/null", base + "/healthz"], check=True)
        samples = {"idle": {}, "busy": {}}
        lock = threading.Lock()
        endpoints = {"healthz": ("/healthz", None), "api/process": ("/api/process", None), "api/stats": ("/api/stats", None),
                     "api/gallery": ("/api/gallery", None), "api/search": ("/api/search", selfie)}
        state = {"phase": "idle"}

        def probe(name, path, data_, phase):
            dt, body = req(base, path, data_, "POST" if data_ else None)
            with lock:
                samples[phase].setdefault(name, []).append(dt)
            return body

        def tick(phase):
            ths = [threading.Thread(target=probe, args=(n, p, d, phase)) for n, (p, d) in endpoints.items()]
            [t.start() for t in ths]
            return ths

        t0 = time.time()
        pend = []
        while time.time() - t0 < a.idle:
            pend += tick("idle")
            time.sleep(1)
        req(base, "/api/process", b"", "POST")
        t1 = time.time()
        done_state = None
        while time.time() - t1 < a.max_seconds:
            pend += tick("busy")
            time.sleep(1)
            _, body = req(base, "/api/process")
            if body:
                st = json.loads(body)
                if st["state"] in ("done", "error"):
                    done_state = st
                    break
        for t in pend:
            t.join(40)
        print(f"code={a.album} threads={a.threads} process_threads={a.process_threads} process_wall={time.time() - t1:.0f}s "
              f"state={done_state and done_state['state']} photos={done_state and done_state.get('done')}")
        print(f"{'phase':5} {'endpoint':12} {'n':>4} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} {'>5s/timeout':>11}")
        for ph in ("idle", "busy"):
            for n in endpoints:
                xs = samples[ph].get(n, [])
                ok = [x for x in xs if x is not None]
                if not xs:
                    continue
                slow = sum(1 for x in xs if x is None or x > 5)
                print(f"{ph:5} {n:12} {len(xs):4} {pct(ok, 50) * 1e3 if ok else float('nan'):8.0f} {pct(ok, 95) * 1e3 if ok else float('nan'):8.0f} "
                      f"{max(ok) * 1e3 if ok else float('nan'):8.0f} {slow:11}")
    finally:
        srv.terminate()
        srv.wait(20)
        if not a.data:
            shutil.rmtree(data, True)


if __name__ == "__main__":
    main()
