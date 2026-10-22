"""Chỉ dùng cho e2e: chạy backend THẬT (server.create_app) trên DATA_DIR tạm, FaceEngine giả (không cần model).  python -I dev/_serve_real.py PORT DATA_DIR TOKEN"""
import os
import sys
from unittest import mock

import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402
from test_hidden import TagEngine  # noqa: E402

port, data, token = int(sys.argv[1]), sys.argv[2], sys.argv[3]
with mock.patch.object(server, "FaceEngine", TagEngine):
    app = server.create_app(data, "unused", ["CPUExecutionProvider"], admin_token=token, app_hosts="127.0.0.1,localhost", min_free=0, max_total=0)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
