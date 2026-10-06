# album — tìm ảnh cưới theo khuôn mặt (prototype cục bộ)

App Python độc lập (thư mục riêng, dependency riêng) — sau này có thể thành
`album.doantien.love` sau Traefik. Hiện **chỉ chạy cục bộ**, chưa có Dockerfile/deploy.
Toàn bộ xử lý nằm trên máy: ảnh, selfie, dữ liệu khuôn mặt không gửi đi đâu, giao diện không
nạp CDN nào.

Khách tải một selfie → app dò mặt, nhúng ArcFace, so cosine với mọi khuôn mặt trong chỉ mục →
lưới ảnh có mặt khách, điểm cao trước, kèm khung đỏ quanh khuôn mặt khớp. Ngưỡng chỉnh bằng
thanh trượt, lọc lại ngay không cần tải lại selfie.

> **Giấy phép model:** trọng số InsightFace `buffalo_l` chỉ cho dùng **phi thương mại**
> (nghiên cứu/cá nhân). Đừng dùng cho dịch vụ thương mại khi chưa xin phép tác giả.

## Chạy từ checkout sạch

Cần Python 3.9+ (macOS: `/usr/bin/python3` là đủ). Mọi lệnh chạy trong `album/`.

```sh
cd album
python3 -m venv .venv                          # venv cục bộ, không cài global
.venv/bin/pip install -r requirements.txt
.venv/bin/python download_models.py            # ~290 MB tải về, giữ det_10g + w600k_r50 trong models/
.venv/bin/python index_photos.py --photos /duong/dan/thu-muc-anh
.venv/bin/python server.py                     # http://127.0.0.1:8000
```

Đo đạc (lập chỉ mục trong thư mục tạm, không đụng `data/`):

```sh
.venv/bin/python measure.py --photos /duong/dan/thu-muc-anh --queries /duong/dan/vai-selfie
```

In ra 4 chỉ số: thời gian lập chỉ mục/ảnh, độ trễ truy vấn (trong máy chủ + đầu-cuối HTTP),
RSS của app (nhàn rỗi và sau các truy vấn), dung lượng chỉ mục/ảnh (embedding+metadata vs thumbnail).

## Cấu hình (biến môi trường hoặc tham số dòng lệnh)

| Env | Tham số | Mặc định |
|---|---|---|
| `ALBUM_DATA_DIR` | `--data-dir` | `album/data` (chứa `index.db`, `thumbs/`) |
| `ALBUM_MODEL_DIR` | `--model-dir` | `album/models` |
| `ALBUM_HOST` / `ALBUM_PORT` | `--host` / `--port` | `127.0.0.1` / `8000` |
| `ALBUM_DET_SIZE` | `index_photos.py --det-size` | `1600` |
| `ALBUM_PROVIDERS` | `--providers` | `CPUExecutionProvider` |

Mặc định của `data`/`models` tính theo thư mục chứa script, không phụ thuộc thư mục đang đứng.

## Lập chỉ mục

- Quét đệ quy `--photos` (jpg/png/webp/bmp/tif), áp EXIF orientation, dò **mọi** khuôn mặt
  (ảnh nhóm cũng vậy), lưu embedding 512-d (float16) + bbox + điểm dò vào `data/index.db`
  và thumbnail 640 px vào `data/thumbs/`.
- **Chạy lại được, theo kiểu tăng dần:** ảnh không đổi (cùng đường dẫn + kích thước + mtime +
  det-size) bị bỏ qua. `--prune` xoá ảnh đã biến mất khỏi thư mục; `--rebuild` làm lại từ đầu.
  Một `--data-dir` ứng với đúng một thư mục ảnh.
- File hỏng/không đọc được, hay ảnh làm lỗi bước dò/nhúng: in `CẢNH BÁO` và bỏ qua, các ảnh còn lại vẫn chạy tiếp.
- Indexer bỏ giới hạn số điểm ảnh của Pillow (ảnh của chủ nhà); server thì không.
- `--min-face` (mặc định 24 px, trên ảnh gốc): bỏ mặt quá nhỏ vì embedding không đáng tin.
- App tự nạp lại chỉ mục khi `index.db` đổi — không cần khởi động lại server.

