# album — tìm ảnh cưới theo khuôn mặt (album.doantien.love)

App Python độc lập (thư mục riêng, dependency riêng): chủ tải nhiều ảnh cưới lên qua trình duyệt,
bấm **Xử lý** để lập chỉ mục khuôn mặt (một job nền, tăng dần), khách tải một selfie và nhận lưới
ảnh có mặt mình, điểm cao trước, kèm khung đỏ quanh khuôn mặt khớp; bấm ảnh để xem bản lớn.
**Không có mật khẩu** (quyết định của chủ dự án) — xem "Chặn lạm dụng". Toàn bộ xử lý nằm trên máy
chủ của mình: ảnh, selfie, dữ liệu khuôn mặt không gửi đi đâu, giao diện không nạp CDN nào.
Triển khai Dokploy: xem [`DEPLOY.md`](DEPLOY.md).

> **Giấy phép model:** trọng số InsightFace `buffalo_l` chỉ cho dùng **phi thương mại**
> (nghiên cứu/cá nhân). Đừng dùng cho dịch vụ thương mại khi chưa xin phép tác giả.

## Chạy từ checkout sạch (giống container)

Cần Python 3.9–3.12. Mọi lệnh chạy trong `album/`.

```sh
cd album
python3 -m venv .venv                          # venv cục bộ, không cài global
.venv/bin/pip install -r requirements.txt
.venv/bin/python download_models.py            # ~290 MB tải về (kiểm SHA-256), giữ det_10g + w600k_r50 trong models/
DATA_DIR=./data PORT=8000 ALBUM_HOST=0.0.0.0 .venv/bin/python server.py
```

- `http://localhost:8000/manage` — tải ảnh lên (kéo thả nhiều ảnh, mỗi ảnh một request, tiến độ từng ảnh + tổng) và nút **Xử lý**.
- `http://localhost:8000/` — khách tải selfie, tìm ảnh, kéo ngưỡng, bấm ảnh để mở bản xem 2000 px (có link tải ảnh gốc).
- Dữ liệu trong `DATA_DIR`: `originals/` (ảnh gốc), `previews/` (2000 px), `thumbs/` (640 px), `index.db`, `tmp/` (upload dở dang). Khởi động lại với cùng `DATA_DIR` là tìm được ngay, không phải Xử lý lại.

**Xử lý:** chạy trong thread nền của chính server (dùng lại model đã nạp), chỉ một job một lúc (bấm lần hai khi đang chạy → 409 "Đang xử lý rồi"), chỉ làm ảnh chưa lập chỉ mục. `GET /api/process` trả tiến độ (done/total, mặt, lỗi, thời gian). Ảnh lỗi chỉ báo lỗi, không dừng job. Tìm kiếm trong lúc xử lý vẫn chạy (chậm hơn) và thấy kết quả mới ngay khi mỗi ảnh xong.

Indexer dòng lệnh vẫn dùng được (cùng lõi `indexer.py`): `.venv/bin/python index_photos.py --photos <thư mục ảnh> --data-dir <DATA_DIR>`; ghi cả thumbnail và bản xem, nhưng dùng thư mục ảnh riêng (không phải `originals/`) thì không hiện nút tải ảnh gốc trên web khi `originals/` không chứa ảnh đó.

Đo RAM + dung lượng/ảnh qua đúng đường chạy thật (khởi động server, tải ảnh lên bằng HTTP, Xử lý, truy vấn; dùng data dir tạm):

```sh
.venv/bin/python measure.py --photos <thư mục/file ảnh, nên kèm 1 ảnh 20–45 MP> --queries <selfie...> [--env ALBUM_LOW_MEM=1]
```

## Cấu hình (biến môi trường hoặc tham số dòng lệnh)

| Env | Tham số | Mặc định |
|---|---|---|
| `DATA_DIR` (hoặc `ALBUM_DATA_DIR`) | `--data-dir` | `album/data` |
| `ALBUM_MODEL_DIR` | `--model-dir` | `album/models` (container: `/opt/models`) |
| `ALBUM_HOST` / `PORT` (hoặc `ALBUM_PORT`) | `--host` / `--port` | `127.0.0.1` / `8000` (container: `0.0.0.0`) |
| `ALBUM_MAX_UPLOAD_MB` | `--max-upload-mb` | `60` |
| `ALBUM_MIN_FREE_MB` | `--min-free-mb` | `2048` |
| `ALBUM_THREADS` | `--threads` | `0` (theo số lõi) |
| `ALBUM_LOW_MEM` | — | chưa đặt; `1` = tắt arena onnxruntime (RAM đỉnh thấp hơn; container đặt sẵn) |
| `ALBUM_DET_SIZE` | `index_photos.py --det-size` | `1600` |
| `ALBUM_PROVIDERS` | `--providers` | `CPUExecutionProvider` |

## Chặn lạm dụng (vì không có mật khẩu)

- Tải lên: chỉ `.jpg/.jpeg/.png/.webp` **và** Pillow đọc được header (định dạng thật JPEG/PNG/WEBP), tối đa 150 MP, tối đa `ALBUM_MAX_UPLOAD_MB`/ảnh (kiểm `Content-Length` rồi đếm khi stream; ghi thẳng ra đĩa, không buffer RAM). Tên file chỉ giữ phần cuối đã làm sạch (không path traversal); trùng nội dung (SHA-256) → bỏ qua; cùng tên khác nội dung → thêm hậu tố `-<sha8>`, không bao giờ ghi đè.
- Ổ đĩa chứa `DATA_DIR` còn trống < `ALBUM_MIN_FREE_MB` → 507 + thông báo tiếng Việt. `/healthz` và `/api/stats` trả `disk_free`/`disk_total`.
- Một job Xử lý một lúc; selfie ≤ 25 MB và ≤ 100 MP (như trước), chỉ trong RAM.
- Mọi response có `X-Robots-Tag: noindex, nofollow`; `/robots.txt` = `Disallow: /`. Không trả đường dẫn hệ thống: chỉ tên file đã lưu; lỗi job đã gỡ đường dẫn tuyệt đối.
- Kiểm bằng script: `.venv/bin/python dev/check_limits.py --photo <ảnh ≤ 2 MB> --selfie x`.

