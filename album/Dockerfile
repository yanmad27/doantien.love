# album.doantien.love — build context = thư mục album/ (Dockerfile path: Dockerfile)
# Mục tiêu linux/amd64. Trên arm64 (Graviton/Ampere/Apple) các wheel dùng ở đây cũng có manylinux aarch64, chưa kiểm.
FROM python:3.11.17-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data PORT=8000 ALBUM_HOST=0.0.0.0 ALBUM_MODEL_DIR=/opt/models \
    ALBUM_LOW_MEM=1 ALBUM_THREADS=2 MALLOC_ARENA_MAX=2

WORKDIR /app

# 1) thư viện (lớp cache riêng): chỉ wheel dựng sẵn, phiên bản ghim trong requirements.txt + constraints-docker.txt
COPY requirements.txt constraints-docker.txt ./
RUN pip install --only-binary=:all: -r requirements.txt -c constraints-docker.txt

# 2) model buffalo_l, kiểm SHA-256 ngay lúc build (container không cần ra mạng khi chạy).
#    Trọng số InsightFace CHỈ dùng phi thương mại.
COPY download_models.py store.py ./
RUN python download_models.py --model-dir /opt/models

# 3) mã nguồn
COPY faces.py indexer.py index_photos.py server.py ./
COPY static ./static

# user không phải root; DATA_DIR thuộc user này (volume mới tạo sẽ kế thừa quyền khi mount vào /data)
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=8).read()"]

# một worker duy nhất (job Xử lý và chỉ mục nằm trong RAM của process này)
CMD ["python", "server.py"]
