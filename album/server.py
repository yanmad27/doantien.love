"""Web app cục bộ: khách tải selfie lên → tìm ảnh có mặt khách trong chỉ mục.

Selfie chỉ nằm trong RAM: gửi dạng body thô (không multipart nên không có file tạm),
xử lý xong là bỏ.
"""
import argparse
import logging
import os
import threading
import time

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import store
from faces import FaceEngine, load_image

log = logging.getLogger("album")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 25 * 1024 * 1024
SCORE_FLOOR = 0.15  # dưới mức này không bao giờ là cùng người, khỏi gửi về
MAX_RESULTS = 300


def create_app(data_dir, model_dir, providers, query_det_size=640):
    app = FastAPI(title="Tìm ảnh cưới")
    engine = FaceEngine(model_dir, providers=providers)
    cache = {"mtime": None, "E": None, "rows": None, "photos": None}
    lock = threading.Lock()

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
        rgb = load_image(body)
        t1 = time.perf_counter()
        faces = engine.detect(rgb, query_det_size)
        t2 = time.perf_counter()
        if not faces:
            return None
        face = max(faces, key=lambda f: f.area)  # mặt to nhất = người chụp selfie
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
        for pid, i in sorted(best.items(), key=lambda kv: -sims[kv[1]])[:MAX_RESULTS]:
            ph = photos[pid]
            res.append(dict(id=pid, name=ph["path"], score=round(float(sims[i]), 4),
                            thumb=f"thumbs/{pid}.jpg", width=ph["width"], height=ph["height"],
                            n_faces=ph["n_faces"], bbox=[round(v, 1) for v in rows[i]["bbox"]]))
        t4 = time.perf_counter()
        return dict(
            query=dict(n_faces=len(faces), bbox=[round(float(v), 1) for v in face.bbox],
                       width=rgb.shape[1], height=rgb.shape[0], det_score=round(face.score, 3)),
            results=res, indexed_photos=len(photos), indexed_faces=len(rows),
            timing_ms=dict(decode=(t1 - t0) * 1e3, detect=(t2 - t1) * 1e3, embed=(t3 - t2) * 1e3,
                           compare=(t4 - t3) * 1e3, total=(t4 - t0) * 1e3))

    @app.post("/api/search")
    async def api_search(request: Request):
        chunks, n = [], 0
        async for c in request.stream():
            n += len(c)
            if n > MAX_BODY:
                raise HTTPException(413, "Ảnh quá lớn (tối đa 25 MB)")
            chunks.append(c)
        body = b"".join(chunks)
        if not body:
            raise HTTPException(400, "Không có dữ liệu ảnh")
        try:
            out = await run_in_threadpool(search, body)
        except HTTPException:
            raise
        except Exception:
            log.exception("search lỗi")
            raise HTTPException(400, "Không đọc được ảnh này")
        if out is None:
            return JSONResponse({"error": "no_face"}, status_code=422)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @app.get("/api/stats")
    def stats():
        _, rows, photos = index()
        return dict(photos=len(photos), faces=len(rows))

    os.makedirs(store.thumb_dir(data_dir), exist_ok=True)
    app.mount("/thumbs", StaticFiles(directory=store.thumb_dir(data_dir)), name="thumbs")

    @app.get("/")
    def home():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("ALBUM_DATA_DIR", "data"))
    ap.add_argument("--model-dir", default=os.environ.get("ALBUM_MODEL_DIR", "models"))
    ap.add_argument("--host", default=os.environ.get("ALBUM_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ALBUM_PORT", 8000)))
    ap.add_argument("--providers", default=os.environ.get("ALBUM_PROVIDERS", "CPUExecutionProvider"))
    a = ap.parse_args()
    app = create_app(a.data_dir, a.model_dir, a.providers.split(","))
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