## Lập chỉ mục

- CLI quét đệ quy `--photos` (jpg/png/webp/bmp/tif); web quét `DATA_DIR/originals/`, áp EXIF orientation, dò **mọi** khuôn mặt
  (ảnh nhóm cũng vậy), lưu embedding 512-d (float16) + bbox + điểm dò vào `data/index.db`
  , thumbnail 640 px vào `thumbs/` và bản xem 2000 px vào `previews/`.
- **Chạy lại được, theo kiểu tăng dần:** ảnh không đổi (cùng đường dẫn + kích thước + mtime +
  det-size) bị bỏ qua. `--prune` xoá ảnh đã biến mất khỏi thư mục; `--rebuild` làm lại từ đầu.
  Một `--data-dir` ứng với đúng một thư mục ảnh.
- File hỏng/không đọc được, hay ảnh làm lỗi bước dò/nhúng: in `CẢNH BÁO` và bỏ qua, các ảnh còn lại vẫn chạy tiếp.
- CLI bỏ giới hạn số điểm ảnh của Pillow (ảnh của chủ nhà); server giữ giới hạn (upload ≤ 150 MP).
- `--min-face` (mặc định 40 px cạnh ngắn của bbox, trên ảnh gốc): bỏ mặt quá nhỏ vì embedding không đáng tin (đo cosine so với bản full-res: ≥66 px 0.93–0.99, ~44 px 0.70–0.95, ~29 px 0.05–0.78). Kích thước mỗi mặt suy ra được từ bbox lưu trong bảng `faces` (`min(x2-x1, y2-y1)`), nên đánh giá ảnh thật có thể thống kê sót theo cỡ mặt; mặt nhỏ hơn ngưỡng không được lưu, muốn đo thì chạy với `--min-face 0`.
- App tự nạp lại chỉ mục khi `index.db` đổi — không cần khởi động lại server.

### Độ phân giải dò (`--det-size`)

Ảnh cưới 20–45 MP; thu về 640 như `FaceAnalysis` mặc định sẽ làm mất mặt nhỏ ở ảnh nhóm.
`--det-size` là cạnh dài sau khi thu nhỏ (giữ tỉ lệ). Mặc định **1600**. Ngoài ra luôn có thêm
các lượt 640 và 320 rẻ tiền rồi gộp bằng NMS, vì SCRFD ngược lại bỏ sót mặt quá to (chân dung/selfie
cận cảnh, mặt chiếm ~80% khung: mất ở 640 và 1600 nhưng thấy ở 320). Cả đường lập chỉ mục lẫn đường truy vấn đều dùng cách này. Embedding luôn cắt từ ảnh gốc độ phân giải đầy đủ; mặt rất to (co >3 lần) được thu nhỏ theo diện tích trước khi warp để khỏi răng cưa. Ảnh xám 16-bit được co về 8-bit đúng thang.

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
- Ảnh tải lên cũng là body thô theo từng file (`POST /api/upload?name=…`, ghi stream ra `DATA_DIR/tmp` rồi đổi tên). Selfie: client gửi body thô tới `/api/search` (không multipart ⇒ không có file tạm do
  framework spool), server giải mã và xử lý trong RAM, không ghi gì ra đĩa.
- Selfie bị giới hạn 25 MB và 100 MP (kiểm kích thước khai báo trong header trước khi giải mã; vượt → HTTP 413). Không đọc được → 400, lỗi khác → 500. Trang `/docs`, `/redoc` đã tắt (mặc định nạp JS từ CDN).
- Kết quả tối đa 300 ảnh; nếu bị cắt, API trả `truncated`/`total_matches` và giao diện báo.
- `download_models.py` ghim SHA-256 của hai file `.onnx`: file thiếu/hỏng/dở dang bị tải lại, ghi qua file `.part` rồi mới đổi tên.
- Selfie nhiều mặt: chọn mặt có (diện tích × điểm dò) lớn nhất, bỏ mặt < 40 px; giao diện vẽ khung đỏ và hiện thông báo tiếng Việt khi có nhiều hơn một mặt. Mọi mặt đều < 40 px → 422 `face_too_small`.
- Điểm = cosine giữa hai embedding chuẩn hoá. Tham khảo: cùng người thường ≳ 0.35–0.4,
  khác người thường < 0.2; vùng 0.2–0.35 là vùng mơ hồ — cần đánh giá trên ảnh thật để chọn
  ngưỡng mặc định (thanh trượt bắt đầu ở 0.35, server chỉ trả về điểm ≥ 0.15, tối đa 300 ảnh).

## Kiểm thử giao diện (chỉ dev)

```sh
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/playwright install chromium
.venv/bin/python dev/e2e.py --photos a.jpg b.jpg big24mp.jpg --selfie s.jpg --out /tmp/shots --pid <PID server>
```
Lái trình duyệt thật: tải lô ảnh lên → Xử lý → tìm bằng selfie → mở ảnh, chụp từng bước (và đo RSS nếu có `--pid`).

## Không commit

`.venv/`, `data/`, `models/`, ảnh, selfie, chỉ mục — đã nằm trong `.gitignore`.