### Độ phân giải dò (`--det-size`)

Ảnh cưới 20–45 MP; thu về 640 như `FaceAnalysis` mặc định sẽ làm mất mặt nhỏ ở ảnh nhóm.
`--det-size` là cạnh dài sau khi thu nhỏ (giữ tỉ lệ). Mặc định **1600**. Ngoài ra luôn có thêm
một lượt 640 rẻ tiền rồi gộp bằng NMS, vì SCRFD ngược lại bỏ sót mặt quá to (chân dung cận
cảnh — thử thấy mặt ~1100 px bị mất ở 1600). Embedding luôn cắt từ ảnh gốc độ phân giải đầy đủ.

Ảnh nội các Obama (3840×2560, 25 mặt thật; mặt nhỏ nhất ~32 px ở ảnh gốc), chỉ riêng bước dò, CPU M3:

| det-size | mặt tìm được | thời gian dò |
|---|---|---|
| 640 | 24 | 0.07 s |
| 1280 | 25 | 0.22 s |
| **1600** | 25 | 0.36 s |
| 2048 | 25 | 0.57 s |
| 3200 | 25 | 1.39 s |

Với ảnh thật 6000 px+ mặt sẽ nhỏ hơn sau khi thu; nếu đánh giá thấy sót mặt nhỏ, thử
`--det-size 2048` hay `2560` (chậm tỉ lệ thuận với diện tích).

## Chi tiết kỹ thuật

- Không dùng gói `insightface` (cần build C-extension): nạp thẳng `det_10g.onnx` (SCRFD) và
  `w600k_r50.onnx` (ArcFace) bằng onnxruntime, tự giải mã SCRFD và căn chỉnh 5 điểm (xem
  `faces.py`). Cùng model, cùng tiền xử lý với `FaceAnalysis`.
- CPU only. Đã thử CoreML EP trên onnxruntime 1.19.2: **lỗi** ở model dò (shape động), nên
  không dùng; `--providers` vẫn cho phép thử bản onnxruntime mới hơn.
- Selfie: client gửi body thô tới `/api/search` (không multipart ⇒ không có file tạm do
  framework spool), server giải mã và xử lý trong RAM, không ghi gì ra đĩa.
- Selfie bị giới hạn 25 MB và 100 MP (kiểm kích thước khai báo trong header trước khi giải mã; vượt → HTTP 413). Không đọc được → 400, lỗi khác → 500. Trang `/docs`, `/redoc` đã tắt (mặc định nạp JS từ CDN).
- Kết quả tối đa 300 ảnh; nếu bị cắt, API trả `truncated`/`total_matches` và giao diện báo.
- `download_models.py` ghim SHA-256 của hai file `.onnx`: file thiếu/hỏng/dở dang bị tải lại, ghi qua file `.part` rồi mới đổi tên.
- Selfie nhiều mặt: dùng mặt **to nhất** (giao diện báo rõ và vẽ khung đỏ).
- Điểm = cosine giữa hai embedding chuẩn hoá. Tham khảo: cùng người thường ≳ 0.35–0.4,
  khác người thường < 0.2; vùng 0.2–0.35 là vùng mơ hồ — cần đánh giá trên ảnh thật để chọn
  ngưỡng mặc định (thanh trượt bắt đầu ở 0.35, server chỉ trả về điểm ≥ 0.15, tối đa 300 ảnh).

## Chụp màn hình giao diện (chỉ dev)

```sh
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/playwright install chromium
.venv/bin/python dev/screenshot.py --selfie selfie.jpg --out shot.png
```

## Không commit

`.venv/`, `data/`, `models/`, ảnh, selfie, chỉ mục — đã nằm trong `.gitignore`.
