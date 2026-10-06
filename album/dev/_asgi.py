"""Gọi thẳng ASGI app (không socket, không httpx): dùng cho test backend để giả Host tuỳ ý."""
import asyncio
from urllib.parse import urlsplit


def call(app, method, url, host="localhost", headers=None, body=b""):
    """Trả (status, {header chữ thường: giá trị}, body bytes). Header lặp lại chỉ giữ cái cuối."""
    u = urlsplit(url)
    hs = [(b"host", host.encode())] if host is not None else []
    hs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body:
        hs.append((b"content-length", str(len(body)).encode()))
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1", method=method, scheme="http", path=u.path,
                 raw_path=u.path.encode(), query_string=u.query.encode(), headers=hs, client=("1.2.3.4", 1), server=("x", 80))
    out = {"status": None, "headers": {}, "body": b"", "read": 0}
    sent = [False]

    async def receive():
        if not sent[0]:
            sent[0] = True
            out["read"] += 1  # đếm lần đọc body
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in m["headers"]}
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    async def run():
        await asyncio.wait_for(app(scope, receive, send), 60)

    asyncio.run(run())
    return out["status"], out["headers"], out["body"], out["read"]
